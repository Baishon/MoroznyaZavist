"""Admin-facing command/callback/message handlers (staff group + log chat)."""
import asyncio
import json
import logging
import re
import shlex
import time
from datetime import datetime, timedelta, timezone

from telegram import (
    ChatPermissions,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    MessageEntity,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    Update,
)
from telegram.constants import ChatType, ParseMode
from telegram.error import BadRequest, Forbidden, RetryAfter
from telegram.ext import ApplicationHandlerStop, ContextTypes

from app.config import (
    ADMIN_LEVEL_TITLES,
    COOPERATION_CHAT_ID,
    LOG_CHAT_ID,
    OFFICIAL_CHANNEL_ID,
    OWNER_ID,
    RULES_CHAT_ID,
    RULES_THREAD_ID,
    TRUSTED_ADMIN_CHAT_ID,
    WORK_CHAT_ID,
)
from app.database.requests import _save_ban_record, _save_profile_record, _save_runtime_snapshot
from app.handlers.candidates import _send_candidate_stage_1
from app.keyboards.inline import (
    _build_active_dialog_admin_keyboard,
    _build_active_session_keyboard,
    _build_astats_gender_editor_keyboard,
    _build_astats_profile_keyboard,
    _build_astats_tip_editor_keyboard,
    _build_info_topic_keyboard,
    _build_info_topic_text,
    _build_main_menu_keyboard,
    _build_setprefix_keyboard,
    _next_info_topic_panel_id,
    _next_prefix_panel_id,
    _prefix_options,
)
from app.services.bans import _apply_ban, enforce_autoban_if_needed
from app.services.messaging import _track_session_message_pair, deliver_message_to_user, notify_blocked_user_in_topic
from app.services.mutes import (
    _admin_mute_permissions,
    _admin_unmute_permissions,
    _clear_admin_mute,
    _get_admin_mute_until,
    _is_admin_muted,
    _mute_remaining_minutes,
)
from app.services.profiles import (
    _admin_default_prefix,
    _admin_matches_gender,
    _admin_supports_mood,
    _build_admin_stats_text,
    _build_user_stats_text,
    _candidate_tip_value,
    _can_use_moderation_commands,
    _effective_admin_level,
    _ensure_profile,
    _has_admin_ban_immunity,
    _has_full_access_prefix,
    _has_admin_rights_level_1_5,
    _has_prefix,
    _is_active_admin_candidate,
    _is_topic_admin,
    _resolve_ban_target,
    _resolve_warn_target,
    _record_admin_reputation_activity,
    _set_last_admin_tag_for_user,
    _set_user_blocked_bot_state,
    is_user_banned,
    format_ban_remaining,
    refresh_timed_warnings,
)

from app.services.topics import (
    _get_topic_state,
    _has_suspicious_username,
    _is_admin_command_text,
    _is_member_of_chat,
    _is_rp_action_text,
    _is_special_admin_chat,
    _next_suspicious_review_id,
    _parse_rp_trigger_text,
    _parse_topic_url,
    _render_rp_action_message,
    _rp_display_name_admin,
    _rp_display_name_user,
    _set_topic_state,
    _special_admin_chat_ids,
    _topic_url,
)

MAX_MODERATION_DURATION_SECONDS = 365 * 24 * 60 * 60


def _format_kyiv_datetime(timestamp: float | None = None) -> str:
    kyiv_tz = timezone(timedelta(hours=3))
    when = datetime.fromtimestamp(float(timestamp if timestamp is not None else time.time()), tz=kyiv_tz)
    return when.strftime("%d.%m.%Y %H:%M:%S")


def _recent_info_topic_actions(actions: list[dict], hours: int = 48) -> list[dict]:
    cutoff = datetime.now(timezone(timedelta(hours=3))) - timedelta(hours=hours)
    recent = []
    for entry in actions:
        try:
            action_time = datetime.strptime(
                str(entry.get("date", "")), "%d.%m.%Y %H:%M:%S"
            ).replace(tzinfo=timezone(timedelta(hours=3)))
        except (TypeError, ValueError):
            continue
        if action_time >= cutoff:
            recent.append(entry)
    return recent


INFO_TOPIC_ACTION_LABELS = {
    "OPEN_PANEL": "Открытие панели",
    "CLOSE_SESSION": "Закрытие сессии",
    "PAUSE_SESSION": "Приостановка сессии",
    "RESUME_SESSION": "Возобновление сессии",
    "WARN_USER": "Выдача предупреждения",
    "VIEW_PROFILE": "Просмотр профиля",
    "DECLINE_USER": "Отказ от пользователя",
    "TAKE_USER": "Принятие пользователя",
    "LOG_VIEW": "Просмотр логов",
    "CANCEL_ACTION": "Отмена действия",
}


def _normalize_info_topic_action(action: str | None, fallback: str | None = None) -> str:
    if action:
        return str(action)
    return str(fallback or "UNKNOWN")


def _ensure_info_topic_session_record(
    context: ContextTypes.DEFAULT_TYPE,
    panel_id: str | None,
    target_user_id: str | int | None,
    issuer_user_id: str | int | None,
    topic_link: str | None = None,
) -> dict:
    logs_bucket = context.application.bot_data.setdefault("info_topic_logs", {})
    record_key = str(panel_id) if panel_id else str(target_user_id or "unknown")
    session = logs_bucket.setdefault(record_key, {
        "panel_id": record_key,
        "target_user_id": str(target_user_id) if target_user_id is not None else None,
        "issuer_user_id": str(issuer_user_id) if issuer_user_id is not None else None,
        "topic_link": topic_link,
        "opened_at": _format_kyiv_datetime(),
        "last_action_at": _format_kyiv_datetime(),
        "status": "active",
        "stats": {
            "total_actions": 0,
            "admin_ids": [],
            "warnings": 0,
            "close_count": 0,
            "pause_count": 0,
            "resume_count": 0,
            "view_profile_count": 0,
            "decline_count": 0,
            "take_count": 0,
            "log_view_count": 0,
        },
        "actions": [],
    })
    if target_user_id is not None and session.get("target_user_id") is None:
        session["target_user_id"] = str(target_user_id)
    if issuer_user_id is not None and session.get("issuer_user_id") is None:
        session["issuer_user_id"] = str(issuer_user_id)
    if topic_link:
        session["topic_link"] = topic_link
    session["panel_id"] = record_key
    return session


def _record_info_topic_action(
    context: ContextTypes.DEFAULT_TYPE,
    target_user_id: str | int,
    admin_user_id: str | int,
    action_type: str,
    reason: str | None = None,
    panel_id: str | None = None,
    duration: float | int | None = None,
    topic_link: str | None = None,
) -> None:
    target_key = str(target_user_id)
    admin_key = str(admin_user_id)

    if panel_id is None:
        panels = context.application.bot_data.get("info_topic_panels", {}) or {}
        for candidate_id, panel in panels.items():
            if str(panel.get("target_user_id") or "") == target_key:
                panel_id = str(candidate_id)
                break
    if panel_id is None:
        return

    session = _ensure_info_topic_session_record(context, panel_id, target_key, admin_key, topic_link)
    active = (context.application.bot_data.get("active_chats", {}) or {}).get(target_key) or {}
    profiles = (context.application.bot_data.get("profiles", {}) or {})
    current_profile = profiles.get(admin_key, {})
    admin_tag = str(
        current_profile.get("tag_admin")
        or current_profile.get("username")
        or active.get("admin_tag")
        or active.get("admin_username")
        or f"id{admin_user_id}"
    )

    action_name = INFO_TOPIC_ACTION_LABELS.get(str(action_type), str(action_type or "Неизвестное действие"))
    entry = {
        "action_type": str(action_type),
        "admin_id": admin_key,
        "tag_admin": admin_tag,
        "button": action_name,
        "date": _format_kyiv_datetime(),
        "reason": reason,
        "duration": duration,
    }
    if reason:
        entry["reason"] = reason
    if duration is not None:
        entry["duration"] = duration

    session["actions"].append(entry)
    session["last_action_at"] = entry["date"]
    stats = session.setdefault("stats", {
        "total_actions": 0,
        "admin_ids": [],
        "warnings": 0,
        "close_count": 0,
        "pause_count": 0,
        "resume_count": 0,
        "view_profile_count": 0,
        "decline_count": 0,
        "take_count": 0,
        "log_view_count": 0,
    })
    stats["total_actions"] = int(stats.get("total_actions", 0)) + 1
    admin_ids = stats.setdefault("admin_ids", [])
    if admin_key not in admin_ids:
        admin_ids.append(admin_key)

    if str(action_type) == "WARN_USER":
        stats["warnings"] = int(stats.get("warnings", 0)) + 1
    elif str(action_type) == "CLOSE_SESSION":
        stats["close_count"] = int(stats.get("close_count", 0)) + 1
        session["status"] = "closed"
    elif str(action_type) == "PAUSE_SESSION":
        stats["pause_count"] = int(stats.get("pause_count", 0)) + 1
        session["status"] = "paused"
    elif str(action_type) == "RESUME_SESSION":
        stats["resume_count"] = int(stats.get("resume_count", 0)) + 1
        session["status"] = "active"
    elif str(action_type) == "VIEW_PROFILE":
        stats["view_profile_count"] = int(stats.get("view_profile_count", 0)) + 1
    elif str(action_type) == "DECLINE_USER":
        stats["decline_count"] = int(stats.get("decline_count", 0)) + 1
    elif str(action_type) == "TAKE_USER":
        stats["take_count"] = int(stats.get("take_count", 0)) + 1
    elif str(action_type) == "LOG_VIEW":
        stats["log_view_count"] = int(stats.get("log_view_count", 0)) + 1

    if str(action_type) not in {"CLOSE_SESSION", "PAUSE_SESSION", "RESUME_SESSION"}:
        session["status"] = session.get("status") or "active"


def _is_info_topic_session_admin(context: ContextTypes.DEFAULT_TYPE, panel: dict | None, user_id: int | str | None) -> bool:
    if panel is None or user_id is None:
        return False
    user_key = str(user_id)
    if str(panel.get("issuer_user_id") or "") == user_key:
        return True
    target_user_id = str(panel.get("target_user_id") or "")
    if not target_user_id:
        return False
    active = (context.application.bot_data.get("active_chats", {}) or {}).get(target_user_id) or {}
    return str(active.get("admin_id") or "") == user_key


def _build_info_topic_logs_text(context: ContextTypes.DEFAULT_TYPE, target_user_id: str | int, panel_id: str | None = None) -> str:
    logs_bucket = (context.application.bot_data.get("info_topic_logs", {}) or {})
    session = logs_bucket.get(str(panel_id)) if panel_id else logs_bucket.get(str(target_user_id))
    if session is None:
        session = logs_bucket.get(str(target_user_id)) or {}
    if not session:
        return "👁‍🗨Действия совершенные inline-кнопками\n\nПока ничего не записано."

    actions = session.get("actions", []) or []
    stats = session.get("stats", {}) or {}
    panel_label = session.get("panel_id") or str(panel_id or target_user_id)
    target_label = session.get("target_user_id") or str(target_user_id)
    issuer_label = session.get("issuer_user_id") or "неизвестно"
    topic_link = session.get("topic_link") or "не указана"
    status = session.get("status") or "active"
    last_action = session.get("last_action_at") or "неизвестно"

    lines = [
        "👁‍🗨Действия совершенные inline-кнопками",
        f"- Панель: #{panel_label}",
        f"- Пользователь: {target_label}",
        f"- Админ открытия: {issuer_label}",
        f"- Тема: {topic_link}",
        f"- Статус: {status}",
        f"- Последнее действие: {last_action}",
        "",
        "📊 Статистика:",
        f"- Всего действий: {stats.get('total_actions', len(actions))}",
        f"- Уникальных админов: {len(stats.get('admin_ids', []))}",
        f"- Предупреждения: {stats.get('warnings', 0)}",
        f"- Закрытия: {stats.get('close_count', 0)}",
        f"- Приостановки: {stats.get('pause_count', 0)}",
        f"- Возобновления: {stats.get('resume_count', 0)}",
        "",
        "🧾 Последние действия:",
    ]

    recent_actions = actions[-20:]
    if not recent_actions:
        lines.append("- Нет действий.")
    else:
        for entry in recent_actions:
            label = entry.get("button") or INFO_TOPIC_ACTION_LABELS.get(entry.get("action_type"), entry.get("action_type") or "Действие")
            text = f'- {entry.get("date", "неизвестно")} | {entry.get("tag_admin", "админ")} | {label}'
            reason = entry.get("reason")
            if reason:
                text = f"{text}\n  Причина: {reason}"
            lines.append(text)
    return "\n".join(lines)

# ==== SECTION: Forum-topic management ====
# /topic del|close|open — lets a topic admin delete, close, or reopen a
# forum topic in one of the allowed chats (work/log/cooperation).
async def topic_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_user:
        return

    allowed_topic_chats = {WORK_CHAT_ID, LOG_CHAT_ID, COOPERATION_CHAT_ID, TRUSTED_ADMIN_CHAT_ID}
    if update.effective_chat.id not in allowed_topic_chats:
        await update.message.reply_text("Команда /topic доступна только в спец-группах.")
        return

    if not _is_topic_admin(context, str(update.effective_user.id)):
        await update.message.reply_text("Команда доступна только администраторам 4 категории и выше.")
        return

    raw_text = update.message.text or ""
    cmd_entity = update.message.entities[0] if update.message.entities else None
    cmd_len = cmd_entity.length if cmd_entity and cmd_entity.type == "bot_command" else len("/topic")
    args_text = raw_text[cmd_len:].strip()
    if not args_text:
        await update.message.reply_text('Используйте: /topic "url" del|close|open')
        return

    try:
        parts = shlex.split(args_text)
    except ValueError:
        await update.message.reply_text('Некорректный формат. Используйте: /topic "url" del|close|open')
        return

    if len(parts) < 2:
        await update.message.reply_text('Используйте: /topic "url" del|close|open')
        return

    topic_url = parts[0]
    action = parts[1].strip().lower()
    if action not in {"del", "close", "open"}:
        await update.message.reply_text('Доступные действия: del, close, open.')
        return

    chat_id, topic_id = _parse_topic_url(topic_url)
    if chat_id is None or topic_id is None:
        await update.message.reply_text('Некорректный URL темы. Нужен формат вида https://t.me/c/<chat_id>/<topic_id>.')
        return

    if chat_id not in allowed_topic_chats:
        await update.message.reply_text("Эта команда управляет только темами в спец-группах.")
        return

    state = _get_topic_state(context, chat_id, topic_id)

    if action == "del":
        try:
            await context.bot.delete_forum_topic(chat_id=chat_id, message_thread_id=topic_id)
            _set_topic_state(context, chat_id, topic_id, "deleted")
            await update.message.reply_text(f"✅Тема {topic_url} удалена.")
        except BadRequest as e:
            text = str(e).lower()
            if "not found" in text or "message thread" in text or "topic" in text:
                await update.message.reply_text("Тема не найдена или уже удалена.")
            else:
                await update.message.reply_text(f"Не удалось удалить тему: {e}")
        except Exception as e:
            await update.message.reply_text(f"Не удалось удалить тему: {e}")
        return

    if action == "close":
        if state == "closed":
            await update.message.reply_text("Тема уже закрыта.")
            return
        if state == "deleted":
            await update.message.reply_text("Тема уже удалена.")
            return

        close_method = getattr(context.bot, "close_forum_topic", None)
        if close_method is None:
            await update.message.reply_text("В этой версии бота закрытие тем недоступно.")
            return

        try:
            await close_method(chat_id=chat_id, message_thread_id=topic_id)
            _set_topic_state(context, chat_id, topic_id, "closed")
            await update.message.reply_text(f"✅Тема {topic_url} закрыта.")
        except BadRequest as e:
            text = str(e).lower()
            if "already closed" in text or "closed" in text:
                _set_topic_state(context, chat_id, topic_id, "closed")
                await update.message.reply_text("Тема уже закрыта.")
            elif "not found" in text or "message thread" in text or "topic" in text:
                await update.message.reply_text("Тема не найдена.")
            else:
                await update.message.reply_text(f"Не удалось закрыть тему: {e}")
        except Exception as e:
            await update.message.reply_text(f"Не удалось закрыть тему: {e}")
        return

    if action == "open":
        if state == "open":
            await update.message.reply_text("Тема уже открыта.")
            return
        if state == "deleted":
            await update.message.reply_text("Тема уже удалена.")
            return

        reopen_method = getattr(context.bot, "reopen_forum_topic", None)
        if reopen_method is None:
            await update.message.reply_text("В этой версии бота открытие тем недоступно.")
            return

        try:
            await reopen_method(chat_id=chat_id, message_thread_id=topic_id)
            _set_topic_state(context, chat_id, topic_id, "open")
            await update.message.reply_text(f"✅Тема {topic_url} открыта.")
        except BadRequest as e:
            text = str(e).lower()
            if "already open" in text or "not closed" in text or "open" in text:
                _set_topic_state(context, chat_id, topic_id, "open")
                await update.message.reply_text("Тема уже открыта.")
            elif "not found" in text or "message thread" in text or "topic" in text:
                await update.message.reply_text("Тема не найдена.")
            else:
                await update.message.reply_text(f"Не удалось открыть тему: {e}")
        except Exception as e:
            await update.message.reply_text(f"Не удалось открыть тему: {e}")
        return



# ==== SECTION: Admin rights & mutes ====
# /makeadmin & /prava grant/inspect staff levels (ADMIN_LEVEL_TITLES);
# /amute, /unmute, and admin_mute_guard_handler enforce a temporary mute
# stored inline on the target admin's profile (see app.services.mutes).
async def _makeadmin_impl(update: Update, context: ContextTypes.DEFAULT_TYPE, owner_bypass: bool = False):
    if update.effective_chat.id != LOG_CHAT_ID or not update.message:
        return

    if owner_bypass and update.effective_user and update.effective_user.id == OWNER_ID:
        issuer_level = 5
    else:
        issuer_profile = _ensure_profile(
            context,
            str(update.effective_user.id),
            update.effective_user.username or f"id{update.effective_user.id}",
        )
        issuer_level = _effective_admin_level(issuer_profile)
        if issuer_level < 4:
            await update.message.reply_text("Команда доступна только администраторам 4 категории и выше.")
            return

    raw_text = update.message.text or ""
    cmd_entity = update.message.entities[0] if update.message.entities else None
    cmd_len = cmd_entity.length if cmd_entity and cmd_entity.type == "bot_command" else len("/makeadmin")
    args_text = raw_text[cmd_len:].strip()
    if not args_text:
        await update.message.reply_text(
            'prefix_text: устанавливается после назначения (через /setprefix)\n'
            'Используйте: /makeadmin "id_profile" "lvl"'
        )
        return

    try:
        parts = shlex.split(args_text)
    except ValueError:
        await update.message.reply_text(
            'prefix_text: устанавливается после назначения (через /setprefix)\n'
            'Некорректный формат. Используйте: /makeadmin "id_profile" "lvl"'
        )
        return

    if len(parts) < 2:
        await update.message.reply_text(
            'prefix_text: устанавливается после назначения (через /setprefix)\n'
            'Используйте: /makeadmin "id_profile" "lvl"'
        )
        return

    target_identifier = parts[0]
    lvl_raw = parts[1].strip()
    if not lvl_raw.isdigit():
        await update.message.reply_text(
            "prefix_text: устанавливается после назначения (через /setprefix)\n"
            "lvl должен быть числом от 0 до 5."
        )
        return
    lvl = int(lvl_raw)
    rank_title = ADMIN_LEVEL_TITLES.get(lvl)
    if lvl != 0 and not rank_title:
        await update.message.reply_text("Доступные уровни: 0, 1, 2, 3, 4, 5.")
        return
    if issuer_level == 4 and lvl not in {0, 1, 2, 3}:
        await update.message.reply_text("Администратор 4 категории может выдавать только уровни 1, 2, 3 (или снимать права в 0).")
        return

    target_user_id, profile = _resolve_warn_target(context, target_identifier)
    if not profile:
        await update.message.reply_text(f'Пользователь с id_profile "{target_identifier}" не найден.')
        return

    preview_prefix = str(profile.get("prefix") or "").strip()
    if not preview_prefix and lvl > 0:
        preview_prefix = str(_admin_default_prefix(lvl) or "").strip()
    preview_prefix_text = preview_prefix or "не установлен"

    target_user_id_int = int(target_user_id)
    if lvl != 0:
        in_work_chat = await _is_member_of_chat(context, WORK_CHAT_ID, target_user_id_int)
        if not in_work_chat:
            await update.message.reply_text("Выдача невозможна: добавьте пользователя в рабочий чат.")
            return

        in_channel = await _is_member_of_chat(context, OFFICIAL_CHANNEL_ID, target_user_id_int)
        if not in_channel:
            await update.message.reply_text("Выдача невозможна: пользователь не подписан на официальный тгк бота.")
            return

    if lvl > 1:
        candidate_status = str(profile.get("admin_candidate_status") or "")
        tag_admin = str(profile.get("tag_admin") or "").strip()
        biography_admin = str(profile.get("biography_admin") or "").strip()
        if candidate_status != "approved" or not tag_admin or not biography_admin:
            await update.message.reply_text(
                f"Префикс: {preview_prefix_text}\n"
                "Выдача невозможна: сначала пользователь должен пройти кандидатуру 1 уровня и заполнить анкету (тег и биография)."
            )
            return

    active = context.application.bot_data.setdefault("active_chats", {})
    active.pop(str(target_user_id), None)
    admin_username = update.effective_user.username or f"id{update.effective_user.id}"

    if lvl == 0:
        current_admin_level = _effective_admin_level(profile)
        if current_admin_level <= 0:
            await update.message.reply_text(
                f'Снятие невозможно: у пользователя "{target_identifier}" нет активных админ-прав.'
            )
            return

        profile["admin_level"] = 0
        profile["admin_rank"] = ""
        profile["admin_candidate"] = False
        profile["admin_candidate_status"] = "none"
        profile.pop("prefixes", None)
        profile.pop("prefix", None)
        state_map = context.application.bot_data.setdefault("admin_candidate_state", {})
        state_map.pop(str(target_user_id), None)

        await update.message.reply_text(
            f'☑️Пользователь "{target_identifier}" был разжалован, админ-права сняты.'
        )

        try:
            await context.bot.send_message(
                chat_id=target_user_id_int,
                text=(
                    "⚠️Ваши админ-права были сняты.\n\n"
                    f'Решение принял: "{admin_username}".'
                ),
            )
        except Forbidden:
            await update.message.reply_text("Пользователь заблокировал бота. Уведомление о разжаловании отправить не удалось.")
        except Exception as e:
            logging.exception("makeadmin demotion notify failed: %s", e)
        _save_profile_record(context, str(target_user_id))
        return

    profile["admin_level"] = lvl
    profile["admin_rank"] = rank_title

    had_prefix = str(profile.get("prefix") or "").strip()
    if not had_prefix:
        default_prefix = _admin_default_prefix(lvl)
        if default_prefix:
            profile["prefix"] = default_prefix
    prefix_text = str(profile.get("prefix") or "не установлен")

    if lvl == 1:
        profile["admin_candidate"] = True
        profile["admin_candidate_status"] = "onboarding"

        candidate_state = context.application.bot_data.setdefault("admin_candidate_state", {})
        candidate_state[str(target_user_id)] = {
            "stage": "await_tag",
            "initiated_by": update.effective_user.id,
            "initiated_by_username": admin_username,
        }

        await update.message.reply_text(
            f'☑️Пользователь "{target_identifier}" был успешно назначен на админ-права, инструктаж ему отправлен в ЛС.\n'
            f'Префикс: {prefix_text}\n'
            'Уровень: 1'
        )

        try:
            await context.bot.send_message(chat_id=target_user_id_int, text="⚙️Клавиатура обновлена.", reply_markup=ReplyKeyboardRemove())
            await context.bot.send_message(
                chat_id=target_user_id_int,
                text=(
                    f"Префикс: {prefix_text}\n"
                    "❤️‍🔥Поздравляем! Вы были назначены администратором 1 уровня. "
                    f'Назначил вас "{admin_username}"\n\n'
                    "📝Перед началой работы вам нужно заполнить информацию о себе..."
                ),
            )
            await asyncio.sleep(2)
            await _send_candidate_stage_1(context, target_user_id_int)
        except Forbidden:
            _set_user_blocked_bot_state(context, str(target_user_id), True)
            await update.message.reply_text("Пользователь заблокировал бота. Инструктаж в ЛС отправить не удалось.")
        except Exception as e:
            logging.exception("makeadmin onboarding send failed: %s", e)
        return

    profile["admin_candidate"] = False
    profile["admin_candidate_status"] = "approved"
    state_map = context.application.bot_data.setdefault("admin_candidate_state", {})
    state_map.pop(str(target_user_id), None)

    await update.message.reply_text(
        f'☑️Пользователь "{target_identifier}" был успешно назначен.\n'
        f'Префикс: {prefix_text}\n'
        f'Уровень: {lvl} ({rank_title}).'
    )

    try:
        await context.bot.send_message(
            chat_id=target_user_id_int,
            text=(
                f"❤️‍🔥Поздравляем! Вы были назначены: {rank_title}. "
                f'Назначил вас "{admin_username}"'
            ),
        )
    except Forbidden:
        _set_user_blocked_bot_state(context, str(target_user_id), True)
        await update.message.reply_text("Пользователь заблокировал бота. Уведомление отправить не удалось.")
    except Exception as e:
        logging.exception("makeadmin notify failed: %s", e)
    _save_profile_record(context, str(target_user_id))



