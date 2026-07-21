"""Admin-facing command/callback/message handlers (staff group + log chat)."""
import asyncio
import json
import logging
import re
import shlex
import time
from datetime import datetime, timedelta

from telegram import (
    ChatPermissions,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    Update,
)
from telegram.constants import ChatType, ParseMode
from telegram.error import BadRequest, Forbidden
from telegram.ext import ApplicationHandlerStop, ContextTypes

from app.config import (
    ADMIN_LEVEL_TITLES,
    COOPERATION_CHAT_ID,
    LOG_CHAT_ID,
    OFFICIAL_CHANNEL_ID,
    OWNER_ID,
    RULES_CHAT_ID,
    RULES_THREAD_ID,
    WORK_CHAT_ID,
)
from app.database.requests import _save_profile_record, _save_runtime_snapshot
from app.handlers.candidates import _send_candidate_stage_1
from app.keyboards.inline import (
    _build_active_dialog_admin_keyboard,
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
from app.services.messaging import deliver_message_to_user, notify_blocked_user_in_topic
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
    _build_admin_stats_text,
    _build_user_stats_text,
    _candidate_tip_value,
    _can_use_moderation_commands,
    _ensure_profile,
    _has_admin_ban_immunity,
    _has_admin_rights_level_1_5,
    _is_topic_admin,
    _resolve_ban_target,
    _resolve_warn_target,
    _set_last_admin_tag_for_user,
    _set_user_blocked_bot_state,
    is_user_banned,
)
from app.services.topics import (
    _get_topic_state,
    _has_suspicious_username,
    _is_admin_command_text,
    _is_member_of_chat,
    _is_rp_action_text,
    _is_special_admin_chat,
    _next_suspicious_review_id,
    _parse_topic_url,
    _set_topic_state,
    _special_admin_chat_ids,
    _topic_url,
)

# ==== SECTION: Forum-topic management ====
# /topic del|close|open — lets a topic admin delete, close, or reopen a
# forum topic in one of the allowed chats (work/log/cooperation).
async def topic_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_user:
        return

    allowed_topic_chats = {WORK_CHAT_ID, LOG_CHAT_ID, COOPERATION_CHAT_ID}
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
        issuer_level = int(issuer_profile.get("admin_level", 0) or 0)
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
        current_admin_level = int(profile.get("admin_level", 0) or 0)
        if current_admin_level <= 0:
            await update.message.reply_text(
                f'Снятие невозможно: у пользователя "{target_identifier}" нет активных админ-прав.'
            )
            return

        profile["admin_level"] = 0
        profile["admin_rank"] = ""
        profile["admin_candidate"] = False
        profile["admin_candidate_status"] = "none"
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
# cooperation_admin_command_guard/sendpiar/anpiar manage the
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
        "/anpiar": anpiar_command_handler,
        "/fullstats": fullstats_command_handler,
        "/sendpiar": sendpiar_command_handler,
        "/pm": pm_command_handler,
        "/prava": prava_command_handler,
        "/ban": ban_command_handler,
        "/unban": unban_command_handler,
        "/warn": warn_command_handler,
        "/unwarn": unwarn_command_handler,
        "/stats": stats_command_handler,
        "/astats": astats_command_handler,
        "/info_topic": info_topic_command_handler,
        "/dump_maps": dump_maps_handler,
    }

    handler = command_handlers.get(command)
    if handler is None:
        return

    await handler(update, context)
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