async def makeadmin_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _makeadmin_impl(update, context, owner_bypass=False)


async def prava_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or update.effective_user.id != OWNER_ID:
        if update.message:
            await update.message.reply_text("Команда доступна только владельцу бота.")
        return

    owner_profile = _ensure_profile(context, str(OWNER_ID), update.effective_user.username or f"id{OWNER_ID}")
    owner_profile["admin_level"] = 5
    owner_profile["admin_rank"] = ADMIN_LEVEL_TITLES[5]
    owner_profile["admin_candidate"] = False
    owner_profile["admin_candidate_status"] = "approved"
    state_map = context.application.bot_data.setdefault("admin_candidate_state", {})
    state_map.pop(str(OWNER_ID), None)
    _save_profile_record(context, str(OWNER_ID))

    if update.message:
        await update.message.reply_text("✅Права 5 категории выданы пользователю с Telegram ID 7545068007.")



async def amute_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != WORK_CHAT_ID or not update.message:
        return

    if not _can_use_moderation_commands(context, str(update.effective_user.id)):
        await update.message.reply_text("Команда доступна только администраторам 2 категории и выше.")
        return

    raw_text = update.message.text or ""
    cmd_entity = update.message.entities[0] if update.message.entities else None
    cmd_len = cmd_entity.length if cmd_entity and cmd_entity.type == "bot_command" else len("/amute")
    args_text = raw_text[cmd_len:].strip()
    if not args_text:
        await update.message.reply_text('Укажите id_profile и minute: /amute "id_profile" "minute"')
        return

    try:
        parts = shlex.split(args_text)
    except ValueError:
        await update.message.reply_text('Некорректный формат. Используйте: /amute "id_profile" "minute"')
        return

    if len(parts) < 2:
        await update.message.reply_text('Укажите id_profile и minute: /amute "id_profile" "minute"')
        return

    target_identifier = parts[0]
    minute_raw = parts[1].strip()
    if not minute_raw.isdigit():
        await update.message.reply_text("minute должен быть целым числом больше 0.")
        return

    minutes = int(minute_raw)
    if minutes <= 0:
        await update.message.reply_text("minute должен быть целым числом больше 0.")
        return
    if minutes * 60 > MAX_MODERATION_DURATION_SECONDS:
        await update.message.reply_text("Максимальный срок мута — 365 дней.")
        return

    target_user_id, profile = _resolve_warn_target(context, target_identifier)
    if not profile:
        await update.message.reply_text(f'Пользователь с id_profile "{target_identifier}" не найден.')
        return

    if _has_admin_rights_level_1_5(profile):
        await update.message.reply_text(f'Невозможно выдать мут: id_profile #{profile.get("id_profile")} имеет админ-права 1-5 уровня.')
        return

    try:
        until_date = datetime.utcnow() + timedelta(minutes=minutes)
        await context.bot.restrict_chat_member(
            chat_id=WORK_CHAT_ID,
            user_id=int(target_user_id),
            permissions=_admin_mute_permissions(),
            until_date=until_date,
            use_independent_chat_permissions=True,
        )
    except Forbidden:
        await update.message.reply_text("Не удалось выдать мут: у бота нет прав ограничивать участников в этой группе.")
        return
    except Exception:
        await update.message.reply_text("Не удалось выдать мут через настройки Telegram.")
        return

    profile["mute_until"] = time.time() + minutes * 60
    profile["mute_reason"] = f"mute for {minutes} minutes"
    profile["mute_set_by"] = str(update.effective_user.id)

    await update.message.reply_text(f'✅Администратор id_profile #{profile.get("id_profile")} получил мут на {minutes} мин.')

    try:
        await context.bot.send_message(
            chat_id=int(target_user_id),
            text=f'⛔️Вам выдан мут на {minutes} мин. В это время ваши сообщения в теме не будут отправляться пользователю.',
        )
    except Exception:
        pass
    _save_profile_record(context, str(target_user_id))



async def unmute_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != WORK_CHAT_ID or not update.message:
        return

    if not _can_use_moderation_commands(context, str(update.effective_user.id)):
        await update.message.reply_text("Команда доступна только администраторам 2 категории и выше.")
        return

    raw_text = update.message.text or ""
    cmd_entity = update.message.entities[0] if update.message.entities else None
    cmd_len = cmd_entity.length if cmd_entity and cmd_entity.type == "bot_command" else len("/aunmute")
    args_text = raw_text[cmd_len:].strip()
    if not args_text:
        await update.message.reply_text('Укажите id_profile: /aunmute "id_profile"')
        return

    try:
        parts = shlex.split(args_text)
    except ValueError:
        await update.message.reply_text('Некорректный формат. Используйте: /aunmute "id_profile"')
        return

    if len(parts) < 1:
        await update.message.reply_text('Укажите id_profile: /aunmute "id_profile"')
        return

    target_identifier = parts[0]
    target_user_id, profile = _resolve_warn_target(context, target_identifier)
    if not profile:
        await update.message.reply_text(f'Пользователь с id_profile "{target_identifier}" не найден.')
        return

    try:
        await context.bot.restrict_chat_member(
            chat_id=WORK_CHAT_ID,
            user_id=int(target_user_id),
            permissions=_admin_unmute_permissions(),
            use_independent_chat_permissions=True,
        )
    except Forbidden:
        await update.message.reply_text("Не удалось снять мут: у бота нет прав изменять ограничения участников в этой группе.")
        return
    except Exception:
        await update.message.reply_text("Не удалось снять мут через настройки Telegram.")
        return

    _clear_admin_mute(profile)
    await update.message.reply_text(f'✅С администратора id_profile #{profile.get("id_profile")} снят мут.')

    try:
        await context.bot.send_message(
            chat_id=int(target_user_id),
            text="✅С вас снят мут. Теперь ваши сообщения снова будут отправляться пользователю.",
        )
    except Exception:
        pass



async def admin_mute_guard_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = getattr(update, "message", None)
    if message is None or not update.effective_user:
        return
    if update.effective_chat.id != WORK_CHAT_ID or update.effective_user.is_bot:
        return

    profile = (context.application.bot_data.get("profiles", {}) or {}).get(str(update.effective_user.id), {})
    if not _is_admin_muted(profile):
        if _get_admin_mute_until(profile) and not _is_admin_muted(profile):
            _clear_admin_mute(profile)
        return

    text = (message.text or "").strip()
    if text.startswith("/amute") or text.startswith("/aunmute"):
        return

    remaining_minutes = _mute_remaining_minutes(profile)
    try:
        await update.message.reply_text(f"⛔️Вы находитесь в муте еще {remaining_minutes} мин.")
    except Exception:
        pass
    raise ApplicationHandlerStop


# ==== SECTION: Log-chat routing & cooperation ads ====
# log_command_router dispatches "/"-commands typed in the log chat;
# cooperation_admin_command_guard/sp/anpiar manage the
# cooperation-prefix ad text an admin can post in the cooperation chat.
async def log_command_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = getattr(update, "message", None)
    if message is None or not update.effective_user:
        return

    if update.effective_chat.id != LOG_CHAT_ID:
        return

    text = (message.text or "").strip()
    if not text.startswith("/"):
        return

    command = text.split(maxsplit=1)[0].split("@")[0].lower()
    command_handlers = {
        "/addrules": add_rules_handler,
        "/delrules": del_rule_handler,
        "/makeadmin": makeadmin_command_handler,
        "/setprefix": setprefix_command_handler,
        "/setrep": setrep_command_handler,
        "/anpiar": anpiar_command_handler,
        "/fullstats": fullstats_command_handler,
        "/sp": sendpiar_command_handler,
        "/pm": pm_command_handler,
        "/prava": prava_command_handler,
        "/ban": ban_command_handler,
        "/pban": permanent_ban_command_handler,
        "/unban": unban_command_handler,
        "/warn": warn_command_handler,
        "/unwarn": unwarn_command_handler,
        "/stats": stats_command_handler,
        "/astats": astats_command_handler,
        "/info_topic": info_topic_command_handler,
        "/givetopic": givetopic_command_handler,
        "/taketopic": taketopic_command_handler,
        "/dump_maps": dump_maps_handler,
    }

    handler = command_handlers.get(command)
    if handler is None:
        return

    await handler(update, context)
    raise ApplicationHandlerStop


async def givetopic_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if (
        not update.message
        or not update.effective_user
        or update.effective_chat.id not in {LOG_CHAT_ID, WORK_CHAT_ID}
    ):
        return

    issuer_profile = _ensure_profile(
        context,
        str(update.effective_user.id),
        update.effective_user.username or f"id{update.effective_user.id}",
    )
    if _effective_admin_level(issuer_profile) < 1:
        await update.message.reply_text("Команда доступна только администраторам 1 категории и выше.")
        return

    try:
        parts = shlex.split(update.message.text or "")
    except ValueError:
        await update.message.reply_text('Используйте: /givetopic "id_profile" "url_topic"')
        return
    if len(parts) < 3:
        await update.message.reply_text('Используйте: /givetopic "id_profile" "url_topic"')
        return

    target_identifier = parts[1].strip()
    topic_link = parts[2].strip()
    chat_id, topic_id = _parse_topic_url(topic_link)
    if chat_id is None or topic_id is None:
        await update.message.reply_text("Некорректная ссылка на тему.")
        return

    topic_map = context.application.bot_data.get("topic_user_map", {}) or {}
    target_user_id = topic_map.get(topic_id) or topic_map.get(str(topic_id))
    active = (
        context.application.bot_data.get("active_chats", {}) or {}
    ).get(str(target_user_id)) if target_user_id else None
    if (
        not active
        or not active.get("active")
        or int(active.get("chat_id", 0) or 0) != int(chat_id)
        or int(active.get("topic_id", 0) or 0) != int(topic_id)
    ):
        await update.message.reply_text("Сессия закрыта или неактуальна: выдавать доступ уже нечему.")
        return

    if (
        str(active.get("admin_id") or "") != str(update.effective_user.id)
        and not _has_full_access_prefix(issuer_profile)
    ):
        await update.message.reply_text(
            "Выдать доступ можно только в своей сессии, которую вы приняли, "
            "или администратору с высшим префиксом."
        )
        return

    recipient_id, recipient_profile = _resolve_warn_target(context, target_identifier)
    if not recipient_profile:
        await update.message.reply_text(f'Администратор с id_profile "{target_identifier}" не найден.')
        return
    if not _has_admin_rights_level_1_5(recipient_profile):
        await update.message.reply_text("У указанного пользователя нет действующих админских прав.")
        return
    if _is_active_admin_candidate(context, recipient_id):
        await update.message.reply_text("Кандидату с неодобренной заявкой нельзя выдавать доступ к сессии.")
        return

    allowed_admin_ids = active.setdefault("allowed_admin_ids", [])
    if recipient_id not in {str(value) for value in allowed_admin_ids}:
        allowed_admin_ids.append(recipient_id)
    _save_runtime_snapshot(context)
    try:
        await context.bot.send_message(
            chat_id=int(recipient_id),
            text=(
                f"✅Вам выдано право писать в теме сессии по ссылке:\n{topic_link}\n"
                "Право действует, пока сессия активна или пока его не отберут."
            ),
        )
    except Exception:
        logging.exception("failed to notify admin %s about topic access grant", recipient_id)
    await update.message.reply_text(
        f"✅Администратору с id_profile #{target_identifier} выдано право писать в указанной активной теме."
    )


async def taketopic_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if (
        not update.message
        or not update.effective_user
        or update.effective_chat.id not in {LOG_CHAT_ID, WORK_CHAT_ID}
    ):
        return

    issuer_profile = _ensure_profile(
        context,
        str(update.effective_user.id),
        update.effective_user.username or f"id{update.effective_user.id}",
    )
    if _effective_admin_level(issuer_profile) < 1:
        await update.message.reply_text("Команда доступна только администраторам 1 категории и выше.")
        return

    try:
        parts = shlex.split(update.message.text or "")
    except ValueError:
        await update.message.reply_text('Используйте: /taketopic "id_profile" "url_topic"')
        return
    if len(parts) < 3:
        await update.message.reply_text('Используйте: /taketopic "id_profile" "url_topic"')
        return

    target_identifier = parts[1].strip()
    topic_link = parts[2].strip()
    chat_id, topic_id = _parse_topic_url(topic_link)
    if chat_id is None or topic_id is None:
        await update.message.reply_text("Некорректная ссылка на тему.")
        return

    topic_map = context.application.bot_data.get("topic_user_map", {}) or {}
    target_user_id = topic_map.get(topic_id) or topic_map.get(str(topic_id))
    active = (
        context.application.bot_data.get("active_chats", {}) or {}
    ).get(str(target_user_id)) if target_user_id else None
    if (
        not active
        or not active.get("active")
        or int(active.get("chat_id", 0) or 0) != int(chat_id)
        or int(active.get("topic_id", 0) or 0) != int(topic_id)
    ):
        await update.message.reply_text("Сессия закрыта или неактуальна: отзывать доступ уже нечего.")
        return

    if (
        str(active.get("admin_id") or "") != str(update.effective_user.id)
        and not _has_full_access_prefix(issuer_profile)
    ):
        await update.message.reply_text(
            "Отобрать доступ можно только в своей сессии, которую вы приняли, "
            "или администратору с высшим префиксом."
        )
        return

    recipient_id, recipient_profile = _resolve_warn_target(context, target_identifier)
    if not recipient_profile:
        await update.message.reply_text(f'Администратор с id_profile "{target_identifier}" не найден.')
        return

    allowed_admin_ids = active.setdefault("allowed_admin_ids", [])
    normalized_allowed = {str(value) for value in allowed_admin_ids}
    if str(recipient_id) not in normalized_allowed:
        await update.message.reply_text(
            f'У администратора с id_profile #{target_identifier} нет выданного доступа к этой теме.'
        )
        return

    active["allowed_admin_ids"] = [
        str(value) for value in allowed_admin_ids if str(value) != str(recipient_id)
    ]
    _save_runtime_snapshot(context)
    try:
        await context.bot.send_message(
            chat_id=int(recipient_id),
            text=(
                f"⚠️У вас отозвано право писать в теме сессии по ссылке:\n{topic_link}\n"
                "Теперь сообщения в этой теме отправлять нельзя."
            ),
        )
    except Exception:
        logging.exception("failed to notify admin %s about topic access removal", recipient_id)
    await update.message.reply_text(
        f"✅У администратора с id_profile #{target_identifier} отобрано право писать в указанной теме."
    )


async def admin_candidate_command_guard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = getattr(update, "message", None)
    user = getattr(update, "effective_user", None)
    chat = getattr(update, "effective_chat", None)
    if message is None or user is None or chat is None:
        return
    if chat.id not in _special_admin_chat_ids() or not _is_active_admin_candidate(context, str(user.id)):
        return
    await message.reply_text("⛔️Во время кандидатуры админские команды недоступны. Дождитесь одобрения заявки.")
    raise ApplicationHandlerStop


async def cooperation_admin_command_guard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = getattr(update, "message", None)
    if message is None or not update.effective_chat:
        return

    if int(update.effective_chat.id) != COOPERATION_CHAT_ID:
        return

    if not _is_admin_command_text(message.text):
        return

    try:
        await message.reply_text("В этом чате админские команды недоступны.")
    except Exception:
        pass
    raise ApplicationHandlerStop



def _strip_command_entities(entities, command_length: int, full_text: str):
    if not entities:
        return []

    results: list[MessageEntity] = []
    for entity in entities:
        if entity.offset + entity.length <= command_length:
            continue
        start = max(entity.offset, command_length)
        end = min(entity.offset + entity.length, len(full_text))
        if end <= start:
            continue

        clipped = MessageEntity(
            type=entity.type,
            offset=start - command_length,
            length=end - start,
            url=getattr(entity, "url", None),
            user=getattr(entity, "user", None),
            language=getattr(entity, "language", None),
            custom_emoji_id=getattr(entity, "custom_emoji_id", None),
        )
        results.append(clipped)
    return results


def _split_text_for_telegram(text: str, entities=None, max_chars: int = 4096):
    if not text:
        return [("", [])]

    if len(text) <= max_chars:
        return [(text, list(entities or []))]

    chunks: list[tuple[str, list[MessageEntity]]] = []
    for start in range(0, len(text), max_chars):
        end = min(len(text), start + max_chars)
        chunk_text = text[start:end]
        chunk_entities = []
        for entity in entities or []:
            entity_start = entity.offset
            entity_end = entity.offset + entity.length
            if entity_end <= start or entity_start >= end:
                continue
            overlap_start = max(entity_start, start)
            overlap_end = min(entity_end, end)
            if overlap_end <= overlap_start:
                continue
            chunk_entities.append(
                MessageEntity(
                    type=entity.type,
                    offset=max(0, overlap_start - start),
                    length=max(0, overlap_end - overlap_start),
                    url=getattr(entity, "url", None),
                    user=getattr(entity, "user", None),
                    language=getattr(entity, "language", None),
                    custom_emoji_id=getattr(entity, "custom_emoji_id", None),
                )
            )
        chunks.append((chunk_text, chunk_entities))
    return chunks


async def _send_telegram_message_with_retry(context, recipient_id: int, *, text: str = None, entities=None, photo_file_id: str = None, video_file_id: str = None, caption: str = None, caption_entities=None):
    send_kwargs = {"chat_id": recipient_id}
    if photo_file_id:
        send_kwargs["photo"] = photo_file_id
        if caption is not None:
            send_kwargs["caption"] = caption
            if caption_entities:
                send_kwargs["caption_entities"] = caption_entities
        return await context.bot.send_photo(**send_kwargs)
    if video_file_id:
        send_kwargs["video"] = video_file_id
        if caption is not None:
            send_kwargs["caption"] = caption
            if caption_entities:
                send_kwargs["caption_entities"] = caption_entities
        return await context.bot.send_video(**send_kwargs)
    send_kwargs["text"] = text
    if entities:
        send_kwargs["entities"] = entities
    return await context.bot.send_message(**send_kwargs)


async def sendpiar_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_chat:
        return

    source_text = update.message.text or update.message.caption or ""
    source_text_stripped = source_text.strip()
    if not re.match(r"^/sp(?:@[\w_]+)?(?:\s|$)", source_text_stripped):
        return

    if int(update.effective_chat.id) != COOPERATION_CHAT_ID:
        await update.message.reply_text("Команда /sp доступна только в чате сотрудничества.")
        return

    bot_data = context.application.bot_data
    now_ts = time.time()
    cooldown_until = float(bot_data.get("sendpiar_cooldown_until", 0) or 0)
    cooldown_was_active = bool(bot_data.get("sendpiar_cooldown_was_active", False))

    if cooldown_until > now_ts:
        remaining_seconds = int(cooldown_until - now_ts)
        remaining_minutes = max(1, (remaining_seconds + 59) // 60)
        await update.message.reply_text(
            f"⏳Команда /sp на кулдауне. Подождите {remaining_minutes} мин."
        )
        return

    if cooldown_until and cooldown_was_active and now_ts >= cooldown_until:
        await update.message.reply_text("✅Кулдаун завершен. Команда /sp снова доступна.")
        bot_data["sendpiar_cooldown_was_active"] = False

    issuer_profile = _ensure_profile(
        context,
        str(update.effective_user.id),
        update.effective_user.username or f"id{update.effective_user.id}",
    )
    if not _has_prefix(issuer_profile, "💎Сотрудничество"):
        await update.message.reply_text("Нельзя выполнить команду: нужен префикс 💎Сотрудничество.")
        return

    cmd_entities = update.message.entities if update.message.text else (update.message.caption_entities or [])
    cmd_entity = cmd_entities[0] if cmd_entities else None
    cmd_len = cmd_entity.length if cmd_entity and cmd_entity.type == "bot_command" else len("/sp")
    args_text = source_text[cmd_len:]

    if not str(args_text).strip():
        await update.message.reply_text('Используйте: /sp "текст" (можно с фото или видео).')
        return

    quoted = re.match(r'^\s*"([\s\S]*)"\s*$', args_text)
    broadcast_text = quoted.group(1) if quoted else args_text.strip()
    if not broadcast_text:
        await update.message.reply_text("Текст рассылки не должен быть пустым.")
        return

    payload_text = broadcast_text
    if not payload_text.lower().startswith("#реклама"):
        payload_text = f"#реклама\n\n{payload_text}"

    payload_entities = _strip_command_entities(cmd_entities, cmd_len, source_text)
    if quoted:
        payload_entities = []

    profiles = context.application.bot_data.setdefault("profiles", {})
    recipient_ids = set()
    for user_id in profiles:
        try:
            recipient_ids.add(int(user_id))
        except (TypeError, ValueError):
            continue
    for user_id in (context.application.bot_data.get("active_chats", {}) or {}):
        try:
            recipient_ids.add(int(user_id))
        except (TypeError, ValueError):
            continue
    for user_id in (context.application.bot_data.get("admin_requests", {}) or {}):
        try:
            recipient_ids.add(int(user_id))
        except (TypeError, ValueError):
            continue
    recipients = sorted(recipient_ids)

    if not recipients:
        await update.message.reply_text("В базе нет пользователей для рассылки.")
        return

    photo_file_id = None
    if update.message.photo:
        try:
            photo_file_id = update.message.photo[-1].file_id
        except Exception:
            photo_file_id = None

    video_file_id = None
    if update.message.video:
        try:
            video_file_id = update.message.video.file_id
        except Exception:
            video_file_id = None

    message_specs: list[dict] = []
    if photo_file_id or video_file_id:
        if len(payload_text) <= 1024:
            message_specs.append(
                {
                    "kind": "media",
                    "caption": payload_text,
                    "caption_entities": payload_entities,
                    "photo_file_id": photo_file_id,
                    "video_file_id": video_file_id,
                }
            )
        else:
            media_caption = payload_text[:1024]
            media_caption_entities = []
            for entity in payload_entities or []:
                entity_start = entity.offset
                entity_end = entity.offset + entity.length
                if entity_start >= 1024 or entity_end <= 0:
                    continue
                overlap_start = max(entity_start, 0)
                overlap_end = min(entity_end, 1024)
                if overlap_end <= overlap_start:
                    continue
                media_caption_entities.append(
                    MessageEntity(
                        type=entity.type,
                        offset=max(0, overlap_start),
                        length=max(0, overlap_end - overlap_start),
                        url=getattr(entity, "url", None),
                        user=getattr(entity, "user", None),
                        language=getattr(entity, "language", None),
                        custom_emoji_id=getattr(entity, "custom_emoji_id", None),
                    )
                )
            message_specs.append(
                {
                    "kind": "media",
                    "caption": media_caption,
                    "caption_entities": media_caption_entities,
                    "photo_file_id": photo_file_id,
                    "video_file_id": video_file_id,
                }
            )
            remaining_text = payload_text[1024:]
            remaining_entities = []
            for entity in payload_entities or []:
                entity_start = entity.offset
                entity_end = entity.offset + entity.length
                if entity_end <= 1024 or entity_start >= len(payload_text):
                    continue
                overlap_start = max(entity_start, 1024)
                overlap_end = min(entity_end, len(payload_text))
                if overlap_end <= overlap_start:
                    continue
                remaining_entities.append(
                    MessageEntity(
                        type=entity.type,
                        offset=max(0, overlap_start - 1024),
                        length=max(0, overlap_end - overlap_start),
                        url=getattr(entity, "url", None),
                        user=getattr(entity, "user", None),
                        language=getattr(entity, "language", None),
                        custom_emoji_id=getattr(entity, "custom_emoji_id", None),
                    )
                )
            for chunk_text, chunk_entities in _split_text_for_telegram(remaining_text, remaining_entities, 4096):
                if chunk_text:
                    message_specs.append({"kind": "text", "text": chunk_text, "entities": chunk_entities})
    else:
        for chunk_text, chunk_entities in _split_text_for_telegram(payload_text, payload_entities, 4096):
            if chunk_text:
                message_specs.append({"kind": "text", "text": chunk_text, "entities": chunk_entities})

    success_count = 0
    failed_count = 0
    for recipient_id in recipients:
        recipient_ok = True
        for spec in message_specs:
            try:
                if spec["kind"] == "media":
                    await _send_telegram_message_with_retry(
                        context,
                        recipient_id,
                        photo_file_id=spec.get("photo_file_id"),
                        video_file_id=spec.get("video_file_id"),
                        caption=spec.get("caption"),
                        caption_entities=spec.get("caption_entities") or None,
                    )
                else:
                    await _send_telegram_message_with_retry(
                        context,
                        recipient_id,
                        text=spec.get("text"),
                        entities=spec.get("entities") or None,
                    )
            except RetryAfter as exc:
                logging.warning("sp rate limit hit for recipient %s; retrying after %s seconds", recipient_id, exc.retry_after)
                await asyncio.sleep(float(exc.retry_after) + 1.0)
                try:
                    if spec["kind"] == "media":
                        await _send_telegram_message_with_retry(
                            context,
                            recipient_id,
                            photo_file_id=spec.get("photo_file_id"),
                            video_file_id=spec.get("video_file_id"),
                            caption=spec.get("caption"),
                            caption_entities=spec.get("caption_entities") or None,
                        )
                    else:
                        await _send_telegram_message_with_retry(
                            context,
                            recipient_id,
                            text=spec.get("text"),
                            entities=spec.get("entities") or None,
                        )
                except Forbidden:
                    logging.info("sp retry recipient %s cannot receive bot messages", recipient_id)
                    recipient_ok = False
                    break
                except Exception:
                    logging.exception("sp retry failed for recipient %s after rate limit", recipient_id)
                    recipient_ok = False
                    break
            except Forbidden:
                logging.info("sp recipient %s cannot receive bot messages", recipient_id)
                recipient_ok = False
                break
            except BadRequest as exc:
                if str(exc) == "User_bot_to_bot_disabled":
                    recipient_ok = False
                    break
                logging.exception("sp failed for recipient %s", recipient_id)
                recipient_ok = False
                break
            except Exception:
                logging.exception("sp failed for recipient %s", recipient_id)
                recipient_ok = False
                break
        if recipient_ok:
            success_count += 1
        else:
            failed_count += 1

    report_text = f"✅Рассылка отправлена всем пользователям бота.\nУспешно: {success_count}\nОшибок: {failed_count}"
    await context.bot.send_message(chat_id=update.effective_chat.id, text=report_text)

    bot_data["sendpiar_cooldown_until"] = time.time() + (15 * 60)
    bot_data["sendpiar_cooldown_was_active"] = True



async def sendpiar_media_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    caption = str(update.message.caption or "").strip()
    if not re.match(r"^/sp(?:@[\w_]+)?(?:\s|$)", caption):
        return
    await sendpiar_command_handler(update, context)
    raise ApplicationHandlerStop



async def anpiar_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or update.effective_chat.id not in _special_admin_chat_ids():
        return

    issuer_profile = _ensure_profile(
        context,
        str(update.effective_user.id),
        update.effective_user.username or f"id{update.effective_user.id}",
    )
    if _effective_admin_level(issuer_profile) < 3:
        await update.message.reply_text("Команда доступна только администраторам 3 категории и выше.")
        return

    raw_text = update.message.text or ""
    cmd_entity = update.message.entities[0] if update.message.entities else None
    cmd_len = cmd_entity.length if cmd_entity and cmd_entity.type == "bot_command" else len("/anpiar")
    args_text = raw_text[cmd_len:].strip()
    if not args_text:
        await update.message.reply_text('Используйте: /anpiar "id_profile" "1-0"')
        return

    try:
        parts = shlex.split(args_text)
    except ValueError:
        await update.message.reply_text('Некорректный формат. Используйте: /anpiar "id_profile" "1-0"')
        return

    if len(parts) < 2:
        await update.message.reply_text('Используйте: /anpiar "id_profile" "1-0"')
        return

    target_identifier = parts[0]
    mode_raw = str(parts[1]).strip()
    if mode_raw not in {"1", "0"}:
        await update.message.reply_text('Второй аргумент должен быть "1" или "0".')
        return

    target_user_id, target_profile = _resolve_warn_target(context, target_identifier)
    if not target_profile:
        await update.message.reply_text(f'Пользователь с id_profile "{target_identifier}" не найден.')
        return

    has_access = bool(target_profile.get("ad_disable_access", False))
    target_id_profile = int(target_profile.get("id_profile", 0) or 0)

    if mode_raw == "1":
        if has_access:
            await update.message.reply_text(f"У пользователя id_profile #{target_id_profile} уже есть права на отключение рекламы.")
            return
        target_profile["ad_disable_access"] = True
        _save_profile_record(context, str(target_user_id))
        await update.message.reply_text(f"✅Права на отключение рекламы выданы пользователю id_profile #{target_id_profile}.")
        try:
            await context.bot.send_message(
                chat_id=int(target_user_id),
                text="✅Вам выданы права на отключение рекламы. Откройте ⚙️Настройки и нажмите 🔕Отключить рекламу.",
            )
        except Exception:
            pass
        return

    if not has_access:
        await update.message.reply_text(f"У пользователя id_profile #{target_id_profile} нет прав на отключение рекламы.")
        return

    target_profile["ad_disable_access"] = False
    target_profile["ad_disable_enabled"] = False
    _save_profile_record(context, str(target_user_id))
    await update.message.reply_text(f"✅Права на отключение рекламы сняты у пользователя id_profile #{target_id_profile}.")
    try:
        await context.bot.send_message(
            chat_id=int(target_user_id),
            text="⚠️Права на отключение рекламы были отозваны.",
        )
    except Exception:
        pass


# ==== SECTION: Warnings, stats & the /astats profile editor ====
# /warn issues a warning by id_profile+reason; /stats, /fullstats, /astats
# and every astats_*_callback below render and edit an admin's profile card
# (tag, bio, gender, cooperation tip text) via app.services.profiles.
async def warn_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id not in {LOG_CHAT_ID, WORK_CHAT_ID} or not update.message:
        return

    if not _can_use_moderation_commands(context, str(update.effective_user.id)):
        await update.message.reply_text("Команда доступна только администраторам 2 категории и выше.")
        return

    raw_text = update.message.text or ""
    cmd_entity = update.message.entities[0] if update.message.entities else None
    cmd_len = cmd_entity.length if cmd_entity and cmd_entity.type == "bot_command" else len("/warn")
    args_text = raw_text[cmd_len:].strip()
    if not args_text:
        await update.message.reply_text('Используйте: /warn "id_profile" "30m" "reason" (s/m/h/d)')
        return

    try:
        parts = shlex.split(args_text)
    except ValueError:
        await update.message.reply_text('Некорректный формат. Используйте: /warn "id_profile" "30m" "reason" (s/m/h/d)')
        return

    if len(parts) < 3:
        await update.message.reply_text('Используйте: /warn "id_profile" "30m" "reason" (s/m/h/d)')
        return

    target_identifier = parts[0]
    duration_match = re.fullmatch(r"([1-9]\d*)\s*(s|m|h|d)", parts[1].strip().lower())
    if not duration_match:
        await update.message.reply_text('Длительность должна быть в формате "30s", "10m", "2h" или "1d".')
        return
    duration_value = int(duration_match.group(1))
    duration_seconds = duration_value * {"s": 1, "m": 60, "h": 3600, "d": 86400}[duration_match.group(2)]
    reason = " ".join(parts[2:]).strip()
    if not reason:
        await update.message.reply_text("Укажите причину предупреждения.")
        return

    target_user_id, profile = _resolve_warn_target(context, target_identifier)
    if not profile:
        await update.message.reply_text(f'Пользователь с id_profile "{target_identifier}" не найден.')
        return

    if _has_admin_rights_level_1_5(profile):
        await update.message.reply_text(
            f'Невозможно выдать предупреждение: id_profile #{profile.get("id_profile")} имеет админ-права 1-5 уровня.'
        )
        return

    current_warn = refresh_timed_warnings(profile)
    profile["warn"] = current_warn + 1
    expires_at = time.time() + duration_seconds
    profile["reason"] = reason
    profile.setdefault("timed_warns", []).append(expires_at)
    _save_profile_record(context, str(target_user_id))
    _schedule_timed_warning_expiry(context, str(target_user_id), expires_at, duration_seconds)
    await enforce_autoban_if_needed(context, str(target_user_id), profile.get("username"))

    await update.message.reply_text(
        f'⚠️Пользователю id_profile #{profile.get("id_profile")} выдано предупреждение на '
        f'{format_ban_remaining(duration_seconds)}. Теперь у него {profile["warn"]} warn(-ов). Причина: "{reason}"'
    )

    try:
        await context.bot.send_message(
            chat_id=int(target_user_id),
            text=(
                f'❗️Вы получили предупреждение на {format_ban_remaining(duration_seconds)} '
                f'с причиной "{reason}" от руководства бота. Теперь у вас {profile["warn"]} предупреждений'
            ),
        )
    except Exception:
        pass
    _save_profile_record(context, str(target_user_id))


async def _expire_timed_warning_job(context: ContextTypes.DEFAULT_TYPE):
    job_data = context.job.data or {}
    user_id = str(job_data.get("user_id") or "")
    expires_at = float(job_data.get("expires_at") or 0)
    await _expire_timed_warning(context, user_id, expires_at)


async def _expire_timed_warning(context: ContextTypes.DEFAULT_TYPE, user_id: str, expires_at: float):
    profiles = context.application.bot_data.setdefault("profiles", {})
    profile = profiles.get(user_id)
    if not profile:
        return

    timed_warns = profile.get("timed_warns", [])
    if not isinstance(timed_warns, list):
        return
    matching_warn = next((item for item in timed_warns if abs(float(item) - expires_at) < 0.01), None)
    if matching_warn is None:
        return

    timed_warns.remove(matching_warn)
    profile["warn"] = max(0, int(profile.get("warn", 0) or 0) - 1)
    _save_profile_record(context, user_id)
    try:
        await context.bot.send_message(
            chat_id=int(user_id),
            text=(
                "✅Срок действия вашего предупреждения истёк. "
                f"Снято предупреждений: 1. Осталось действующих: {profile['warn']}."
            ),
        )
    except Exception:
        logging.exception("Failed to notify user %s about expired warning", user_id)


def _schedule_timed_warning_expiry(
    context: ContextTypes.DEFAULT_TYPE,
    user_id: str,
    expires_at: float,
    duration_seconds: int,
) -> None:
    tasks = context.application.bot_data.setdefault("timed_warning_tasks", {})
    task_key = f"{user_id}:{expires_at}"
    tasks[task_key] = asyncio.create_task(
        _expire_timed_warning_after_delay(context, user_id, expires_at, duration_seconds)
    )


async def _expire_timed_warning_after_delay(
    context: ContextTypes.DEFAULT_TYPE,
    user_id: str,
    expires_at: float,
    duration_seconds: int,
) -> None:
    await asyncio.sleep(duration_seconds)
    await _expire_timed_warning(context, user_id, expires_at)



async def stats_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    allowed_special_chats = _special_admin_chat_ids()
    if update.effective_chat.id not in allowed_special_chats or not update.message:
        return

    if not _can_use_moderation_commands(context, str(update.effective_user.id)):
        await update.message.reply_text("Команда доступна только администраторам 2 категории и выше.")
        return

    raw_text = update.message.text or ""
    cmd_entity = update.message.entities[0] if update.message.entities else None
    cmd_len = cmd_entity.length if cmd_entity and cmd_entity.type == "bot_command" else len("/stats")
    args_text = raw_text[cmd_len:].strip()
    if not args_text:
        await update.message.reply_text('Используйте: /stats "id_profile"')
        return

    try:
        parts = shlex.split(args_text)
    except ValueError:
        await update.message.reply_text('Некорректный формат. Используйте: /stats "id_profile"')
        return

    if len(parts) < 1:
        await update.message.reply_text('Используйте: /stats "id_profile"')
        return

    target_identifier = parts[0]
    target_user_id, profile = _resolve_warn_target(context, target_identifier)
    if not profile:
        await update.message.reply_text(f'Пользователь с id_profile "{target_identifier}" не найден.')
        return

    await update.message.reply_text(_build_user_stats_text(context, str(target_user_id), profile))



async def fullstats_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id not in _special_admin_chat_ids() or not update.message:
        return

    issuer_profile = _ensure_profile(
        context,
        str(update.effective_user.id),
        update.effective_user.username or f"id{update.effective_user.id}",
    )
    if _effective_admin_level(issuer_profile) < 4:
        await update.message.reply_text("Команда доступна только администраторам 4 категории и выше.")
        return

    profiles = context.application.bot_data.setdefault("profiles", {}) or {}
    active_chats = context.application.bot_data.setdefault("active_chats", {}) or {}

    users_regist = len(profiles)
    banned_users_regist = sum(1 for p in profiles.values() if bool((p or {}).get("bot_blocked_by_user", False)))
    admins_regist = 0
    send_regist = 0
    for profile in profiles.values():
        profile_obj = profile or {}
        try:
            if int(profile_obj.get("admin_level", 0) or 0) in {1, 2, 3, 4, 5}:
                admins_regist += 1
        except Exception:
            pass
        try:
            send_regist += int(profile_obj.get("message_user", 0) or 0)
        except Exception:
            pass

    session_regist = sum(1 for s in active_chats.values() if bool((s or {}).get("active")))
    otvet_regist = int(context.application.bot_data.get("total_admin_replies", 0) or 0)

    await update.message.reply_text(
        "🔍Полная статистика базы данных морозной зависти\n\n"
        f"Зарегистрировано пользователей: {users_regist}\n"
        f"Пользователей которые заблокировали бота: {banned_users_regist}\n"
        f"Администраторов: {admins_regist}\n"
        f"Активных сессий: {session_regist}\n"
        f"Отправленных сообщений: {send_regist}\n"
        f"Полученных ответов: {otvet_regist}"
    )


async def admins_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_user or not update.effective_chat:
        return
    if (
        update.effective_chat.type != ChatType.PRIVATE
        and update.effective_chat.id not in _special_admin_chat_ids()
    ):
        return

    issuer_profile = _ensure_profile(
        context,
        str(update.effective_user.id),
        update.effective_user.username or f"id{update.effective_user.id}",
    )
    if _effective_admin_level(issuer_profile) < 3:
        await update.message.reply_text("Команда доступна только администраторам 3 категории и выше.")
        return

    admin_rows = []
    for telegram_id, profile in (context.application.bot_data.get("profiles", {}) or {}).items():
        profile = profile or {}
        level = _effective_admin_level(profile)
        if level < 1:
            continue
        username = str(profile.get("username") or "").strip()
        username_text = username if username.startswith("@") else f"@{username}" if username else "не указан"
        nick = str(profile.get("tag_admin") or profile.get("user_nickname") or "не указан").strip()
        prefix = str(profile.get("prefix") or "не установлен").strip()
        try:
            profile_id = int(profile.get("id_profile", 0) or 0)
        except (TypeError, ValueError):
            profile_id = 0
        reputation = int(profile.get("admin_reputation", 0) or 0)
        admin_rows.append((profile_id, nick, username_text, telegram_id, prefix, reputation))

    admin_rows.sort(key=lambda row: (row[0] <= 0, row[0], row[3]))
    if not admin_rows:
        await update.message.reply_text("Администраторы не найдены.")
        return

    lines = ["👥 Список администраторов", ""]
    for profile_id, nick, username_text, telegram_id, prefix, reputation in admin_rows:
        lines.append(
            f"Ник: {nick} | {username_text} | id_profile: {profile_id} | "
            f"id_telegram: {telegram_id} | \"{prefix}\" | ⭐ Репутация: {reputation}"
        )
    await update.message.reply_text("\n".join(lines))


async def _send_moderation_user_list(update: Update, context: ContextTypes.DEFAULT_TYPE, *, list_type: str) -> None:
    if not update.message or not update.effective_user or not _is_special_admin_chat(update.effective_chat.id):
        return

    issuer_profile = _ensure_profile(
        context,
        str(update.effective_user.id),
        update.effective_user.username or f"id{update.effective_user.id}",
    )
    if not _can_use_moderation_commands(context, str(update.effective_user.id)):
        await update.message.reply_text("Команда доступна только администраторам 2 категории и выше.")
        return

    rows = []
    profiles = context.application.bot_data.get("profiles", {}) or {}
    for telegram_id, profile in profiles.items():
        profile = profile or {}
        if list_type == "ban":
            if not is_user_banned(context, str(telegram_id)):
                continue
        elif refresh_timed_warnings(profile) <= 0:
            continue

        username = str(profile.get("username") or "").strip()
        username_text = username if username.startswith("@") else f"@{username}" if username else "не указан"
        nickname = str(profile.get("user_nickname") or "не указан").strip()
        try:
            profile_id = int(profile.get("id_profile", 0) or 0)
        except (TypeError, ValueError):
            profile_id = 0
        rows.append((profile_id, nickname, username_text, telegram_id))

    rows.sort(key=lambda row: (row[0] <= 0, row[0], str(row[3])))
    title = "⛔ Список пользователей с действующей блокировкой" if list_type == "ban" else "⚠️ Список пользователей с предупреждениями"
    if not rows:
        await update.message.reply_text(f"{title}\n\nСписок пуст.")
        return

    lines = [title, ""]
    for profile_id, nickname, username_text, telegram_id in rows:
        lines.append(
            f"Ник: {nickname}\n"
            f"{username_text}\n"
            f"id_profile: {profile_id}\n"
            f"id_telegram: {telegram_id}\n"
        )
    await update.message.reply_text("\n".join(lines).rstrip())


async def banlist_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _send_moderation_user_list(update, context, list_type="ban")


async def warnlist_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _send_moderation_user_list(update, context, list_type="warn")


async def astats_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    allowed_special_chats = _special_admin_chat_ids()
    if update.effective_chat.id not in allowed_special_chats or not update.message:
        return

    issuer_profile = _ensure_profile(
        context,
        str(update.effective_user.id),
        update.effective_user.username or f"id{update.effective_user.id}",
    )
    if _effective_admin_level(issuer_profile) < 4:
        await update.message.reply_text("Команда доступна только администраторам 4 категории и выше.")
        return

    raw_text = update.message.text or ""
    cmd_entity = update.message.entities[0] if update.message.entities else None
    cmd_len = cmd_entity.length if cmd_entity and cmd_entity.type == "bot_command" else len("/astats")
    args_text = raw_text[cmd_len:].strip()
    if not args_text:
        await update.message.reply_text('Используйте: /astats "id_profile"')
        return

    try:
        parts = shlex.split(args_text)
    except ValueError:
        await update.message.reply_text('Некорректный формат. Используйте: /astats "id_profile"')
        return

    if len(parts) < 1:
        await update.message.reply_text('Используйте: /astats "id_profile"')
        return

    target_identifier = parts[0]
    target_user_id, profile = _resolve_warn_target(context, target_identifier)
    if not profile:
        await update.message.reply_text(f'Пользователь с id_profile "{target_identifier}" не найден.')
        return

    if not _has_admin_rights_level_1_5(profile):
        await update.message.reply_text(
            f'Нельзя использовать /astats: id_profile #{profile.get("id_profile")} не является администратором.'
        )
        return

    if update.effective_chat.id == TRUSTED_ADMIN_CHAT_ID:
        await update.message.reply_text(
            _build_admin_stats_text(str(target_user_id), profile),
            parse_mode=ParseMode.HTML,
        )
        return

    astats_sessions = context.application.bot_data.setdefault("astats_tag_sessions", {})
    astats_seq = int(context.application.bot_data.get("astats_tag_seq", 0) or 0) + 1
    context.application.bot_data["astats_tag_seq"] = astats_seq
    session_id = str(astats_seq)
    astats_sessions[session_id] = {
        "issuer_user_id": str(update.effective_user.id),
        "target_user_id": str(target_user_id),
        "target_id_profile": int(profile.get("id_profile", 0) or 0),
        "chat_id": int(update.effective_chat.id),
        "status": "idle",
        "prompt_message_id": None,
    }

    await update.message.reply_text(
        _build_admin_stats_text(str(target_user_id), profile),
        parse_mode=ParseMode.HTML,
        reply_markup=_build_astats_profile_keyboard(session_id),
    )



async def astats_tag_change_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    data = update.callback_query.data or ""
    parts = data.split("_")
    if len(parts) < 4:
        return
    session_id = parts[3]

    sessions = context.application.bot_data.setdefault("astats_tag_sessions", {})
    session = sessions.get(session_id)
    if not session:
        await update.callback_query.answer("Панель устарела", show_alert=True)
        return

    if str(update.effective_user.id) != str(session.get("issuer_user_id")):
        await update.callback_query.answer("Кнопка доступна только автору /astats", show_alert=True)
        return

    issuer_profile = _ensure_profile(
        context,
        str(update.effective_user.id),
        update.effective_user.username or f"id{update.effective_user.id}",
    )
    if _effective_admin_level(issuer_profile) < 4:
        await update.callback_query.answer("Недостаточно прав", show_alert=True)
        return

    pending_by_admin = context.application.bot_data.setdefault("pending_astats_tag_by_admin", {})
    pending_by_chat = context.application.bot_data.setdefault("pending_astats_tag_by_chat", {})
    pending_bio_by_admin = context.application.bot_data.setdefault("pending_astats_bio_by_admin", {})
    pending_bio_by_admin.pop(str(update.effective_user.id), None)
    previous_session_id = pending_by_admin.get(str(update.effective_user.id))
    if previous_session_id and previous_session_id != session_id:
        previous_session = sessions.get(str(previous_session_id))
        if previous_session:
            previous_session["status"] = "idle"
            previous_session["prompt_message_id"] = None

    prompt = await update.callback_query.message.reply_text(
        "Отправьте новый тег (должен начинаться с #) ответом на это сообщение.",
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("отмена", callback_data=f"astats_tag_cancel_{session_id}")]]
        ),
    )

    session["status"] = "await_tag"
    session["chat_id"] = int(update.effective_chat.id)
    session["prompt_message_id"] = prompt.message_id
    pending_by_admin[str(update.effective_user.id)] = session_id
    pending_by_chat[str(update.effective_chat.id)] = session_id



async def astats_bio_change_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    data = update.callback_query.data or ""
    parts = data.split("_")
    if len(parts) < 4:
        return
    session_id = parts[3]

    sessions = context.application.bot_data.setdefault("astats_tag_sessions", {})
    session = sessions.get(session_id)
    if not session:
        await update.callback_query.answer("Панель устарела", show_alert=True)
        return

    if str(update.effective_user.id) != str(session.get("issuer_user_id")):
        await update.callback_query.answer("Кнопка доступна только автору /astats", show_alert=True)
        return

    issuer_profile = _ensure_profile(
        context,
        str(update.effective_user.id),
        update.effective_user.username or f"id{update.effective_user.id}",
    )
    if _effective_admin_level(issuer_profile) < 4:
        await update.callback_query.answer("Недостаточно прав", show_alert=True)
        return

    pending_by_admin = context.application.bot_data.setdefault("pending_astats_tag_by_admin", {})
    pending_by_admin.pop(str(update.effective_user.id), None)
    pending_bio_by_admin = context.application.bot_data.setdefault("pending_astats_bio_by_admin", {})
    pending_bio_by_admin[str(update.effective_user.id)] = session_id

    session["status"] = "await_bio"
    session["editor_message_id"] = int(update.callback_query.message.message_id)
    session["chat_id"] = int(update.effective_chat.id)
    session["prompt_message_id"] = None

    try:
        await update.callback_query.message.edit_text(
            "Отправьте новую биографию администратора.",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("отмена", callback_data=f"astats_bio_cancel_{session_id}")]]
            ),
        )
    except Exception:
        pass



async def astats_tip_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    data = update.callback_query.data or ""
    parts = data.split("_")
    if len(parts) < 4:
        return
    session_id = parts[3]

    sessions = context.application.bot_data.setdefault("astats_tag_sessions", {})
    session = sessions.get(session_id)
    if not session:
        await update.callback_query.answer("Панель устарела", show_alert=True)
        return

    if str(update.effective_user.id) != str(session.get("issuer_user_id")):
        await update.callback_query.answer("Кнопка доступна только автору /astats", show_alert=True)
        return

    issuer_profile = _ensure_profile(
        context,
        str(update.effective_user.id),
        update.effective_user.username or f"id{update.effective_user.id}",
    )
    if _effective_admin_level(issuer_profile) < 4:
        await update.callback_query.answer("Недостаточно прав", show_alert=True)
        return

    pending_by_admin = context.application.bot_data.setdefault("pending_astats_tag_by_admin", {})
    pending_by_admin.pop(str(update.effective_user.id), None)
    pending_bio_by_admin = context.application.bot_data.setdefault("pending_astats_bio_by_admin", {})
    pending_bio_by_admin.pop(str(update.effective_user.id), None)

    target_user_id = str(session.get("target_user_id"))
    target_profile = _ensure_profile(context, target_user_id, f"id{target_user_id}")
    username_text = str(target_profile.get("username") or f"id{target_user_id}")

    existing_tip = str(target_profile.get("tip_admin") or "")
    selected_keys = []
    if "🗣️" in existing_tip:
        selected_keys.append("chat")
    if "❤️" in existing_tip:
        selected_keys.append("support")
    if "🔥" in existing_tip:
        selected_keys.append("flirt")

    session["status"] = "astats_tip_edit"
    session["tip_selected_keys"] = selected_keys

    try:
        await update.callback_query.message.edit_text(
            f"☕️Панель редактирования диалогов у админа {username_text}",
            reply_markup=_build_astats_tip_editor_keyboard(session_id, selected_keys),
        )
    except Exception:
        pass