async def sendpiar_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_chat:
        return

    source_text = update.message.text or update.message.caption or ""
    source_text_stripped = source_text.strip()
    if not source_text_stripped.startswith("/sendpiar") and not source_text_stripped.startswith("/sendpiar@"):
        return

    if int(update.effective_chat.id) != COOPERATION_CHAT_ID:
        await update.message.reply_text("Команда /sendpiar доступна только в чате сотрудничества.")
        return

    bot_data = context.application.bot_data
    now_ts = time.time()
    cooldown_until = float(bot_data.get("sendpiar_cooldown_until", 0) or 0)
    cooldown_was_active = bool(bot_data.get("sendpiar_cooldown_was_active", False))

    if cooldown_until > now_ts:
        remaining_seconds = int(cooldown_until - now_ts)
        remaining_minutes = max(1, (remaining_seconds + 59) // 60)
        await update.message.reply_text(
            f"⏳Команда /sendpiar на кулдауне. Подождите {remaining_minutes} мин."
        )
        return

    if cooldown_until and cooldown_was_active and now_ts >= cooldown_until:
        await update.message.reply_text("✅Кулдаун завершен. Команда /sendpiar снова доступна.")
        bot_data["sendpiar_cooldown_was_active"] = False

    issuer_profile = _ensure_profile(
        context,
        str(update.effective_user.id),
        update.effective_user.username or f"id{update.effective_user.id}",
    )
    issuer_prefix = str(issuer_profile.get("prefix") or "").strip()
    if issuer_prefix != "💎Сотрудничество":
        await update.message.reply_text("Нельзя выполнить команду: нужен префикс 💎Сотрудничество.")
        return

    cmd_entities = update.message.entities if update.message.text else (update.message.caption_entities or [])
    cmd_entity = cmd_entities[0] if cmd_entities else None
    cmd_len = cmd_entity.length if cmd_entity and cmd_entity.type == "bot_command" else len("/sendpiar")
    args_text = source_text[cmd_len:]

    if not str(args_text).strip():
        await update.message.reply_text('Используйте: /sendpiar "текст" (можно с фото или видео).')
        return

    quoted = re.match(r'^\s*"([\s\S]*)"\s*$', args_text)
    broadcast_text = quoted.group(1) if quoted else args_text.strip()
    if not broadcast_text:
        await update.message.reply_text("Текст рассылки не должен быть пустым.")
        return

    payload_text = broadcast_text
    if not payload_text.lower().startswith("#реклама"):
        payload_text = f"#реклама\n\n{payload_text}"

    profiles = context.application.bot_data.setdefault("profiles", {})
    recipients: list[int] = []
    immune_profiles: list[int] = []
    for user_id, profile_obj in profiles.items():
        profile_data = profile_obj or {}
        if bool(profile_data.get("ad_disable_enabled", False)):
            try:
                immune_profiles.append(int(profile_data.get("id_profile", 0) or 0))
            except Exception:
                pass
            continue
        try:
            recipients.append(int(user_id))
        except Exception:
            continue

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

    success_count = 0
    failed_count = 0
    for recipient_id in recipients:
        try:
            if photo_file_id:
                await context.bot.send_photo(chat_id=recipient_id, photo=photo_file_id, caption=payload_text)
            elif video_file_id:
                await context.bot.send_video(chat_id=recipient_id, video=video_file_id, caption=payload_text)
            else:
                await context.bot.send_message(chat_id=recipient_id, text=payload_text)
            success_count += 1
        except Exception:
            failed_count += 1

    report_text = f"✅Рассылка отправлена всем пользователям бота.\nУспешно: {success_count}\nОшибок: {failed_count}"
    if immune_profiles:
        immune_profiles = sorted([pid for pid in immune_profiles if int(pid or 0) > 0])
        immune_lines = "\n".join([f"id_profile #{pid} имеет иммунитет к рекламе" for pid in immune_profiles])
        report_text = f"{report_text}\n\n{immune_lines}"

    await update.message.reply_text(report_text)

    bot_data["sendpiar_cooldown_until"] = time.time() + (15 * 60)
    bot_data["sendpiar_cooldown_was_active"] = True



async def sendpiar_media_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    caption = str(update.message.caption or "").strip()
    if not caption.startswith("/sendpiar") and not caption.startswith("/sendpiar@"):
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
    if int(issuer_profile.get("admin_level", 0) or 0) < 3:
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
    if update.effective_chat.id != LOG_CHAT_ID or not update.message:
        return

    if not _can_use_moderation_commands(context, str(update.effective_user.id)):
        await update.message.reply_text("Команда доступна только администраторам 2 категории и выше.")
        return

    raw_text = update.message.text or ""
    cmd_entity = update.message.entities[0] if update.message.entities else None
    cmd_len = cmd_entity.length if cmd_entity and cmd_entity.type == "bot_command" else len("/warn")
    args_text = raw_text[cmd_len:].strip()
    if not args_text:
        await update.message.reply_text("Укажите id_profile и причину: /warn \"id_profile\" \"reason\"")
        return

    try:
        parts = shlex.split(args_text)
    except ValueError:
        await update.message.reply_text("Некорректный формат. Используйте: /warn \"id_profile\" \"reason\"")
        return

    if len(parts) < 2:
        await update.message.reply_text("Укажите id_profile и причину: /warn \"id_profile\" \"reason\"")
        return

    target_identifier = parts[0]
    reason = " ".join(parts[1:]).strip()
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

    current_warn = int(profile.get("warn", 0) or 0)
    profile["warn"] = current_warn + 1
    profile["reason"] = reason
    await enforce_autoban_if_needed(context, str(target_user_id), profile.get("username"))

    await update.message.reply_text(
        f'⚠️Пользователю id_profile #{profile.get("id_profile")} выдано предупреждение. Теперь у него {profile["warn"]} warn(-ов). Причина: "{reason}"'
    )

    try:
        await context.bot.send_message(
            chat_id=int(target_user_id),
            text=f'❗️Вы получили предупреждение с причиной "{reason}" от руководства бота. Теперь у вас {profile["warn"]} предупреждений',
        )
    except Exception:
        pass
    _save_profile_record(context, str(target_user_id))



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
    if int(issuer_profile.get("admin_level", 0) or 0) < 4:
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



async def astats_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    allowed_special_chats = _special_admin_chat_ids()
    if update.effective_chat.id not in allowed_special_chats or not update.message:
        return

    issuer_profile = _ensure_profile(
        context,
        str(update.effective_user.id),
        update.effective_user.username or f"id{update.effective_user.id}",
    )
    if int(issuer_profile.get("admin_level", 0) or 0) < 4:
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
    if int(issuer_profile.get("admin_level", 0) or 0) < 4:
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
    if int(issuer_profile.get("admin_level", 0) or 0) < 4:
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
    if int(issuer_profile.get("admin_level", 0) or 0) < 4:
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
    if int(issuer_profile.get("admin_level", 0) or 0) < 4:
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
    if int(issuer_profile.get("admin_level", 0) or 0) < 4:
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
    if int(issuer_profile.get("admin_level", 0) or 0) < 3:
        await update.message.reply_text("Команда доступна только администраторам 3 категории и выше.")
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
        "issuer_user_id": str(update.effective_user.id),
        "target_user_id": str(target_user_id),
        "chat_id": chat_id,
        "topic_id": topic_id,
        "topic_link": topic_link,
    }

    target_profile = _ensure_profile(context, str(target_user_id), active.get("topic_base_name") or f"id{target_user_id}")
    admin_username = str(active.get("admin_username") or "админ")
    username_pz = str(target_profile.get("username") or active.get("topic_base_name") or f"id{target_user_id}")
    detect = int(active.get("detect_topic", 0) or 0)
    msg_topic = int(active.get("msg_topic_user", 0) or 0) + int(active.get("msg_topic_admin", 0) or 0)
    rp_topic = int(active.get("rp_topic", 0) or 0)
    date_value = str(active.get("session_started_at") or "не указана")

    text = _build_info_topic_text(username_pz, admin_username, detect, msg_topic, rp_topic, date_value, topic_link)
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

    if str(update.effective_user.id) != str(panel.get("issuer_user_id")):
        await update.callback_query.answer("Кнопка доступна только автору /info_topic", show_alert=True)
        return

    issuer_profile = _ensure_profile(
        context,
        str(update.effective_user.id),
        update.effective_user.username or f"id{update.effective_user.id}",
    )
    if int(issuer_profile.get("admin_level", 0) or 0) < 3:
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

    if str(update.effective_user.id) != str(panel.get("issuer_user_id")):
        await update.callback_query.answer("Кнопка доступна только автору /info_topic", show_alert=True)
        return

    issuer_profile = _ensure_profile(
        context,
        str(update.effective_user.id),
        update.effective_user.username or f"id{update.effective_user.id}",
    )
    if int(issuer_profile.get("admin_level", 0) or 0) < 3:
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

    if str(update.effective_user.id) != str(panel.get("issuer_user_id")):
        await update.callback_query.answer("Кнопка доступна только автору /info_topic", show_alert=True)
        return

    active = context.application.bot_data.get("active_chats", {}).get(str(panel.get("target_user_id"))) or {}
    paused = bool(active.get("management_paused"))
    panel.pop("pending_action", None)
    panel_text = str(panel.get("panel_text") or "")
    try:
        await update.callback_query.message.edit_text(
            panel_text,
            reply_markup=_build_info_topic_keyboard(panel_id, paused=paused),
        )
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
        panels.pop(panel_id, None)
        return

    if action == "stop":
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

    if str(update.effective_user.id) != str(panel.get("issuer_user_id")):
        await update.callback_query.answer("Кнопка доступна только автору /info_topic", show_alert=True)
        return

    issuer_profile = _ensure_profile(context, str(update.effective_user.id), update.effective_user.username or f"id{update.effective_user.id}")
    if int(issuer_profile.get("admin_level", 0) or 0) < 3:
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

    if str(update.effective_user.id) != str(panel.get("issuer_user_id")):
        await update.callback_query.answer("Кнопка доступна только автору /info_topic", show_alert=True)
        return

    issuer_profile = _ensure_profile(context, str(update.effective_user.id), update.effective_user.username or f"id{update.effective_user.id}")
    if int(issuer_profile.get("admin_level", 0) or 0) < 3:
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

    if str(update.effective_user.id) != str(panel.get("issuer_user_id")):
        await update.callback_query.answer("Кнопка доступна только автору /info_topic", show_alert=True)
        return

    issuer_profile = _ensure_profile(context, str(update.effective_user.id), update.effective_user.username or f"id{update.effective_user.id}")
    if int(issuer_profile.get("admin_level", 0) or 0) < 3:
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
    if int(issuer_profile.get("admin_level", 0) or 0) < 4:
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
    if int(issuer_profile.get("admin_level", 0) or 0) < 4:
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
    if int(issuer_profile.get("admin_level", 0) or 0) != 5:
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
        "selected_prefix_key": None,
    }

    existing_prefix = str(profile.get("prefix") or "").strip()
    for key, label in _prefix_options():
        if existing_prefix == label:
            pending_panels[panel_id]["selected_prefix_key"] = key
            break

    await update.message.reply_text(
        f"☕️Открыта панель редактирования префикса человека {target_id_profile}",
        reply_markup=_build_setprefix_keyboard(panel_id, pending_panels[panel_id]["selected_prefix_key"]),
    )



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
    if int(issuer_profile.get("admin_level", 0) or 0) != 5:
        await update.callback_query.answer("Недостаточно прав", show_alert=True)
        return

    current = str(panel.get("selected_prefix_key") or "")
    panel["selected_prefix_key"] = None if current == selected_key else selected_key
    try:
        await update.callback_query.message.edit_reply_markup(
            reply_markup=_build_setprefix_keyboard(panel_id, panel.get("selected_prefix_key"))
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
    if int(issuer_profile.get("admin_level", 0) or 0) != 5:
        await update.callback_query.answer("Недостаточно прав", show_alert=True)
        return

    target_user_id = str(panel.get("target_user_id"))
    target_profile = _ensure_profile(context, target_user_id, f"id{target_user_id}")

    selected_key = str(panel.get("selected_prefix_key") or "")
    selected_label = None
    for key, label in _prefix_options():
        if key == selected_key:
            selected_label = label
            break

    if selected_label:
        target_profile["prefix"] = selected_label
    else:
        target_profile.pop("prefix", None)
    _save_profile_record(context, target_user_id)

    target_id_profile = int(target_profile.get("id_profile", 0) or panel.get("target_id_profile", 0) or 0)
    prefix_text = str(target_profile.get("prefix") or "не установлен")
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
    if update.effective_chat.id != LOG_CHAT_ID or not update.message:
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
        await update.message.reply_text('Укажите id_profile и причину: /ban "id_profile" "reason"')
        return

    try:
        parts = shlex.split(args_text)
    except ValueError:
        await update.message.reply_text('Некорректный формат. Используйте: /ban "id_profile" "reason"')
        return

    if len(parts) < 2:
        await update.message.reply_text('Укажите id_profile и причину: /ban "id_profile" "reason"')
        return

    target_identifier = parts[0]
    reason = " ".join(parts[1:]).strip()
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

    await _apply_ban(context, str(target_user_id), profile.get("username"), reason, "BAN")
    await update.message.reply_text(
        f'⛔Пользователь id_profile #{profile.get("id_profile")} заблокирован. Причина: "{reason}"'
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
    await update.callback_query.answer("Пользователь принят")
    data = update.callback_query.data  # e.g. "take_user_12345"
    parts = data.split("_")
    if len(parts) < 3:
        return
    request_user_id = parts[2]

    # delete the original admin request panel
    try:
        await update.callback_query.message.delete()
    except Exception:
        pass

    # find the request info
    app_requests = context.application.bot_data.setdefault("admin_requests", {})
    req_info = app_requests.get(str(request_user_id))
    if not req_info:
        return

    chat_id = req_info.get("chat_id")
    topic_id = req_info.get("topic_id")
    username = req_info.get("username")
    mood = req_info.get("mood")
    admin_username = f"@{update.effective_user.username}" if update.effective_user.username else f"id{update.effective_user.id}"
    admin_profile = _ensure_profile(
        context,
        str(update.effective_user.id),
        update.effective_user.username or f"id{update.effective_user.id}",
    )
    admin_tag = str(admin_profile.get("tag_admin") or admin_username)
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
    user_text_2 = (
        "╭─ 📌 𝓘𝓷𝓯𝓸 ─╮\n\n"
        "⚙️ Временно недоступна пересылка:\n\n"
        "• 📷 Фото\n"
        "• 🎥 Видео\n"
        "• 🎞 GIF\n"
        "• 😊 Стикеров\n"
        "• 📁 Файлов\n\n"
        "Это связано с технической ошибкой на стороне сервера.\n\n"
        "🛠 Мы уже занимаемся её устранением. Спасибо за терпение!\n\n"
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
        await context.bot.send_message(chat_id=requester_chat_id, text=user_text_2)
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
        "topic_base_name": username,
        "paused": False,
        "pause_topic_message_id": None,
        "suspicious_bypass_user_to_admin_until": 0,
        "suspicious_bypass_admin_to_user_until": 0,
        "msg_topic_user": 0,
        "msg_topic_admin": 0,
        "detect_topic": 0,
        "rp_topic": 0,
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
            [[KeyboardButton("🤧Отказаться от админа"), KeyboardButton("💤Приостановить общение")]],
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

    # Only consider messages in the designated special group and in a forum topic
    if update.effective_chat.id != WORK_CHAT_ID:
        return

    # Ignore bots
    if not update.effective_user or update.effective_user.is_bot:
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

    profile = (context.application.bot_data.get("profiles", {}) or {}).get(str(target_user), {})
    allowed_username = profile.get("username")
    suspicious_text = (getattr(message, "text", None) or getattr(message, "caption", None) or "").strip()
    bypass_until = float((active_target or {}).get("suspicious_bypass_admin_to_user_until", 0) or 0)
    if _has_suspicious_username(suspicious_text, allowed_username=allowed_username) and time.time() >= bypass_until:
        if active_target:
            active_target["detect_topic"] = int(active_target.get("detect_topic", 0) or 0) + 1
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

    # Forward the message to the user's private chat.
    try:
        if update.message.text:
            await context.bot.send_message(chat_id=int(target_user), text=update.message.text)
            _set_user_blocked_bot_state(context, str(target_user), False)
            if active_target:
                active_target["msg_topic_admin"] = int(active_target.get("msg_topic_admin", 0) or 0) + 1
                context.application.bot_data["total_admin_replies"] = int(context.application.bot_data.get("total_admin_replies", 0) or 0) + 1
                if _is_rp_action_text(update.message.text or update.message.caption):
                    active_target["rp_topic"] = int(active_target.get("rp_topic", 0) or 0) + 1
            logging.info("Forwarded text from group topic %s msg=%s to user %s via send_message", topic_id, update.message.message_id, target_user)
            return

        res = await context.bot.copy_message(chat_id=int(target_user), from_chat_id=update.message.chat_id, message_id=update.message.message_id)
        _set_user_blocked_bot_state(context, str(target_user), False)
        if active_target:
            active_target["msg_topic_admin"] = int(active_target.get("msg_topic_admin", 0) or 0) + 1
            context.application.bot_data["total_admin_replies"] = int(context.application.bot_data.get("total_admin_replies", 0) or 0) + 1
            if _is_rp_action_text(update.message.text or update.message.caption):
                active_target["rp_topic"] = int(active_target.get("rp_topic", 0) or 0) + 1
        logging.info("Forwarded non-text message from group topic %s msg=%s to user %s (copied id=%s)", topic_id, update.message.message_id, target_user, getattr(res, 'message_id', None))
        return
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
        await deliver_message_to_user(context.bot, update.message, int(target_user))
        _set_user_blocked_bot_state(context, str(target_user), False)
        if active_target:
            active_target["msg_topic_admin"] = int(active_target.get("msg_topic_admin", 0) or 0) + 1
            context.application.bot_data["total_admin_replies"] = int(context.application.bot_data.get("total_admin_replies", 0) or 0) + 1
            if _is_rp_action_text(update.message.text or update.message.caption):
                active_target["rp_topic"] = int(active_target.get("rp_topic", 0) or 0) + 1
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