async def astats_tip_toggle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    data = update.callback_query.data or ""
    parts = data.split("_")
    if len(parts) < 5:
        return
    session_id = parts[3]
    tip_key = parts[4]

    if tip_key not in {"chat", "support", "flirt"}:
        return

    sessions = context.application.bot_data.setdefault("astats_tag_sessions", {})
    session = sessions.get(session_id)
    if not session:
        await update.callback_query.answer("Панель устарела", show_alert=True)
        return

    if str(update.effective_user.id) != str(session.get("issuer_user_id")):
        await update.callback_query.answer("Кнопка доступна только автору /astats", show_alert=True)
        return

    selected = list(session.get("tip_selected_keys") or [])
    if tip_key in selected:
        selected.remove(tip_key)
    else:
        selected.append(tip_key)
    session["tip_selected_keys"] = selected

    try:
        await update.callback_query.message.edit_reply_markup(
            reply_markup=_build_astats_tip_editor_keyboard(session_id, selected)
        )
    except Exception:
        pass



async def astats_tip_apply_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    data = update.callback_query.data or ""
    parts = data.split("_")
    if len(parts) < 4:
        return
    session_id = parts[3]

    sessions = context.application.bot_data.setdefault("astats_tag_sessions", {})
    session = sessions.get(session_id)
    if not session:
        await update.callback_query.answer("Панель устарела", show_alert=True)
        return

    if str(update.effective_user.id) != str(session.get("issuer_user_id")):
        await update.callback_query.answer("Кнопка доступна только автору /astats", show_alert=True)
        return

    issuer_profile = _ensure_profile(
        context,
        str(update.effective_user.id),
        update.effective_user.username or f"id{update.effective_user.id}",
    )
    if _effective_admin_level(issuer_profile) < 4:
        await update.callback_query.answer("Недостаточно прав", show_alert=True)
        return

    selected = list(session.get("tip_selected_keys") or [])
    tip_value = _candidate_tip_value(selected)

    target_user_id = str(session.get("target_user_id"))
    target_profile = _ensure_profile(context, target_user_id, f"id{target_user_id}")
    target_profile["tip_admin"] = tip_value
    _save_profile_record(context, target_user_id)

    try:
        await context.bot.send_message(
            chat_id=int(target_user_id),
            text=f"📍Ваш тип диалогов был изменен. Теперь вы можете общаться на: {tip_value}",
        )
    except Exception:
        pass

    session["status"] = "idle"
    await update.callback_query.answer("Смена диалогов успешно завершена", show_alert=True)

    try:
        await update.callback_query.message.edit_text(
            _build_admin_stats_text(str(target_user_id), target_profile),
            parse_mode=ParseMode.HTML,
            reply_markup=_build_astats_profile_keyboard(session_id),
        )
    except Exception:
        pass



async def astats_active_pz_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    data = update.callback_query.data or ""
    parts = data.split("_")
    if len(parts) < 4:
        return
    session_id = parts[3]

    sessions = context.application.bot_data.setdefault("astats_tag_sessions", {})
    session = sessions.get(session_id)
    if not session:
        await update.callback_query.answer("Панель устарела", show_alert=True)
        return

    if str(update.effective_user.id) != str(session.get("issuer_user_id")):
        await update.callback_query.answer("Кнопка доступна только автору /astats", show_alert=True)
        return

    issuer_profile = _ensure_profile(
        context,
        str(update.effective_user.id),
        update.effective_user.username or f"id{update.effective_user.id}",
    )
    if _effective_admin_level(issuer_profile) < 4:
        await update.callback_query.answer("Недостаточно прав", show_alert=True)
        return

    target_user_id = str(session.get("target_user_id"))
    target_profile = _ensure_profile(context, target_user_id, f"id{target_user_id}")
    admin_username = str(target_profile.get("username") or f"id{target_user_id}")

    try:
        await update.callback_query.message.edit_text(
            f"👨‍👦Панель управления активными ПЗ админа {admin_username}",
        )
    except Exception:
        pass

    active_sessions = context.application.bot_data.get("active_chats", {}) or {}
    found_sessions = []
    for requester_user_id, active in active_sessions.items():
        if not active or not active.get("active"):
            continue
        if str(active.get("admin_id")) != target_user_id:
            continue
        found_sessions.append((str(requester_user_id), active))

    if not found_sessions:
        await update.callback_query.message.reply_text("Ничего не найдено")
        return

    for requester_user_id, active in found_sessions:
        requester_profile = _ensure_profile(context, requester_user_id, active.get("topic_base_name") or f"id{requester_user_id}")
        username_pz = str(requester_profile.get("username") or active.get("topic_base_name") or f"id{requester_user_id}")
        user_messages = int(active.get("msg_topic_user", 0) or 0)
        admin_messages = int(active.get("msg_topic_admin", 0) or 0)
        msg_topic = user_messages + admin_messages
        date_value = str(active.get("session_started_at") or "не указана")
        topic_link = _topic_url(active.get("chat_id"), active.get("topic_id"))

        await update.callback_query.message.reply_text(
            f"ℹ️Активное общение с {username_pz}\n"
            f"📥Сообщений: \"{msg_topic}\"\n\n"
            f"Регистрация общения: \"{date_value}\"\n"
            f"/info_topic \"{topic_link}\""
        )



async def info_topic_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    allowed_special_chats = _special_admin_chat_ids()
    if update.effective_chat.id not in allowed_special_chats or not update.message:
        return

    issuer_profile = _ensure_profile(
        context,
        str(update.effective_user.id),
        update.effective_user.username or f"id{update.effective_user.id}",
    )
    if not _has_full_access_prefix(issuer_profile):
        await update.message.reply_text(
            "Нельзя выполнить команду: нужен префикс 👁Logs"
        )
        return

    raw_text = update.message.text or ""
    cmd_entity = update.message.entities[0] if update.message.entities else None
    cmd_len = cmd_entity.length if cmd_entity and cmd_entity.type == "bot_command" else len("/info_topic")
    args_text = raw_text[cmd_len:].strip()
    if not args_text:
        await update.message.reply_text('Используйте: /info_topic "topic_link"')
        return

    try:
        parts = shlex.split(args_text)
    except ValueError:
        await update.message.reply_text('Некорректный формат. Используйте: /info_topic "topic_link"')
        return

    if len(parts) < 1:
        await update.message.reply_text('Используйте: /info_topic "topic_link"')
        return

    topic_link = parts[0]
    chat_id, topic_id = _parse_topic_url(topic_link)
    if chat_id is None or topic_id is None:
        await update.message.reply_text('Некорректный URL темы. Нужен формат вида https://t.me/c/<chat_id>/<topic_id>.')
        return

    topic_map = context.application.bot_data.setdefault("topic_user_map", {})
    target_user_id = topic_map.get(topic_id) or topic_map.get(str(topic_id))
    active_chats = context.application.bot_data.setdefault("active_chats", {})
    active = active_chats.get(str(target_user_id)) if target_user_id else None
    if not active or not active.get("active"):
        await update.message.reply_text("Тема не найдена или сессия уже завершена.")
        return

    if int(active.get("chat_id", 0) or 0) != int(chat_id) or int(active.get("topic_id", 0) or 0) != int(topic_id):
        await update.message.reply_text("Тема не найдена или уже неактуальна.")
        return

    panel_id = _next_info_topic_panel_id(context)
    panels = context.application.bot_data.setdefault("info_topic_panels", {})
    panels[panel_id] = {
        "panel_id": panel_id,
        "issuer_user_id": str(update.effective_user.id),
        "target_user_id": str(target_user_id),
        "chat_id": chat_id,
        "topic_id": topic_id,
        "topic_link": topic_link,
    }
    _record_info_topic_action(
        context,
        target_user_id,
        update.effective_user.id,
        "OPEN_PANEL",
        reason="Открыта панель управления сессией",
        panel_id=panel_id,
        topic_link=topic_link,
    )

    target_profile = _ensure_profile(context, str(target_user_id), active.get("topic_base_name") or f"id{target_user_id}")
    admin_username = str(active.get("admin_username") or "админ")
    username_pz = str(target_profile.get("username") or active.get("topic_base_name") or f"id{target_user_id}")
    detect = int(active.get("detect_topic", 0) or 0)
    detect_last = str(active.get("last_suspicious_message") or "нет")
    msg_topic = int(active.get("msg_topic_user", 0) or 0) + int(active.get("msg_topic_admin", 0) or 0)
    rp_topic = int(active.get("rp_topic", 0) or 0)
    rp_topic_last = str(active.get("last_rp_action") or "нет")
    date_value = str(active.get("session_started_at") or "не указана")

    text = _build_info_topic_text(
        username_pz,
        admin_username,
        detect,
        detect_last,
        msg_topic,
        rp_topic,
        rp_topic_last,
        date_value,
        topic_link,
    )
    panels[panel_id]["panel_text"] = text
    try:
        await context.bot.send_message(
            chat_id=LOG_CHAT_ID,
            text=text,
            reply_markup=_build_info_topic_keyboard(panel_id, paused=bool(active.get("management_paused"))),
        )
    except Exception as e:
        logging.exception("info_topic_command_handler failed: %s", e)



async def info_topic_close_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    panel_id = (update.callback_query.data or "").split("_")[-1]
    panels = context.application.bot_data.setdefault("info_topic_panels", {})
    panel = panels.get(panel_id)
    if not panel:
        await update.callback_query.answer("Панель устарела", show_alert=True)
        return

    if not _is_info_topic_session_admin(context, panel, update.effective_user.id):
        await update.callback_query.answer("Доступно только админу этой сессии", show_alert=True)
        return

    issuer_profile = _ensure_profile(
        context,
        str(update.effective_user.id),
        update.effective_user.username or f"id{update.effective_user.id}",
    )
    if _effective_admin_level(issuer_profile) < 3:
        await update.callback_query.answer("Недостаточно прав", show_alert=True)
        return

    panel["pending_action"] = "close"
    try:
        await update.callback_query.message.edit_reply_markup(reply_markup=_build_info_topic_keyboard(panel_id, pending_action="close"))
    except Exception:
        pass



async def info_topic_stop_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    panel_id = (update.callback_query.data or "").split("_")[-1]
    panels = context.application.bot_data.setdefault("info_topic_panels", {})
    panel = panels.get(panel_id)
    if not panel:
        await update.callback_query.answer("Панель устарела", show_alert=True)
        return

    if not _is_info_topic_session_admin(context, panel, update.effective_user.id):
        await update.callback_query.answer("Доступно только админу этой сессии", show_alert=True)
        return

    issuer_profile = _ensure_profile(
        context,
        str(update.effective_user.id),
        update.effective_user.username or f"id{update.effective_user.id}",
    )
    if _effective_admin_level(issuer_profile) < 3:
        await update.callback_query.answer("Недостаточно прав", show_alert=True)
        return

    panel["pending_action"] = "stop"
    try:
        await update.callback_query.message.edit_reply_markup(reply_markup=_build_info_topic_keyboard(panel_id, pending_action="stop"))
    except Exception:
        pass



async def info_topic_cancel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer("Отмена")
    panel_id = (update.callback_query.data or "").split("_")[-1]
    panels = context.application.bot_data.setdefault("info_topic_panels", {})
    panel = panels.get(panel_id)
    if not panel:
        return

    if not _is_info_topic_session_admin(context, panel, update.effective_user.id):
        await update.callback_query.answer("Доступно только админу этой сессии", show_alert=True)
        return

    active = context.application.bot_data.get("active_chats", {}).get(str(panel.get("target_user_id"))) or {}
    paused = bool(active.get("management_paused"))
    _record_info_topic_action(context, panel.get("target_user_id"), update.effective_user.id, "CANCEL_ACTION", reason="Отмена действия в панели управления", panel_id=panel_id)
    panel.pop("pending_action", None)
    panel_text = str(panel.get("panel_text") or "")
    try:
        await update.callback_query.message.edit_text(
            panel_text,
            reply_markup=_build_info_topic_keyboard(panel_id, paused=paused),
        )
    except Exception:
        pass


async def info_topic_logs_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    panel_id = (update.callback_query.data or "").split("_")[-1]
    panel = context.application.bot_data.setdefault("info_topic_panels", {}).get(panel_id)
    if not panel:
        await update.callback_query.answer("Панель устарела", show_alert=True)
        return

    if not _is_info_topic_session_admin(context, panel, update.effective_user.id):
        await update.callback_query.answer("Доступно только админу этой сессии", show_alert=True)
        return

    target_user_id = str(panel.get("target_user_id") or "")
    _record_info_topic_action(context, target_user_id, update.effective_user.id, "LOG_VIEW", panel_id=panel_id)
    text = _build_info_topic_logs_text(context, target_user_id, panel_id=panel_id)
    try:
        await update.callback_query.message.edit_text(text)
    except Exception:
        pass


def _build_info_topic_full_stats_text(context: ContextTypes.DEFAULT_TYPE, target_user_id: str | int, panel_id: str | None = None) -> str:
    logs_bucket = (context.application.bot_data.get("info_topic_logs", {}) or {})
    session = logs_bucket.get(str(panel_id)) if panel_id else logs_bucket.get(str(target_user_id))
    if session is None:
        return "📊Полная статистика\n\nДанные по этой сессии отсутствуют."
    stats = session.get("stats", {}) or {}
    actions = session.get("actions", []) or []
    recent_actions = _recent_info_topic_actions(actions)
    recent_admin_ids = {str(entry.get("admin_id")) for entry in recent_actions if entry.get("admin_id") is not None}
    admin_ids = list(recent_admin_ids)
    unique_admins = [
        (context.application.bot_data.get("profiles", {}) or {}).get(str(admin_id), {}).get("tag_admin")
        or f"id{admin_id}"
        for admin_id in admin_ids
    ]
    recent_counts = {
        "total": len(recent_actions),
        "warnings": sum(entry.get("action_type") == "WARN_USER" for entry in recent_actions),
        "close": sum(entry.get("action_type") == "CLOSE_SESSION" for entry in recent_actions),
        "pause": sum(entry.get("action_type") == "PAUSE_SESSION" for entry in recent_actions),
        "resume": sum(entry.get("action_type") == "RESUME_SESSION" for entry in recent_actions),
    }

    lines = [
        "📊Полная статистика по сессии",
        f"- Панель: #{session.get('panel_id') or panel_id or target_user_id}",
        f"- Пользователь: {session.get('target_user_id') or target_user_id}",
        f"- Админ открытия: {session.get('issuer_user_id') or 'неизвестно'}",
        f"- Тема: {session.get('topic_link') or 'не указана'}",
        f"- Статус: {session.get('status') or 'active'}",
        f"- Дата открытия: {session.get('opened_at') or 'неизвестно'}",
        f"- Последнее действие: {session.get('last_action_at') or 'неизвестно'}",
        "",
        "📈 Действия админов за последние 48 часов:",
        f"- Всего нажатий кнопок: {recent_counts['total']}",
        f"- Уникальных админов: {len(unique_admins)}",
        f"- Предупреждения: {recent_counts['warnings']}",
        f"- Закрытия: {recent_counts['close']}",
        f"- Приостановки: {recent_counts['pause']}",
        f"- Возобновления: {recent_counts['resume']}",
        "",
        "🧑‍💼 Администраторы:",
    ]
    if unique_admins:
        for admin in unique_admins:
            lines.append(f"- {admin}")
    else:
        lines.append("- Нет данных")

    lines.extend(["", "⏱ Ключевые события:"])
    for entry in recent_actions[-10:]:
        label = entry.get("button") or INFO_TOPIC_ACTION_LABELS.get(entry.get("action_type"), entry.get("action_type") or "Действие")
        lines.append(f"- {entry.get('date', 'неизвестно')} | {entry.get('tag_admin', 'админ')} | {label}")
    return "\n".join(lines)


async def info_topic_stats_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    panel_id = (update.callback_query.data or "").split("_")[-1]
    panel = context.application.bot_data.setdefault("info_topic_panels", {}).get(panel_id)
    if not panel:
        await update.callback_query.answer("Панель устарела", show_alert=True)
        return

    if not _is_info_topic_session_admin(context, panel, update.effective_user.id):
        await update.callback_query.answer("Доступно только админу этой сессии", show_alert=True)
        return

    target_user_id = str(panel.get("target_user_id") or "")
    _record_info_topic_action(context, target_user_id, update.effective_user.id, "LOG_VIEW", reason="Полная статистика", panel_id=panel_id)
    text = _build_info_topic_full_stats_text(context, target_user_id, panel_id=panel_id)
    try:
        await update.callback_query.message.edit_text(text)
    except Exception:
        pass


async def _finish_info_topic_action(context: ContextTypes.DEFAULT_TYPE, panel_id: str, action: str) -> None:
    panels = context.application.bot_data.setdefault("info_topic_panels", {})
    panel = panels.get(panel_id)
    if not panel:
        return

    target_user_id = str(panel.get("target_user_id"))
    chat_id = int(panel.get("chat_id", 0) or 0)
    topic_id = int(panel.get("topic_id", 0) or 0)
    active = context.application.bot_data.setdefault("active_chats", {}).get(target_user_id)
    if not active:
        return

    username_pz = str((context.application.bot_data.get("profiles", {}) or {}).get(target_user_id, {}).get("username") or active.get("topic_base_name") or f"id{target_user_id}")
    admin_username = str(active.get("admin_username") or "админ")

    if action == "close":
        _record_info_topic_action(context, target_user_id, panel.get("issuer_user_id") or "system", "CLOSE_SESSION", panel_id=panel_id)
        active.pop("management_paused", None)
        active.pop("management_close_pending", None)
        try:
            await context.bot.edit_forum_topic(
                chat_id=chat_id,
                message_thread_id=topic_id,
                name=f"{active.get('topic_base_name') or username_pz} (Закрыто руководством)",
            )
        except Exception:
            pass
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                message_thread_id=topic_id,
                text="❗️Руководство бота закрыло сессию, Тема больше не актуальна",
            )
        except Exception:
            pass
        try:
            await context.bot.send_message(
                chat_id=int(target_user_id),
                text="❗️Ваша сессия была завершена руководством бота.",
            )
        except Exception:
            pass
        active["active"] = False
        context.application.bot_data.get("topic_user_map", {}).pop(topic_id, None)
        context.application.bot_data.get("topic_user_map", {}).pop(str(topic_id), None)
        context.application.bot_data.get("active_chats", {}).pop(target_user_id, None)
        try:
            await context.bot.send_message(chat_id=LOG_CHAT_ID, text=f"✅Сессия {username_pz} с админом {admin_username} закрыта руководством.")
        except Exception:
            pass
        context.application.bot_data.get("info_topic_logs", {}).pop(panel_id, None)
        panels.pop(panel_id, None)
        return

    if action == "stop":
        _record_info_topic_action(context, target_user_id, panel.get("issuer_user_id") or "system", "PAUSE_SESSION", panel_id=panel_id)
        active["management_paused"] = True
        try:
            await context.bot.edit_forum_topic(
                chat_id=chat_id,
                message_thread_id=topic_id,
                name=f"{active.get('topic_base_name') or username_pz} (Остановлено руководством)",
            )
        except Exception:
            pass
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                message_thread_id=topic_id,
                text="💤Руководство бота остановила общение в этой теме, сообщение отправляться не будут.",
            )
        except Exception:
            pass
        try:
            await context.bot.send_message(
                chat_id=int(target_user_id),
                text="💤Сессия была остановлена руководством бота. Сообщения временно не отправляются.",
            )
        except Exception:
            pass
        panel["pending_action"] = None
        try:
            await context.bot.send_message(chat_id=LOG_CHAT_ID, text=f"💤Сессия {username_pz} с админом {admin_username} остановлена руководством.")
        except Exception:
            pass
        return

    if action == "resume":
        _record_info_topic_action(context, target_user_id, panel.get("issuer_user_id") or "system", "RESUME_SESSION", panel_id=panel_id)
        active.pop("management_paused", None)
        try:
            await context.bot.edit_forum_topic(
                chat_id=chat_id,
                message_thread_id=topic_id,
                name=str(active.get("topic_base_name") or username_pz),
            )
        except Exception:
            pass
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                message_thread_id=topic_id,
                text="✅Тема снова актуальная.",
            )
        except Exception:
            pass
        try:
            await context.bot.send_message(
                chat_id=int(target_user_id),
                text="✅Тема снова актуальная. Пересылка сообщений восстановлена.",
            )
        except Exception:
            pass
        panel["pending_action"] = None
        try:
            await context.bot.send_message(chat_id=LOG_CHAT_ID, text=f"✅Сессия {username_pz} с админом {admin_username} возобновлена руководством.")
        except Exception:
            pass



async def info_topic_close_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    panel_id = (update.callback_query.data or "").split("_")[-1]
    panel = context.application.bot_data.setdefault("info_topic_panels", {}).get(panel_id)
    if not panel:
        await update.callback_query.answer("Панель устарела", show_alert=True)
        return

    if not _is_info_topic_session_admin(context, panel, update.effective_user.id):
        await update.callback_query.answer("Доступно только админу этой сессии", show_alert=True)
        return

    issuer_profile = _ensure_profile(context, str(update.effective_user.id), update.effective_user.username or f"id{update.effective_user.id}")
    if _effective_admin_level(issuer_profile) < 3:
        await update.callback_query.answer("Недостаточно прав", show_alert=True)
        return

    await _finish_info_topic_action(context, panel_id, "close")
    try:
        await update.callback_query.message.edit_text("✅Сессия закрыта руководством бота.")
    except Exception:
        pass



async def info_topic_stop_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    panel_id = (update.callback_query.data or "").split("_")[-1]
    panel = context.application.bot_data.setdefault("info_topic_panels", {}).get(panel_id)
    if not panel:
        await update.callback_query.answer("Панель устарела", show_alert=True)
        return

    if not _is_info_topic_session_admin(context, panel, update.effective_user.id):
        await update.callback_query.answer("Доступно только админу этой сессии", show_alert=True)
        return

    issuer_profile = _ensure_profile(context, str(update.effective_user.id), update.effective_user.username or f"id{update.effective_user.id}")
    if _effective_admin_level(issuer_profile) < 3:
        await update.callback_query.answer("Недостаточно прав", show_alert=True)
        return

    await _finish_info_topic_action(context, panel_id, "stop")
    try:
        await update.callback_query.message.edit_text(
            "💤Сессия остановлена руководством бота.",
            reply_markup=_build_info_topic_keyboard(panel_id, paused=True),
        )
    except Exception:
        pass



async def info_topic_resume_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    panel_id = (update.callback_query.data or "").split("_")[-1]
    panel = context.application.bot_data.setdefault("info_topic_panels", {}).get(panel_id)
    if not panel:
        await update.callback_query.answer("Панель устарела", show_alert=True)
        return

    if not _is_info_topic_session_admin(context, panel, update.effective_user.id):
        await update.callback_query.answer("Доступно только админу этой сессии", show_alert=True)
        return

    issuer_profile = _ensure_profile(context, str(update.effective_user.id), update.effective_user.username or f"id{update.effective_user.id}")
    if _effective_admin_level(issuer_profile) < 3:
        await update.callback_query.answer("Недостаточно прав", show_alert=True)
        return

    panel_text = str(panel.get("panel_text") or "✅Тема снова актуальная.")

    await _finish_info_topic_action(context, panel_id, "resume")
    try:
        await update.callback_query.message.edit_text(panel_text, reply_markup=_build_info_topic_keyboard(panel_id, paused=False))
    except Exception:
        pass



async def astats_gender_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    data = update.callback_query.data or ""
    parts = data.split("_")
    if len(parts) < 4:
        return
    session_id = parts[3]

    sessions = context.application.bot_data.setdefault("astats_tag_sessions", {})
    session = sessions.get(session_id)
    if not session:
        await update.callback_query.answer("Панель устарела", show_alert=True)
        return

    if str(update.effective_user.id) != str(session.get("issuer_user_id")):
        await update.callback_query.answer("Кнопка доступна только автору /astats", show_alert=True)
        return

    issuer_profile = _ensure_profile(
        context,
        str(update.effective_user.id),
        update.effective_user.username or f"id{update.effective_user.id}",
    )
    if _effective_admin_level(issuer_profile) < 4:
        await update.callback_query.answer("Недостаточно прав", show_alert=True)
        return

    try:
        await update.callback_query.message.edit_text(
            "☕️Редактор смены пола администратору",
            reply_markup=_build_astats_gender_editor_keyboard(session_id),
        )
    except Exception:
        pass



async def astats_gender_set_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    data = update.callback_query.data or ""
    parts = data.split("_")
    if len(parts) < 5:
        return
    session_id = parts[3]
    gender_key = parts[4]

    if gender_key not in {"male", "female"}:
        return

    sessions = context.application.bot_data.setdefault("astats_tag_sessions", {})
    session = sessions.get(session_id)
    if not session:
        await update.callback_query.answer("Панель устарела", show_alert=True)
        return

    if str(update.effective_user.id) != str(session.get("issuer_user_id")):
        await update.callback_query.answer("Кнопка доступна только автору /astats", show_alert=True)
        return

    issuer_profile = _ensure_profile(
        context,
        str(update.effective_user.id),
        update.effective_user.username or f"id{update.effective_user.id}",
    )
    if _effective_admin_level(issuer_profile) < 4:
        await update.callback_query.answer("Недостаточно прав", show_alert=True)
        return

    target_user_id = str(session.get("target_user_id"))
    target_profile = _ensure_profile(context, target_user_id, f"id{target_user_id}")
    admin_gender = "🙎‍♂️Мальчик" if gender_key == "male" else "🙍‍♀️Девочка"
    target_profile["admin_gender"] = admin_gender
    _save_profile_record(context, target_user_id)

    try:
        await context.bot.send_message(
            chat_id=LOG_CHAT_ID,
            text=(
                f"✅Смена пола выполнена: id_profile #{target_profile.get('id_profile')} -> {admin_gender}. "
                f"Инициатор: id{update.effective_user.id}"
            ),
        )
    except Exception:
        pass

    try:
        await context.bot.send_message(
            chat_id=int(target_user_id),
            text=f"📍Ваш пол был изменен. Теперь вы {admin_gender}",
        )
    except Exception:
        pass

    try:
        await update.callback_query.message.edit_text(
            _build_admin_stats_text(str(target_user_id), target_profile),
            parse_mode=ParseMode.HTML,
            reply_markup=_build_astats_profile_keyboard(session_id),
        )
    except Exception:
        pass



async def astats_gender_back_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer("Отмена")
    data = update.callback_query.data or ""
    parts = data.split("_")
    if len(parts) < 4:
        return
    session_id = parts[3]

    sessions = context.application.bot_data.setdefault("astats_tag_sessions", {})
    session = sessions.get(session_id)
    if not session:
        await update.callback_query.answer("Панель устарела", show_alert=True)
        return

    if str(update.effective_user.id) != str(session.get("issuer_user_id")):
        await update.callback_query.answer("Кнопка доступна только автору /astats", show_alert=True)
        return

    target_user_id = str(session.get("target_user_id"))
    target_profile = _ensure_profile(context, target_user_id, f"id{target_user_id}")

    try:
        await update.callback_query.message.edit_text(
            _build_admin_stats_text(str(target_user_id), target_profile),
            parse_mode=ParseMode.HTML,
            reply_markup=_build_astats_profile_keyboard(session_id),
        )
    except Exception:
        pass



async def astats_bio_cancel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer("Отмена")
    data = update.callback_query.data or ""
    parts = data.split("_")
    if len(parts) < 4:
        return
    session_id = parts[3]

    sessions = context.application.bot_data.setdefault("astats_tag_sessions", {})
    session = sessions.get(session_id)
    if not session:
        await update.callback_query.answer("Панель устарела", show_alert=True)
        return

    if str(update.effective_user.id) != str(session.get("issuer_user_id")):
        await update.callback_query.answer("Кнопка доступна только автору /astats", show_alert=True)
        return

    pending_bio_by_admin = context.application.bot_data.setdefault("pending_astats_bio_by_admin", {})
    pending_bio_by_admin.pop(str(update.effective_user.id), None)

    session["status"] = "idle"
    target_user_id = str(session.get("target_user_id"))
    target_profile = _ensure_profile(context, target_user_id, f"id{target_user_id}")

    try:
        await update.callback_query.message.edit_text(
            _build_admin_stats_text(str(target_user_id), target_profile),
            parse_mode=ParseMode.HTML,
            reply_markup=_build_astats_profile_keyboard(session_id),
        )
    except Exception:
        pass



async def astats_tag_cancel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer("Отмена")
    data = update.callback_query.data or ""
    parts = data.split("_")
    if len(parts) < 4:
        return
    session_id = parts[3]

    sessions = context.application.bot_data.setdefault("astats_tag_sessions", {})
    session = sessions.get(session_id)
    if not session:
        return

    if str(update.effective_user.id) != str(session.get("issuer_user_id")):
        await update.callback_query.answer("Кнопка доступна только автору /astats", show_alert=True)
        return

    pending_by_admin = context.application.bot_data.setdefault("pending_astats_tag_by_admin", {})
    pending_by_admin.pop(str(update.effective_user.id), None)
    pending_by_chat = context.application.bot_data.setdefault("pending_astats_tag_by_chat", {})
    pending_by_chat.pop(str(update.effective_chat.id), None)

    prompt_message_id = session.get("prompt_message_id")
    if prompt_message_id:
        try:
            await context.bot.delete_message(chat_id=int(session.get("chat_id")), message_id=int(prompt_message_id))
        except Exception:
            pass
    try:
        await update.callback_query.message.delete()
    except Exception:
        pass

    session["status"] = "idle"
    session["prompt_message_id"] = None



async def astats_bio_view_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    data = update.callback_query.data or ""
    parts = data.split("_")
    if len(parts) < 4:
        return

    target_user_id = str(parts[3])
    if str(update.effective_user.id) != target_user_id:
        await update.callback_query.answer("Кнопка доступна только владельцу профиля", show_alert=True)
        return

    profile = _ensure_profile(context, target_user_id, f"id{target_user_id}")
    biography_text = str(profile.get("biography_admin") or "не заполнена")
    await update.callback_query.message.reply_text(f"☺️Ваша новая биография: {biography_text}")



async def handle_astats_tag_input_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_user or update.effective_user.is_bot:
        return

    pending_by_admin = context.application.bot_data.setdefault("pending_astats_tag_by_admin", {})
    pending_by_chat = context.application.bot_data.setdefault("pending_astats_tag_by_chat", {})
    sessions = context.application.bot_data.setdefault("astats_tag_sessions", {})
    admin_user_id = str(update.effective_user.id)
    session_id = pending_by_admin.get(admin_user_id)
    resolved_from = "admin"

    reply_message_id = int(getattr(getattr(update.message, "reply_to_message", None), "message_id", 0) or 0)
    if not session_id and reply_message_id:
        for candidate_id, candidate in sessions.items():
            if str(candidate.get("status")) != "await_tag":
                continue
            if int(candidate.get("chat_id", 0) or 0) != int(update.effective_chat.id):
                continue
            if int(candidate.get("prompt_message_id", 0) or 0) != reply_message_id:
                continue
            session_id = str(candidate_id)
            resolved_from = "reply"
            break

    if not session_id:
        session_id = pending_by_chat.get(str(update.effective_chat.id))
        resolved_from = "chat"
    if not session_id:
        return

    session = sessions.get(str(session_id))
    if not session:
        pending_by_admin.pop(admin_user_id, None)
        pending_by_chat.pop(str(update.effective_chat.id), None)
        return

    if str(update.effective_user.id) != str(session.get("issuer_user_id")):
        if resolved_from == "chat":
            await update.message.reply_text("Эту смену тега запустил другой администратор.")
            raise ApplicationHandlerStop
        return

    if str(session.get("status")) != "await_tag":
        return

    if int(session.get("chat_id", 0) or 0) != int(update.effective_chat.id):
        await update.message.reply_text("Отправьте тег в тот же чат, где была нажата кнопка смены тега.")
        raise ApplicationHandlerStop

    if resolved_from != "reply" and not pending_by_admin.get(admin_user_id):
        await update.message.reply_text("Ответьте новым тегом на сообщение бота с просьбой отправить тег.")
        raise ApplicationHandlerStop

    new_tag = (update.message.text or "").strip()
    if not new_tag.startswith("#"):
        await update.message.reply_text("Тег должен начинаться с #.")
        raise ApplicationHandlerStop

    target_user_id = str(session.get("target_user_id"))
    target_profile = _ensure_profile(context, target_user_id, f"id{target_user_id}")
    old_tag = str(target_profile.get("tag_admin") or "не указан")
    target_profile["tag_admin_new"] = new_tag
    _save_profile_record(context, target_user_id)

    try:
        await context.bot.send_message(
            chat_id=int(target_user_id),
            text=f"📍Ваш уникальный тег {old_tag} был изменен на {new_tag}",
        )
    except Exception:
        pass

    target_profile["tag_admin"] = str(target_profile.get("tag_admin_new") or new_tag)
    target_profile.pop("tag_admin_new", None)
    _save_profile_record(context, target_user_id)

    await update.message.reply_text("✅Замена тега успешна.")

    prompt_message_id = session.get("prompt_message_id")
    if prompt_message_id:
        try:
            await context.bot.delete_message(chat_id=int(session.get("chat_id")), message_id=int(prompt_message_id))
        except Exception:
            pass

    pending_by_admin.pop(admin_user_id, None)
    pending_by_chat.pop(str(update.effective_chat.id), None)
    sessions.pop(str(session_id), None)
    raise ApplicationHandlerStop



async def handle_astats_bio_input_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_user or update.effective_user.is_bot:
        return

    pending_bio_by_admin = context.application.bot_data.setdefault("pending_astats_bio_by_admin", {})
    sessions = context.application.bot_data.setdefault("astats_tag_sessions", {})
    admin_user_id = str(update.effective_user.id)
    session_id = pending_bio_by_admin.get(admin_user_id)
    if not session_id:
        return

    session = sessions.get(str(session_id))
    if not session:
        pending_bio_by_admin.pop(admin_user_id, None)
        return

    if str(session.get("status")) != "await_bio":
        return

    if int(session.get("chat_id", 0) or 0) != int(update.effective_chat.id):
        return

    biography_text = (update.message.text or "").strip()
    if not biography_text:
        await update.message.reply_text("Биография не должна быть пустой.")
        raise ApplicationHandlerStop

    target_user_id = str(session.get("target_user_id"))
    target_profile = _ensure_profile(context, target_user_id, f"id{target_user_id}")
    target_profile["biography_admin"] = biography_text
    _save_profile_record(context, target_user_id)

    try:
        await context.bot.send_message(
            chat_id=int(target_user_id),
            text="📍Ваша биография была изменена.",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("📖Посмотреть", callback_data=f"astats_bio_view_{target_user_id}")]]
            ),
        )
    except Exception:
        pass

    await update.message.reply_text("✅Биография успешно изменена.")

    editor_message_id = int(session.get("editor_message_id", 0) or 0)
    if editor_message_id:
        try:
            await context.bot.edit_message_text(
                chat_id=int(session.get("chat_id")),
                message_id=editor_message_id,
                text=_build_admin_stats_text(str(target_user_id), target_profile),
                parse_mode=ParseMode.HTML,
                reply_markup=_build_astats_profile_keyboard(str(session_id)),
            )
        except Exception:
            pass

    pending_bio_by_admin.pop(admin_user_id, None)
    session["status"] = "idle"
    raise ApplicationHandlerStop


# ==== SECTION: Direct admin messaging & prefix management ====
# /pm and /kus let staff message a user/chat directly from the log chat;
# /setprefix and its callbacks pick the ad-prefix label (see
# app.keyboards.inline._prefix_options) shown on an admin's cooperation posts.
async def pm_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not _is_special_admin_chat(update.effective_chat.id):
        return

    issuer_profile = _ensure_profile(
        context,
        str(update.effective_user.id),
        update.effective_user.username or f"id{update.effective_user.id}",
    )
    if not _has_admin_rights_level_1_5(issuer_profile):
        await update.message.reply_text("Команда доступна только администраторам 1 категории и выше.")
        return

    raw_text = update.message.text or ""
    cmd_entity = update.message.entities[0] if update.message.entities else None
    cmd_len = cmd_entity.length if cmd_entity and cmd_entity.type == "bot_command" else len("/pm")
    args_text = raw_text[cmd_len:].strip()
    if not args_text:
        await update.message.reply_text('Используйте: /pm "id_profile" "текст"')
        return

    try:
        parts = shlex.split(args_text)
    except ValueError:
        await update.message.reply_text('Некорректный формат. Используйте: /pm "id_profile" "текст"')
        return

    if len(parts) < 2:
        await update.message.reply_text('Используйте: /pm "id_profile" "текст"')
        return

    target_identifier = parts[0]
    dm_text = " ".join(parts[1:]).strip()
    if not dm_text:
        await update.message.reply_text("Текст сообщения не должен быть пустым.")
        return

    if _has_suspicious_username(dm_text):
        await update.message.reply_text("Отправка отклонена: текст не должен содержать @username или ссылки t.me.")
        return

    target_user_id, profile = _resolve_warn_target(context, target_identifier)
    if not profile:
        await update.message.reply_text(f'Пользователь с id_profile "{target_identifier}" не найден.')
        return

    username = str(profile.get("username") or f"id{target_user_id}")
    try:
        await context.bot.send_message(chat_id=int(target_user_id), text=dm_text)
    except Forbidden:
        _set_user_blocked_bot_state(context, str(target_user_id), True)
        await update.message.reply_text(f'Не удалось отправить ЛС пользователю "{username}": пользователь заблокировал бота.')
        return
    except Exception:
        await update.message.reply_text(f'Не удалось отправить ЛС пользователю "{username}".')
        return

    await update.message.reply_text(f'✅ЛС успешно отправлено пользователю "{username}" (id_profile #{profile.get("id_profile")}).')



async def kus_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = getattr(update, "message", None)
    user = update.effective_user
    chat = update.effective_chat
    if message is None or not user or chat is None:
        return

    if chat.type == ChatType.PRIVATE:
        active = context.application.bot_data.get("active_chats", {}).get(str(user.id))
        if not active or not active.get("active"):
            await message.reply_text("У вас нет активной переписки.")
            raise ApplicationHandlerStop

        if active.get("paused") or active.get("management_paused"):
            await message.reply_text("Сессия сейчас остановлена. Сначала возобновите общение.")
            raise ApplicationHandlerStop

        admin_id = str(active.get("admin_id") or "")
        topic_id = active.get("topic_id")
        chat_id = active.get("chat_id")
        admin_profile = _ensure_profile(context, admin_id, active.get("admin_username") or f"id{admin_id}")
        user_profile = _ensure_profile(context, str(user.id), user.username or f"id{user.id}")

        admin_tag = str(admin_profile.get("tag_admin") or active.get("admin_tag") or active.get("admin_username") or f"id{admin_id}")
        username_pz = str(user_profile.get("username") or active.get("topic_base_name") or f"id{user.id}")

        try:
            await message.reply_text(f"Вы укусили {admin_tag}")
        except Exception:
            pass

        if chat_id and topic_id is not None:
            try:
                await context.bot.send_message(
                    chat_id=chat_id,
                    message_thread_id=topic_id,
                    text=f"#RP 💕Вас укусил(-а) {username_pz}",
                )
            except Exception:
                pass

        active["rp_topic"] = int(active.get("rp_topic", 0) or 0) + 1
        raise ApplicationHandlerStop

    if chat.id != WORK_CHAT_ID:
        return

    topic_id = getattr(message, "message_thread_id", None)
    if topic_id is None:
        await message.reply_text("Команда доступна только в теме активного диалога.")
        raise ApplicationHandlerStop

    topic_map = context.application.bot_data.get("topic_user_map", {}) or {}
    target_user_id = topic_map.get(topic_id) or topic_map.get(str(topic_id))
    active = (context.application.bot_data.get("active_chats", {}) or {}).get(str(target_user_id)) if target_user_id else None
    if not active or not active.get("active"):
        await message.reply_text("У вас нет активной переписки.")
        raise ApplicationHandlerStop

    if active.get("paused") or active.get("management_paused"):
        await message.reply_text("Сессия сейчас остановлена. Сначала возобновите общение.")
        raise ApplicationHandlerStop

    admin_profile = _ensure_profile(context, str(user.id), user.username or f"id{user.id}")
    target_profile = _ensure_profile(context, str(target_user_id), active.get("topic_base_name") or f"id{target_user_id}")

    admin_tag = str(admin_profile.get("tag_admin") or admin_profile.get("username") or f"id{user.id}")
    username_pz = str(target_profile.get("username") or active.get("topic_base_name") or f"id{target_user_id}")

    try:
        await message.reply_text(f"Вы укусили {username_pz}")
    except Exception:
        pass

    try:
        await context.bot.send_message(
            chat_id=int(target_user_id),
            text=f"Вас укусил(-а) {admin_tag}",
        )
    except Exception:
        pass

    active["rp_topic"] = int(active.get("rp_topic", 0) or 0) + 1
    raise ApplicationHandlerStop



async def setprefix_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not _is_special_admin_chat(update.effective_chat.id):
        return

    issuer_profile = _ensure_profile(
        context,
        str(update.effective_user.id),
        update.effective_user.username or f"id{update.effective_user.id}",
    )
    if _effective_admin_level(issuer_profile) != 5:
        await update.message.reply_text("Команда доступна только администраторам 5 категории.")
        return

    raw_text = update.message.text or ""
    cmd_entity = update.message.entities[0] if update.message.entities else None
    cmd_len = cmd_entity.length if cmd_entity and cmd_entity.type == "bot_command" else len("/setprefix")
    args_text = raw_text[cmd_len:].strip()
    if not args_text:
        await update.message.reply_text('Используйте: /setprefix "id_profile"')
        return

    try:
        parts = shlex.split(args_text)
    except ValueError:
        await update.message.reply_text('Некорректный формат. Используйте: /setprefix "id_profile"')
        return

    if len(parts) < 1:
        await update.message.reply_text('Используйте: /setprefix "id_profile"')
        return

    target_identifier = parts[0]
    target_user_id, profile = _resolve_warn_target(context, target_identifier)
    if not profile:
        await update.message.reply_text(f'Пользователь с id_profile "{target_identifier}" не найден.')
        return
    if not _has_admin_rights_level_1_5(profile):
        await update.message.reply_text(
            f'Нельзя открыть панель: id_profile #{profile.get("id_profile")} не имеет админ-прав 1-5 уровня.'
        )
        return

    target_id_profile = int(profile.get("id_profile", 0) or 0)
    panel_id = _next_prefix_panel_id(context)
    pending_panels = context.application.bot_data.setdefault("pending_prefix_edit_panels", {})
    pending_panels[panel_id] = {
        "issuer_user_id": str(update.effective_user.id),
        "target_user_id": str(target_user_id),
        "target_id_profile": target_id_profile,
        "selected_prefix_keys": [],
    }

    existing_prefix = str(profile.get("prefix") or "").strip()
    for key, label in _prefix_options():
        if label in [item.strip() for item in existing_prefix.split(",")]:
            pending_panels[panel_id]["selected_prefix_keys"].append(key)

    await update.message.reply_text(
        f"☕️Открыта панель редактирования префикса человека {target_id_profile}",
        reply_markup=_build_setprefix_keyboard(panel_id, pending_panels[panel_id]["selected_prefix_keys"]),
    )


async def setrep_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not _is_special_admin_chat(update.effective_chat.id):
        return

    issuer_profile = _ensure_profile(
        context,
        str(update.effective_user.id),
        update.effective_user.username or f"id{update.effective_user.id}",
    )
    if _effective_admin_level(issuer_profile) != 5:
        await update.message.reply_text("Команда доступна только администраторам 5 категории.")
        return

    raw_text = update.message.text or ""
    cmd_entity = update.message.entities[0] if update.message.entities else None
    cmd_len = cmd_entity.length if cmd_entity and cmd_entity.type == "bot_command" else len("/setrep")
    args_text = raw_text[cmd_len:].strip()
    if not args_text:
        await update.message.reply_text('Используйте: /setrep "id_profile" "count"')
        return

    try:
        parts = shlex.split(args_text)
    except ValueError:
        await update.message.reply_text('Некорректный формат. Используйте: /setrep "id_profile" "count"')
        return

    if len(parts) < 2 or not parts[1].isdigit():
        await update.message.reply_text('Используйте: /setrep "id_profile" "count", где count — целое число не меньше 0.')
        return

    target_identifier = parts[0]
    reputation = int(parts[1])
    target_user_id, profile = _resolve_warn_target(context, target_identifier)
    if not profile:
        await update.message.reply_text(f'Пользователь с id_profile "{target_identifier}" не найден.')
        return
    if not _has_admin_rights_level_1_5(profile):
        await update.message.reply_text(
            f'Нельзя изменить репутацию: id_profile #{profile.get("id_profile")} не является администратором.'
        )
        return

    profile["admin_reputation"] = reputation
    _save_profile_record(context, str(target_user_id))
    target_id_profile = int(profile.get("id_profile", 0) or 0)
    await update.message.reply_text(
        f"✅Репутация администратора id_profile #{target_id_profile} изменена на {reputation}."
    )

    try:
        await context.bot.send_message(
            chat_id=int(target_user_id),
            text=f"⭐Ваша репутация администратора изменена на {reputation}.",
        )
    except Forbidden:
        await update.message.reply_text(
            "Не удалось уведомить администратора в личных сообщениях: пользователь заблокировал бота."
        )
    except Exception:
        logging.exception("setrep_command_handler failed to notify admin %s", target_user_id)


async def setprefix_select_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    data = update.callback_query.data or ""
    parts = data.split("_")
    if len(parts) < 4:
        return
    panel_id = parts[2]
    selected_key = parts[3]

    valid_keys = {key for key, _ in _prefix_options()}
    if selected_key not in valid_keys:
        return

    pending_panels = context.application.bot_data.setdefault("pending_prefix_edit_panels", {})
    panel = pending_panels.get(panel_id)
    if not panel:
        await update.callback_query.answer("Панель редактирования устарела", show_alert=True)
        return

    if str(update.effective_user.id) != str(panel.get("issuer_user_id")):
        await update.callback_query.answer("Кнопка доступна только автору панели", show_alert=True)
        return

    issuer_profile = _ensure_profile(
        context,
        str(update.effective_user.id),
        update.effective_user.username or f"id{update.effective_user.id}",
    )
    if _effective_admin_level(issuer_profile) != 5:
        await update.callback_query.answer("Недостаточно прав", show_alert=True)
        return

    selected_keys = set(panel.get("selected_prefix_keys") or [])
    if selected_key in selected_keys:
        selected_keys.remove(selected_key)
    else:
        if len(selected_keys) >= 3:
            await update.callback_query.answer("Можно выбрать не больше 3 префиксов.", show_alert=True)
            return
        selected_keys.add(selected_key)
    panel["selected_prefix_keys"] = sorted(selected_keys)
    try:
        await update.callback_query.message.edit_reply_markup(
            reply_markup=_build_setprefix_keyboard(panel_id, panel.get("selected_prefix_keys"))
        )
    except Exception:
        pass



async def setprefix_apply_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    panel_id = (update.callback_query.data or "").split("_")[-1]
    pending_panels = context.application.bot_data.setdefault("pending_prefix_edit_panels", {})
    panel = pending_panels.get(panel_id)
    if not panel:
        await update.callback_query.answer("Панель редактирования устарела", show_alert=True)
        return

    if str(update.effective_user.id) != str(panel.get("issuer_user_id")):
        await update.callback_query.answer("Кнопка доступна только автору панели", show_alert=True)
        return

    issuer_profile = _ensure_profile(
        context,
        str(update.effective_user.id),
        update.effective_user.username or f"id{update.effective_user.id}",
    )
    if _effective_admin_level(issuer_profile) != 5:
        await update.callback_query.answer("Недостаточно прав", show_alert=True)
        return

    target_user_id = str(panel.get("target_user_id"))
    target_profile = _ensure_profile(context, target_user_id, f"id{target_user_id}")

    selected_keys = panel.get("selected_prefix_keys") or []
    selected_labels = []
    for key, label in _prefix_options():
        if key in selected_keys:
            selected_labels.append(label)

    if selected_labels:
        target_profile["prefixes"] = selected_labels
        target_profile["prefix"] = ", ".join(selected_labels)
    else:
        target_profile.pop("prefixes", None)
        target_profile.pop("prefix", None)
    _save_profile_record(context, target_user_id)

    target_id_profile = int(target_profile.get("id_profile", 0) or panel.get("target_id_profile", 0) or 0)
    prefix_text = ", ".join(selected_labels) if selected_labels else "не установлен"
    try:
        await update.callback_query.message.edit_text(
            f"✅Префикс для id_profile #{target_id_profile} применен: {prefix_text}"
        )
    except Exception:
        pass

    try:
        await context.bot.send_message(
            chat_id=int(target_user_id),
            text=f"✅Ваш админ-префикс был отредактирован. Теперь ваш префикс: {prefix_text}",
        )
    except Exception:
        pass

    pending_panels.pop(panel_id, None)


# ==== SECTION: Ban/unban moderation & user profile review ====
# /unwarn, /ban, /unban and the admin_take/warn_user/confirm_warn button
# flow (issued from a user's profile card) apply moderation actions via
# app.services.bans and persist through app.database.requests.
async def unwarn_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id not in {LOG_CHAT_ID, WORK_CHAT_ID} or not update.message:
        return

    if not _can_use_moderation_commands(context, str(update.effective_user.id)):
        await update.message.reply_text("Команда доступна только администраторам 2 категории и выше.")
        return

    raw_text = update.message.text or ""
    cmd_entity = update.message.entities[0] if update.message.entities else None
    cmd_len = cmd_entity.length if cmd_entity and cmd_entity.type == "bot_command" else len("/unwarn")
    args_text = raw_text[cmd_len:].strip()
    if not args_text:
        await update.message.reply_text('Укажите id_profile: /unwarn "id_profile"')
        return

    try:
        parts = shlex.split(args_text)
    except ValueError:
        await update.message.reply_text('Некорректный формат. Используйте: /unwarn "id_profile"')
        return

    if len(parts) < 1:
        await update.message.reply_text('Укажите id_profile: /unwarn "id_profile"')
        return

    target_identifier = parts[0]
    target_user_id, profile = _resolve_warn_target(context, target_identifier)
    if not profile:
        await update.message.reply_text(f'Пользователь с id_profile "{target_identifier}" не найден.')
        return

    current_warn = int(profile.get("warn", 0) or 0)
    if current_warn <= 0:
        await update.message.reply_text(f'У пользователя id_profile #{profile.get("id_profile")} нет предупреждений для снятия.')
        return

    profile["warn"] = current_warn - 1
    if profile["warn"] <= 0:
        profile["reason"] = "нет причин"
    _save_profile_record(context, str(target_user_id))

    await update.message.reply_text(
        f'✅Пользователю id_profile #{profile.get("id_profile")} снято предупреждение. Теперь у него {profile["warn"]} warn(-ов).'
    )

    try:
        await context.bot.send_message(
            chat_id=int(target_user_id),
            text=f'✅С Вас сняли предупреждение. Теперь у вас {profile["warn"]} предупреждений',
        )
    except Exception:
        pass



async def ban_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not _is_special_admin_chat(update.effective_chat.id):
        return

    if not _can_use_moderation_commands(context, str(update.effective_user.id)):
        await update.message.reply_text("Команда доступна только администраторам 2 категории и выше.")
        return

    raw_text = update.message.text or ""
    cmd_entity = update.message.entities[0] if update.message.entities else None
    cmd_len = cmd_entity.length if cmd_entity and cmd_entity.type == "bot_command" else len("/ban")
    args_text = raw_text[cmd_len:].strip()
    if not args_text:
        await update.message.reply_text('Используйте: /ban "id_profile" "30m" "reason" (s/m/h/d)')
        return

    try:
        parts = shlex.split(args_text)
    except ValueError:
        await update.message.reply_text('Некорректный формат. Используйте: /ban "id_profile" "30m" "reason" (s/m/h/d)')
        return

    if len(parts) < 3:
        await update.message.reply_text('Используйте: /ban "id_profile" "30m" "reason" (s/m/h/d)')
        return

    target_identifier = parts[0]
    duration_match = re.fullmatch(
        r"([1-9]\d*)\s*"
        r"(s|sec(?:ond)?s?|m|min(?:ute)?s?|h|hour?s?|d|day?s?|"
        r"сек(?:унда|унды|унд)?|мин(?:ута|уты|ут)?|ч(?:ас|аса|асов)?|д(?:ень|ня|ней)?)",
        parts[1].strip().lower(),
    )
    if not duration_match:
        await update.message.reply_text(
            'Длительность должна быть числом с единицей: "30s", "10m", "2h" или "1d".'
        )
        return
    duration_value = int(duration_match.group(1))
    unit = duration_match.group(2)
    if unit.startswith(("s", "сек")):
        duration_multiplier = 1
    elif unit.startswith(("m", "мин")):
        duration_multiplier = 60
    elif unit.startswith(("h", "ч")):
        duration_multiplier = 60 * 60
    else:
        duration_multiplier = 24 * 60 * 60
    duration_seconds = duration_value * duration_multiplier
    if duration_seconds > MAX_MODERATION_DURATION_SECONDS:
        await update.message.reply_text("Максимальный срок бана — 365 дней.")
        return
    reason = " ".join(parts[2:]).strip()
    if not reason:
        await update.message.reply_text("Укажите причину блокировки.")
        return

    target_user_id, profile = _resolve_ban_target(context, target_identifier)
    if not profile:
        await update.message.reply_text(f'Пользователь с id_profile "{target_identifier}" не найден.')
        return

    if _has_admin_rights_level_1_5(profile) or _has_admin_ban_immunity(profile):
        await update.message.reply_text(
            f'Невозможно выдать бан: id_profile #{profile.get("id_profile")} имеет админ-права 1-5 уровня.'
        )
        return

    await _apply_ban(
        context,
        str(target_user_id),
        profile.get("username"),
        reason,
        "BAN",
        duration_seconds=duration_seconds,
    )
    await update.message.reply_text(
        f'⛔Пользователь id_profile #{profile.get("id_profile")} заблокирован на {parts[1]}. Причина: "{reason}"'
    )


async def permanent_ban_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not _is_special_admin_chat(update.effective_chat.id):
        return

    if not _can_use_moderation_commands(context, str(update.effective_user.id)):
        await update.message.reply_text("Команда доступна только администраторам 2 категории и выше.")
        return

    raw_text = update.message.text or ""
    cmd_entity = update.message.entities[0] if update.message.entities else None
    cmd_len = cmd_entity.length if cmd_entity and cmd_entity.type == "bot_command" else len("/pban")
    args_text = raw_text[cmd_len:].strip()
    if not args_text:
        await update.message.reply_text('Используйте: /pban "id_profile" "reason"')
        return

    try:
        parts = shlex.split(args_text)
    except ValueError:
        await update.message.reply_text('Некорректный формат. Используйте: /pban "id_profile" "reason"')
        return

    if len(parts) < 2:
        await update.message.reply_text('Используйте: /pban "id_profile" "reason"')
        return

    target_identifier = parts[0]
    reason = " ".join(parts[1:]).strip()
    target_user_id, profile = _resolve_ban_target(context, target_identifier)
    if not profile:
        await update.message.reply_text(f'Пользователь с id_profile "{target_identifier}" не найден.')
        return

    if _has_admin_rights_level_1_5(profile) or _has_admin_ban_immunity(profile):
        await update.message.reply_text(
            f'Невозможно выдать бан: id_profile #{profile.get("id_profile")} имеет админ-права 1-5 уровня.'
        )
        return

    banned_users = context.application.bot_data.setdefault("banned_users", {})
    banned_users.pop(str(target_user_id), None)
    _save_ban_record(context, str(target_user_id))
    applied = await _apply_ban(
        context,
        str(target_user_id),
        profile.get("username"),
        reason,
        "PERMANENT BAN",
    )
    if not applied:
        await update.message.reply_text("Не удалось выдать бессрочный бан пользователю.")
        return

    await update.message.reply_text(
        f'⛔Пользователь id_profile #{profile.get("id_profile")} заблокирован навсегда. '
        f'Причина: "{reason}"'
    )



async def unban_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not _is_special_admin_chat(update.effective_chat.id):
        return

    if not _can_use_moderation_commands(context, str(update.effective_user.id)):
        await update.message.reply_text("Команда доступна только администраторам 2 категории и выше.")
        return

    raw_text = update.message.text or ""
    cmd_entity = update.message.entities[0] if update.message.entities else None
    cmd_len = cmd_entity.length if cmd_entity and cmd_entity.type == "bot_command" else len("/unban")
    args_text = raw_text[cmd_len:].strip()
    if not args_text:
        await update.message.reply_text('Укажите id_profile: /unban "id_profile"')
        return

    try:
        parts = shlex.split(args_text)
    except ValueError:
        await update.message.reply_text('Некорректный формат. Используйте: /unban "id_profile"')
        return

    if len(parts) < 1:
        await update.message.reply_text('Укажите id_profile: /unban "id_profile"')
        return

    target_identifier = parts[0]
    target_user_id, profile = _resolve_ban_target(context, target_identifier)
    if not profile:
        await update.message.reply_text(f'Пользователь с id_profile "{target_identifier}" не найден.')
        return

    banned_users = context.application.bot_data.setdefault("banned_users", {})
    if not banned_users.pop(str(target_user_id), None):
        await update.message.reply_text(f'Пользователь id_profile #{profile.get("id_profile")} не находится в бане.')
        return
    _save_ban_record(context, str(target_user_id))

    await update.message.reply_text(f'✅Пользователь id_profile #{profile.get("id_profile")} разбанен.')

    try:
        await context.bot.send_message(
            chat_id=int(target_user_id),
            text="✅С вашей учетной записи снята блокировка. Функции бота снова доступны.",
        )
    except Exception:
        pass


async def admin_take_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user:
        return
    admin_profile = _ensure_profile(
        context,
        str(update.effective_user.id),
        update.effective_user.username or f"id{update.effective_user.id}",
    )
    if not _has_admin_rights_level_1_5(admin_profile):
        await update.callback_query.answer("Действие доступно только администраторам 1 категории и выше.", show_alert=True)
        return
    data = update.callback_query.data  # e.g. "take_user_12345"
    parts = data.split("_")
    if len(parts) < 3:
        return
    request_user_id = parts[2]

    # find the request info
    app_requests = context.application.bot_data.setdefault("admin_requests", {})
    req_info = app_requests.get(str(request_user_id))
    if not req_info:
        await update.callback_query.answer("Заявка более недоступна", show_alert=True)
        return

    mood = req_info.get("mood")
    if not _admin_supports_mood(admin_profile, mood):
        await update.callback_query.answer(
            "❌Вы не можете взять данного пользователя, так как этот тип общения не указан в вашем профиле.",
            show_alert=True,
        )
        return

    requested_gender = req_info.get("gender")
    if not _admin_matches_gender(admin_profile, requested_gender):
        await update.callback_query.answer(
            "❌Вы не можете взять данного пользователя, так как ваш пол не соответствует запрошенному.",
            show_alert=True,
        )
        return

    await update.callback_query.answer("Пользователь принят")
    _record_info_topic_action(context, request_user_id, update.effective_user.id, "TAKE_USER", reason="Запрос принят")

    # delete the original admin request panel
    try:
        await update.callback_query.message.delete()
    except Exception:
        pass

    user_requests = context.user_data.get("admin_request", {})
    if str(request_user_id) in user_requests:
        del user_requests[str(request_user_id)]
    app_requests.pop(str(request_user_id), None)

    async def _refresh_user_session_menu():
        await asyncio.sleep(5)
        active = (context.application.bot_data.get("active_chats", {}) or {}).get(str(request_user_id))
        if not active or not active.get("active"):
            return
        try:
            await context.bot.send_message(
                chat_id=int(request_user_id),
                text="📱Меню диалога обновлено.",
                reply_markup=_build_active_session_keyboard(),
            )
        except Exception:
            pass

    asyncio.create_task(_refresh_user_session_menu())

    chat_id = req_info.get("chat_id")
    topic_id = req_info.get("topic_id")
    username = req_info.get("username")
    admin_username = f"@{update.effective_user.username}" if update.effective_user.username else f"id{update.effective_user.id}"
    admin_tag = str(admin_profile.get("tag_admin") or admin_username)
    session_topic_name = str(admin_tag or admin_username)
    requester_profile = _ensure_profile(context, str(request_user_id), username)
    requester_id_profile = requester_profile.get("id_profile")

    accept_text = (
        f"📊Вы находитесь в переписке на тему {mood}\n"
        f"Имя пользователя: {username}\n"
        f"Айди профиля: {requester_id_profile}\n"
        f"Администратор {admin_username}"
    )

    try:
        buttons = _build_active_dialog_admin_keyboard(request_user_id)
        if topic_id:
            accepted_msg = await context.bot.send_message(
                chat_id=chat_id,
                text=accept_text,
                message_thread_id=topic_id,
                reply_markup=buttons,
            )
        else:
            accepted_msg = await context.bot.send_message(chat_id=chat_id, text=accept_text, reply_markup=buttons)
        await context.bot.pin_chat_message(chat_id=chat_id, message_id=accepted_msg.message_id, disable_notification=True)
    except Exception:
        accepted_msg = None

    # Ensure replies to the acceptance/pinned message route to the user
    try:
        if accepted_msg is not None:
            group_map = context.application.bot_data.setdefault("group_message_map", {})
            group_map[accepted_msg.message_id] = str(request_user_id)
            group_map[str(accepted_msg.message_id)] = str(request_user_id)
    except Exception:
        pass
    # send user DM messages
    requester_chat_id = int(request_user_id)
    user_text_1 = (
        "╭─ ❀ 𝓢𝔂𝓼𝓽𝓮𝓶 ─╮\n\n"
        f'✅ "{admin_tag}" принял ваш запрос.\n\n'
        "💭 Администратор уже подключается к чату и совсем скоро начнёт диалог с вами.\n\n"
        "📨 Вся указанная вами информация уже передана ему, поэтому он немного знаком с вашей ситуацией.\n\n"
        "⏳ Пожалуйста, оставайтесь в чате.\n\n"
        "💌 Если ожидание немного затянется — просто отправьте любое сообщение. Администратор обязательно ответит.\n\n"
        "╰────────────────╯"
    )
    bio_button = InlineKeyboardMarkup(
        [[InlineKeyboardButton("🔭Биография админа", callback_data=f"show_admin_bio_{request_user_id}")]]
    )
    try:
        sent_user_intro = await context.bot.send_message(chat_id=requester_chat_id, text=user_text_1, reply_markup=bio_button)
        try:
            await context.bot.pin_chat_message(
                chat_id=requester_chat_id,
                message_id=sent_user_intro.message_id,
                disable_notification=True,
            )
        except Exception:
            pass
    except Exception:
        pass

    if topic_id is not None:
        try:
            await context.bot.edit_forum_topic(
                chat_id=chat_id,
                message_thread_id=topic_id,
                name=session_topic_name,
            )
        except Exception:
            pass

    # enable user-side cancel button and active chat forwarding state
    user_session = context.application.bot_data.setdefault("active_chats", {})
    user_session[str(request_user_id)] = {
        "user_id": request_user_id,
        "chat_id": chat_id,
        "topic_id": topic_id,
        "admin_id": str(update.effective_user.id),
        "admin_username": admin_username,
        "admin_tag": admin_tag,
        "admin_biography": str(admin_profile.get("biography_admin") or ""),
        "topic_base_name": session_topic_name,
        "paused": False,
        "pause_topic_message_id": None,
        "suspicious_bypass_user_to_admin_until": 0,
        "suspicious_bypass_admin_to_user_until": 0,
        "msg_topic_user": 0,
        "msg_topic_admin": 0,
        "detect_topic": 0,
        "rp_topic": 0,
        "last_suspicious_message": "",
        "last_rp_action": "",
        "session_started_at": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
        "active": True,
    }
    if topic_id:
        topic_map = context.application.bot_data.setdefault("topic_user_map", {})
        # store both int and str keys to be robust when Telegram returns int or str
        try:
            topic_map[int(topic_id)] = str(request_user_id)
        except Exception:
            pass
        topic_map[str(topic_id)] = str(request_user_id)

    try:
        kb = ReplyKeyboardMarkup(
            [
                [KeyboardButton("🤧Отказаться от админа"), KeyboardButton("💤Приостановить общение")],
                [KeyboardButton("🕘Проверить онлайн админа")],
            ],
            resize_keyboard=True,
            one_time_keyboard=False,
        )
        await context.bot.send_message(chat_id=requester_chat_id, text="Если вам администратор не понравился, то вы можете отменить его кнопкой ниже.", reply_markup=kb)
    except Exception:
        pass

    _save_runtime_snapshot(context)



async def show_user_profile_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _can_use_moderation_commands(context, str(update.effective_user.id)):
        await update.callback_query.answer("Действие доступно только администраторам 2 категории и выше.", show_alert=True)
        return

    await update.callback_query.answer()
    data = update.callback_query.data or ""
    parts = data.split("_")
    if len(parts) < 4:
        return

    request_user_id = parts[3]
    active = (context.application.bot_data.get("active_chats", {}) or {}).get(str(request_user_id))
    if not active or not active.get("active"):
        await update.callback_query.answer("Тема больше неактуальна", show_alert=True)
        return

    topic_id = getattr(update.callback_query.message, "message_thread_id", None)
    active_topic_id = active.get("topic_id")
    if topic_id is not None and active_topic_id is not None and str(topic_id) != str(active_topic_id):
        await update.callback_query.answer("Тема больше неактуальна", show_alert=True)
        return

    profile = _ensure_profile(context, str(request_user_id), active.get("topic_base_name") or f"id{request_user_id}")
    _record_info_topic_action(context, request_user_id, update.effective_user.id, "VIEW_PROFILE", reason="Профиль пользователя открыт")
    await update.callback_query.message.reply_text(_build_user_stats_text(context, str(request_user_id), profile))



async def warn_user_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _can_use_moderation_commands(context, str(update.effective_user.id)):
        await update.callback_query.answer("Действие доступно только администраторам 2 категории и выше.", show_alert=True)
        return
    await update.callback_query.answer()
    data = update.callback_query.data
    parts = data.split("_")
    if len(parts) < 3:
        return
    request_user_id = parts[2]

    # Do not hide buttons for active topics; only block stale topics.
    topic_id = getattr(update.callback_query.message, "message_thread_id", None)
    active = context.application.bot_data.get("active_chats", {}).get(str(request_user_id))
    if not active or not active.get("active"):
        await update.callback_query.answer("Тема больше неактуальна", show_alert=True)
        return
    active_topic_id = active.get("topic_id")
    if topic_id is not None and active_topic_id is not None and str(topic_id) != str(active_topic_id):
        await update.callback_query.answer("Тема больше неактуальна", show_alert=True)
        return

    app_requests = context.application.bot_data.setdefault("admin_requests", {})
    req_info = app_requests.get(str(request_user_id))
    username = req_info.get("username") if req_info else f"id{request_user_id}"
    _record_info_topic_action(context, request_user_id, update.effective_user.id, "WARN_USER", reason="Админ открыл окно предупреждения")

    thread_id = getattr(update.callback_query.message, "message_thread_id", None)
    prompt_text = f'Вы собираетесь предупредить своего пользователя "{username}"?'
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🔁 Отмена", callback_data=f"cancel_warn_{request_user_id}"),
                InlineKeyboardButton("Выдать предупреждение", callback_data=f"confirm_warn_{request_user_id}"),
            ]
        ]
    )
    try:
        if thread_id is not None:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=prompt_text,
                message_thread_id=thread_id,
                reply_markup=keyboard,
            )
        else:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=prompt_text,
                reply_markup=keyboard,
            )
    except Exception as e:
        logging.exception("warn_user_callback failed: %s", e)



async def cancel_warn_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer("Отмена")
    try:
        await update.callback_query.message.delete()
    except Exception as e:
        logging.exception("cancel_warn_callback failed: %s", e)
    request_user_id = (update.callback_query.data or "").split("_")[-1]
    if request_user_id:
        _record_info_topic_action(context, request_user_id, update.effective_user.id, "CANCEL_ACTION", reason="Отмена выдачи предупреждения")



async def confirm_warn_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    data = update.callback_query.data
    parts = data.split("_")
    if len(parts) < 3:
        return
    request_user_id = parts[2]

    app_requests = context.application.bot_data.setdefault("admin_requests", {})
    req_info = app_requests.get(str(request_user_id))
    username = req_info.get("username") if req_info else f"id{request_user_id}"

    try:
        await update.callback_query.message.delete()
    except Exception as e:
        logging.exception("confirm_warn_callback delete failed: %s", e)

    thread_id = getattr(update.callback_query.message, "message_thread_id", None)
    prompt_text = (
        "📌Укажите причину выдачи предупреждения пользователю (пунктом)\n"
        f"Правила можно посмотреть здесь {_topic_url(RULES_CHAT_ID, RULES_THREAD_ID)}"
    )
    try:
        if thread_id is not None:
            prompt_msg = await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=prompt_text,
                message_thread_id=thread_id,
            )
        else:
            prompt_msg = await context.bot.send_message(chat_id=update.effective_chat.id, text=prompt_text)
    except Exception as e:
        logging.exception("confirm_warn_callback send prompt failed: %s", e)
        return

    pending_warns = context.application.bot_data.setdefault("pending_warns", {})
    key = f"{update.effective_chat.id}:{thread_id if thread_id is not None else 'none'}"
    pending_warns[key] = {
        "request_user_id": str(request_user_id),
        "username": username,
        "prompt_message_id": prompt_msg.message_id,
        "chat_id": update.effective_chat.id,
        "thread_id": thread_id,
    }



async def handle_warn_reason_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_user or update.effective_user.is_bot:
        return

    chat_id = update.effective_chat.id
    thread_id = getattr(update.message, "message_thread_id", None)
    pending_warns = context.application.bot_data.setdefault("pending_warns", {})
    key = f"{chat_id}:{thread_id if thread_id is not None else 'none'}"
    pending = pending_warns.get(key)
    if not pending:
        return

    reason = getattr(update.message, "text", None) or getattr(update.message, "caption", None) or "(без причины)"
    request_user_id = pending.get("request_user_id")
    username = pending.get("username") or f"id{request_user_id}"

    try:
        await context.bot.delete_message(chat_id=chat_id, message_id=pending.get("prompt_message_id"))
    except Exception as e:
        logging.exception("handle_warn_reason_message delete prompt failed: %s", e)

    profile = _ensure_profile(context, str(request_user_id), f"id{request_user_id}")

    current_warn = int(profile.get("warn", 0) or 0)
    if current_warn >= 3:
        if current_warn > 3:
            profile["warn"] = 3
        try:
            if thread_id is not None:
                await context.bot.send_message(
                    chat_id=chat_id,
                    message_thread_id=thread_id,
                    text="❌У этого пользователя больше 3 предупреждений, больше выдать нельзя.",
                )
            else:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text="❌У этого пользователя больше 3 предупреждений, больше выдать нельзя.",
                )
        except Exception:
            pass
        pending_warns.pop(key, None)
        raise ApplicationHandlerStop

    profile["warn"] += 1
    profile["reason"] = reason
    _record_info_topic_action(context, request_user_id, update.effective_user.id, "WARN_USER", reason=reason)
    await enforce_autoban_if_needed(context, str(request_user_id), username)

    success_text = (
        f'✅Вы успешно выдали предупреждение пользователю "{username}" с причиной "{reason}". '
        f'Теперь у него {profile["warn"]} предупреждений'
    )
    try:
        if thread_id is not None:
            await context.bot.send_message(chat_id=chat_id, text=success_text, message_thread_id=thread_id)
        else:
            await context.bot.send_message(chat_id=chat_id, text=success_text)
    except Exception as e:
        logging.exception("handle_warn_reason_message success message failed: %s", e)

    user_text = (
        "╭─ 🚨 𝓝𝓸𝓽𝓲𝓬𝓮 ─╮\n\n"
        "❗️Вы получили предупреждение от своего администратора.\n\n"
        f"✦ Причина: {reason}\n"
        f"✦ Всего предупреждений: {profile['warn']}\n\n"
        "╰────────────────╯"
    )
    try:
        await context.bot.send_message(chat_id=int(request_user_id), text=user_text)
    except Exception as e:
        logging.exception("handle_warn_reason_message notify user failed: %s", e)

    pending_warns.pop(key, None)
    _save_runtime_snapshot(context)
    raise ApplicationHandlerStop


# ==== SECTION: Decline-request review flow ====
# When staff decline an admin-candidate application, this flow (through
# approve/reject_decline_callback) collects a reason and notifies the
# applicant. unknown_chat_guard, dump_maps_handler (owner-only debug dump),
# and the rules-message admin commands (add/del_rule) are grouped in below
# as the remaining misc/debug commands in this module.
async def admin_decline_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _can_use_moderation_commands(context, str(update.effective_user.id)):
        await update.callback_query.answer("Действие доступно только администраторам 2 категории и выше.", show_alert=True)
        return
    await update.callback_query.answer()
    data = update.callback_query.data  # e.g. "decline_user_12345"
    parts = data.split("_")
    if len(parts) < 3:
        return
    request_user_id = parts[2]

    # Active-dialog decline only.
    topic_id = getattr(update.callback_query.message, "message_thread_id", None)
    active = context.application.bot_data.get("active_chats", {}).get(str(request_user_id))
    if not active or not active.get("active"):
        await update.callback_query.answer("Тема больше неактуальна", show_alert=True)
        return
    active_topic_id = active.get("topic_id")
    if topic_id is not None and active_topic_id is not None and str(topic_id) != str(active_topic_id):
        await update.callback_query.answer("Тема больше неактуальна", show_alert=True)
        return

    chat_id = update.effective_chat.id
    thread_id = getattr(update.callback_query.message, "message_thread_id", None)
    if thread_id is None:
        # Some callback surfaces may lose forum thread id; fall back to active session topic.
        thread_id = active_topic_id
    app_requests = context.application.bot_data.setdefault("admin_requests", {})
    req_info = app_requests.get(str(request_user_id)) or {}
    username = req_info.get("username") or f"id{request_user_id}"
    admin_username = f"@{update.effective_user.username}" if update.effective_user and update.effective_user.username else f"id{update.effective_user.id}"
    _record_info_topic_action(context, request_user_id, update.effective_user.id, "DECLINE_USER", reason="Админ открыл окно отказа от пользователя")

    try:
        if thread_id:
            prompt = await context.bot.send_message(
                chat_id=chat_id,
                message_thread_id=thread_id,
                text="💬Пожалуйста, укажите причину почему вы собираетесь отказаться от пользователя",
            )
        else:
            prompt = await context.bot.send_message(
                chat_id=chat_id,
                text="💬Пожалуйста, укажите причину почему вы собираетесь отказаться от пользователя",
            )
    except Exception as e:
        logging.exception("admin_decline_callback failed to send prompt: %s", e)
        return

    pending_declines = context.application.bot_data.setdefault("pending_decline_input", {})
    key = f"{chat_id}:{thread_id if thread_id is not None else 'none'}"
    pending_declines[key] = {
        "request_user_id": str(request_user_id),
        "username": username,
        "admin_username": admin_username,
        "admin_id": update.effective_user.id,
        "chat_id": chat_id,
        "thread_id": thread_id,
        "prompt_message_id": prompt.message_id,
        "mode": "active_chat",
    }
    pending_by_admin = context.application.bot_data.setdefault("pending_decline_by_admin", {})
    pending_by_admin[str(update.effective_user.id)] = key
    _save_runtime_snapshot(context)



async def admin_decline_request_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _can_use_moderation_commands(context, str(update.effective_user.id)):
        await update.callback_query.answer("Действие доступно только администраторам 2 категории и выше.", show_alert=True)
        return
    await update.callback_query.answer()
    data = update.callback_query.data  # e.g. "decline_request_12345"
    parts = data.split("_")
    if len(parts) < 3:
        return
    request_user_id = parts[2]

    topic_id = getattr(update.callback_query.message, "message_thread_id", None)
    app_requests = context.application.bot_data.setdefault("admin_requests", {})
    req_check = app_requests.get(str(request_user_id))
    if not req_check:
        await update.callback_query.answer("Тема больше неактуальна", show_alert=True)
        return
    req_topic_id = req_check.get("topic_id")
    if topic_id is not None and req_topic_id is not None and str(topic_id) != str(req_topic_id):
        await update.callback_query.answer("Тема больше неактуальна", show_alert=True)
        return

    chat_id = update.effective_chat.id
    thread_id = getattr(update.callback_query.message, "message_thread_id", None)
    if thread_id is None:
        # Keep pending key consistent with the actual request topic when callback has no thread id.
        thread_id = req_topic_id
    req_info = req_check or {}
    username = req_info.get("username") or f"id{request_user_id}"
    admin_username = f"@{update.effective_user.username}" if update.effective_user and update.effective_user.username else f"id{update.effective_user.id}"
    _record_info_topic_action(context, request_user_id, update.effective_user.id, "DECLINE_USER", reason="Админ открыл окно отказа запроса")

    try:
        cancel_button = InlineKeyboardMarkup(
            [[InlineKeyboardButton("отмена", callback_data=f"cancel_decline_request_{request_user_id}")]]
        )
        if thread_id is not None:
            prompt = await context.bot.send_message(
                chat_id=chat_id,
                message_thread_id=thread_id,
                text="ℹ️Введите причину чтобы отказать запрос в поиске админа",
                reply_markup=cancel_button,
            )
        else:
            prompt = await context.bot.send_message(
                chat_id=chat_id,
                text="ℹ️Введите причину чтобы отказать запрос в поиске админа",
                reply_markup=cancel_button,
            )
    except Exception as e:
        logging.exception("admin_decline_request_callback failed to send prompt: %s", e)
        return

    pending_declines = context.application.bot_data.setdefault("pending_decline_input", {})
    key = f"{chat_id}:{thread_id if thread_id is not None else 'none'}"
    pending_declines[key] = {
        "request_user_id": str(request_user_id),
        "username": username,
        "admin_username": admin_username,
        "admin_id": update.effective_user.id,
        "chat_id": chat_id,
        "thread_id": thread_id,
        "prompt_message_id": prompt.message_id,
        "topic_id": req_check.get("topic_id"),
        "mode": "search_request",
    }
    pending_by_admin = context.application.bot_data.setdefault("pending_decline_by_admin", {})
    pending_by_admin[str(update.effective_user.id)] = key



async def cancel_decline_request_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer("Отмена")
    data = update.callback_query.data
    parts = data.split("_")
    if len(parts) < 4:
        return
    request_user_id = parts[3]

    pending_declines = context.application.bot_data.setdefault("pending_decline_input", {})
    pending_by_admin = context.application.bot_data.setdefault("pending_decline_by_admin", {})
    for key, pending in list(pending_declines.items()):
        if pending.get("mode") == "search_request" and pending.get("request_user_id") == str(request_user_id):
            try:
                await update.callback_query.message.delete()
            except Exception:
                pass
            _record_info_topic_action(context, request_user_id, update.effective_user.id, "CANCEL_ACTION", reason="Отмена отказа от запроса")
            pending_declines.pop(key, None)
            admin_id = pending.get("admin_id")
            if admin_id is not None:
                pending_by_admin.pop(str(admin_id), None)
            _save_runtime_snapshot(context)
            break



async def handle_decline_input_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_user or update.effective_user.is_bot:
        return

    chat_id = update.effective_chat.id
    thread_id = getattr(update.message, "message_thread_id", None)
    key = f"{chat_id}:{thread_id if thread_id is not None else 'none'}"
    pending_declines = context.application.bot_data.setdefault("pending_decline_input", {})
    pending_by_admin = context.application.bot_data.setdefault("pending_decline_by_admin", {})
    pending = pending_declines.get(key)
    if not pending:
        admin_key = pending_by_admin.get(str(update.effective_user.id))
        if admin_key:
            admin_pending = pending_declines.get(admin_key)
            if admin_pending:
                pending = admin_pending
                key = admin_key
            else:
                pending_by_admin.pop(str(update.effective_user.id), None)
    if not pending:
        # Fallback matcher: find pending by chat/admin/thread compatibility.
        for fallback_key, fallback_pending in pending_declines.items():
            if fallback_pending.get("chat_id") != chat_id:
                continue
            if fallback_pending.get("admin_id") != update.effective_user.id:
                continue
            pending_thread = fallback_pending.get("thread_id")
            if pending_thread is None or thread_id is None or str(pending_thread) == str(thread_id):
                pending = fallback_pending
                key = fallback_key
                pending_by_admin[str(update.effective_user.id)] = fallback_key
                break
    if not pending:
        return

    if update.effective_user.id != pending.get("admin_id"):
        raise ApplicationHandlerStop

    text_reason = (update.message.text or "").strip()
    entities = update.message.entities or []
    has_link_entity = any(getattr(ent, "type", None) in {"url", "text_link"} for ent in entities)
    has_link_text = bool(re.search(r"https?://|t\.me/", text_reason, flags=re.IGNORECASE))
    invalid_media = any([
        getattr(update.message, "sticker", None),
        getattr(update.message, "animation", None),
        getattr(update.message, "video", None),
        getattr(update.message, "document", None),
        getattr(update.message, "photo", None),
        getattr(update.message, "audio", None),
        getattr(update.message, "voice", None),
    ])

    if invalid_media or not text_reason or has_link_entity or has_link_text:
        try:
            if thread_id is not None:
                await context.bot.send_message(
                    chat_id=chat_id,
                    message_thread_id=thread_id,
                    text="Такое нельзя назвать причиной. Отправьте только текст без ссылок.",
                )
            else:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text="Такое нельзя назвать причиной. Отправьте только текст без ссылок.",
                )
        except Exception:
            pass
        raise ApplicationHandlerStop

    pending["reason_otkaz_ot_username"] = text_reason
    _record_info_topic_action(context, pending.get("request_user_id") or request_user_id, update.effective_user.id, "DECLINE_USER", reason=text_reason)

    if pending.get("mode") == "search_request":
        request_user_id = pending.get("request_user_id")
        username = pending.get("username") or f"id{request_user_id}"
        topic_id = pending.get("topic_id")

        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=update.message.message_id)
        except Exception:
            pass
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=pending.get("prompt_message_id"))
        except Exception:
            pass

        app_reqs = context.application.bot_data.setdefault("admin_requests", {})
        app_reqs.pop(str(request_user_id), None)
        try:
            context.user_data.get("admin_request", {}).pop(str(request_user_id), None)
        except Exception:
            pass

        profile = _ensure_profile(context, str(request_user_id), username)
        profile["reason_otkaz_ot_username"] = text_reason
        _set_last_admin_tag_for_user(context, request_user_id, pending.get("admin_username"))

        if topic_id is not None:
            try:
                await context.bot.edit_forum_topic(
                    chat_id=chat_id,
                    message_thread_id=topic_id,
                    name=f"{username} (Закрыто системой)",
                )
            except Exception:
                pass
            try:
                topic_map = context.application.bot_data.setdefault("topic_user_map", {})
                topic_map.pop(topic_id, None)
                topic_map.pop(str(topic_id), None)
            except Exception:
                pass
            try:
                await context.bot.send_message(
                    chat_id=chat_id,
                    message_thread_id=topic_id,
                    text="✅Пользователь успешно получил уведомление о отказе в поиске админа",
                )
            except Exception:
                pass
        else:
            try:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text="✅Пользователь успешно получил уведомление о отказе в поиске админа",
                )
            except Exception:
                pass

        try:
            await context.bot.send_message(
                chat_id=LOG_CHAT_ID,
                text=f'⭕️Администратор "{pending.get("admin_username")}" закрыл запрос о поиске админа пользователю "{username}"',
            )
        except Exception:
            pass

        try:
            await context.bot.send_message(
                chat_id=int(request_user_id),
                text=(
                    "🌸 **Запрос был отказан**\n\n"
                    "К сожалению, сейчас мы не можем найти для Вас администратора.\n\n"
                    f'Это не связано с Вами лично — просто в данный момент ваш запрос был отклонен по причине "{text_reason}"\n\n'
                    "Пожалуйста, попробуйте снова через некоторое время. Возможно, через час или 2.\n\n"
                    "Берегите себя. Мы помним о Вас! 💛"
                ),
                parse_mode=ParseMode.MARKDOWN,
            )
        except Exception as e:
            logging.exception("failed to notify user about declined search request: %s", e)

        try:
            kb = _build_main_menu_keyboard(context, int(request_user_id), profile.get("username"))
            await context.bot.send_message(chat_id=int(request_user_id), text="Выберите действие в меню ниже:", reply_markup=kb)
        except Exception:
            pass

        pending_declines.pop(key, None)
        pending_by_admin.pop(str(update.effective_user.id), None)
        _save_runtime_snapshot(context)
        raise ApplicationHandlerStop

    try:
        await context.bot.delete_message(chat_id=chat_id, message_id=update.message.message_id)
    except Exception:
        pass
    try:
        await context.bot.delete_message(chat_id=chat_id, message_id=pending.get("prompt_message_id"))
    except Exception:
        pass

    try:
        if thread_id is not None:
            await context.bot.send_message(
                chat_id=chat_id,
                message_thread_id=thread_id,
                text="✅Ваш запрос на отказ от пользователя был отправлен старшей администрации, ожидайте.",
            )
        else:
            await context.bot.send_message(
                chat_id=chat_id,
                text="✅Ваш запрос на отказ от пользователя был отправлен старшей администрации, ожидайте.",
            )
    except Exception:
        pass

    seq = context.application.bot_data.get("decline_review_seq", 0) + 1
    context.application.bot_data["decline_review_seq"] = seq
    review_id = str(seq)

    review_text = (
        f'⚠️Запрос на отказ пользователя от "{pending.get("admin_username")}"\n\n'
        f'Пришло уведомление, что админ "{pending.get("admin_username")}" хочет отказаться от своего пользователя "{pending.get("username")}" по причине "{pending.get("reason_otkaz_ot_username")}".\n\n'
        "Выберите кнопку ниже⬇️"
    )
    review_kb = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🟢Одобрить", callback_data=f"approve_decline_{review_id}"),
                InlineKeyboardButton("🔴Отказать", callback_data=f"reject_decline_{review_id}"),
            ]
        ]
    )

    try:
        sent = await context.bot.send_message(chat_id=LOG_CHAT_ID, text=review_text, reply_markup=review_kb)
    except Exception as e:
        logging.exception("failed to send decline review request: %s", e)
        pending_declines.pop(key, None)
        raise ApplicationHandlerStop

    decline_reviews = context.application.bot_data.setdefault("pending_decline_reviews", {})
    decline_reviews[review_id] = {
        "review_message_id": sent.message_id,
        "review_text": review_text,
        "request_user_id": pending.get("request_user_id"),
        "username": pending.get("username"),
        "admin_username": pending.get("admin_username"),
        "reason_otkaz_ot_username": pending.get("reason_otkaz_ot_username"),
        "chat_id": pending.get("chat_id"),
        "thread_id": pending.get("thread_id"),
    }
    pending_declines.pop(key, None)
    pending_by_admin.pop(str(update.effective_user.id), None)
    _save_runtime_snapshot(context)
    raise ApplicationHandlerStop



async def approve_decline_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    data = update.callback_query.data
    parts = data.split("_")
    if len(parts) < 3:
        return
    review_id = parts[2]

    reviews = context.application.bot_data.setdefault("pending_decline_reviews", {})
    review = reviews.get(review_id)
    if not review:
        return

    approved_text = review.get("review_text", "") + "\n\nОдобрено🟩"
    try:
        await update.callback_query.message.edit_text(approved_text)
    except Exception as e:
        logging.exception("approve_decline_callback edit failed: %s", e)

    chat_id = review.get("chat_id")
    thread_id = review.get("thread_id")
    try:
        msg = f'💔Запрос на отказ от пользователя "{review.get("username")}" был успешно одобрен руководством бота, он больше не будет получать от вас сообщение.'
        if thread_id is not None:
            await context.bot.send_message(chat_id=chat_id, message_thread_id=thread_id, text=msg)
        else:
            await context.bot.send_message(chat_id=chat_id, text=msg)
    except Exception:
        pass

    user_id = str(review.get("request_user_id"))
    active = context.application.bot_data.setdefault("active_chats", {})
    session = active.pop(user_id, None)
    last_admin_tag = None
    topic_id = None
    if session:
        topic_id = session.get("topic_id")
        last_admin_tag = session.get("admin_tag") or session.get("admin_username")
    if not last_admin_tag:
        last_admin_tag = review.get("admin_username")
    _set_last_admin_tag_for_user(context, user_id, last_admin_tag)

    if topic_id is None:
        app_req = context.application.bot_data.get("admin_requests", {}).get(user_id)
        if app_req:
            topic_id = app_req.get("topic_id")

    if topic_id is not None:
        try:
            await context.bot.edit_forum_topic(
                chat_id=chat_id,
                message_thread_id=topic_id,
                name=f'{review.get("username") or f"id{user_id}"} (Закрыто админом)',
            )
        except Exception as e:
            logging.exception("approve_decline_callback topic rename failed: %s", e)

    if topic_id is not None:
        topic_map = context.application.bot_data.setdefault("topic_user_map", {})
        topic_map.pop(topic_id, None)
        topic_map.pop(str(topic_id), None)

    try:
        del context.application.bot_data.setdefault("admin_requests", {})[user_id]
    except Exception:
        pass

    user_text = (
        "🙏 **Администратор отказался от вас.**\n\n"
        "К сожалению, наш специалист не смог продолжить общение с Вами.\n\n"
        "Это случается в работе - иногда просто не совпал характер, или вы нарушали правила анонимности или другое.\n\n"
        "Не принимайте это на свой счёт. Вы — замечательный собеседник, и мы уверены, что другой администратор с радостью Вас примет.\n\n"
        "Вы все еще можете найти другого администратора на свой вкус.\n\n"
        "❤️ Мы всегда рядом."
    )
    try:
        kb = _build_main_menu_keyboard(context, int(user_id), review.get("username"))
        await context.bot.send_message(chat_id=int(user_id), text=user_text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)
        await context.bot.send_message(chat_id=int(user_id), text="Выберите действие в меню ниже:", reply_markup=kb)
    except Exception as e:
        logging.exception("approve_decline_callback notify user failed: %s", e)

    reviews.pop(review_id, None)



async def reject_decline_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    data = update.callback_query.data
    parts = data.split("_")
    if len(parts) < 3:
        return
    review_id = parts[2]

    reviews = context.application.bot_data.setdefault("pending_decline_reviews", {})
    review = reviews.get(review_id)
    if not review:
        return

    rejected_text = review.get("review_text", "") + "\n\nОтказано🟥"
    try:
        await update.callback_query.message.edit_text(rejected_text)
    except Exception as e:
        logging.exception("reject_decline_callback edit failed: %s", e)

    try:
        msg = f'❌Запрос на отказ от пользователя "{review.get("username")}" отклонен руководством.'
        if review.get("thread_id") is not None:
            await context.bot.send_message(chat_id=review.get("chat_id"), message_thread_id=review.get("thread_id"), text=msg)
        else:
            await context.bot.send_message(chat_id=review.get("chat_id"), text=msg)
    except Exception:
        pass

    reviews.pop(review_id, None)



async def unknown_chat_guard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if not chat:
        return

    if chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        return

    allowed_chat_ids = _special_admin_chat_ids()
    if chat.id in allowed_chat_ids:
        return

    try:
        await context.bot.send_message(chat_id=chat.id, text="🚫Я не могу здесь находиться, покидаю чат..")
    except Exception:
        pass

    try:
        await context.bot.leave_chat(chat_id=chat.id)
    except Exception:
        pass

    raise ApplicationHandlerStop



async def dump_maps_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Restrict access to the configured OWNER_ID
    try:
        requester = update.effective_user
        if not requester:
            return
        if requester.id != OWNER_ID:
            try:
                await update.message.reply_text("Доступ запрещён.")
            except Exception:
                pass
            return
        # Collect maps
        topic_map = context.application.bot_data.get("topic_user_map", {}) or {}
        group_map = context.application.bot_data.get("group_message_map", {}) or {}
        admin_reqs = context.application.bot_data.get("admin_requests", {}) or {}
        active = context.application.bot_data.get("active_chats", {}) or {}

        def _sample(d, n=20):
            try:
                keys = list(d.keys())[:n]
                return {k: d[k] for k in keys}
            except Exception:
                return str(list(d.keys())[:n])

        payload = {
            "topic_map_len": len(topic_map),
            "group_map_len": len(group_map),
            "admin_requests_len": len(admin_reqs),
            "active_chats_len": len(active),
            "topic_map_sample": _sample(topic_map, 20),
            "group_map_sample": _sample(group_map, 20),
            "admin_requests_sample": _sample(admin_reqs, 20),
        }

        text = "DEBUG MAPS:\n" + json.dumps(payload, ensure_ascii=False, indent=2)

        # Send as a private message to the requester if possible, otherwise log to LOG_CHAT_ID
        try:
            await context.bot.send_message(chat_id=requester.id, text=text)
            await update.message.reply_text("Да мой великий Иванушка, дамп карт успешно тебе в лс отправил")
        except Exception:
            try:
                await context.bot.send_message(chat_id=LOG_CHAT_ID, text=text)
                await update.message.reply_text(f"Не удалось отправить личное сообщение. Дамп отправлен в лог-чат {LOG_CHAT_ID}.")
            except Exception:
                await update.message.reply_text("Не удалось отправить дамп карт ни в личку, ни в лог-чат.")
    except Exception as e:
        logging.exception("dump_maps_handler failed: %s", e)



async def add_rules_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != LOG_CHAT_ID:
        return
    if not update.effective_user or not _is_topic_admin(context, str(update.effective_user.id)):
        await update.message.reply_text("Команда доступна только администраторам 4 категории и выше.")
        return

    existing = context.application.bot_data.get("global_rules_text")
    if existing:
        await update.message.reply_text("Правила уже существуют, чтобы их удалить напишите /delrules")
        return

    raw_text = update.message.text or ""
    cmd_entity = update.message.entities[0] if update.message.entities else None
    cmd_len = cmd_entity.length if cmd_entity and cmd_entity.type == "bot_command" else len("/addrules")
    rules_text = raw_text[cmd_len:]
    if rules_text[:1] in {" ", "\n", "\t"}:
        rules_text = rules_text[1:]

    if not rules_text.strip():
        await update.message.reply_text("Укажите текст правил после команды /addrules")
        return

    context.application.bot_data["global_rules_text"] = rules_text
    await update.message.reply_text("📍Правила успешно сменены")

    rules_thread_id = context.application.bot_data.get("rules_thread_id", RULES_THREAD_ID)

    try:
        sent = await context.bot.send_message(
            chat_id=RULES_CHAT_ID,
            message_thread_id=rules_thread_id,
            text=rules_text,
        )
    except BadRequest as e:
        if "Message thread not found" not in str(e):
            logging.exception("add_rules_handler publish failed: %s", e)
            return
        # Repair rules topic automatically when stored thread id is stale.
        try:
            new_topic = await context.bot.create_forum_topic(chat_id=RULES_CHAT_ID, name="Правила")
            rules_thread_id = new_topic.message_thread_id
            context.application.bot_data["rules_thread_id"] = rules_thread_id
            sent = await context.bot.send_message(
                chat_id=RULES_CHAT_ID,
                message_thread_id=rules_thread_id,
                text=rules_text,
            )
        except Exception as create_err:
            logging.exception("add_rules_handler publish failed after topic recreate: %s", create_err)
            return
    except Exception as e:
        logging.exception("add_rules_handler publish failed: %s", e)
        return

    context.application.bot_data["global_rules_message_id"] = sent.message_id
    try:
        await context.bot.pin_chat_message(
            chat_id=RULES_CHAT_ID,
            message_id=sent.message_id,
            disable_notification=True,
        )
    except Exception:
        pass



async def del_rule_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != LOG_CHAT_ID:
        return
    if not update.effective_user or not _is_topic_admin(context, str(update.effective_user.id)):
        await update.message.reply_text("Команда доступна только администраторам 4 категории и выше.")
        return

    existing_rules_text = context.application.bot_data.get("global_rules_text")
    existing_rules_message_id = context.application.bot_data.get("global_rules_message_id")
    if not existing_rules_text and not existing_rules_message_id:
        await context.bot.send_message(
            chat_id=LOG_CHAT_ID,
            text="ℹ️Правила не установлены: Используйте команду /addrules чтобы добавить новые правила.",
        )
        return

    rules_thread_id = context.application.bot_data.get("rules_thread_id", RULES_THREAD_ID)

    try:
        await context.bot.delete_forum_topic(
            chat_id=RULES_CHAT_ID,
            message_thread_id=rules_thread_id,
        )
    except Exception as e:
        logging.exception("del_rule_handler delete_forum_topic failed: %s", e)

    try:
        new_topic = await context.bot.create_forum_topic(chat_id=RULES_CHAT_ID, name="Правила")
        context.application.bot_data["rules_thread_id"] = new_topic.message_thread_id
    except Exception as e:
        logging.exception("del_rule_handler create_forum_topic failed: %s", e)

    context.application.bot_data.pop("global_rules_text", None)
    context.application.bot_data.pop("global_rules_message_id", None)
    await update.message.reply_text("✅Правила успешно удалены. Используйте команду /addrules чтобы добавить новые правила.")



async def admin_group_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = getattr(update, "message", None)
    if message is None:
        return

    logging.info(
        "MESSAGE TYPE: text=%r caption=%r photo=%s video=%s sticker=%s thread=%s msg_id=%s",
        message.text,
        message.caption,
        bool(getattr(message, "photo", None)),
        bool(getattr(message, "video", None)),
        bool(getattr(message, "sticker", None)),
        getattr(message, "message_thread_id", None),
        message.message_id,
    )

    # Track registered admins in the work and trusted admin chats. Messages in
    # the trusted chat are observed only for online status and never forwarded.
    if update.effective_chat.id not in {WORK_CHAT_ID, TRUSTED_ADMIN_CHAT_ID}:
        return

    # Ignore bots
    if not update.effective_user or update.effective_user.is_bot:
        return
    profiles = context.application.bot_data.get("profiles", {}) or {}
    existing_profile = profiles.get(str(update.effective_user.id))
    if update.effective_chat.id == TRUSTED_ADMIN_CHAT_ID:
        if not existing_profile or _effective_admin_level(existing_profile) < 1:
            return
        activity_profile = existing_profile
    else:
        activity_profile = _ensure_profile(
            context,
            str(update.effective_user.id),
            update.effective_user.username or f"id{update.effective_user.id}",
        )
    group_activity = context.application.bot_data.setdefault("admin_work_chat_activity", {})
    group_activity[str(update.effective_user.id)] = time.time()
    activity_profile["last_work_chat_message_at"] = group_activity[str(update.effective_user.id)]
    if update.effective_chat.id == TRUSTED_ADMIN_CHAT_ID:
        _save_profile_record(context, str(update.effective_user.id))
        return

    logging.info("ADMIN_GROUP_HANDLER HIT: chat=%s user=%s thread=%s text=%r", update.effective_chat.id, update.effective_user.id, getattr(message, "message_thread_id", None), getattr(message, "text", None))

    # Ignore commands from admins (messages starting with '/')
    if getattr(message, "text", "") and message.text.strip().startswith("/"):
        return

    # Only forward messages that are inside a message thread (forum topic)
    topic_id = getattr(message, "message_thread_id", None)
    logging.info("GROUP MESSAGE: text=%r thread=%s id=%s", message.text, topic_id, message.message_id)
    if topic_id is None:
        return

    profile = (context.application.bot_data.get("profiles", {}) or {}).get(str(update.effective_user.id), {})
    if _is_active_admin_candidate(context, str(update.effective_user.id)):
        try:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                message_thread_id=topic_id,
                text="⛔️Во время кандидатуры админские права недоступны. Сообщение не отправлено.",
            )
        except Exception:
            logging.exception(
                "failed to notify candidate %s in topic %s",
                update.effective_user.id,
                topic_id,
            )
        return
    if _is_admin_muted(profile):
        remaining_minutes = _mute_remaining_minutes(profile)
        try:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                message_thread_id=topic_id,
                text=f"⛔️Вы находитесь в муте еще {remaining_minutes} мин. Сообщение не отправлено.",
            )
        except Exception:
            pass
        return

    if _get_admin_mute_until(profile) and not _is_admin_muted(profile):
        _clear_admin_mute(profile)

    # Hard-stop forwarding while warning reason is being collected in this topic.
    pending_warns = context.application.bot_data.get("pending_warns", {}) or {}
    warn_key = f"{update.effective_chat.id}:{topic_id if topic_id is not None else 'none'}"
    if warn_key in pending_warns:
        try:
            await handle_warn_reason_message(update, context)
        except ApplicationHandlerStop:
            pass
        return

    pending_declines = context.application.bot_data.get("pending_decline_input", {}) or {}
    pending_by_admin = context.application.bot_data.get("pending_decline_by_admin", {}) or {}
    decline_key = f"{update.effective_chat.id}:{topic_id if topic_id is not None else 'none'}"
    has_admin_decline_pending = bool(pending_by_admin.get(str(update.effective_user.id)))
    if decline_key in pending_declines or has_admin_decline_pending:
        try:
            await handle_decline_input_message(update, context)
        except ApplicationHandlerStop:
            pass
        # Never forward admin message while decline reason flow is active.
        return

    # Find the user associated with this topic
    topic_map = context.application.bot_data.get("topic_user_map", {}) or {}
    logging.info("topic_map=%s", topic_map)
    target_user = topic_map.get(topic_id) or topic_map.get(str(topic_id))
    if not target_user:
        # no mapping -> nothing to forward
        logging.info("No topic->user mapping for topic_id=%s", topic_id)
        return

    if is_user_banned(context, str(target_user)):
        logging.info("Target user %s is banned; skip forwarding", target_user)
        return

    active_target = (context.application.bot_data.get("active_chats", {}) or {}).get(str(target_user))
    if active_target and active_target.get("paused"):
        logging.info("Target user %s session is paused; skip forwarding", target_user)
        return
    if active_target and active_target.get("management_paused"):
        logging.info("Target user %s session is management-paused; skip forwarding", target_user)
        return

    sender_id = str(update.effective_user.id)
    session_admin_id = str(active_target.get("admin_id") or "") if active_target else ""
    allowed_admin_ids = {
        str(value) for value in (active_target or {}).get("allowed_admin_ids", [])
    }
    if (
        sender_id != session_admin_id
        and sender_id not in allowed_admin_ids
        and not _has_full_access_prefix(profile)
    ):
        try:
            await message.delete()
        except Exception:
            logging.exception(
                "failed to delete message from unauthorized admin %s in topic %s",
                sender_id,
                topic_id,
            )
        username = update.effective_user.username
        sender_label = f"@{username}" if username else f"id{sender_id}"
        try:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                message_thread_id=topic_id,
                text=(
                    f"⛔️{sender_label}, это чужая тема. "
                    "У вас нет доступа отправлять сюда сообщения."
                ),
            )
        except Exception:
            logging.exception(
                "failed to notify unauthorized admin %s in topic %s",
                sender_id,
                topic_id,
            )
        return

    if not _has_admin_rights_level_1_5(profile):
        try:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                message_thread_id=topic_id,
                text="⛔️У вас нет действующих админских прав. Сообщение не отправлено пользователю.",
            )
        except Exception:
            logging.exception(
                "failed to notify non-admin sender %s in active topic %s",
                update.effective_user.id,
                topic_id,
            )
        return

    profile = (context.application.bot_data.get("profiles", {}) or {}).get(str(target_user), {})
    allowed_username = profile.get("username")
    suspicious_text = (getattr(message, "text", None) or getattr(message, "caption", None) or "").strip()
    bypass_until = float((active_target or {}).get("suspicious_bypass_admin_to_user_until", 0) or 0)
    if _has_suspicious_username(suspicious_text, allowed_username=allowed_username) and time.time() >= bypass_until:
        if active_target:
            active_target["detect_topic"] = int(active_target.get("detect_topic", 0) or 0) + 1
            active_target["last_suspicious_message"] = suspicious_text
        admin_username = update.effective_user.username or f"id{update.effective_user.id}"
        topic_link = _topic_url(update.effective_chat.id, topic_id)
        review_id = _next_suspicious_review_id(context)

        pending = context.application.bot_data.setdefault("pending_suspicious_admin_msgs", {})
        pending[review_id] = {
            "target_user": str(target_user),
            "admin_username": admin_username,
            "chat_id": update.effective_chat.id,
            "topic_id": topic_id,
            "source_chat_id": update.message.chat_id,
            "source_message_id": update.message.message_id,
            "text": suspicious_text,
        }

        try:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                message_thread_id=topic_id,
                text="⚠️Сообщение является подозрительным и отправлено на проверку старшей администрации.",
            )
        except Exception:
            pass

        review_text = (
            "💬Подозрительное сообщение\n\n"
            f'Админ "{admin_username}" попытался отправить сообщение в тему "{topic_link}" с текстом "{suspicious_text}"\n\n'
            "Выберите действие ниже⬇️"
        )
        review_kb = InlineKeyboardMarkup(
            [[
                InlineKeyboardButton("🟢Отправить сообщение", callback_data=f"susp_admin_allow_{review_id}"),
                InlineKeyboardButton("🔴Не отправлять сообщение", callback_data=f"susp_admin_deny_{review_id}"),
            ]]
        )
        try:
            await context.bot.send_message(chat_id=LOG_CHAT_ID, text=review_text, reply_markup=review_kb)
        except Exception as e:
            logging.exception("admin suspicious review send failed: %s", e)
        return

    rp_trigger = _parse_rp_trigger_text(getattr(message, "text", None) or getattr(message, "caption", None))
    if rp_trigger:
        trigger_name, template = rp_trigger
        admin_profile = _ensure_profile(context, str(update.effective_user.id), update.effective_user.username or f"id{update.effective_user.id}")
        target_profile = _ensure_profile(context, str(target_user), active_target.get("topic_base_name") or f"id{target_user}")
        admin_name = _rp_display_name_admin(admin_profile, fallback_username=update.effective_user.username, fallback_user_id=update.effective_user.id)
        user_name = _rp_display_name_user(target_profile, fallback_username=active_target.get("topic_base_name") or target_profile.get("username"), fallback_user_id=target_user)
        rendered = _render_rp_action_message(
            template,
            sender_is_admin=True,
            name_admin=admin_name,
            name_user=user_name,
            trigger_name=trigger_name,
        )
        wrapped_text = f"💞RP : {rendered}"
        try:
            topic_message = await context.bot.send_message(chat_id=update.effective_chat.id, message_thread_id=topic_id, text=wrapped_text)
            try:
                sent = await context.bot.send_message(chat_id=int(target_user), text=wrapped_text)
                _track_session_message_pair(context, int(update.effective_chat.id), int(topic_message.message_id), int(target_user), int(sent.message_id))
                _track_session_message_pair(context, int(update.message.chat_id), int(update.message.message_id), int(target_user), int(sent.message_id))
            except Exception:
                pass
        except Exception:
            try:
                sent = await context.bot.send_message(chat_id=int(target_user), text=wrapped_text)
                _track_session_message_pair(context, int(update.message.chat_id), int(update.message.message_id), int(target_user), int(sent.message_id))
            except Exception:
                pass
        _set_user_blocked_bot_state(context, str(target_user), False)
        if active_target:
            active_target["msg_topic_admin"] = int(active_target.get("msg_topic_admin", 0) or 0) + 1
            active_target["rp_topic"] = int(active_target.get("rp_topic", 0) or 0) + 1
            active_target["last_rp_action"] = getattr(message, "text", None) or getattr(message, "caption", None) or "RP"
            context.application.bot_data["total_admin_replies"] = int(context.application.bot_data.get("total_admin_replies", 0) or 0) + 1
            _record_admin_reputation_activity(context, update.effective_user.id, messages=1, rp_commands=1)
        return

    # Forward the message to the user's private chat.
    try:
        if update.message.text:
            sent = await context.bot.send_message(chat_id=int(target_user), text=update.message.text)
            _track_session_message_pair(context, int(update.message.chat_id), int(update.message.message_id), int(target_user), int(sent.message_id))
            _set_user_blocked_bot_state(context, str(target_user), False)
            if active_target:
                active_target["msg_topic_admin"] = int(active_target.get("msg_topic_admin", 0) or 0) + 1
                context.application.bot_data["total_admin_replies"] = int(context.application.bot_data.get("total_admin_replies", 0) or 0) + 1
                if _is_rp_action_text(update.message.text or update.message.caption):
                    active_target["rp_topic"] = int(active_target.get("rp_topic", 0) or 0) + 1
                    active_target["last_rp_action"] = getattr(message, "text", None) or getattr(message, "caption", None) or "RP"
                _record_admin_reputation_activity(
                    context,
                    update.effective_user.id,
                    messages=1,
                    rp_commands=int(_is_rp_action_text(update.message.text or update.message.caption)),
                )
            logging.info("Forwarded text from group topic %s msg=%s to user %s via send_message", topic_id, update.message.message_id, target_user)
            return

        res = await context.bot.copy_message(chat_id=int(target_user), from_chat_id=update.message.chat_id, message_id=update.message.message_id)
        _track_session_message_pair(context, int(update.message.chat_id), int(update.message.message_id), int(target_user), int(res.message_id))
        _set_user_blocked_bot_state(context, str(target_user), False)
        if active_target:
            active_target["msg_topic_admin"] = int(active_target.get("msg_topic_admin", 0) or 0) + 1
            context.application.bot_data["total_admin_replies"] = int(context.application.bot_data.get("total_admin_replies", 0) or 0) + 1
            if _is_rp_action_text(update.message.text or update.message.caption):
                active_target["rp_topic"] = int(active_target.get("rp_topic", 0) or 0) + 1
                active_target["last_rp_action"] = getattr(message, "text", None) or getattr(message, "caption", None) or "RP"
            _record_admin_reputation_activity(
                context,
                update.effective_user.id,
                messages=1,
                rp_commands=int(_is_rp_action_text(update.message.text or update.message.caption)),
            )
        logging.info("Forwarded non-text message from group topic %s msg=%s to user %s (copied id=%s)", topic_id, update.message.message_id, target_user, getattr(res, 'message_id', None))
        return
    except BadRequest as e:
        err = str(e).lower()
        if "can't be copied" in err or "message can't be copied" in err:
            logging.warning("Copy failed for user %s topic %s: %s; using fallback delivery", target_user, topic_id, e)
        else:
            logging.exception("Forwarding to user %s failed in copy_message: %s", target_user, e)
    except Forbidden as e:
        err = str(e).lower()
        if "bot was blocked by the user" in err:
            await notify_blocked_user_in_topic(context, update.effective_chat.id, topic_id, str(target_user))
            logging.info("User %s blocked bot; notified topic %s", target_user, topic_id)
            return
        logging.exception("Forwarding to user %s forbidden: %s", target_user, e)
    except Exception as e:
        logging.exception("Forwarding to user %s failed: %s", target_user, e)

    # Fallback: deliver by inspecting content type
    try:
        fallback_sent = await deliver_message_to_user(context.bot, update.message, int(target_user))
        if fallback_sent is not None and getattr(fallback_sent, "message_id", None):
            _track_session_message_pair(
                context,
                int(update.message.chat_id),
                int(update.message.message_id),
                int(target_user),
                int(fallback_sent.message_id),
            )
        _set_user_blocked_bot_state(context, str(target_user), False)
        if active_target:
            active_target["msg_topic_admin"] = int(active_target.get("msg_topic_admin", 0) or 0) + 1
            context.application.bot_data["total_admin_replies"] = int(context.application.bot_data.get("total_admin_replies", 0) or 0) + 1
            if _is_rp_action_text(update.message.text or update.message.caption):
                active_target["rp_topic"] = int(active_target.get("rp_topic", 0) or 0) + 1
                active_target["last_rp_action"] = getattr(message, "text", None) or getattr(message, "caption", None) or "RP"
            _record_admin_reputation_activity(
                context,
                update.effective_user.id,
                messages=1,
                rp_commands=int(_is_rp_action_text(update.message.text or update.message.caption)),
            )
        logging.info("Fallback delivered message %s to user %s", update.message.message_id, target_user)
    except Forbidden as e:
        err = str(e).lower()
        if "bot was blocked by the user" in err:
            await notify_blocked_user_in_topic(context, update.effective_chat.id, topic_id, str(target_user))
            logging.info("User %s blocked bot (fallback); notified topic %s", target_user, topic_id)
            return
        logging.exception("Fallback forbidden for user %s: %s", target_user, e)
    except Exception as e:
        logging.exception("Fallback delivery failed for user %s: %s", target_user, e)


async def warn_user_cancel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    data = update.callback_query.data
    parts = data.split("_")
    if len(parts) < 4:
        return
    review_id = parts[3]

    pending = context.application.bot_data.setdefault("pending_user_cancel_reviews", {})
    review = pending.get(review_id)
    if not review:
        return

    user_id = review.get("user_id")
    profile = _ensure_profile(context, str(user_id), review.get("username") or f"id{user_id}")
    profile["warn"] += 1
    profile["reason"] = "Нарушение правил отказа от администратора от руководства бота"
    await enforce_autoban_if_needed(context, str(user_id), review.get("username"))

    try:
        await context.bot.send_message(
            chat_id=int(user_id),
            text=f'❗️Вы получили предупреждение с причиной "Нарушение правил отказа от администратора" от руководства бота. Теперь у вас предупреждений {profile["warn"]}',
        )
    except Exception as e:
        logging.exception("warn_user_cancel_callback failed to notify user: %s", e)

    edited_text = (
        "🟥Отказ пользователя от администратора\n\n"
        f'Пришло уведомление, что пользователь нашего бота "{review.get("username")}" отказался от администратора "{review.get("admin_username")}" '
        f'по причине "{review.get("reason_otkazik")}"\n\n'
        "Пользователь получил предупреждение✅"
    )
    try:
        await update.callback_query.message.edit_text(edited_text)
    except Exception as e:
        logging.exception("warn_user_cancel_callback failed to edit review: %s", e)

    pending.pop(review_id, None)



async def suspicious_admin_allow_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    review_id = (update.callback_query.data or "").split("_")[-1]
    pending = context.application.bot_data.setdefault("pending_suspicious_admin_msgs", {})
    review = pending.get(review_id)
    if not review:
        return

    target_user = int(review.get("target_user"))
    sent = False
    try:
        await context.bot.copy_message(
            chat_id=target_user,
            from_chat_id=review.get("source_chat_id"),
            message_id=review.get("source_message_id"),
        )
        sent = True
    except Exception:
        try:
            await context.bot.send_message(chat_id=target_user, text=review.get("text") or "")
            sent = True
        except Exception:
            sent = False

    if sent:
        active = context.application.bot_data.setdefault("active_chats", {})
        session = active.get(str(target_user))
        if session:
            session["suspicious_bypass_admin_to_user_until"] = time.time() + 15 * 60
            context.application.bot_data["total_admin_replies"] = int(context.application.bot_data.get("total_admin_replies", 0) or 0) + 1
        try:
            await update.callback_query.message.edit_text((update.callback_query.message.text or "") + "\n\n✅Разрешено")
        except Exception:
            pass

    pending.pop(review_id, None)



async def suspicious_admin_deny_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    review_id = (update.callback_query.data or "").split("_")[-1]
    pending = context.application.bot_data.setdefault("pending_suspicious_admin_msgs", {})
    review = pending.get(review_id)
    if not review:
        return

    try:
        await context.bot.send_message(
            chat_id=review.get("chat_id"),
            message_thread_id=review.get("topic_id"),
            text="⛔Проверка не пройдена, сообщение не отправится пользователю в ЛС.",
        )
    except Exception:
        pass

    try:
        await update.callback_query.message.edit_text((update.callback_query.message.text or "") + "\n\n❌Не отправлено")
    except Exception:
        pass

    pending.pop(review_id, None)



async def suspicious_user_allow_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    review_id = (update.callback_query.data or "").split("_")[-1]
    pending = context.application.bot_data.setdefault("pending_suspicious_user_msgs", {})
    review = pending.get(review_id)
    if not review:
        return

    sent = False
    try:
        await context.bot.copy_message(
            chat_id=review.get("chat_id"),
            from_chat_id=review.get("source_chat_id"),
            message_id=review.get("source_message_id"),
            message_thread_id=review.get("topic_id"),
        )
        sent = True
    except Exception:
        try:
            await context.bot.send_message(
                chat_id=review.get("chat_id"),
                message_thread_id=review.get("topic_id"),
                text=review.get("text") or "",
            )
            sent = True
        except Exception:
            sent = False

    if sent:
        active = context.application.bot_data.setdefault("active_chats", {})
        session = active.get(str(review.get("user_id")))
        if session:
            session["suspicious_bypass_user_to_admin_until"] = time.time() + 15 * 60
        try:
            await context.bot.send_message(
                chat_id=int(review.get("user_id")),
                text="✅Проверка пройдена, сообщение отправлено администратору.",
            )
        except Exception:
            pass

        try:
            await update.callback_query.message.edit_text((update.callback_query.message.text or "") + "\n\n✅Разрешено")
        except Exception:
            pass

    pending.pop(review_id, None)



async def suspicious_user_deny_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    review_id = (update.callback_query.data or "").split("_")[-1]
    pending = context.application.bot_data.setdefault("pending_suspicious_user_msgs", {})
    review = pending.get(review_id)
    if not review:
        return

    try:
        await context.bot.send_message(
            chat_id=int(review.get("user_id")),
            text="⛔Сообщение является нарушением бота и не было отправлено администратору.",
        )
    except Exception:
        pass

    try:
        await update.callback_query.message.edit_text((update.callback_query.message.text or "") + "\n\n❌Не отправлено")
    except Exception:
        pass

    pending.pop(review_id, None)



async def suspicious_user_warn_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    review_id = (update.callback_query.data or "").split("_")[-1]
    pending = context.application.bot_data.setdefault("pending_suspicious_user_msgs", {})
    review = pending.get(review_id)
    if not review:
        return

    user_id = str(review.get("user_id"))
    profile = _ensure_profile(context, user_id, review.get("username") or f"id{user_id}")
    profile["warn"] = int(profile.get("warn", 0) or 0) + 1
    profile["reason"] = "Подозрительное сообщение с чужим username"
    await enforce_autoban_if_needed(context, user_id, profile.get("username"))
    _save_profile_record(context, user_id)

    try:
        await context.bot.send_message(
            chat_id=int(user_id),
            text="⛔Сообщение не было отправлено администратору. Вы получили предупреждение.",
        )
    except Exception:
        pass

    try:
        await update.callback_query.message.edit_text((update.callback_query.message.text or "") + "\n\nПользователь получил предупреждение")
    except Exception:
        pass

    pending.pop(review_id, None)


async def approve_candidate_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    review_id = (update.callback_query.data or "").split("_")[-1]
    pending = context.application.bot_data.setdefault("pending_admin_candidate_reviews", {})
    review = pending.get(review_id)
    if not review:
        return

    user_id = str(review.get("user_id"))
    profile = _ensure_profile(context, user_id, review.get("username") or f"id{user_id}")
    profile["admin_level"] = 1
    profile["admin_rank"] = "Стажер"
    profile["admin_candidate"] = False
    profile["admin_candidate_status"] = "approved"
    _save_profile_record(context, user_id)

    state_map = context.application.bot_data.setdefault("admin_candidate_state", {})
    state_map.pop(user_id, None)

    try:
        await context.bot.send_message(
            chat_id=int(user_id),
            text="✅Ваша кандидатура одобрена. Вам выданы права категории 1.",
        )
    except Exception:
        pass

    try:
        await update.callback_query.message.edit_text((update.callback_query.message.text or "") + "\n\n✅Кандидат одобрен")
    except Exception:
        pass

    pending.pop(review_id, None)



async def reject_candidate_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    review_id = (update.callback_query.data or "").split("_")[-1]
    pending = context.application.bot_data.setdefault("pending_admin_candidate_reviews", {})
    review = pending.get(review_id)
    if not review:
        return

    prompt_kb = InlineKeyboardMarkup(
        [[InlineKeyboardButton("отмена", callback_data=f"cancel_candidate_reject_{review_id}")]]
    )
    prompt = await context.bot.send_message(
        chat_id=LOG_CHAT_ID,
        text="Укажите причину отказа кандидату:",
        reply_markup=prompt_kb,
    )

    pending_reject = context.application.bot_data.setdefault("pending_candidate_rejections", {})
    pending_reject[review_id] = {
        "admin_id": update.effective_user.id,
        "candidate_user_id": str(review.get("user_id")),
        "prompt_message_id": prompt.message_id,
        "review_message_id": update.callback_query.message.message_id,
    }
    _save_runtime_snapshot(context)



async def cancel_candidate_reject_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer("Отмена")
    review_id = (update.callback_query.data or "").split("_")[-1]
    pending_reject = context.application.bot_data.setdefault("pending_candidate_rejections", {})
    pending_reject.pop(review_id, None)
    try:
        await update.callback_query.message.delete()
    except Exception:
        pass
    _save_runtime_snapshot(context)



async def candidate_reject_reason_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != LOG_CHAT_ID or not update.message or not update.effective_user:
        return

    pending_reject = context.application.bot_data.setdefault("pending_candidate_rejections", {})
    if not pending_reject:
        return

    review_id = None
    review = None
    for rid, item in pending_reject.items():
        if item.get("admin_id") == update.effective_user.id:
            review_id = rid
            review = item
            break
    if not review:
        return

    reason = (update.message.text or "").strip()
    if not reason:
        await update.message.reply_text("Причина не может быть пустой.")
        raise ApplicationHandlerStop

    candidate_user_id = str(review.get("candidate_user_id"))
    profile = _ensure_profile(context, candidate_user_id, f"id{candidate_user_id}")
    profile["tag_admin"] = ""
    profile["biography_admin"] = ""
    profile["tip_admin"] = ""
    profile["admin_gender"] = ""
    profile["admin_candidate"] = True
    profile["admin_candidate_status"] = "onboarding"
    _save_profile_record(context, candidate_user_id)

    state_map = context.application.bot_data.setdefault("admin_candidate_state", {})
    state_map[candidate_user_id] = {
        "stage": "await_reject_restart",
        "initiated_by": update.effective_user.id,
        "initiated_by_username": update.effective_user.username or f"id{update.effective_user.id}",
    }
    _save_runtime_snapshot(context)

    try:
        await context.bot.send_message(
            chat_id=int(candidate_user_id),
            text=f'❌Заявка не была принята. Причина: "{reason}"\n\nНачните заново с этапа 1.',
        )
        await _send_candidate_stage_1(context, int(candidate_user_id))
    except Exception:
        pass

    try:
        await update.message.reply_text("✅Сообщение отправлено кандидату в ЛС.")
    except Exception:
        pass

    pending_reviews = context.application.bot_data.setdefault("pending_admin_candidate_reviews", {})
    pending_reviews.pop(review_id, None)
    pending_reject.pop(review_id, None)

    try:
        if review.get("prompt_message_id"):
            await context.bot.delete_message(chat_id=LOG_CHAT_ID, message_id=review.get("prompt_message_id"))
    except Exception:
        pass

    _save_runtime_snapshot(context)
    raise ApplicationHandlerStop
