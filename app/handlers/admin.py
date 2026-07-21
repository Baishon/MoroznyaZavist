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

async def topic_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_user:
        return

    allowed_topic_chats = {WORK_CHAT_ID, LOG_CHAT_ID, COOPERATION_CHAT_ID}
    if update.effective_chat.id not in allowed_topic_chats:
        await update.message.reply_text("РљРѕРјР°РЅРґР° /topic РґРѕСЃС‚СѓРїРЅР° С‚РѕР»СЊРєРѕ РІ СЃРїРµС†-РіСЂСѓРїРїР°С….")
        return

    if not _is_topic_admin(context, str(update.effective_user.id)):
        await update.message.reply_text("РљРѕРјР°РЅРґР° РґРѕСЃС‚СѓРїРЅР° С‚РѕР»СЊРєРѕ Р°РґРјРёРЅРёСЃС‚СЂР°С‚РѕСЂР°Рј 4 РєР°С‚РµРіРѕСЂРёРё Рё РІС‹С€Рµ.")
        return

    raw_text = update.message.text or ""
    cmd_entity = update.message.entities[0] if update.message.entities else None
    cmd_len = cmd_entity.length if cmd_entity and cmd_entity.type == "bot_command" else len("/topic")
    args_text = raw_text[cmd_len:].strip()
    if not args_text:
        await update.message.reply_text('РСЃРїРѕР»СЊР·СѓР№С‚Рµ: /topic "url" del|close|open')
        return

    try:
        parts = shlex.split(args_text)
    except ValueError:
        await update.message.reply_text('РќРµРєРѕСЂСЂРµРєС‚РЅС‹Р№ С„РѕСЂРјР°С‚. РСЃРїРѕР»СЊР·СѓР№С‚Рµ: /topic "url" del|close|open')
        return

    if len(parts) < 2:
        await update.message.reply_text('РСЃРїРѕР»СЊР·СѓР№С‚Рµ: /topic "url" del|close|open')
        return

    topic_url = parts[0]
    action = parts[1].strip().lower()
    if action not in {"del", "close", "open"}:
        await update.message.reply_text('Р”РѕСЃС‚СѓРїРЅС‹Рµ РґРµР№СЃС‚РІРёСЏ: del, close, open.')
        return

    chat_id, topic_id = _parse_topic_url(topic_url)
    if chat_id is None or topic_id is None:
        await update.message.reply_text('РќРµРєРѕСЂСЂРµРєС‚РЅС‹Р№ URL С‚РµРјС‹. РќСѓР¶РµРЅ С„РѕСЂРјР°С‚ РІРёРґР° https://t.me/c/<chat_id>/<topic_id>.')
        return

    if chat_id not in allowed_topic_chats:
        await update.message.reply_text("Р­С‚Р° РєРѕРјР°РЅРґР° СѓРїСЂР°РІР»СЏРµС‚ С‚РѕР»СЊРєРѕ С‚РµРјР°РјРё РІ СЃРїРµС†-РіСЂСѓРїРїР°С….")
        return

    state = _get_topic_state(context, chat_id, topic_id)

    if action == "del":
        try:
            await context.bot.delete_forum_topic(chat_id=chat_id, message_thread_id=topic_id)
            _set_topic_state(context, chat_id, topic_id, "deleted")
            await update.message.reply_text(f"вњ…РўРµРјР° {topic_url} СѓРґР°Р»РµРЅР°.")
        except BadRequest as e:
            text = str(e).lower()
            if "not found" in text or "message thread" in text or "topic" in text:
                await update.message.reply_text("РўРµРјР° РЅРµ РЅР°Р№РґРµРЅР° РёР»Рё СѓР¶Рµ СѓРґР°Р»РµРЅР°.")
            else:
                await update.message.reply_text(f"РќРµ СѓРґР°Р»РѕСЃСЊ СѓРґР°Р»РёС‚СЊ С‚РµРјСѓ: {e}")
        except Exception as e:
            await update.message.reply_text(f"РќРµ СѓРґР°Р»РѕСЃСЊ СѓРґР°Р»РёС‚СЊ С‚РµРјСѓ: {e}")
        return

    if action == "close":
        if state == "closed":
            await update.message.reply_text("РўРµРјР° СѓР¶Рµ Р·Р°РєСЂС‹С‚Р°.")
            return
        if state == "deleted":
            await update.message.reply_text("РўРµРјР° СѓР¶Рµ СѓРґР°Р»РµРЅР°.")
            return

        close_method = getattr(context.bot, "close_forum_topic", None)
        if close_method is None:
            await update.message.reply_text("Р’ СЌС‚РѕР№ РІРµСЂСЃРёРё Р±РѕС‚Р° Р·Р°РєСЂС‹С‚РёРµ С‚РµРј РЅРµРґРѕСЃС‚СѓРїРЅРѕ.")
            return

        try:
            await close_method(chat_id=chat_id, message_thread_id=topic_id)
            _set_topic_state(context, chat_id, topic_id, "closed")
            await update.message.reply_text(f"вњ…РўРµРјР° {topic_url} Р·Р°РєСЂС‹С‚Р°.")
        except BadRequest as e:
            text = str(e).lower()
            if "already closed" in text or "closed" in text:
                _set_topic_state(context, chat_id, topic_id, "closed")
                await update.message.reply_text("РўРµРјР° СѓР¶Рµ Р·Р°РєСЂС‹С‚Р°.")
            elif "not found" in text or "message thread" in text or "topic" in text:
                await update.message.reply_text("РўРµРјР° РЅРµ РЅР°Р№РґРµРЅР°.")
            else:
                await update.message.reply_text(f"РќРµ СѓРґР°Р»РѕСЃСЊ Р·Р°РєСЂС‹С‚СЊ С‚РµРјСѓ: {e}")
        except Exception as e:
            await update.message.reply_text(f"РќРµ СѓРґР°Р»РѕСЃСЊ Р·Р°РєСЂС‹С‚СЊ С‚РµРјСѓ: {e}")
        return

    if action == "open":
        if state == "open":
            await update.message.reply_text("РўРµРјР° СѓР¶Рµ РѕС‚РєСЂС‹С‚Р°.")
            return
        if state == "deleted":
            await update.message.reply_text("РўРµРјР° СѓР¶Рµ СѓРґР°Р»РµРЅР°.")
            return

        reopen_method = getattr(context.bot, "reopen_forum_topic", None)
        if reopen_method is None:
            await update.message.reply_text("Р’ СЌС‚РѕР№ РІРµСЂСЃРёРё Р±РѕС‚Р° РѕС‚РєСЂС‹С‚РёРµ С‚РµРј РЅРµРґРѕСЃС‚СѓРїРЅРѕ.")
            return

        try:
            await reopen_method(chat_id=chat_id, message_thread_id=topic_id)
            _set_topic_state(context, chat_id, topic_id, "open")
            await update.message.reply_text(f"вњ…РўРµРјР° {topic_url} РѕС‚РєСЂС‹С‚Р°.")
        except BadRequest as e:
            text = str(e).lower()
            if "already open" in text or "not closed" in text or "open" in text:
                _set_topic_state(context, chat_id, topic_id, "open")
                await update.message.reply_text("РўРµРјР° СѓР¶Рµ РѕС‚РєСЂС‹С‚Р°.")
            elif "not found" in text or "message thread" in text or "topic" in text:
                await update.message.reply_text("РўРµРјР° РЅРµ РЅР°Р№РґРµРЅР°.")
            else:
                await update.message.reply_text(f"РќРµ СѓРґР°Р»РѕСЃСЊ РѕС‚РєСЂС‹С‚СЊ С‚РµРјСѓ: {e}")
        except Exception as e:
            await update.message.reply_text(f"РќРµ СѓРґР°Р»РѕСЃСЊ РѕС‚РєСЂС‹С‚СЊ С‚РµРјСѓ: {e}")
        return



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
            await update.message.reply_text("РљРѕРјР°РЅРґР° РґРѕСЃС‚СѓРїРЅР° С‚РѕР»СЊРєРѕ Р°РґРјРёРЅРёСЃС‚СЂР°С‚РѕСЂР°Рј 4 РєР°С‚РµРіРѕСЂРёРё Рё РІС‹С€Рµ.")
            return

    raw_text = update.message.text or ""
    cmd_entity = update.message.entities[0] if update.message.entities else None
    cmd_len = cmd_entity.length if cmd_entity and cmd_entity.type == "bot_command" else len("/makeadmin")
    args_text = raw_text[cmd_len:].strip()
    if not args_text:
        await update.message.reply_text(
            'prefix_text: СѓСЃС‚Р°РЅР°РІР»РёРІР°РµС‚СЃСЏ РїРѕСЃР»Рµ РЅР°Р·РЅР°С‡РµРЅРёСЏ (С‡РµСЂРµР· /setprefix)\n'
            'РСЃРїРѕР»СЊР·СѓР№С‚Рµ: /makeadmin "id_profile" "lvl"'
        )
        return

    try:
        parts = shlex.split(args_text)
    except ValueError:
        await update.message.reply_text(
            'prefix_text: СѓСЃС‚Р°РЅР°РІР»РёРІР°РµС‚СЃСЏ РїРѕСЃР»Рµ РЅР°Р·РЅР°С‡РµРЅРёСЏ (С‡РµСЂРµР· /setprefix)\n'
            'РќРµРєРѕСЂСЂРµРєС‚РЅС‹Р№ С„РѕСЂРјР°С‚. РСЃРїРѕР»СЊР·СѓР№С‚Рµ: /makeadmin "id_profile" "lvl"'
        )
        return

    if len(parts) < 2:
        await update.message.reply_text(
            'prefix_text: СѓСЃС‚Р°РЅР°РІР»РёРІР°РµС‚СЃСЏ РїРѕСЃР»Рµ РЅР°Р·РЅР°С‡РµРЅРёСЏ (С‡РµСЂРµР· /setprefix)\n'
            'РСЃРїРѕР»СЊР·СѓР№С‚Рµ: /makeadmin "id_profile" "lvl"'
        )
        return

    target_identifier = parts[0]
    lvl_raw = parts[1].strip()
    if not lvl_raw.isdigit():
        await update.message.reply_text(
            "prefix_text: СѓСЃС‚Р°РЅР°РІР»РёРІР°РµС‚СЃСЏ РїРѕСЃР»Рµ РЅР°Р·РЅР°С‡РµРЅРёСЏ (С‡РµСЂРµР· /setprefix)\n"
            "lvl РґРѕР»Р¶РµРЅ Р±С‹С‚СЊ С‡РёСЃР»РѕРј РѕС‚ 0 РґРѕ 5."
        )
        return
    lvl = int(lvl_raw)
    rank_title = ADMIN_LEVEL_TITLES.get(lvl)
    if lvl != 0 and not rank_title:
        await update.message.reply_text("Р”РѕСЃС‚СѓРїРЅС‹Рµ СѓСЂРѕРІРЅРё: 0, 1, 2, 3, 4, 5.")
        return
    if issuer_level == 4 and lvl not in {0, 1, 2, 3}:
        await update.message.reply_text("РђРґРјРёРЅРёСЃС‚СЂР°С‚РѕСЂ 4 РєР°С‚РµРіРѕСЂРёРё РјРѕР¶РµС‚ РІС‹РґР°РІР°С‚СЊ С‚РѕР»СЊРєРѕ СѓСЂРѕРІРЅРё 1, 2, 3 (РёР»Рё СЃРЅРёРјР°С‚СЊ РїСЂР°РІР° РІ 0).")
        return

    target_user_id, profile = _resolve_warn_target(context, target_identifier)
    if not profile:
        await update.message.reply_text(f'РџРѕР»СЊР·РѕРІР°С‚РµР»СЊ СЃ id_profile "{target_identifier}" РЅРµ РЅР°Р№РґРµРЅ.')
        return

    preview_prefix = str(profile.get("prefix") or "").strip()
    if not preview_prefix and lvl > 0:
        preview_prefix = str(_admin_default_prefix(lvl) or "").strip()
    preview_prefix_text = preview_prefix or "РЅРµ СѓСЃС‚Р°РЅРѕРІР»РµРЅ"

    target_user_id_int = int(target_user_id)
    in_work_chat = await _is_member_of_chat(context, WORK_CHAT_ID, target_user_id_int)
    if not in_work_chat:
        await update.message.reply_text("Р’С‹РґР°С‡Р° РЅРµРІРѕР·РјРѕР¶РЅР°: РґРѕР±Р°РІСЊС‚Рµ РїРѕР»СЊР·РѕРІР°С‚РµР»СЏ РІ СЂР°Р±РѕС‡РёР№ С‡Р°С‚.")
        return

    in_channel = await _is_member_of_chat(context, OFFICIAL_CHANNEL_ID, target_user_id_int)
    if not in_channel:
        await update.message.reply_text("Р’С‹РґР°С‡Р° РЅРµРІРѕР·РјРѕР¶РЅР°: РїРѕР»СЊР·РѕРІР°С‚РµР»СЊ РЅРµ РїРѕРґРїРёСЃР°РЅ РЅР° РѕС„РёС†РёР°Р»СЊРЅС‹Р№ С‚РіРє Р±РѕС‚Р°.")
        return

    if lvl > 1:
        candidate_status = str(profile.get("admin_candidate_status") or "")
        tag_admin = str(profile.get("tag_admin") or "").strip()
        biography_admin = str(profile.get("biography_admin") or "").strip()
        if candidate_status != "approved" or not tag_admin or not biography_admin:
            await update.message.reply_text(
                f"РџСЂРµС„РёРєСЃ: {preview_prefix_text}\n"
                "Р’С‹РґР°С‡Р° РЅРµРІРѕР·РјРѕР¶РЅР°: СЃРЅР°С‡Р°Р»Р° РїРѕР»СЊР·РѕРІР°С‚РµР»СЊ РґРѕР»Р¶РµРЅ РїСЂРѕР№С‚Рё РєР°РЅРґРёРґР°С‚СѓСЂСѓ 1 СѓСЂРѕРІРЅСЏ Рё Р·Р°РїРѕР»РЅРёС‚СЊ Р°РЅРєРµС‚Сѓ (С‚РµРі Рё Р±РёРѕРіСЂР°С„РёСЏ)."
            )
            return

    active = context.application.bot_data.setdefault("active_chats", {})
    active.pop(str(target_user_id), None)
    admin_username = update.effective_user.username or f"id{update.effective_user.id}"

    if lvl == 0:
        current_admin_level = int(profile.get("admin_level", 0) or 0)
        if current_admin_level <= 0:
            await update.message.reply_text(
                f'РЎРЅСЏС‚РёРµ РЅРµРІРѕР·РјРѕР¶РЅРѕ: Сѓ РїРѕР»СЊР·РѕРІР°С‚РµР»СЏ "{target_identifier}" РЅРµС‚ Р°РєС‚РёРІРЅС‹С… Р°РґРјРёРЅ-РїСЂР°РІ.'
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
            f'в‘пёЏРџРѕР»СЊР·РѕРІР°С‚РµР»СЊ "{target_identifier}" Р±С‹Р» СЂР°Р·Р¶Р°Р»РѕРІР°РЅ, Р°РґРјРёРЅ-РїСЂР°РІР° СЃРЅСЏС‚С‹.'
        )

        try:
            await context.bot.send_message(
                chat_id=target_user_id_int,
                text=(
                    "вљ пёЏР’Р°С€Рё Р°РґРјРёРЅ-РїСЂР°РІР° Р±С‹Р»Рё СЃРЅСЏС‚С‹.\n\n"
                    f'Р РµС€РµРЅРёРµ РїСЂРёРЅСЏР»: "{admin_username}".'
                ),
            )
        except Forbidden:
            await update.message.reply_text("РџРѕР»СЊР·РѕРІР°С‚РµР»СЊ Р·Р°Р±Р»РѕРєРёСЂРѕРІР°Р» Р±РѕС‚Р°. РЈРІРµРґРѕРјР»РµРЅРёРµ Рѕ СЂР°Р·Р¶Р°Р»РѕРІР°РЅРёРё РѕС‚РїСЂР°РІРёС‚СЊ РЅРµ СѓРґР°Р»РѕСЃСЊ.")
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
    prefix_text = str(profile.get("prefix") or "РЅРµ СѓСЃС‚Р°РЅРѕРІР»РµРЅ")

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
            f'в‘пёЏРџРѕР»СЊР·РѕРІР°С‚РµР»СЊ "{target_identifier}" Р±С‹Р» СѓСЃРїРµС€РЅРѕ РЅР°Р·РЅР°С‡РµРЅ РЅР° Р°РґРјРёРЅ-РїСЂР°РІР°, РёРЅСЃС‚СЂСѓРєС‚Р°Р¶ РµРјСѓ РѕС‚РїСЂР°РІР»РµРЅ РІ Р›РЎ.\n'
            f'РџСЂРµС„РёРєСЃ: {prefix_text}\n'
            'РЈСЂРѕРІРµРЅСЊ: 1'
        )

        try:
            await context.bot.send_message(chat_id=target_user_id_int, text="вљ™пёЏРљР»Р°РІРёР°С‚СѓСЂР° РѕР±РЅРѕРІР»РµРЅР°.", reply_markup=ReplyKeyboardRemove())
            await context.bot.send_message(
                chat_id=target_user_id_int,
                text=(
                    f"РџСЂРµС„РёРєСЃ: {prefix_text}\n"
                    "вќ¤пёЏвЂЌрџ”ҐРџРѕР·РґСЂР°РІР»СЏРµРј! Р’С‹ Р±С‹Р»Рё РЅР°Р·РЅР°С‡РµРЅС‹ Р°РґРјРёРЅРёСЃС‚СЂР°С‚РѕСЂРѕРј 1 СѓСЂРѕРІРЅСЏ. "
                    f'РќР°Р·РЅР°С‡РёР» РІР°СЃ "{admin_username}"\n\n'
                    "рџ“ќРџРµСЂРµРґ РЅР°С‡Р°Р»РѕР№ СЂР°Р±РѕС‚С‹ РІР°Рј РЅСѓР¶РЅРѕ Р·Р°РїРѕР»РЅРёС‚СЊ РёРЅС„РѕСЂРјР°С†РёСЋ Рѕ СЃРµР±Рµ..."
                ),
            )
            await asyncio.sleep(2)
            await _send_candidate_stage_1(context, target_user_id_int)
        except Forbidden:
            await update.message.reply_text("РџРѕР»СЊР·РѕРІР°С‚РµР»СЊ Р·Р°Р±Р»РѕРєРёСЂРѕРІР°Р» Р±РѕС‚Р°. РРЅСЃС‚СЂСѓРєС‚Р°Р¶ РІ Р›РЎ РѕС‚РїСЂР°РІРёС‚СЊ РЅРµ СѓРґР°Р»РѕСЃСЊ.")
        except Exception as e:
            logging.exception("makeadmin onboarding send failed: %s", e)
        return

    profile["admin_candidate"] = False
    profile["admin_candidate_status"] = "approved"
    state_map = context.application.bot_data.setdefault("admin_candidate_state", {})
    state_map.pop(str(target_user_id), None)

    await update.message.reply_text(
        f'в‘пёЏРџРѕР»СЊР·РѕРІР°С‚РµР»СЊ "{target_identifier}" Р±С‹Р» СѓСЃРїРµС€РЅРѕ РЅР°Р·РЅР°С‡РµРЅ.\n'
        f'РџСЂРµС„РёРєСЃ: {prefix_text}\n'
        f'РЈСЂРѕРІРµРЅСЊ: {lvl} ({rank_title}).'
    )

    try:
        await context.bot.send_message(
            chat_id=target_user_id_int,
            text=(
                f"вќ¤пёЏвЂЌрџ”ҐРџРѕР·РґСЂР°РІР»СЏРµРј! Р’С‹ Р±С‹Р»Рё РЅР°Р·РЅР°С‡РµРЅС‹: {rank_title}. "
                f'РќР°Р·РЅР°С‡РёР» РІР°СЃ "{admin_username}"'
            ),
        )
    except Forbidden:
        await update.message.reply_text("РџРѕР»СЊР·РѕРІР°С‚РµР»СЊ Р·Р°Р±Р»РѕРєРёСЂРѕРІР°Р» Р±РѕС‚Р°. РЈРІРµРґРѕРјР»РµРЅРёРµ РѕС‚РїСЂР°РІРёС‚СЊ РЅРµ СѓРґР°Р»РѕСЃСЊ.")
    except Exception as e:
        logging.exception("makeadmin notify failed: %s", e)
    _save_profile_record(context, str(target_user_id))



async def makeadmin_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _makeadmin_impl(update, context, owner_bypass=False)



async def prava_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or update.effective_user.id != OWNER_ID:
        if update.message:
            await update.message.reply_text("РљРѕРјР°РЅРґР° РґРѕСЃС‚СѓРїРЅР° С‚РѕР»СЊРєРѕ РІР»Р°РґРµР»СЊС†Сѓ Р±РѕС‚Р°.")
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
        await update.message.reply_text("вњ…РџСЂР°РІР° 5 РєР°С‚РµРіРѕСЂРёРё РІС‹РґР°РЅС‹ РїРѕР»СЊР·РѕРІР°С‚РµР»СЋ СЃ Telegram ID 7545068007.")



async def amute_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != WORK_CHAT_ID or not update.message:
        return

    if not _can_use_moderation_commands(context, str(update.effective_user.id)):
        await update.message.reply_text("РљРѕРјР°РЅРґР° РґРѕСЃС‚СѓРїРЅР° С‚РѕР»СЊРєРѕ Р°РґРјРёРЅРёСЃС‚СЂР°С‚РѕСЂР°Рј 2 РєР°С‚РµРіРѕСЂРёРё Рё РІС‹С€Рµ.")
        return

    raw_text = update.message.text or ""
    cmd_entity = update.message.entities[0] if update.message.entities else None
    cmd_len = cmd_entity.length if cmd_entity and cmd_entity.type == "bot_command" else len("/amute")
    args_text = raw_text[cmd_len:].strip()
    if not args_text:
        await update.message.reply_text('РЈРєР°Р¶РёС‚Рµ id_profile Рё minute: /amute "id_profile" "minute"')
        return

    try:
        parts = shlex.split(args_text)
    except ValueError:
        await update.message.reply_text('РќРµРєРѕСЂСЂРµРєС‚РЅС‹Р№ С„РѕСЂРјР°С‚. РСЃРїРѕР»СЊР·СѓР№С‚Рµ: /amute "id_profile" "minute"')
        return

    if len(parts) < 2:
        await update.message.reply_text('РЈРєР°Р¶РёС‚Рµ id_profile Рё minute: /amute "id_profile" "minute"')
        return

    target_identifier = parts[0]
    minute_raw = parts[1].strip()
    if not minute_raw.isdigit():
        await update.message.reply_text("minute РґРѕР»Р¶РµРЅ Р±С‹С‚СЊ С†РµР»С‹Рј С‡РёСЃР»РѕРј Р±РѕР»СЊС€Рµ 0.")
        return

    minutes = int(minute_raw)
    if minutes <= 0:
        await update.message.reply_text("minute РґРѕР»Р¶РµРЅ Р±С‹С‚СЊ С†РµР»С‹Рј С‡РёСЃР»РѕРј Р±РѕР»СЊС€Рµ 0.")
        return

    target_user_id, profile = _resolve_warn_target(context, target_identifier)
    if not profile:
        await update.message.reply_text(f'РџРѕР»СЊР·РѕРІР°С‚РµР»СЊ СЃ id_profile "{target_identifier}" РЅРµ РЅР°Р№РґРµРЅ.')
        return

    if _has_admin_rights_level_1_5(profile):
        await update.message.reply_text(f'РќРµРІРѕР·РјРѕР¶РЅРѕ РІС‹РґР°С‚СЊ РјСѓС‚: id_profile #{profile.get("id_profile")} РёРјРµРµС‚ Р°РґРјРёРЅ-РїСЂР°РІР° 1-5 СѓСЂРѕРІРЅСЏ.')
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
        await update.message.reply_text("РќРµ СѓРґР°Р»РѕСЃСЊ РІС‹РґР°С‚СЊ РјСѓС‚: Сѓ Р±РѕС‚Р° РЅРµС‚ РїСЂР°РІ РѕРіСЂР°РЅРёС‡РёРІР°С‚СЊ СѓС‡Р°СЃС‚РЅРёРєРѕРІ РІ СЌС‚РѕР№ РіСЂСѓРїРїРµ.")
        return
    except Exception:
        await update.message.reply_text("РќРµ СѓРґР°Р»РѕСЃСЊ РІС‹РґР°С‚СЊ РјСѓС‚ С‡РµСЂРµР· РЅР°СЃС‚СЂРѕР№РєРё Telegram.")
        return

    profile["mute_until"] = time.time() + minutes * 60
    profile["mute_reason"] = f"mute for {minutes} minutes"
    profile["mute_set_by"] = str(update.effective_user.id)

    await update.message.reply_text(f'вњ…РђРґРјРёРЅРёСЃС‚СЂР°С‚РѕСЂ id_profile #{profile.get("id_profile")} РїРѕР»СѓС‡РёР» РјСѓС‚ РЅР° {minutes} РјРёРЅ.')

    try:
        await context.bot.send_message(
            chat_id=int(target_user_id),
            text=f'в›”пёЏР’Р°Рј РІС‹РґР°РЅ РјСѓС‚ РЅР° {minutes} РјРёРЅ. Р’ СЌС‚Рѕ РІСЂРµРјСЏ РІР°С€Рё СЃРѕРѕР±С‰РµРЅРёСЏ РІ С‚РµРјРµ РЅРµ Р±СѓРґСѓС‚ РѕС‚РїСЂР°РІР»СЏС‚СЊСЃСЏ РїРѕР»СЊР·РѕРІР°С‚РµР»СЋ.',
        )
    except Exception:
        pass
    _save_profile_record(context, str(target_user_id))



async def unmute_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != WORK_CHAT_ID or not update.message:
        return

    if not _can_use_moderation_commands(context, str(update.effective_user.id)):
        await update.message.reply_text("РљРѕРјР°РЅРґР° РґРѕСЃС‚СѓРїРЅР° С‚РѕР»СЊРєРѕ Р°РґРјРёРЅРёСЃС‚СЂР°С‚РѕСЂР°Рј 2 РєР°С‚РµРіРѕСЂРёРё Рё РІС‹С€Рµ.")
        return

    raw_text = update.message.text or ""
    cmd_entity = update.message.entities[0] if update.message.entities else None
    cmd_len = cmd_entity.length if cmd_entity and cmd_entity.type == "bot_command" else len("/aunmute")
    args_text = raw_text[cmd_len:].strip()
    if not args_text:
        await update.message.reply_text('РЈРєР°Р¶РёС‚Рµ id_profile: /aunmute "id_profile"')
        return

    try:
        parts = shlex.split(args_text)
    except ValueError:
        await update.message.reply_text('РќРµРєРѕСЂСЂРµРєС‚РЅС‹Р№ С„РѕСЂРјР°С‚. РСЃРїРѕР»СЊР·СѓР№С‚Рµ: /aunmute "id_profile"')
        return

    if len(parts) < 1:
        await update.message.reply_text('РЈРєР°Р¶РёС‚Рµ id_profile: /aunmute "id_profile"')
        return

    target_identifier = parts[0]
    target_user_id, profile = _resolve_warn_target(context, target_identifier)
    if not profile:
        await update.message.reply_text(f'РџРѕР»СЊР·РѕРІР°С‚РµР»СЊ СЃ id_profile "{target_identifier}" РЅРµ РЅР°Р№РґРµРЅ.')
        return

    try:
        await context.bot.restrict_chat_member(
            chat_id=WORK_CHAT_ID,
            user_id=int(target_user_id),
            permissions=_admin_unmute_permissions(),
            use_independent_chat_permissions=True,
        )
    except Forbidden:
        await update.message.reply_text("РќРµ СѓРґР°Р»РѕСЃСЊ СЃРЅСЏС‚СЊ РјСѓС‚: Сѓ Р±РѕС‚Р° РЅРµС‚ РїСЂР°РІ РёР·РјРµРЅСЏС‚СЊ РѕРіСЂР°РЅРёС‡РµРЅРёСЏ СѓС‡Р°СЃС‚РЅРёРєРѕРІ РІ СЌС‚РѕР№ РіСЂСѓРїРїРµ.")
        return
    except Exception:
        await update.message.reply_text("РќРµ СѓРґР°Р»РѕСЃСЊ СЃРЅСЏС‚СЊ РјСѓС‚ С‡РµСЂРµР· РЅР°СЃС‚СЂРѕР№РєРё Telegram.")
        return

    _clear_admin_mute(profile)
    await update.message.reply_text(f'вњ…РЎ Р°РґРјРёРЅРёСЃС‚СЂР°С‚РѕСЂР° id_profile #{profile.get("id_profile")} СЃРЅСЏС‚ РјСѓС‚.')

    try:
        await context.bot.send_message(
            chat_id=int(target_user_id),
            text="вњ…РЎ РІР°СЃ СЃРЅСЏС‚ РјСѓС‚. РўРµРїРµСЂСЊ РІР°С€Рё СЃРѕРѕР±С‰РµРЅРёСЏ СЃРЅРѕРІР° Р±СѓРґСѓС‚ РѕС‚РїСЂР°РІР»СЏС‚СЊСЃСЏ РїРѕР»СЊР·РѕРІР°С‚РµР»СЋ.",
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
        await update.message.reply_text(f"в›”пёЏР’С‹ РЅР°С…РѕРґРёС‚РµСЃСЊ РІ РјСѓС‚Рµ РµС‰Рµ {remaining_minutes} РјРёРЅ.")
    except Exception:
        pass
    raise ApplicationHandlerStop



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
        await message.reply_text("Р’ СЌС‚РѕРј С‡Р°С‚Рµ Р°РґРјРёРЅСЃРєРёРµ РєРѕРјР°РЅРґС‹ РЅРµРґРѕСЃС‚СѓРїРЅС‹.")
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
        await update.message.reply_text("РљРѕРјР°РЅРґР° /sendpiar РґРѕСЃС‚СѓРїРЅР° С‚РѕР»СЊРєРѕ РІ С‡Р°С‚Рµ СЃРѕС‚СЂСѓРґРЅРёС‡РµСЃС‚РІР°.")
        return

    bot_data = context.application.bot_data
    now_ts = time.time()
    cooldown_until = float(bot_data.get("sendpiar_cooldown_until", 0) or 0)
    cooldown_was_active = bool(bot_data.get("sendpiar_cooldown_was_active", False))

    if cooldown_until > now_ts:
        remaining_seconds = int(cooldown_until - now_ts)
        remaining_minutes = max(1, (remaining_seconds + 59) // 60)
        await update.message.reply_text(
            f"вЏіРљРѕРјР°РЅРґР° /sendpiar РЅР° РєСѓР»РґР°СѓРЅРµ. РџРѕРґРѕР¶РґРёС‚Рµ {remaining_minutes} РјРёРЅ."
        )
        return

    if cooldown_until and cooldown_was_active and now_ts >= cooldown_until:
        await update.message.reply_text("вњ…РљСѓР»РґР°СѓРЅ Р·Р°РІРµСЂС€РµРЅ. РљРѕРјР°РЅРґР° /sendpiar СЃРЅРѕРІР° РґРѕСЃС‚СѓРїРЅР°.")
        bot_data["sendpiar_cooldown_was_active"] = False

    issuer_profile = _ensure_profile(
        context,
        str(update.effective_user.id),
        update.effective_user.username or f"id{update.effective_user.id}",
    )
    issuer_prefix = str(issuer_profile.get("prefix") or "").strip()
    if issuer_prefix != "рџ’ЋРЎРѕС‚СЂСѓРґРЅРёС‡РµСЃС‚РІРѕ":
        await update.message.reply_text("РќРµР»СЊР·СЏ РІС‹РїРѕР»РЅРёС‚СЊ РєРѕРјР°РЅРґСѓ: РЅСѓР¶РµРЅ РїСЂРµС„РёРєСЃ рџ’ЋРЎРѕС‚СЂСѓРґРЅРёС‡РµСЃС‚РІРѕ.")
        return

    cmd_entities = update.message.entities if update.message.text else (update.message.caption_entities or [])
    cmd_entity = cmd_entities[0] if cmd_entities else None
    cmd_len = cmd_entity.length if cmd_entity and cmd_entity.type == "bot_command" else len("/sendpiar")
    args_text = source_text[cmd_len:]

    if not str(args_text).strip():
        await update.message.reply_text('РСЃРїРѕР»СЊР·СѓР№С‚Рµ: /sendpiar "С‚РµРєСЃС‚" (РјРѕР¶РЅРѕ СЃ С„РѕС‚Рѕ РёР»Рё РІРёРґРµРѕ).')
        return

    quoted = re.match(r'^\s*"([\s\S]*)"\s*$', args_text)
    broadcast_text = quoted.group(1) if quoted else args_text.strip()
    if not broadcast_text:
        await update.message.reply_text("РўРµРєСЃС‚ СЂР°СЃСЃС‹Р»РєРё РЅРµ РґРѕР»Р¶РµРЅ Р±С‹С‚СЊ РїСѓСЃС‚С‹Рј.")
        return

    payload_text = broadcast_text
    if not payload_text.lower().startswith("#СЂРµРєР»Р°РјР°"):
        payload_text = f"#СЂРµРєР»Р°РјР°\n\n{payload_text}"

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
        await update.message.reply_text("Р’ Р±Р°Р·Рµ РЅРµС‚ РїРѕР»СЊР·РѕРІР°С‚РµР»РµР№ РґР»СЏ СЂР°СЃСЃС‹Р»РєРё.")
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

    report_text = f"вњ…Р Р°СЃСЃС‹Р»РєР° РѕС‚РїСЂР°РІР»РµРЅР° РІСЃРµРј РїРѕР»СЊР·РѕРІР°С‚РµР»СЏРј Р±РѕС‚Р°.\nРЈСЃРїРµС€РЅРѕ: {success_count}\nРћС€РёР±РѕРє: {failed_count}"
    if immune_profiles:
        immune_profiles = sorted([pid for pid in immune_profiles if int(pid or 0) > 0])
        immune_lines = "\n".join([f"id_profile #{pid} РёРјРµРµС‚ РёРјРјСѓРЅРёС‚РµС‚ Рє СЂРµРєР»Р°РјРµ" for pid in immune_profiles])
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
        await update.message.reply_text("РљРѕРјР°РЅРґР° РґРѕСЃС‚СѓРїРЅР° С‚РѕР»СЊРєРѕ Р°РґРјРёРЅРёСЃС‚СЂР°С‚РѕСЂР°Рј 3 РєР°С‚РµРіРѕСЂРёРё Рё РІС‹С€Рµ.")
        return

    raw_text = update.message.text or ""
    cmd_entity = update.message.entities[0] if update.message.entities else None
    cmd_len = cmd_entity.length if cmd_entity and cmd_entity.type == "bot_command" else len("/anpiar")
    args_text = raw_text[cmd_len:].strip()
    if not args_text:
        await update.message.reply_text('РСЃРїРѕР»СЊР·СѓР№С‚Рµ: /anpiar "id_profile" "1-0"')
        return

    try:
        parts = shlex.split(args_text)
    except ValueError:
        await update.message.reply_text('РќРµРєРѕСЂСЂРµРєС‚РЅС‹Р№ С„РѕСЂРјР°С‚. РСЃРїРѕР»СЊР·СѓР№С‚Рµ: /anpiar "id_profile" "1-0"')
        return

    if len(parts) < 2:
        await update.message.reply_text('РСЃРїРѕР»СЊР·СѓР№С‚Рµ: /anpiar "id_profile" "1-0"')
        return

    target_identifier = parts[0]
    mode_raw = str(parts[1]).strip()
    if mode_raw not in {"1", "0"}:
        await update.message.reply_text('Р’С‚РѕСЂРѕР№ Р°СЂРіСѓРјРµРЅС‚ РґРѕР»Р¶РµРЅ Р±С‹С‚СЊ "1" РёР»Рё "0".')
        return

    target_user_id, target_profile = _resolve_warn_target(context, target_identifier)
    if not target_profile:
        await update.message.reply_text(f'РџРѕР»СЊР·РѕРІР°С‚РµР»СЊ СЃ id_profile "{target_identifier}" РЅРµ РЅР°Р№РґРµРЅ.')
        return

    has_access = bool(target_profile.get("ad_disable_access", False))
    target_id_profile = int(target_profile.get("id_profile", 0) or 0)

    if mode_raw == "1":
        if has_access:
            await update.message.reply_text(f"РЈ РїРѕР»СЊР·РѕРІР°С‚РµР»СЏ id_profile #{target_id_profile} СѓР¶Рµ РµСЃС‚СЊ РїСЂР°РІР° РЅР° РѕС‚РєР»СЋС‡РµРЅРёРµ СЂРµРєР»Р°РјС‹.")
            return
        target_profile["ad_disable_access"] = True
        _save_profile_record(context, str(target_user_id))
        await update.message.reply_text(f"вњ…РџСЂР°РІР° РЅР° РѕС‚РєР»СЋС‡РµРЅРёРµ СЂРµРєР»Р°РјС‹ РІС‹РґР°РЅС‹ РїРѕР»СЊР·РѕРІР°С‚РµР»СЋ id_profile #{target_id_profile}.")
        try:
            await context.bot.send_message(
                chat_id=int(target_user_id),
                text="вњ…Р’Р°Рј РІС‹РґР°РЅС‹ РїСЂР°РІР° РЅР° РѕС‚РєР»СЋС‡РµРЅРёРµ СЂРµРєР»Р°РјС‹. РћС‚РєСЂРѕР№С‚Рµ вљ™пёЏРќР°СЃС‚СЂРѕР№РєРё Рё РЅР°Р¶РјРёС‚Рµ рџ”•РћС‚РєР»СЋС‡РёС‚СЊ СЂРµРєР»Р°РјСѓ.",
            )
        except Exception:
            pass
        return

    if not has_access:
        await update.message.reply_text(f"РЈ РїРѕР»СЊР·РѕРІР°С‚РµР»СЏ id_profile #{target_id_profile} РЅРµС‚ РїСЂР°РІ РЅР° РѕС‚РєР»СЋС‡РµРЅРёРµ СЂРµРєР»Р°РјС‹.")
        return

    target_profile["ad_disable_access"] = False
    target_profile["ad_disable_enabled"] = False
    _save_profile_record(context, str(target_user_id))
    await update.message.reply_text(f"вњ…РџСЂР°РІР° РЅР° РѕС‚РєР»СЋС‡РµРЅРёРµ СЂРµРєР»Р°РјС‹ СЃРЅСЏС‚С‹ Сѓ РїРѕР»СЊР·РѕРІР°С‚РµР»СЏ id_profile #{target_id_profile}.")
    try:
        await context.bot.send_message(
            chat_id=int(target_user_id),
            text="вљ пёЏРџСЂР°РІР° РЅР° РѕС‚РєР»СЋС‡РµРЅРёРµ СЂРµРєР»Р°РјС‹ Р±С‹Р»Рё РѕС‚РѕР·РІР°РЅС‹.",
        )
    except Exception:
        pass



async def warn_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != LOG_CHAT_ID or not update.message:
        return

    if not _can_use_moderation_commands(context, str(update.effective_user.id)):
        await update.message.reply_text("РљРѕРјР°РЅРґР° РґРѕСЃС‚СѓРїРЅР° С‚РѕР»СЊРєРѕ Р°РґРјРёРЅРёСЃС‚СЂР°С‚РѕСЂР°Рј 2 РєР°С‚РµРіРѕСЂРёРё Рё РІС‹С€Рµ.")
        return

    raw_text = update.message.text or ""
    cmd_entity = update.message.entities[0] if update.message.entities else None
    cmd_len = cmd_entity.length if cmd_entity and cmd_entity.type == "bot_command" else len("/warn")
    args_text = raw_text[cmd_len:].strip()
    if not args_text:
        await update.message.reply_text("РЈРєР°Р¶РёС‚Рµ id_profile Рё РїСЂРёС‡РёРЅСѓ: /warn \"id_profile\" \"reason\"")
        return

    try:
        parts = shlex.split(args_text)
    except ValueError:
        await update.message.reply_text("РќРµРєРѕСЂСЂРµРєС‚РЅС‹Р№ С„РѕСЂРјР°С‚. РСЃРїРѕР»СЊР·СѓР№С‚Рµ: /warn \"id_profile\" \"reason\"")
        return

    if len(parts) < 2:
        await update.message.reply_text("РЈРєР°Р¶РёС‚Рµ id_profile Рё РїСЂРёС‡РёРЅСѓ: /warn \"id_profile\" \"reason\"")
        return

    target_identifier = parts[0]
    reason = " ".join(parts[1:]).strip()
    if not reason:
        await update.message.reply_text("РЈРєР°Р¶РёС‚Рµ РїСЂРёС‡РёРЅСѓ РїСЂРµРґСѓРїСЂРµР¶РґРµРЅРёСЏ.")
        return

    target_user_id, profile = _resolve_warn_target(context, target_identifier)
    if not profile:
        await update.message.reply_text(f'РџРѕР»СЊР·РѕРІР°С‚РµР»СЊ СЃ id_profile "{target_identifier}" РЅРµ РЅР°Р№РґРµРЅ.')
        return

    if _has_admin_rights_level_1_5(profile):
        await update.message.reply_text(
            f'РќРµРІРѕР·РјРѕР¶РЅРѕ РІС‹РґР°С‚СЊ РїСЂРµРґСѓРїСЂРµР¶РґРµРЅРёРµ: id_profile #{profile.get("id_profile")} РёРјРµРµС‚ Р°РґРјРёРЅ-РїСЂР°РІР° 1-5 СѓСЂРѕРІРЅСЏ.'
        )
        return

    current_warn = int(profile.get("warn", 0) or 0)
    profile["warn"] = current_warn + 1
    profile["reason"] = reason
    await enforce_autoban_if_needed(context, str(target_user_id), profile.get("username"))

    await update.message.reply_text(
        f'вљ пёЏРџРѕР»СЊР·РѕРІР°С‚РµР»СЋ id_profile #{profile.get("id_profile")} РІС‹РґР°РЅРѕ РїСЂРµРґСѓРїСЂРµР¶РґРµРЅРёРµ. РўРµРїРµСЂСЊ Сѓ РЅРµРіРѕ {profile["warn"]} warn(-РѕРІ). РџСЂРёС‡РёРЅР°: "{reason}"'
    )

    try:
        await context.bot.send_message(
            chat_id=int(target_user_id),
            text=f'вќ—пёЏР’С‹ РїРѕР»СѓС‡РёР»Рё РїСЂРµРґСѓРїСЂРµР¶РґРµРЅРёРµ СЃ РїСЂРёС‡РёРЅРѕР№ "{reason}" РѕС‚ СЂСѓРєРѕРІРѕРґСЃС‚РІР° Р±РѕС‚Р°. РўРµРїРµСЂСЊ Сѓ РІР°СЃ {profile["warn"]} РїСЂРµРґСѓРїСЂРµР¶РґРµРЅРёР№',
        )
    except Exception:
        pass
    _save_profile_record(context, str(target_user_id))



async def stats_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    allowed_special_chats = _special_admin_chat_ids()
    if update.effective_chat.id not in allowed_special_chats or not update.message:
        return

    if not _can_use_moderation_commands(context, str(update.effective_user.id)):
        await update.message.reply_text("РљРѕРјР°РЅРґР° РґРѕСЃС‚СѓРїРЅР° С‚РѕР»СЊРєРѕ Р°РґРјРёРЅРёСЃС‚СЂР°С‚РѕСЂР°Рј 2 РєР°С‚РµРіРѕСЂРёРё Рё РІС‹С€Рµ.")
        return

    raw_text = update.message.text or ""
    cmd_entity = update.message.entities[0] if update.message.entities else None
    cmd_len = cmd_entity.length if cmd_entity and cmd_entity.type == "bot_command" else len("/stats")
    args_text = raw_text[cmd_len:].strip()
    if not args_text:
        await update.message.reply_text('РСЃРїРѕР»СЊР·СѓР№С‚Рµ: /stats "id_profile"')
        return

    try:
        parts = shlex.split(args_text)
    except ValueError:
        await update.message.reply_text('РќРµРєРѕСЂСЂРµРєС‚РЅС‹Р№ С„РѕСЂРјР°С‚. РСЃРїРѕР»СЊР·СѓР№С‚Рµ: /stats "id_profile"')
        return

    if len(parts) < 1:
        await update.message.reply_text('РСЃРїРѕР»СЊР·СѓР№С‚Рµ: /stats "id_profile"')
        return

    target_identifier = parts[0]
    target_user_id, profile = _resolve_warn_target(context, target_identifier)
    if not profile:
        await update.message.reply_text(f'РџРѕР»СЊР·РѕРІР°С‚РµР»СЊ СЃ id_profile "{target_identifier}" РЅРµ РЅР°Р№РґРµРЅ.')
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
        await update.message.reply_text("РљРѕРјР°РЅРґР° РґРѕСЃС‚СѓРїРЅР° С‚РѕР»СЊРєРѕ Р°РґРјРёРЅРёСЃС‚СЂР°С‚РѕСЂР°Рј 4 РєР°С‚РµРіРѕСЂРёРё Рё РІС‹С€Рµ.")
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
        "рџ”ЌРџРѕР»РЅР°СЏ СЃС‚Р°С‚РёСЃС‚РёРєР° Р±Р°Р·С‹ РґР°РЅРЅС‹С… РјРѕСЂРѕР·РЅРѕР№ Р·Р°РІРёСЃС‚Рё\n\n"
        f"Р—Р°СЂРµРіРёСЃС‚СЂРёСЂРѕРІР°РЅРѕ РїРѕР»СЊР·РѕРІР°С‚РµР»РµР№: {users_regist}\n"
        f"РџРѕР»СЊР·РѕРІР°С‚РµР»РµР№ РєРѕС‚РѕСЂС‹Рµ Р·Р°Р±Р»РѕРєРёСЂРѕРІР°Р»Рё Р±РѕС‚Р°: {banned_users_regist}\n"
        f"РђРґРјРёРЅРёСЃС‚СЂР°С‚РѕСЂРѕРІ: {admins_regist}\n"
        f"РђРєС‚РёРІРЅС‹С… СЃРµСЃСЃРёР№: {session_regist}\n"
        f"РћС‚РїСЂР°РІР»РµРЅРЅС‹С… СЃРѕРѕР±С‰РµРЅРёР№: {send_regist}\n"
        f"РџРѕР»СѓС‡РµРЅРЅС‹С… РѕС‚РІРµС‚РѕРІ: {otvet_regist}"
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
        await update.message.reply_text("РљРѕРјР°РЅРґР° РґРѕСЃС‚СѓРїРЅР° С‚РѕР»СЊРєРѕ Р°РґРјРёРЅРёСЃС‚СЂР°С‚РѕСЂР°Рј 4 РєР°С‚РµРіРѕСЂРёРё Рё РІС‹С€Рµ.")
        return

    raw_text = update.message.text or ""
    cmd_entity = update.message.entities[0] if update.message.entities else None
    cmd_len = cmd_entity.length if cmd_entity and cmd_entity.type == "bot_command" else len("/astats")
    args_text = raw_text[cmd_len:].strip()
    if not args_text:
        await update.message.reply_text('РСЃРїРѕР»СЊР·СѓР№С‚Рµ: /astats "id_profile"')
        return

    try:
        parts = shlex.split(args_text)
    except ValueError:
        await update.message.reply_text('РќРµРєРѕСЂСЂРµРєС‚РЅС‹Р№ С„РѕСЂРјР°С‚. РСЃРїРѕР»СЊР·СѓР№С‚Рµ: /astats "id_profile"')
        return

    if len(parts) < 1:
        await update.message.reply_text('РСЃРїРѕР»СЊР·СѓР№С‚Рµ: /astats "id_profile"')
        return

    target_identifier = parts[0]
    target_user_id, profile = _resolve_warn_target(context, target_identifier)
    if not profile:
        await update.message.reply_text(f'РџРѕР»СЊР·РѕРІР°С‚РµР»СЊ СЃ id_profile "{target_identifier}" РЅРµ РЅР°Р№РґРµРЅ.')
        return

    if not _has_admin_rights_level_1_5(profile):
        await update.message.reply_text(
            f'РќРµР»СЊР·СЏ РёСЃРїРѕР»СЊР·РѕРІР°С‚СЊ /astats: id_profile #{profile.get("id_profile")} РЅРµ СЏРІР»СЏРµС‚СЃСЏ Р°РґРјРёРЅРёСЃС‚СЂР°С‚РѕСЂРѕРј.'
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
        await update.callback_query.answer("РџР°РЅРµР»СЊ СѓСЃС‚Р°СЂРµР»Р°", show_alert=True)
        return

    if str(update.effective_user.id) != str(session.get("issuer_user_id")):
        await update.callback_query.answer("РљРЅРѕРїРєР° РґРѕСЃС‚СѓРїРЅР° С‚РѕР»СЊРєРѕ Р°РІС‚РѕСЂСѓ /astats", show_alert=True)
        return

    issuer_profile = _ensure_profile(
        context,
        str(update.effective_user.id),
        update.effective_user.username or f"id{update.effective_user.id}",
    )
    if int(issuer_profile.get("admin_level", 0) or 0) < 4:
        await update.callback_query.answer("РќРµРґРѕСЃС‚Р°С‚РѕС‡РЅРѕ РїСЂР°РІ", show_alert=True)
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
        "РћС‚РїСЂР°РІСЊС‚Рµ РЅРѕРІС‹Р№ С‚РµРі (РґРѕР»Р¶РµРЅ РЅР°С‡РёРЅР°С‚СЊСЃСЏ СЃ #) РѕС‚РІРµС‚РѕРј РЅР° СЌС‚Рѕ СЃРѕРѕР±С‰РµРЅРёРµ.",
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("РѕС‚РјРµРЅР°", callback_data=f"astats_tag_cancel_{session_id}")]]
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
        await update.callback_query.answer("РџР°РЅРµР»СЊ СѓСЃС‚Р°СЂРµР»Р°", show_alert=True)
        return

    if str(update.effective_user.id) != str(session.get("issuer_user_id")):
        await update.callback_query.answer("РљРЅРѕРїРєР° РґРѕСЃС‚СѓРїРЅР° С‚РѕР»СЊРєРѕ Р°РІС‚РѕСЂСѓ /astats", show_alert=True)
        return

    issuer_profile = _ensure_profile(
        context,
        str(update.effective_user.id),
        update.effective_user.username or f"id{update.effective_user.id}",
    )
    if int(issuer_profile.get("admin_level", 0) or 0) < 4:
        await update.callback_query.answer("РќРµРґРѕСЃС‚Р°С‚РѕС‡РЅРѕ РїСЂР°РІ", show_alert=True)
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
            "РћС‚РїСЂР°РІСЊС‚Рµ РЅРѕРІСѓСЋ Р±РёРѕРіСЂР°С„РёСЋ Р°РґРјРёРЅРёСЃС‚СЂР°С‚РѕСЂР°.",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("РѕС‚РјРµРЅР°", callback_data=f"astats_bio_cancel_{session_id}")]]
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
        await update.callback_query.answer("РџР°РЅРµР»СЊ СѓСЃС‚Р°СЂРµР»Р°", show_alert=True)
        return

    if str(update.effective_user.id) != str(session.get("issuer_user_id")):
        await update.callback_query.answer("РљРЅРѕРїРєР° РґРѕСЃС‚СѓРїРЅР° С‚РѕР»СЊРєРѕ Р°РІС‚РѕСЂСѓ /astats", show_alert=True)
        return

    issuer_profile = _ensure_profile(
        context,
        str(update.effective_user.id),
        update.effective_user.username or f"id{update.effective_user.id}",
    )
    if int(issuer_profile.get("admin_level", 0) or 0) < 4:
        await update.callback_query.answer("РќРµРґРѕСЃС‚Р°С‚РѕС‡РЅРѕ РїСЂР°РІ", show_alert=True)
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
    if "рџ—ЈпёЏ" in existing_tip:
        selected_keys.append("chat")
    if "вќ¤пёЏ" in existing_tip:
        selected_keys.append("support")
    if "рџ”Ґ" in existing_tip:
        selected_keys.append("flirt")

    session["status"] = "astats_tip_edit"
    session["tip_selected_keys"] = selected_keys

    try:
        await update.callback_query.message.edit_text(
            f"в•пёЏРџР°РЅРµР»СЊ СЂРµРґР°РєС‚РёСЂРѕРІР°РЅРёСЏ РґРёР°Р»РѕРіРѕРІ Сѓ Р°РґРјРёРЅР° {username_text}",
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
        await update.callback_query.answer("РџР°РЅРµР»СЊ СѓСЃС‚Р°СЂРµР»Р°", show_alert=True)
        return

    if str(update.effective_user.id) != str(session.get("issuer_user_id")):
        await update.callback_query.answer("РљРЅРѕРїРєР° РґРѕСЃС‚СѓРїРЅР° С‚РѕР»СЊРєРѕ Р°РІС‚РѕСЂСѓ /astats", show_alert=True)
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
        await update.callback_query.answer("РџР°РЅРµР»СЊ СѓСЃС‚Р°СЂРµР»Р°", show_alert=True)
        return

    if str(update.effective_user.id) != str(session.get("issuer_user_id")):
        await update.callback_query.answer("РљРЅРѕРїРєР° РґРѕСЃС‚СѓРїРЅР° С‚РѕР»СЊРєРѕ Р°РІС‚РѕСЂСѓ /astats", show_alert=True)
        return

    issuer_profile = _ensure_profile(
        context,
        str(update.effective_user.id),
        update.effective_user.username or f"id{update.effective_user.id}",
    )
    if int(issuer_profile.get("admin_level", 0) or 0) < 4:
        await update.callback_query.answer("РќРµРґРѕСЃС‚Р°С‚РѕС‡РЅРѕ РїСЂР°РІ", show_alert=True)
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
            text=f"рџ“ЌР’Р°С€ С‚РёРї РґРёР°Р»РѕРіРѕРІ Р±С‹Р» РёР·РјРµРЅРµРЅ. РўРµРїРµСЂСЊ РІС‹ РјРѕР¶РµС‚Рµ РѕР±С‰Р°С‚СЊСЃСЏ РЅР°: {tip_value}",
        )
    except Exception:
        pass

    session["status"] = "idle"
    await update.callback_query.answer("РЎРјРµРЅР° РґРёР°Р»РѕРіРѕРІ СѓСЃРїРµС€РЅРѕ Р·Р°РІРµСЂС€РµРЅР°", show_alert=True)

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
        await update.callback_query.answer("РџР°РЅРµР»СЊ СѓСЃС‚Р°СЂРµР»Р°", show_alert=True)
        return

    if str(update.effective_user.id) != str(session.get("issuer_user_id")):
        await update.callback_query.answer("РљРЅРѕРїРєР° РґРѕСЃС‚СѓРїРЅР° С‚РѕР»СЊРєРѕ Р°РІС‚РѕСЂСѓ /astats", show_alert=True)
        return

    issuer_profile = _ensure_profile(
        context,
        str(update.effective_user.id),
        update.effective_user.username or f"id{update.effective_user.id}",
    )
    if int(issuer_profile.get("admin_level", 0) or 0) < 4:
        await update.callback_query.answer("РќРµРґРѕСЃС‚Р°С‚РѕС‡РЅРѕ РїСЂР°РІ", show_alert=True)
        return

    target_user_id = str(session.get("target_user_id"))
    target_profile = _ensure_profile(context, target_user_id, f"id{target_user_id}")
    admin_username = str(target_profile.get("username") or f"id{target_user_id}")

    try:
        await update.callback_query.message.edit_text(
            f"рџ‘ЁвЂЌрџ‘¦РџР°РЅРµР»СЊ СѓРїСЂР°РІР»РµРЅРёСЏ Р°РєС‚РёРІРЅС‹РјРё РџР— Р°РґРјРёРЅР° {admin_username}",
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
        await update.callback_query.message.reply_text("РќРёС‡РµРіРѕ РЅРµ РЅР°Р№РґРµРЅРѕ")
        return

    for requester_user_id, active in found_sessions:
        requester_profile = _ensure_profile(context, requester_user_id, active.get("topic_base_name") or f"id{requester_user_id}")
        username_pz = str(requester_profile.get("username") or active.get("topic_base_name") or f"id{requester_user_id}")
        user_messages = int(active.get("msg_topic_user", 0) or 0)
        admin_messages = int(active.get("msg_topic_admin", 0) or 0)
        msg_topic = user_messages + admin_messages
        date_value = str(active.get("session_started_at") or "РЅРµ СѓРєР°Р·Р°РЅР°")
        topic_link = _topic_url(active.get("chat_id"), active.get("topic_id"))

        await update.callback_query.message.reply_text(
            f"в„№пёЏРђРєС‚РёРІРЅРѕРµ РѕР±С‰РµРЅРёРµ СЃ {username_pz}\n"
            f"рџ“ҐРЎРѕРѕР±С‰РµРЅРёР№: \"{msg_topic}\"\n\n"
            f"Р РµРіРёСЃС‚СЂР°С†РёСЏ РѕР±С‰РµРЅРёСЏ: \"{date_value}\"\n"
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
        await update.message.reply_text("РљРѕРјР°РЅРґР° РґРѕСЃС‚СѓРїРЅР° С‚РѕР»СЊРєРѕ Р°РґРјРёРЅРёСЃС‚СЂР°С‚РѕСЂР°Рј 3 РєР°С‚РµРіРѕСЂРёРё Рё РІС‹С€Рµ.")
        return

    raw_text = update.message.text or ""
    cmd_entity = update.message.entities[0] if update.message.entities else None
    cmd_len = cmd_entity.length if cmd_entity and cmd_entity.type == "bot_command" else len("/info_topic")
    args_text = raw_text[cmd_len:].strip()
    if not args_text:
        await update.message.reply_text('РСЃРїРѕР»СЊР·СѓР№С‚Рµ: /info_topic "topic_link"')
        return

    try:
        parts = shlex.split(args_text)
    except ValueError:
        await update.message.reply_text('РќРµРєРѕСЂСЂРµРєС‚РЅС‹Р№ С„РѕСЂРјР°С‚. РСЃРїРѕР»СЊР·СѓР№С‚Рµ: /info_topic "topic_link"')
        return

    if len(parts) < 1:
        await update.message.reply_text('РСЃРїРѕР»СЊР·СѓР№С‚Рµ: /info_topic "topic_link"')
        return

    topic_link = parts[0]
    chat_id, topic_id = _parse_topic_url(topic_link)
    if chat_id is None or topic_id is None:
        await update.message.reply_text('РќРµРєРѕСЂСЂРµРєС‚РЅС‹Р№ URL С‚РµРјС‹. РќСѓР¶РµРЅ С„РѕСЂРјР°С‚ РІРёРґР° https://t.me/c/<chat_id>/<topic_id>.')
        return

    topic_map = context.application.bot_data.setdefault("topic_user_map", {})
    target_user_id = topic_map.get(topic_id) or topic_map.get(str(topic_id))
    active_chats = context.application.bot_data.setdefault("active_chats", {})
    active = active_chats.get(str(target_user_id)) if target_user_id else None
    if not active or not active.get("active"):
        await update.message.reply_text("РўРµРјР° РЅРµ РЅР°Р№РґРµРЅР° РёР»Рё СЃРµСЃСЃРёСЏ СѓР¶Рµ Р·Р°РІРµСЂС€РµРЅР°.")
        return

    if int(active.get("chat_id", 0) or 0) != int(chat_id) or int(active.get("topic_id", 0) or 0) != int(topic_id):
        await update.message.reply_text("РўРµРјР° РЅРµ РЅР°Р№РґРµРЅР° РёР»Рё СѓР¶Рµ РЅРµР°РєС‚СѓР°Р»СЊРЅР°.")
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
    admin_username = str(active.get("admin_username") or "Р°РґРјРёРЅ")
    username_pz = str(target_profile.get("username") or active.get("topic_base_name") or f"id{target_user_id}")
    detect = int(active.get("detect_topic", 0) or 0)
    msg_topic = int(active.get("msg_topic_user", 0) or 0) + int(active.get("msg_topic_admin", 0) or 0)
    rp_topic = int(active.get("rp_topic", 0) or 0)
    date_value = str(active.get("session_started_at") or "РЅРµ СѓРєР°Р·Р°РЅР°")

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
        await update.callback_query.answer("РџР°РЅРµР»СЊ СѓСЃС‚Р°СЂРµР»Р°", show_alert=True)
        return

    if str(update.effective_user.id) != str(panel.get("issuer_user_id")):
        await update.callback_query.answer("РљРЅРѕРїРєР° РґРѕСЃС‚СѓРїРЅР° С‚РѕР»СЊРєРѕ Р°РІС‚РѕСЂСѓ /info_topic", show_alert=True)
        return

    issuer_profile = _ensure_profile(
        context,
        str(update.effective_user.id),
        update.effective_user.username or f"id{update.effective_user.id}",
    )
    if int(issuer_profile.get("admin_level", 0) or 0) < 3:
        await update.callback_query.answer("РќРµРґРѕСЃС‚Р°С‚РѕС‡РЅРѕ РїСЂР°РІ", show_alert=True)
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
        await update.callback_query.answer("РџР°РЅРµР»СЊ СѓСЃС‚Р°СЂРµР»Р°", show_alert=True)
        return

    if str(update.effective_user.id) != str(panel.get("issuer_user_id")):
        await update.callback_query.answer("РљРЅРѕРїРєР° РґРѕСЃС‚СѓРїРЅР° С‚РѕР»СЊРєРѕ Р°РІС‚РѕСЂСѓ /info_topic", show_alert=True)
        return

    issuer_profile = _ensure_profile(
        context,
        str(update.effective_user.id),
        update.effective_user.username or f"id{update.effective_user.id}",
    )
    if int(issuer_profile.get("admin_level", 0) or 0) < 3:
        await update.callback_query.answer("РќРµРґРѕСЃС‚Р°С‚РѕС‡РЅРѕ РїСЂР°РІ", show_alert=True)
        return

    panel["pending_action"] = "stop"
    try:
        await update.callback_query.message.edit_reply_markup(reply_markup=_build_info_topic_keyboard(panel_id, pending_action="stop"))
    except Exception:
        pass



async def info_topic_cancel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer("РћС‚РјРµРЅР°")
    panel_id = (update.callback_query.data or "").split("_")[-1]
    panels = context.application.bot_data.setdefault("info_topic_panels", {})
    panel = panels.get(panel_id)
    if not panel:
        return

    if str(update.effective_user.id) != str(panel.get("issuer_user_id")):
        await update.callback_query.answer("РљРЅРѕРїРєР° РґРѕСЃС‚СѓРїРЅР° С‚РѕР»СЊРєРѕ Р°РІС‚РѕСЂСѓ /info_topic", show_alert=True)
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
    admin_username = str(active.get("admin_username") or "Р°РґРјРёРЅ")

    if action == "close":
        active.pop("management_paused", None)
        active.pop("management_close_pending", None)
        try:
            await context.bot.edit_forum_topic(
                chat_id=chat_id,
                message_thread_id=topic_id,
                name=f"{active.get('topic_base_name') or username_pz} (Р—Р°РєСЂС‹С‚Рѕ СЂСѓРєРѕРІРѕРґСЃС‚РІРѕРј)",
            )
        except Exception:
            pass
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                message_thread_id=topic_id,
                text="вќ—пёЏР СѓРєРѕРІРѕРґСЃС‚РІРѕ Р±РѕС‚Р° Р·Р°РєСЂС‹Р»Рѕ СЃРµСЃСЃРёСЋ, РўРµРјР° Р±РѕР»СЊС€Рµ РЅРµ Р°РєС‚СѓР°Р»СЊРЅР°",
            )
        except Exception:
            pass
        try:
            await context.bot.send_message(
                chat_id=int(target_user_id),
                text="вќ—пёЏР’Р°С€Р° СЃРµСЃСЃРёСЏ Р±С‹Р»Р° Р·Р°РІРµСЂС€РµРЅР° СЂСѓРєРѕРІРѕРґСЃС‚РІРѕРј Р±РѕС‚Р°.",
            )
        except Exception:
            pass
        active["active"] = False
        context.application.bot_data.get("topic_user_map", {}).pop(topic_id, None)
        context.application.bot_data.get("topic_user_map", {}).pop(str(topic_id), None)
        context.application.bot_data.get("active_chats", {}).pop(target_user_id, None)
        try:
            await context.bot.send_message(chat_id=LOG_CHAT_ID, text=f"вњ…РЎРµСЃСЃРёСЏ {username_pz} СЃ Р°РґРјРёРЅРѕРј {admin_username} Р·Р°РєСЂС‹С‚Р° СЂСѓРєРѕРІРѕРґСЃС‚РІРѕРј.")
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
                name=f"{active.get('topic_base_name') or username_pz} (РћСЃС‚Р°РЅРѕРІР»РµРЅРѕ СЂСѓРєРѕРІРѕРґСЃС‚РІРѕРј)",
            )
        except Exception:
            pass
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                message_thread_id=topic_id,
                text="рџ’¤Р СѓРєРѕРІРѕРґСЃС‚РІРѕ Р±РѕС‚Р° РѕСЃС‚Р°РЅРѕРІРёР»Р° РѕР±С‰РµРЅРёРµ РІ СЌС‚РѕР№ С‚РµРјРµ, СЃРѕРѕР±С‰РµРЅРёРµ РѕС‚РїСЂР°РІР»СЏС‚СЊСЃСЏ РЅРµ Р±СѓРґСѓС‚.",
            )
        except Exception:
            pass
        try:
            await context.bot.send_message(
                chat_id=int(target_user_id),
                text="рџ’¤РЎРµСЃСЃРёСЏ Р±С‹Р»Р° РѕСЃС‚Р°РЅРѕРІР»РµРЅР° СЂСѓРєРѕРІРѕРґСЃС‚РІРѕРј Р±РѕС‚Р°. РЎРѕРѕР±С‰РµРЅРёСЏ РІСЂРµРјРµРЅРЅРѕ РЅРµ РѕС‚РїСЂР°РІР»СЏСЋС‚СЃСЏ.",
            )
        except Exception:
            pass
        panel["pending_action"] = None
        try:
            await context.bot.send_message(chat_id=LOG_CHAT_ID, text=f"рџ’¤РЎРµСЃСЃРёСЏ {username_pz} СЃ Р°РґРјРёРЅРѕРј {admin_username} РѕСЃС‚Р°РЅРѕРІР»РµРЅР° СЂСѓРєРѕРІРѕРґСЃС‚РІРѕРј.")
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
                text="вњ…РўРµРјР° СЃРЅРѕРІР° Р°РєС‚СѓР°Р»СЊРЅР°СЏ.",
            )
        except Exception:
            pass
        try:
            await context.bot.send_message(
                chat_id=int(target_user_id),
                text="вњ…РўРµРјР° СЃРЅРѕРІР° Р°РєС‚СѓР°Р»СЊРЅР°СЏ. РџРµСЂРµСЃС‹Р»РєР° СЃРѕРѕР±С‰РµРЅРёР№ РІРѕСЃСЃС‚Р°РЅРѕРІР»РµРЅР°.",
            )
        except Exception:
            pass
        panel["pending_action"] = None
        try:
            await context.bot.send_message(chat_id=LOG_CHAT_ID, text=f"вњ…РЎРµСЃСЃРёСЏ {username_pz} СЃ Р°РґРјРёРЅРѕРј {admin_username} РІРѕР·РѕР±РЅРѕРІР»РµРЅР° СЂСѓРєРѕРІРѕРґСЃС‚РІРѕРј.")
        except Exception:
            pass



async def info_topic_close_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    panel_id = (update.callback_query.data or "").split("_")[-1]
    panel = context.application.bot_data.setdefault("info_topic_panels", {}).get(panel_id)
    if not panel:
        await update.callback_query.answer("РџР°РЅРµР»СЊ СѓСЃС‚Р°СЂРµР»Р°", show_alert=True)
        return

    if str(update.effective_user.id) != str(panel.get("issuer_user_id")):
        await update.callback_query.answer("РљРЅРѕРїРєР° РґРѕСЃС‚СѓРїРЅР° С‚РѕР»СЊРєРѕ Р°РІС‚РѕСЂСѓ /info_topic", show_alert=True)
        return

    issuer_profile = _ensure_profile(context, str(update.effective_user.id), update.effective_user.username or f"id{update.effective_user.id}")
    if int(issuer_profile.get("admin_level", 0) or 0) < 3:
        await update.callback_query.answer("РќРµРґРѕСЃС‚Р°С‚РѕС‡РЅРѕ РїСЂР°РІ", show_alert=True)
        return

    await _finish_info_topic_action(context, panel_id, "close")
    try:
        await update.callback_query.message.edit_text("вњ…РЎРµСЃСЃРёСЏ Р·Р°РєСЂС‹С‚Р° СЂСѓРєРѕРІРѕРґСЃС‚РІРѕРј Р±РѕС‚Р°.")
    except Exception:
        pass



async def info_topic_stop_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    panel_id = (update.callback_query.data or "").split("_")[-1]
    panel = context.application.bot_data.setdefault("info_topic_panels", {}).get(panel_id)
    if not panel:
        await update.callback_query.answer("РџР°РЅРµР»СЊ СѓСЃС‚Р°СЂРµР»Р°", show_alert=True)
        return

    if str(update.effective_user.id) != str(panel.get("issuer_user_id")):
        await update.callback_query.answer("РљРЅРѕРїРєР° РґРѕСЃС‚СѓРїРЅР° С‚РѕР»СЊРєРѕ Р°РІС‚РѕСЂСѓ /info_topic", show_alert=True)
        return

    issuer_profile = _ensure_profile(context, str(update.effective_user.id), update.effective_user.username or f"id{update.effective_user.id}")
    if int(issuer_profile.get("admin_level", 0) or 0) < 3:
        await update.callback_query.answer("РќРµРґРѕСЃС‚Р°С‚РѕС‡РЅРѕ РїСЂР°РІ", show_alert=True)
        return

    await _finish_info_topic_action(context, panel_id, "stop")
    try:
        await update.callback_query.message.edit_text(
            "рџ’¤РЎРµСЃСЃРёСЏ РѕСЃС‚Р°РЅРѕРІР»РµРЅР° СЂСѓРєРѕРІРѕРґСЃС‚РІРѕРј Р±РѕС‚Р°.",
            reply_markup=_build_info_topic_keyboard(panel_id, paused=True),
        )
    except Exception:
        pass



async def info_topic_resume_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    panel_id = (update.callback_query.data or "").split("_")[-1]
    panel = context.application.bot_data.setdefault("info_topic_panels", {}).get(panel_id)
    if not panel:
        await update.callback_query.answer("РџР°РЅРµР»СЊ СѓСЃС‚Р°СЂРµР»Р°", show_alert=True)
        return

    if str(update.effective_user.id) != str(panel.get("issuer_user_id")):
        await update.callback_query.answer("РљРЅРѕРїРєР° РґРѕСЃС‚СѓРїРЅР° С‚РѕР»СЊРєРѕ Р°РІС‚РѕСЂСѓ /info_topic", show_alert=True)
        return

    issuer_profile = _ensure_profile(context, str(update.effective_user.id), update.effective_user.username or f"id{update.effective_user.id}")
    if int(issuer_profile.get("admin_level", 0) or 0) < 3:
        await update.callback_query.answer("РќРµРґРѕСЃС‚Р°С‚РѕС‡РЅРѕ РїСЂР°РІ", show_alert=True)
        return

    panel_text = str(panel.get("panel_text") or "вњ…РўРµРјР° СЃРЅРѕРІР° Р°РєС‚СѓР°Р»СЊРЅР°СЏ.")

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
        await update.callback_query.answer("РџР°РЅРµР»СЊ СѓСЃС‚Р°СЂРµР»Р°", show_alert=True)
        return

    if str(update.effective_user.id) != str(session.get("issuer_user_id")):
        await update.callback_query.answer("РљРЅРѕРїРєР° РґРѕСЃС‚СѓРїРЅР° С‚РѕР»СЊРєРѕ Р°РІС‚РѕСЂСѓ /astats", show_alert=True)
        return

    issuer_profile = _ensure_profile(
        context,
        str(update.effective_user.id),
        update.effective_user.username or f"id{update.effective_user.id}",
    )
    if int(issuer_profile.get("admin_level", 0) or 0) < 4:
        await update.callback_query.answer("РќРµРґРѕСЃС‚Р°С‚РѕС‡РЅРѕ РїСЂР°РІ", show_alert=True)
        return

    try:
        await update.callback_query.message.edit_text(
            "в•пёЏР РµРґР°РєС‚РѕСЂ СЃРјРµРЅС‹ РїРѕР»Р° Р°РґРјРёРЅРёСЃС‚СЂР°С‚РѕСЂСѓ",
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
        await update.callback_query.answer("РџР°РЅРµР»СЊ СѓСЃС‚Р°СЂРµР»Р°", show_alert=True)
        return

    if str(update.effective_user.id) != str(session.get("issuer_user_id")):
        await update.callback_query.answer("РљРЅРѕРїРєР° РґРѕСЃС‚СѓРїРЅР° С‚РѕР»СЊРєРѕ Р°РІС‚РѕСЂСѓ /astats", show_alert=True)
        return

    issuer_profile = _ensure_profile(
        context,
        str(update.effective_user.id),
        update.effective_user.username or f"id{update.effective_user.id}",
    )
    if int(issuer_profile.get("admin_level", 0) or 0) < 4:
        await update.callback_query.answer("РќРµРґРѕСЃС‚Р°С‚РѕС‡РЅРѕ РїСЂР°РІ", show_alert=True)
        return

    target_user_id = str(session.get("target_user_id"))
    target_profile = _ensure_profile(context, target_user_id, f"id{target_user_id}")
    admin_gender = "рџ™ЋвЂЌв™‚пёЏРњР°Р»СЊС‡РёРє" if gender_key == "male" else "рџ™ЌвЂЌв™ЂпёЏР”РµРІРѕС‡РєР°"
    target_profile["admin_gender"] = admin_gender
    _save_profile_record(context, target_user_id)

    try:
        await context.bot.send_message(
            chat_id=LOG_CHAT_ID,
            text=(
                f"вњ…РЎРјРµРЅР° РїРѕР»Р° РІС‹РїРѕР»РЅРµРЅР°: id_profile #{target_profile.get('id_profile')} -> {admin_gender}. "
                f"РРЅРёС†РёР°С‚РѕСЂ: id{update.effective_user.id}"
            ),
        )
    except Exception:
        pass

    try:
        await context.bot.send_message(
            chat_id=int(target_user_id),
            text=f"рџ“ЌР’Р°С€ РїРѕР» Р±С‹Р» РёР·РјРµРЅРµРЅ. РўРµРїРµСЂСЊ РІС‹ {admin_gender}",
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
    await update.callback_query.answer("РћС‚РјРµРЅР°")
    data = update.callback_query.data or ""
    parts = data.split("_")
    if len(parts) < 4:
        return
    session_id = parts[3]

    sessions = context.application.bot_data.setdefault("astats_tag_sessions", {})
    session = sessions.get(session_id)
    if not session:
        await update.callback_query.answer("РџР°РЅРµР»СЊ СѓСЃС‚Р°СЂРµР»Р°", show_alert=True)
        return

    if str(update.effective_user.id) != str(session.get("issuer_user_id")):
        await update.callback_query.answer("РљРЅРѕРїРєР° РґРѕСЃС‚СѓРїРЅР° С‚РѕР»СЊРєРѕ Р°РІС‚РѕСЂСѓ /astats", show_alert=True)
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
    await update.callback_query.answer("РћС‚РјРµРЅР°")
    data = update.callback_query.data or ""
    parts = data.split("_")
    if len(parts) < 4:
        return
    session_id = parts[3]

    sessions = context.application.bot_data.setdefault("astats_tag_sessions", {})
    session = sessions.get(session_id)
    if not session:
        await update.callback_query.answer("РџР°РЅРµР»СЊ СѓСЃС‚Р°СЂРµР»Р°", show_alert=True)
        return

    if str(update.effective_user.id) != str(session.get("issuer_user_id")):
        await update.callback_query.answer("РљРЅРѕРїРєР° РґРѕСЃС‚СѓРїРЅР° С‚РѕР»СЊРєРѕ Р°РІС‚РѕСЂСѓ /astats", show_alert=True)
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
    await update.callback_query.answer("РћС‚РјРµРЅР°")
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
        await update.callback_query.answer("РљРЅРѕРїРєР° РґРѕСЃС‚СѓРїРЅР° С‚РѕР»СЊРєРѕ Р°РІС‚РѕСЂСѓ /astats", show_alert=True)
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
        await update.callback_query.answer("РљРЅРѕРїРєР° РґРѕСЃС‚СѓРїРЅР° С‚РѕР»СЊРєРѕ РІР»Р°РґРµР»СЊС†Сѓ РїСЂРѕС„РёР»СЏ", show_alert=True)
        return

    profile = _ensure_profile(context, target_user_id, f"id{target_user_id}")
    biography_text = str(profile.get("biography_admin") or "РЅРµ Р·Р°РїРѕР»РЅРµРЅР°")
    await update.callback_query.message.reply_text(f"вєпёЏР’Р°С€Р° РЅРѕРІР°СЏ Р±РёРѕРіСЂР°С„РёСЏ: {biography_text}")



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
            await update.message.reply_text("Р­С‚Сѓ СЃРјРµРЅСѓ С‚РµРіР° Р·Р°РїСѓСЃС‚РёР» РґСЂСѓРіРѕР№ Р°РґРјРёРЅРёСЃС‚СЂР°С‚РѕСЂ.")
            raise ApplicationHandlerStop
        return

    if str(session.get("status")) != "await_tag":
        return

    if int(session.get("chat_id", 0) or 0) != int(update.effective_chat.id):
        await update.message.reply_text("РћС‚РїСЂР°РІСЊС‚Рµ С‚РµРі РІ С‚РѕС‚ Р¶Рµ С‡Р°С‚, РіРґРµ Р±С‹Р»Р° РЅР°Р¶Р°С‚Р° РєРЅРѕРїРєР° СЃРјРµРЅС‹ С‚РµРіР°.")
        raise ApplicationHandlerStop

    if resolved_from != "reply" and not pending_by_admin.get(admin_user_id):
        await update.message.reply_text("РћС‚РІРµС‚СЊС‚Рµ РЅРѕРІС‹Рј С‚РµРіРѕРј РЅР° СЃРѕРѕР±С‰РµРЅРёРµ Р±РѕС‚Р° СЃ РїСЂРѕСЃСЊР±РѕР№ РѕС‚РїСЂР°РІРёС‚СЊ С‚РµРі.")
        raise ApplicationHandlerStop

    new_tag = (update.message.text or "").strip()
    if not new_tag.startswith("#"):
        await update.message.reply_text("РўРµРі РґРѕР»Р¶РµРЅ РЅР°С‡РёРЅР°С‚СЊСЃСЏ СЃ #.")
        raise ApplicationHandlerStop

    target_user_id = str(session.get("target_user_id"))
    target_profile = _ensure_profile(context, target_user_id, f"id{target_user_id}")
    old_tag = str(target_profile.get("tag_admin") or "РЅРµ СѓРєР°Р·Р°РЅ")
    target_profile["tag_admin_new"] = new_tag
    _save_profile_record(context, target_user_id)

    try:
        await context.bot.send_message(
            chat_id=int(target_user_id),
            text=f"рџ“ЌР’Р°С€ СѓРЅРёРєР°Р»СЊРЅС‹Р№ С‚РµРі {old_tag} Р±С‹Р» РёР·РјРµРЅРµРЅ РЅР° {new_tag}",
        )
    except Exception:
        pass

    target_profile["tag_admin"] = str(target_profile.get("tag_admin_new") or new_tag)
    target_profile.pop("tag_admin_new", None)
    _save_profile_record(context, target_user_id)

    await update.message.reply_text("вњ…Р—Р°РјРµРЅР° С‚РµРіР° СѓСЃРїРµС€РЅР°.")

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
        await update.message.reply_text("Р‘РёРѕРіСЂР°С„РёСЏ РЅРµ РґРѕР»Р¶РЅР° Р±С‹С‚СЊ РїСѓСЃС‚РѕР№.")
        raise ApplicationHandlerStop

    target_user_id = str(session.get("target_user_id"))
    target_profile = _ensure_profile(context, target_user_id, f"id{target_user_id}")
    target_profile["biography_admin"] = biography_text
    _save_profile_record(context, target_user_id)

    try:
        await context.bot.send_message(
            chat_id=int(target_user_id),
            text="рџ“ЌР’Р°С€Р° Р±РёРѕРіСЂР°С„РёСЏ Р±С‹Р»Р° РёР·РјРµРЅРµРЅР°.",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("рџ“–РџРѕСЃРјРѕС‚СЂРµС‚СЊ", callback_data=f"astats_bio_view_{target_user_id}")]]
            ),
        )
    except Exception:
        pass

    await update.message.reply_text("вњ…Р‘РёРѕРіСЂР°С„РёСЏ СѓСЃРїРµС€РЅРѕ РёР·РјРµРЅРµРЅР°.")

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



async def pm_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not _is_special_admin_chat(update.effective_chat.id):
        return

    issuer_profile = _ensure_profile(
        context,
        str(update.effective_user.id),
        update.effective_user.username or f"id{update.effective_user.id}",
    )
    if not _has_admin_rights_level_1_5(issuer_profile):
        await update.message.reply_text("РљРѕРјР°РЅРґР° РґРѕСЃС‚СѓРїРЅР° С‚РѕР»СЊРєРѕ Р°РґРјРёРЅРёСЃС‚СЂР°С‚РѕСЂР°Рј 1 РєР°С‚РµРіРѕСЂРёРё Рё РІС‹С€Рµ.")
        return

    raw_text = update.message.text or ""
    cmd_entity = update.message.entities[0] if update.message.entities else None
    cmd_len = cmd_entity.length if cmd_entity and cmd_entity.type == "bot_command" else len("/pm")
    args_text = raw_text[cmd_len:].strip()
    if not args_text:
        await update.message.reply_text('РСЃРїРѕР»СЊР·СѓР№С‚Рµ: /pm "id_profile" "С‚РµРєСЃС‚"')
        return

    try:
        parts = shlex.split(args_text)
    except ValueError:
        await update.message.reply_text('РќРµРєРѕСЂСЂРµРєС‚РЅС‹Р№ С„РѕСЂРјР°С‚. РСЃРїРѕР»СЊР·СѓР№С‚Рµ: /pm "id_profile" "С‚РµРєСЃС‚"')
        return

    if len(parts) < 2:
        await update.message.reply_text('РСЃРїРѕР»СЊР·СѓР№С‚Рµ: /pm "id_profile" "С‚РµРєСЃС‚"')
        return

    target_identifier = parts[0]
    dm_text = " ".join(parts[1:]).strip()
    if not dm_text:
        await update.message.reply_text("РўРµРєСЃС‚ СЃРѕРѕР±С‰РµРЅРёСЏ РЅРµ РґРѕР»Р¶РµРЅ Р±С‹С‚СЊ РїСѓСЃС‚С‹Рј.")
        return

    if _has_suspicious_username(dm_text):
        await update.message.reply_text("РћС‚РїСЂР°РІРєР° РѕС‚РєР»РѕРЅРµРЅР°: С‚РµРєСЃС‚ РЅРµ РґРѕР»Р¶РµРЅ СЃРѕРґРµСЂР¶Р°С‚СЊ @username РёР»Рё СЃСЃС‹Р»РєРё t.me.")
        return

    target_user_id, profile = _resolve_warn_target(context, target_identifier)
    if not profile:
        await update.message.reply_text(f'РџРѕР»СЊР·РѕРІР°С‚РµР»СЊ СЃ id_profile "{target_identifier}" РЅРµ РЅР°Р№РґРµРЅ.')
        return

    username = str(profile.get("username") or f"id{target_user_id}")
    try:
        await context.bot.send_message(chat_id=int(target_user_id), text=dm_text)
    except Forbidden:
        await update.message.reply_text(f'РќРµ СѓРґР°Р»РѕСЃСЊ РѕС‚РїСЂР°РІРёС‚СЊ Р›РЎ РїРѕР»СЊР·РѕРІР°С‚РµР»СЋ "{username}": РїРѕР»СЊР·РѕРІР°С‚РµР»СЊ Р·Р°Р±Р»РѕРєРёСЂРѕРІР°Р» Р±РѕС‚Р°.')
        return
    except Exception:
        await update.message.reply_text(f'РќРµ СѓРґР°Р»РѕСЃСЊ РѕС‚РїСЂР°РІРёС‚СЊ Р›РЎ РїРѕР»СЊР·РѕРІР°С‚РµР»СЋ "{username}".')
        return

    await update.message.reply_text(f'вњ…Р›РЎ СѓСЃРїРµС€РЅРѕ РѕС‚РїСЂР°РІР»РµРЅРѕ РїРѕР»СЊР·РѕРІР°С‚РµР»СЋ "{username}" (id_profile #{profile.get("id_profile")}).')



async def kus_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = getattr(update, "message", None)
    user = update.effective_user
    chat = update.effective_chat
    if message is None or not user or chat is None:
        return

    if chat.type == ChatType.PRIVATE:
        active = context.application.bot_data.get("active_chats", {}).get(str(user.id))
        if not active or not active.get("active"):
            await message.reply_text("РЈ РІР°СЃ РЅРµС‚ Р°РєС‚РёРІРЅРѕР№ РїРµСЂРµРїРёСЃРєРё.")
            raise ApplicationHandlerStop

        if active.get("paused") or active.get("management_paused"):
            await message.reply_text("РЎРµСЃСЃРёСЏ СЃРµР№С‡Р°СЃ РѕСЃС‚Р°РЅРѕРІР»РµРЅР°. РЎРЅР°С‡Р°Р»Р° РІРѕР·РѕР±РЅРѕРІРёС‚Рµ РѕР±С‰РµРЅРёРµ.")
            raise ApplicationHandlerStop

        admin_id = str(active.get("admin_id") or "")
        topic_id = active.get("topic_id")
        chat_id = active.get("chat_id")
        admin_profile = _ensure_profile(context, admin_id, active.get("admin_username") or f"id{admin_id}")
        user_profile = _ensure_profile(context, str(user.id), user.username or f"id{user.id}")

        admin_tag = str(admin_profile.get("tag_admin") or active.get("admin_tag") or active.get("admin_username") or f"id{admin_id}")
        username_pz = str(user_profile.get("username") or active.get("topic_base_name") or f"id{user.id}")

        try:
            await message.reply_text(f"Р’С‹ СѓРєСѓСЃРёР»Рё {admin_tag}")
        except Exception:
            pass

        if chat_id and topic_id is not None:
            try:
                await context.bot.send_message(
                    chat_id=chat_id,
                    message_thread_id=topic_id,
                    text=f"#RP рџ’•Р’Р°СЃ СѓРєСѓСЃРёР»(-Р°) {username_pz}",
                )
            except Exception:
                pass

        active["rp_topic"] = int(active.get("rp_topic", 0) or 0) + 1
        raise ApplicationHandlerStop

    if chat.id != WORK_CHAT_ID:
        return

    topic_id = getattr(message, "message_thread_id", None)
    if topic_id is None:
        await message.reply_text("РљРѕРјР°РЅРґР° РґРѕСЃС‚СѓРїРЅР° С‚РѕР»СЊРєРѕ РІ С‚РµРјРµ Р°РєС‚РёРІРЅРѕРіРѕ РґРёР°Р»РѕРіР°.")
        raise ApplicationHandlerStop

    topic_map = context.application.bot_data.get("topic_user_map", {}) or {}
    target_user_id = topic_map.get(topic_id) or topic_map.get(str(topic_id))
    active = (context.application.bot_data.get("active_chats", {}) or {}).get(str(target_user_id)) if target_user_id else None
    if not active or not active.get("active"):
        await message.reply_text("РЈ РІР°СЃ РЅРµС‚ Р°РєС‚РёРІРЅРѕР№ РїРµСЂРµРїРёСЃРєРё.")
        raise ApplicationHandlerStop

    if active.get("paused") or active.get("management_paused"):
        await message.reply_text("РЎРµСЃСЃРёСЏ СЃРµР№С‡Р°СЃ РѕСЃС‚Р°РЅРѕРІР»РµРЅР°. РЎРЅР°С‡Р°Р»Р° РІРѕР·РѕР±РЅРѕРІРёС‚Рµ РѕР±С‰РµРЅРёРµ.")
        raise ApplicationHandlerStop

    admin_profile = _ensure_profile(context, str(user.id), user.username or f"id{user.id}")
    target_profile = _ensure_profile(context, str(target_user_id), active.get("topic_base_name") or f"id{target_user_id}")

    admin_tag = str(admin_profile.get("tag_admin") or admin_profile.get("username") or f"id{user.id}")
    username_pz = str(target_profile.get("username") or active.get("topic_base_name") or f"id{target_user_id}")

    try:
        await message.reply_text(f"Р’С‹ СѓРєСѓСЃРёР»Рё {username_pz}")
    except Exception:
        pass

    try:
        await context.bot.send_message(
            chat_id=int(target_user_id),
            text=f"Р’Р°СЃ СѓРєСѓСЃРёР»(-Р°) {admin_tag}",
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
        await update.message.reply_text("РљРѕРјР°РЅРґР° РґРѕСЃС‚СѓРїРЅР° С‚РѕР»СЊРєРѕ Р°РґРјРёРЅРёСЃС‚СЂР°С‚РѕСЂР°Рј 5 РєР°С‚РµРіРѕСЂРёРё.")
        return

    raw_text = update.message.text or ""
    cmd_entity = update.message.entities[0] if update.message.entities else None
    cmd_len = cmd_entity.length if cmd_entity and cmd_entity.type == "bot_command" else len("/setprefix")
    args_text = raw_text[cmd_len:].strip()
    if not args_text:
        await update.message.reply_text('РСЃРїРѕР»СЊР·СѓР№С‚Рµ: /setprefix "id_profile"')
        return

    try:
        parts = shlex.split(args_text)
    except ValueError:
        await update.message.reply_text('РќРµРєРѕСЂСЂРµРєС‚РЅС‹Р№ С„РѕСЂРјР°С‚. РСЃРїРѕР»СЊР·СѓР№С‚Рµ: /setprefix "id_profile"')
        return

    if len(parts) < 1:
        await update.message.reply_text('РСЃРїРѕР»СЊР·СѓР№С‚Рµ: /setprefix "id_profile"')
        return

    target_identifier = parts[0]
    target_user_id, profile = _resolve_warn_target(context, target_identifier)
    if not profile:
        await update.message.reply_text(f'РџРѕР»СЊР·РѕРІР°С‚РµР»СЊ СЃ id_profile "{target_identifier}" РЅРµ РЅР°Р№РґРµРЅ.')
        return
    if not _has_admin_rights_level_1_5(profile):
        await update.message.reply_text(
            f'РќРµР»СЊР·СЏ РѕС‚РєСЂС‹С‚СЊ РїР°РЅРµР»СЊ: id_profile #{profile.get("id_profile")} РЅРµ РёРјРµРµС‚ Р°РґРјРёРЅ-РїСЂР°РІ 1-5 СѓСЂРѕРІРЅСЏ.'
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
        f"в•пёЏРћС‚РєСЂС‹С‚Р° РїР°РЅРµР»СЊ СЂРµРґР°РєС‚РёСЂРѕРІР°РЅРёСЏ РїСЂРµС„РёРєСЃР° С‡РµР»РѕРІРµРєР° {target_id_profile}",
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
        await update.callback_query.answer("РџР°РЅРµР»СЊ СЂРµРґР°РєС‚РёСЂРѕРІР°РЅРёСЏ СѓСЃС‚Р°СЂРµР»Р°", show_alert=True)
        return

    if str(update.effective_user.id) != str(panel.get("issuer_user_id")):
        await update.callback_query.answer("РљРЅРѕРїРєР° РґРѕСЃС‚СѓРїРЅР° С‚РѕР»СЊРєРѕ Р°РІС‚РѕСЂСѓ РїР°РЅРµР»Рё", show_alert=True)
        return

    issuer_profile = _ensure_profile(
        context,
        str(update.effective_user.id),
        update.effective_user.username or f"id{update.effective_user.id}",
    )
    if int(issuer_profile.get("admin_level", 0) or 0) != 5:
        await update.callback_query.answer("РќРµРґРѕСЃС‚Р°С‚РѕС‡РЅРѕ РїСЂР°РІ", show_alert=True)
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
        await update.callback_query.answer("РџР°РЅРµР»СЊ СЂРµРґР°РєС‚РёСЂРѕРІР°РЅРёСЏ СѓСЃС‚Р°СЂРµР»Р°", show_alert=True)
        return

    if str(update.effective_user.id) != str(panel.get("issuer_user_id")):
        await update.callback_query.answer("РљРЅРѕРїРєР° РґРѕСЃС‚СѓРїРЅР° С‚РѕР»СЊРєРѕ Р°РІС‚РѕСЂСѓ РїР°РЅРµР»Рё", show_alert=True)
        return

    issuer_profile = _ensure_profile(
        context,
        str(update.effective_user.id),
        update.effective_user.username or f"id{update.effective_user.id}",
    )
    if int(issuer_profile.get("admin_level", 0) or 0) != 5:
        await update.callback_query.answer("РќРµРґРѕСЃС‚Р°С‚РѕС‡РЅРѕ РїСЂР°РІ", show_alert=True)
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
    prefix_text = str(target_profile.get("prefix") or "РЅРµ СѓСЃС‚Р°РЅРѕРІР»РµРЅ")
    try:
        await update.callback_query.message.edit_text(
            f"вњ…РџСЂРµС„РёРєСЃ РґР»СЏ id_profile #{target_id_profile} РїСЂРёРјРµРЅРµРЅ: {prefix_text}"
        )
    except Exception:
        pass

    try:
        await context.bot.send_message(
            chat_id=int(target_user_id),
            text=f"вњ…Р’Р°С€ Р°РґРјРёРЅ-РїСЂРµС„РёРєСЃ Р±С‹Р» РѕС‚СЂРµРґР°РєС‚РёСЂРѕРІР°РЅ. РўРµРїРµСЂСЊ РІР°С€ РїСЂРµС„РёРєСЃ: {prefix_text}",
        )
    except Exception:
        pass

    pending_panels.pop(panel_id, None)



async def unwarn_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != LOG_CHAT_ID or not update.message:
        return

    if not _can_use_moderation_commands(context, str(update.effective_user.id)):
        await update.message.reply_text("РљРѕРјР°РЅРґР° РґРѕСЃС‚СѓРїРЅР° С‚РѕР»СЊРєРѕ Р°РґРјРёРЅРёСЃС‚СЂР°С‚РѕСЂР°Рј 2 РєР°С‚РµРіРѕСЂРёРё Рё РІС‹С€Рµ.")
        return

    raw_text = update.message.text or ""
    cmd_entity = update.message.entities[0] if update.message.entities else None
    cmd_len = cmd_entity.length if cmd_entity and cmd_entity.type == "bot_command" else len("/unwarn")
    args_text = raw_text[cmd_len:].strip()
    if not args_text:
        await update.message.reply_text('РЈРєР°Р¶РёС‚Рµ id_profile: /unwarn "id_profile"')
        return

    try:
        parts = shlex.split(args_text)
    except ValueError:
        await update.message.reply_text('РќРµРєРѕСЂСЂРµРєС‚РЅС‹Р№ С„РѕСЂРјР°С‚. РСЃРїРѕР»СЊР·СѓР№С‚Рµ: /unwarn "id_profile"')
        return

    if len(parts) < 1:
        await update.message.reply_text('РЈРєР°Р¶РёС‚Рµ id_profile: /unwarn "id_profile"')
        return

    target_identifier = parts[0]
    target_user_id, profile = _resolve_warn_target(context, target_identifier)
    if not profile:
        await update.message.reply_text(f'РџРѕР»СЊР·РѕРІР°С‚РµР»СЊ СЃ id_profile "{target_identifier}" РЅРµ РЅР°Р№РґРµРЅ.')
        return

    current_warn = int(profile.get("warn", 0) or 0)
    if current_warn <= 0:
        await update.message.reply_text(f'РЈ РїРѕР»СЊР·РѕРІР°С‚РµР»СЏ id_profile #{profile.get("id_profile")} РЅРµС‚ РїСЂРµРґСѓРїСЂРµР¶РґРµРЅРёР№ РґР»СЏ СЃРЅСЏС‚РёСЏ.')
        return

    profile["warn"] = current_warn - 1
    if profile["warn"] <= 0:
        profile["reason"] = "РЅРµС‚ РїСЂРёС‡РёРЅ"
    _save_profile_record(context, str(target_user_id))

    await update.message.reply_text(
        f'вњ…РџРѕР»СЊР·РѕРІР°С‚РµР»СЋ id_profile #{profile.get("id_profile")} СЃРЅСЏС‚Рѕ РїСЂРµРґСѓРїСЂРµР¶РґРµРЅРёРµ. РўРµРїРµСЂСЊ Сѓ РЅРµРіРѕ {profile["warn"]} warn(-РѕРІ).'
    )

    try:
        await context.bot.send_message(
            chat_id=int(target_user_id),
            text=f'вњ…РЎ Р’Р°СЃ СЃРЅСЏР»Рё РїСЂРµРґСѓРїСЂРµР¶РґРµРЅРёРµ. РўРµРїРµСЂСЊ Сѓ РІР°СЃ {profile["warn"]} РїСЂРµРґСѓРїСЂРµР¶РґРµРЅРёР№',
        )
    except Exception:
        pass



async def ban_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not _is_special_admin_chat(update.effective_chat.id):
        return

    if not _can_use_moderation_commands(context, str(update.effective_user.id)):
        await update.message.reply_text("РљРѕРјР°РЅРґР° РґРѕСЃС‚СѓРїРЅР° С‚РѕР»СЊРєРѕ Р°РґРјРёРЅРёСЃС‚СЂР°С‚РѕСЂР°Рј 2 РєР°С‚РµРіРѕСЂРёРё Рё РІС‹С€Рµ.")
        return

    raw_text = update.message.text or ""
    cmd_entity = update.message.entities[0] if update.message.entities else None
    cmd_len = cmd_entity.length if cmd_entity and cmd_entity.type == "bot_command" else len("/ban")
    args_text = raw_text[cmd_len:].strip()
    if not args_text:
        await update.message.reply_text('РЈРєР°Р¶РёС‚Рµ id_profile Рё РїСЂРёС‡РёРЅСѓ: /ban "id_profile" "reason"')
        return

    try:
        parts = shlex.split(args_text)
    except ValueError:
        await update.message.reply_text('РќРµРєРѕСЂСЂРµРєС‚РЅС‹Р№ С„РѕСЂРјР°С‚. РСЃРїРѕР»СЊР·СѓР№С‚Рµ: /ban "id_profile" "reason"')
        return

    if len(parts) < 2:
        await update.message.reply_text('РЈРєР°Р¶РёС‚Рµ id_profile Рё РїСЂРёС‡РёРЅСѓ: /ban "id_profile" "reason"')
        return

    target_identifier = parts[0]
    reason = " ".join(parts[1:]).strip()
    if not reason:
        await update.message.reply_text("РЈРєР°Р¶РёС‚Рµ РїСЂРёС‡РёРЅСѓ Р±Р»РѕРєРёСЂРѕРІРєРё.")
        return

    target_user_id, profile = _resolve_ban_target(context, target_identifier)
    if not profile:
        await update.message.reply_text(f'РџРѕР»СЊР·РѕРІР°С‚РµР»СЊ СЃ id_profile "{target_identifier}" РЅРµ РЅР°Р№РґРµРЅ.')
        return

    if _has_admin_rights_level_1_5(profile) or _has_admin_ban_immunity(profile):
        await update.message.reply_text(
            f'РќРµРІРѕР·РјРѕР¶РЅРѕ РІС‹РґР°С‚СЊ Р±Р°РЅ: id_profile #{profile.get("id_profile")} РёРјРµРµС‚ Р°РґРјРёРЅ-РїСЂР°РІР° 1-5 СѓСЂРѕРІРЅСЏ.'
        )
        return

    await _apply_ban(context, str(target_user_id), profile.get("username"), reason, "BAN")
    await update.message.reply_text(
        f'в›”РџРѕР»СЊР·РѕРІР°С‚РµР»СЊ id_profile #{profile.get("id_profile")} Р·Р°Р±Р»РѕРєРёСЂРѕРІР°РЅ. РџСЂРёС‡РёРЅР°: "{reason}"'
    )



async def unban_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not _is_special_admin_chat(update.effective_chat.id):
        return

    if not _can_use_moderation_commands(context, str(update.effective_user.id)):
        await update.message.reply_text("РљРѕРјР°РЅРґР° РґРѕСЃС‚СѓРїРЅР° С‚РѕР»СЊРєРѕ Р°РґРјРёРЅРёСЃС‚СЂР°С‚РѕСЂР°Рј 2 РєР°С‚РµРіРѕСЂРёРё Рё РІС‹С€Рµ.")
        return

    raw_text = update.message.text or ""
    cmd_entity = update.message.entities[0] if update.message.entities else None
    cmd_len = cmd_entity.length if cmd_entity and cmd_entity.type == "bot_command" else len("/unban")
    args_text = raw_text[cmd_len:].strip()
    if not args_text:
        await update.message.reply_text('РЈРєР°Р¶РёС‚Рµ id_profile: /unban "id_profile"')
        return

    try:
        parts = shlex.split(args_text)
    except ValueError:
        await update.message.reply_text('РќРµРєРѕСЂСЂРµРєС‚РЅС‹Р№ С„РѕСЂРјР°С‚. РСЃРїРѕР»СЊР·СѓР№С‚Рµ: /unban "id_profile"')
        return

    if len(parts) < 1:
        await update.message.reply_text('РЈРєР°Р¶РёС‚Рµ id_profile: /unban "id_profile"')
        return

    target_identifier = parts[0]
    target_user_id, profile = _resolve_ban_target(context, target_identifier)
    if not profile:
        await update.message.reply_text(f'РџРѕР»СЊР·РѕРІР°С‚РµР»СЊ СЃ id_profile "{target_identifier}" РЅРµ РЅР°Р№РґРµРЅ.')
        return

    banned_users = context.application.bot_data.setdefault("banned_users", {})
    if not banned_users.pop(str(target_user_id), None):
        await update.message.reply_text(f'РџРѕР»СЊР·РѕРІР°С‚РµР»СЊ id_profile #{profile.get("id_profile")} РЅРµ РЅР°С…РѕРґРёС‚СЃСЏ РІ Р±Р°РЅРµ.')
        return

    await update.message.reply_text(f'вњ…РџРѕР»СЊР·РѕРІР°С‚РµР»СЊ id_profile #{profile.get("id_profile")} СЂР°Р·Р±Р°РЅРµРЅ.')

    try:
        await context.bot.send_message(
            chat_id=int(target_user_id),
            text="вњ…РЎ РІР°С€РµР№ СѓС‡РµС‚РЅРѕР№ Р·Р°РїРёСЃРё СЃРЅСЏС‚Р° Р±Р»РѕРєРёСЂРѕРІРєР°. Р¤СѓРЅРєС†РёРё Р±РѕС‚Р° СЃРЅРѕРІР° РґРѕСЃС‚СѓРїРЅС‹.",
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
        await update.callback_query.answer("Р”РµР№СЃС‚РІРёРµ РґРѕСЃС‚СѓРїРЅРѕ С‚РѕР»СЊРєРѕ Р°РґРјРёРЅРёСЃС‚СЂР°С‚РѕСЂР°Рј 1 РєР°С‚РµРіРѕСЂРёРё Рё РІС‹С€Рµ.", show_alert=True)
        return
    await update.callback_query.answer("РџРѕР»СЊР·РѕРІР°С‚РµР»СЊ РїСЂРёРЅСЏС‚")
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
        f"рџ“ЉР’С‹ РЅР°С…РѕРґРёС‚РµСЃСЊ РІ РїРµСЂРµРїРёСЃРєРµ РЅР° С‚РµРјСѓ {mood}\n"
        f"РРјСЏ РїРѕР»СЊР·РѕРІР°С‚РµР»СЏ: {username}\n"
        f"РђР№РґРё РїСЂРѕС„РёР»СЏ: {requester_id_profile}\n"
        f"РђРґРјРёРЅРёСЃС‚СЂР°С‚РѕСЂ {admin_username}"
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
        "в•­в”Ђ вќЂ рќ“ўрќ”‚рќ“јрќ“Ѕрќ“®рќ“¶ в”Ђв•®\n\n"
        f'вњ… "{admin_tag}" РїСЂРёРЅСЏР» РІР°С€ Р·Р°РїСЂРѕСЃ.\n\n'
        "рџ’­ РђРґРјРёРЅРёСЃС‚СЂР°С‚РѕСЂ СѓР¶Рµ РїРѕРґРєР»СЋС‡Р°РµС‚СЃСЏ Рє С‡Р°С‚Сѓ Рё СЃРѕРІСЃРµРј СЃРєРѕСЂРѕ РЅР°С‡РЅС‘С‚ РґРёР°Р»РѕРі СЃ РІР°РјРё.\n\n"
        "рџ“Ё Р’СЃСЏ СѓРєР°Р·Р°РЅРЅР°СЏ РІР°РјРё РёРЅС„РѕСЂРјР°С†РёСЏ СѓР¶Рµ РїРµСЂРµРґР°РЅР° РµРјСѓ, РїРѕСЌС‚РѕРјСѓ РѕРЅ РЅРµРјРЅРѕРіРѕ Р·РЅР°РєРѕРј СЃ РІР°С€РµР№ СЃРёС‚СѓР°С†РёРµР№.\n\n"
        "вЏі РџРѕР¶Р°Р»СѓР№СЃС‚Р°, РѕСЃС‚Р°РІР°Р№С‚РµСЃСЊ РІ С‡Р°С‚Рµ.\n\n"
        "рџ’Њ Р•СЃР»Рё РѕР¶РёРґР°РЅРёРµ РЅРµРјРЅРѕРіРѕ Р·Р°С‚СЏРЅРµС‚СЃСЏ вЂ” РїСЂРѕСЃС‚Рѕ РѕС‚РїСЂР°РІСЊС‚Рµ Р»СЋР±РѕРµ СЃРѕРѕР±С‰РµРЅРёРµ. РђРґРјРёРЅРёСЃС‚СЂР°С‚РѕСЂ РѕР±СЏР·Р°С‚РµР»СЊРЅРѕ РѕС‚РІРµС‚РёС‚.\n\n"
        "в•°в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв•Ї"
    )
    user_text_2 = (
        "в•­в”Ђ рџ“Њ рќ“рќ“·рќ“Їрќ“ё в”Ђв•®\n\n"
        "вљ™пёЏ Р’СЂРµРјРµРЅРЅРѕ РЅРµРґРѕСЃС‚СѓРїРЅР° РїРµСЂРµСЃС‹Р»РєР°:\n\n"
        "вЂў рџ“· Р¤РѕС‚Рѕ\n"
        "вЂў рџЋҐ Р’РёРґРµРѕ\n"
        "вЂў рџЋћ GIF\n"
        "вЂў рџЉ РЎС‚РёРєРµСЂРѕРІ\n"
        "вЂў рџ“Ѓ Р¤Р°Р№Р»РѕРІ\n\n"
        "Р­С‚Рѕ СЃРІСЏР·Р°РЅРѕ СЃ С‚РµС…РЅРёС‡РµСЃРєРѕР№ РѕС€РёР±РєРѕР№ РЅР° СЃС‚РѕСЂРѕРЅРµ СЃРµСЂРІРµСЂР°.\n\n"
        "рџ›  РњС‹ СѓР¶Рµ Р·Р°РЅРёРјР°РµРјСЃСЏ РµС‘ СѓСЃС‚СЂР°РЅРµРЅРёРµРј. РЎРїР°СЃРёР±Рѕ Р·Р° С‚РµСЂРїРµРЅРёРµ!\n\n"
        "в•°в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв•Ї"
    )
    bio_button = InlineKeyboardMarkup(
        [[InlineKeyboardButton("рџ”­Р‘РёРѕРіСЂР°С„РёСЏ Р°РґРјРёРЅР°", callback_data=f"show_admin_bio_{request_user_id}")]]
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
            [[KeyboardButton("рџ¤§РћС‚РєР°Р·Р°С‚СЊСЃСЏ РѕС‚ Р°РґРјРёРЅР°"), KeyboardButton("рџ’¤РџСЂРёРѕСЃС‚Р°РЅРѕРІРёС‚СЊ РѕР±С‰РµРЅРёРµ")]],
            resize_keyboard=True,
            one_time_keyboard=False,
        )
        await context.bot.send_message(chat_id=requester_chat_id, text="Р•СЃР»Рё РІР°Рј Р°РґРјРёРЅРёСЃС‚СЂР°С‚РѕСЂ РЅРµ РїРѕРЅСЂР°РІРёР»СЃСЏ, С‚Рѕ РІС‹ РјРѕР¶РµС‚Рµ РѕС‚РјРµРЅРёС‚СЊ РµРіРѕ РєРЅРѕРїРєРѕР№ РЅРёР¶Рµ.", reply_markup=kb)
    except Exception:
        pass

    _save_runtime_snapshot(context)



async def show_user_profile_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _can_use_moderation_commands(context, str(update.effective_user.id)):
        await update.callback_query.answer("Р”РµР№СЃС‚РІРёРµ РґРѕСЃС‚СѓРїРЅРѕ С‚РѕР»СЊРєРѕ Р°РґРјРёРЅРёСЃС‚СЂР°С‚РѕСЂР°Рј 2 РєР°С‚РµРіРѕСЂРёРё Рё РІС‹С€Рµ.", show_alert=True)
        return

    await update.callback_query.answer()
    data = update.callback_query.data or ""
    parts = data.split("_")
    if len(parts) < 4:
        return

    request_user_id = parts[3]
    active = (context.application.bot_data.get("active_chats", {}) or {}).get(str(request_user_id))
    if not active or not active.get("active"):
        await update.callback_query.answer("РўРµРјР° Р±РѕР»СЊС€Рµ РЅРµР°РєС‚СѓР°Р»СЊРЅР°", show_alert=True)
        return

    topic_id = getattr(update.callback_query.message, "message_thread_id", None)
    active_topic_id = active.get("topic_id")
    if topic_id is not None and active_topic_id is not None and str(topic_id) != str(active_topic_id):
        await update.callback_query.answer("РўРµРјР° Р±РѕР»СЊС€Рµ РЅРµР°РєС‚СѓР°Р»СЊРЅР°", show_alert=True)
        return

    profile = _ensure_profile(context, str(request_user_id), active.get("topic_base_name") or f"id{request_user_id}")
    await update.callback_query.message.reply_text(_build_user_stats_text(context, str(request_user_id), profile))



async def warn_user_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _can_use_moderation_commands(context, str(update.effective_user.id)):
        await update.callback_query.answer("Р”РµР№СЃС‚РІРёРµ РґРѕСЃС‚СѓРїРЅРѕ С‚РѕР»СЊРєРѕ Р°РґРјРёРЅРёСЃС‚СЂР°С‚РѕСЂР°Рј 2 РєР°С‚РµРіРѕСЂРёРё Рё РІС‹С€Рµ.", show_alert=True)
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
        await update.callback_query.answer("РўРµРјР° Р±РѕР»СЊС€Рµ РЅРµР°РєС‚СѓР°Р»СЊРЅР°", show_alert=True)
        return
    active_topic_id = active.get("topic_id")
    if topic_id is not None and active_topic_id is not None and str(topic_id) != str(active_topic_id):
        await update.callback_query.answer("РўРµРјР° Р±РѕР»СЊС€Рµ РЅРµР°РєС‚СѓР°Р»СЊРЅР°", show_alert=True)
        return

    app_requests = context.application.bot_data.setdefault("admin_requests", {})
    req_info = app_requests.get(str(request_user_id))
    username = req_info.get("username") if req_info else f"id{request_user_id}"

    thread_id = getattr(update.callback_query.message, "message_thread_id", None)
    prompt_text = f'Р’С‹ СЃРѕР±РёСЂР°РµС‚РµСЃСЊ РїСЂРµРґСѓРїСЂРµРґРёС‚СЊ СЃРІРѕРµРіРѕ РїРѕР»СЊР·РѕРІР°С‚РµР»СЏ "{username}"?'
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("рџ”Ѓ РћС‚РјРµРЅР°", callback_data=f"cancel_warn_{request_user_id}"),
                InlineKeyboardButton("Р’С‹РґР°С‚СЊ РїСЂРµРґСѓРїСЂРµР¶РґРµРЅРёРµ", callback_data=f"confirm_warn_{request_user_id}"),
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
    await update.callback_query.answer("РћС‚РјРµРЅР°")
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
        "рџ“ЊРЈРєР°Р¶РёС‚Рµ РїСЂРёС‡РёРЅСѓ РІС‹РґР°С‡Рё РїСЂРµРґСѓРїСЂРµР¶РґРµРЅРёСЏ РїРѕР»СЊР·РѕРІР°С‚РµР»СЋ (РїСѓРЅРєС‚РѕРј)\n"
        "РџСЂР°РІРёР»Р° РјРѕР¶РЅРѕ РїРѕСЃРјРѕС‚СЂРµС‚СЊ Р·РґРµСЃСЊ https://t.me/c/4417963273/209"
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

    reason = getattr(update.message, "text", None) or getattr(update.message, "caption", None) or "(Р±РµР· РїСЂРёС‡РёРЅС‹)"
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
                    text="вќЊРЈ СЌС‚РѕРіРѕ РїРѕР»СЊР·РѕРІР°С‚РµР»СЏ Р±РѕР»СЊС€Рµ 3 РїСЂРµРґСѓРїСЂРµР¶РґРµРЅРёР№, Р±РѕР»СЊС€Рµ РІС‹РґР°С‚СЊ РЅРµР»СЊР·СЏ.",
                )
            else:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text="вќЊРЈ СЌС‚РѕРіРѕ РїРѕР»СЊР·РѕРІР°С‚РµР»СЏ Р±РѕР»СЊС€Рµ 3 РїСЂРµРґСѓРїСЂРµР¶РґРµРЅРёР№, Р±РѕР»СЊС€Рµ РІС‹РґР°С‚СЊ РЅРµР»СЊР·СЏ.",
                )
        except Exception:
            pass
        pending_warns.pop(key, None)
        raise ApplicationHandlerStop

    profile["warn"] += 1
    profile["reason"] = reason
    await enforce_autoban_if_needed(context, str(request_user_id), username)

    success_text = (
        f'вњ…Р’С‹ СѓСЃРїРµС€РЅРѕ РІС‹РґР°Р»Рё РїСЂРµРґСѓРїСЂРµР¶РґРµРЅРёРµ РїРѕР»СЊР·РѕРІР°С‚РµР»СЋ "{username}" СЃ РїСЂРёС‡РёРЅРѕР№ "{reason}". '
        f'РўРµРїРµСЂСЊ Сѓ РЅРµРіРѕ {profile["warn"]} РїСЂРµРґСѓРїСЂРµР¶РґРµРЅРёР№'
    )
    try:
        if thread_id is not None:
            await context.bot.send_message(chat_id=chat_id, text=success_text, message_thread_id=thread_id)
        else:
            await context.bot.send_message(chat_id=chat_id, text=success_text)
    except Exception as e:
        logging.exception("handle_warn_reason_message success message failed: %s", e)

    user_text = (
        "в•­в”Ђ рџљЁ рќ“ќрќ“ёрќ“Ѕрќ“Ірќ“¬рќ“® в”Ђв•®\n\n"
        "вќ—пёЏР’С‹ РїРѕР»СѓС‡РёР»Рё РїСЂРµРґСѓРїСЂРµР¶РґРµРЅРёРµ РѕС‚ СЃРІРѕРµРіРѕ Р°РґРјРёРЅРёСЃС‚СЂР°С‚РѕСЂР°.\n\n"
        f"вњ¦ РџСЂРёС‡РёРЅР°: {reason}\n"
        f"вњ¦ Р’СЃРµРіРѕ РїСЂРµРґСѓРїСЂРµР¶РґРµРЅРёР№: {profile['warn']}\n\n"
        "в•°в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв•Ї"
    )
    try:
        await context.bot.send_message(chat_id=int(request_user_id), text=user_text)
    except Exception as e:
        logging.exception("handle_warn_reason_message notify user failed: %s", e)

    pending_warns.pop(key, None)
    _save_runtime_snapshot(context)
    raise ApplicationHandlerStop



async def admin_decline_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _can_use_moderation_commands(context, str(update.effective_user.id)):
        await update.callback_query.answer("Р”РµР№СЃС‚РІРёРµ РґРѕСЃС‚СѓРїРЅРѕ С‚РѕР»СЊРєРѕ Р°РґРјРёРЅРёСЃС‚СЂР°С‚РѕСЂР°Рј 2 РєР°С‚РµРіРѕСЂРёРё Рё РІС‹С€Рµ.", show_alert=True)
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
        await update.callback_query.answer("РўРµРјР° Р±РѕР»СЊС€Рµ РЅРµР°РєС‚СѓР°Р»СЊРЅР°", show_alert=True)
        return
    active_topic_id = active.get("topic_id")
    if topic_id is not None and active_topic_id is not None and str(topic_id) != str(active_topic_id):
        await update.callback_query.answer("РўРµРјР° Р±РѕР»СЊС€Рµ РЅРµР°РєС‚СѓР°Р»СЊРЅР°", show_alert=True)
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
                text="рџ’¬РџРѕР¶Р°Р»СѓР№СЃС‚Р°, СѓРєР°Р¶РёС‚Рµ РїСЂРёС‡РёРЅСѓ РїРѕС‡РµРјСѓ РІС‹ СЃРѕР±РёСЂР°РµС‚РµСЃСЊ РѕС‚РєР°Р·Р°С‚СЊСЃСЏ РѕС‚ РїРѕР»СЊР·РѕРІР°С‚РµР»СЏ",
            )
        else:
            prompt = await context.bot.send_message(
                chat_id=chat_id,
                text="рџ’¬РџРѕР¶Р°Р»СѓР№СЃС‚Р°, СѓРєР°Р¶РёС‚Рµ РїСЂРёС‡РёРЅСѓ РїРѕС‡РµРјСѓ РІС‹ СЃРѕР±РёСЂР°РµС‚РµСЃСЊ РѕС‚РєР°Р·Р°С‚СЊСЃСЏ РѕС‚ РїРѕР»СЊР·РѕРІР°С‚РµР»СЏ",
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
        await update.callback_query.answer("Р”РµР№СЃС‚РІРёРµ РґРѕСЃС‚СѓРїРЅРѕ С‚РѕР»СЊРєРѕ Р°РґРјРёРЅРёСЃС‚СЂР°С‚РѕСЂР°Рј 2 РєР°С‚РµРіРѕСЂРёРё Рё РІС‹С€Рµ.", show_alert=True)
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
        await update.callback_query.answer("РўРµРјР° Р±РѕР»СЊС€Рµ РЅРµР°РєС‚СѓР°Р»СЊРЅР°", show_alert=True)
        return
    req_topic_id = req_check.get("topic_id")
    if topic_id is not None and req_topic_id is not None and str(topic_id) != str(req_topic_id):
        await update.callback_query.answer("РўРµРјР° Р±РѕР»СЊС€Рµ РЅРµР°РєС‚СѓР°Р»СЊРЅР°", show_alert=True)
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
            [[InlineKeyboardButton("РѕС‚РјРµРЅР°", callback_data=f"cancel_decline_request_{request_user_id}")]]
        )
        if thread_id is not None:
            prompt = await context.bot.send_message(
                chat_id=chat_id,
                message_thread_id=thread_id,
                text="в„№пёЏР’РІРµРґРёС‚Рµ РїСЂРёС‡РёРЅСѓ С‡С‚РѕР±С‹ РѕС‚РєР°Р·Р°С‚СЊ Р·Р°РїСЂРѕСЃ РІ РїРѕРёСЃРєРµ Р°РґРјРёРЅР°",
                reply_markup=cancel_button,
            )
        else:
            prompt = await context.bot.send_message(
                chat_id=chat_id,
                text="в„№пёЏР’РІРµРґРёС‚Рµ РїСЂРёС‡РёРЅСѓ С‡С‚РѕР±С‹ РѕС‚РєР°Р·Р°С‚СЊ Р·Р°РїСЂРѕСЃ РІ РїРѕРёСЃРєРµ Р°РґРјРёРЅР°",
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
    await update.callback_query.answer("РћС‚РјРµРЅР°")
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
                    text="РўР°РєРѕРµ РЅРµР»СЊР·СЏ РЅР°Р·РІР°С‚СЊ РїСЂРёС‡РёРЅРѕР№. РћС‚РїСЂР°РІСЊС‚Рµ С‚РѕР»СЊРєРѕ С‚РµРєСЃС‚ Р±РµР· СЃСЃС‹Р»РѕРє.",
                )
            else:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text="РўР°РєРѕРµ РЅРµР»СЊР·СЏ РЅР°Р·РІР°С‚СЊ РїСЂРёС‡РёРЅРѕР№. РћС‚РїСЂР°РІСЊС‚Рµ С‚РѕР»СЊРєРѕ С‚РµРєСЃС‚ Р±РµР· СЃСЃС‹Р»РѕРє.",
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
                    name=f"{username} (Р—Р°РєСЂС‹С‚Рѕ СЃРёСЃС‚РµРјРѕР№)",
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
                    text="вњ…РџРѕР»СЊР·РѕРІР°С‚РµР»СЊ СѓСЃРїРµС€РЅРѕ РїРѕР»СѓС‡РёР» СѓРІРµРґРѕРјР»РµРЅРёРµ Рѕ РѕС‚РєР°Р·Рµ РІ РїРѕРёСЃРєРµ Р°РґРјРёРЅР°",
                )
            except Exception:
                pass
        else:
            try:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text="вњ…РџРѕР»СЊР·РѕРІР°С‚РµР»СЊ СѓСЃРїРµС€РЅРѕ РїРѕР»СѓС‡РёР» СѓРІРµРґРѕРјР»РµРЅРёРµ Рѕ РѕС‚РєР°Р·Рµ РІ РїРѕРёСЃРєРµ Р°РґРјРёРЅР°",
                )
            except Exception:
                pass

        try:
            await context.bot.send_message(
                chat_id=LOG_CHAT_ID,
                text=f'в­•пёЏРђРґРјРёРЅРёСЃС‚СЂР°С‚РѕСЂ "{pending.get("admin_username")}" Р·Р°РєСЂС‹Р» Р·Р°РїСЂРѕСЃ Рѕ РїРѕРёСЃРєРµ Р°РґРјРёРЅР° РїРѕР»СЊР·РѕРІР°С‚РµР»СЋ "{username}"',
            )
        except Exception:
            pass

        try:
            await context.bot.send_message(
                chat_id=int(request_user_id),
                text=(
                    "рџЊё **Р—Р°РїСЂРѕСЃ Р±С‹Р» РѕС‚РєР°Р·Р°РЅ**\n\n"
                    "Рљ СЃРѕР¶Р°Р»РµРЅРёСЋ, СЃРµР№С‡Р°СЃ РјС‹ РЅРµ РјРѕР¶РµРј РЅР°Р№С‚Рё РґР»СЏ Р’Р°СЃ Р°РґРјРёРЅРёСЃС‚СЂР°С‚РѕСЂР°.\n\n"
                    f'Р­С‚Рѕ РЅРµ СЃРІСЏР·Р°РЅРѕ СЃ Р’Р°РјРё Р»РёС‡РЅРѕ вЂ” РїСЂРѕСЃС‚Рѕ РІ РґР°РЅРЅС‹Р№ РјРѕРјРµРЅС‚ РІР°С€ Р·Р°РїСЂРѕСЃ Р±С‹Р» РѕС‚РєР»РѕРЅРµРЅ РїРѕ РїСЂРёС‡РёРЅРµ "{text_reason}"\n\n'
                    "РџРѕР¶Р°Р»СѓР№СЃС‚Р°, РїРѕРїСЂРѕР±СѓР№С‚Рµ СЃРЅРѕРІР° С‡РµСЂРµР· РЅРµРєРѕС‚РѕСЂРѕРµ РІСЂРµРјСЏ. Р’РѕР·РјРѕР¶РЅРѕ, С‡РµСЂРµР· С‡Р°СЃ РёР»Рё 2.\n\n"
                    "Р‘РµСЂРµРіРёС‚Рµ СЃРµР±СЏ. РњС‹ РїРѕРјРЅРёРј Рѕ Р’Р°СЃ! рџ’›"
                ),
                parse_mode=ParseMode.MARKDOWN,
            )
        except Exception as e:
            logging.exception("failed to notify user about declined search request: %s", e)

        try:
            kb = _build_main_menu_keyboard(context, int(request_user_id), profile.get("username"))
            await context.bot.send_message(chat_id=int(request_user_id), text="Р’С‹Р±РµСЂРёС‚Рµ РґРµР№СЃС‚РІРёРµ РІ РјРµРЅСЋ РЅРёР¶Рµ:", reply_markup=kb)
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
                text="вњ…Р’Р°С€ Р·Р°РїСЂРѕСЃ РЅР° РѕС‚РєР°Р· РѕС‚ РїРѕР»СЊР·РѕРІР°С‚РµР»СЏ Р±С‹Р» РѕС‚РїСЂР°РІР»РµРЅ СЃС‚Р°СЂС€РµР№ Р°РґРјРёРЅРёСЃС‚СЂР°С†РёРё, РѕР¶РёРґР°Р№С‚Рµ.",
            )
        else:
            await context.bot.send_message(
                chat_id=chat_id,
                text="вњ…Р’Р°С€ Р·Р°РїСЂРѕСЃ РЅР° РѕС‚РєР°Р· РѕС‚ РїРѕР»СЊР·РѕРІР°С‚РµР»СЏ Р±С‹Р» РѕС‚РїСЂР°РІР»РµРЅ СЃС‚Р°СЂС€РµР№ Р°РґРјРёРЅРёСЃС‚СЂР°С†РёРё, РѕР¶РёРґР°Р№С‚Рµ.",
            )
    except Exception:
        pass

    seq = context.application.bot_data.get("decline_review_seq", 0) + 1
    context.application.bot_data["decline_review_seq"] = seq
    review_id = str(seq)

    review_text = (
        f'вљ пёЏР—Р°РїСЂРѕСЃ РЅР° РѕС‚РєР°Р· РїРѕР»СЊР·РѕРІР°С‚РµР»СЏ РѕС‚ "{pending.get("admin_username")}"\n\n'
        f'РџСЂРёС€Р»Рѕ СѓРІРµРґРѕРјР»РµРЅРёРµ, С‡С‚Рѕ Р°РґРјРёРЅ "{pending.get("admin_username")}" С…РѕС‡РµС‚ РѕС‚РєР°Р·Р°С‚СЊСЃСЏ РѕС‚ СЃРІРѕРµРіРѕ РїРѕР»СЊР·РѕРІР°С‚РµР»СЏ "{pending.get("username")}" РїРѕ РїСЂРёС‡РёРЅРµ "{pending.get("reason_otkaz_ot_username")}".\n\n'
        "Р’С‹Р±РµСЂРёС‚Рµ РєРЅРѕРїРєСѓ РЅРёР¶Рµв¬‡пёЏ"
    )
    review_kb = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("рџџўРћРґРѕР±СЂРёС‚СЊ", callback_data=f"approve_decline_{review_id}"),
                InlineKeyboardButton("рџ”ґРћС‚РєР°Р·Р°С‚СЊ", callback_data=f"reject_decline_{review_id}"),
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

    approved_text = review.get("review_text", "") + "\n\nРћРґРѕР±СЂРµРЅРѕрџџ©"
    try:
        await update.callback_query.message.edit_text(approved_text)
    except Exception as e:
        logging.exception("approve_decline_callback edit failed: %s", e)

    chat_id = review.get("chat_id")
    thread_id = review.get("thread_id")
    try:
        msg = f'рџ’”Р—Р°РїСЂРѕСЃ РЅР° РѕС‚РєР°Р· РѕС‚ РїРѕР»СЊР·РѕРІР°С‚РµР»СЏ "{review.get("username")}" Р±С‹Р» СѓСЃРїРµС€РЅРѕ РѕРґРѕР±СЂРµРЅ СЂСѓРєРѕРІРѕРґСЃС‚РІРѕРј Р±РѕС‚Р°, РѕРЅ Р±РѕР»СЊС€Рµ РЅРµ Р±СѓРґРµС‚ РїРѕР»СѓС‡Р°С‚СЊ РѕС‚ РІР°СЃ СЃРѕРѕР±С‰РµРЅРёРµ.'
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
                name=f'{review.get("username") or f"id{user_id}"} (Р—Р°РєСЂС‹С‚Рѕ Р°РґРјРёРЅРѕРј)',
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
        "рџ™Џ **РђРґРјРёРЅРёСЃС‚СЂР°С‚РѕСЂ РѕС‚РєР°Р·Р°Р»СЃСЏ РѕС‚ РІР°СЃ.**\n\n"
        "Рљ СЃРѕР¶Р°Р»РµРЅРёСЋ, РЅР°С€ СЃРїРµС†РёР°Р»РёСЃС‚ РЅРµ СЃРјРѕРі РїСЂРѕРґРѕР»Р¶РёС‚СЊ РѕР±С‰РµРЅРёРµ СЃ Р’Р°РјРё.\n\n"
        "Р­С‚Рѕ СЃР»СѓС‡Р°РµС‚СЃСЏ РІ СЂР°Р±РѕС‚Рµ - РёРЅРѕРіРґР° РїСЂРѕСЃС‚Рѕ РЅРµ СЃРѕРІРїР°Р» С…Р°СЂР°РєС‚РµСЂ, РёР»Рё РІС‹ РЅР°СЂСѓС€Р°Р»Рё РїСЂР°РІРёР»Р° Р°РЅРѕРЅРёРјРЅРѕСЃС‚Рё РёР»Рё РґСЂСѓРіРѕРµ.\n\n"
        "РќРµ РїСЂРёРЅРёРјР°Р№С‚Рµ СЌС‚Рѕ РЅР° СЃРІРѕР№ СЃС‡С‘С‚. Р’С‹ вЂ” Р·Р°РјРµС‡Р°С‚РµР»СЊРЅС‹Р№ СЃРѕР±РµСЃРµРґРЅРёРє, Рё РјС‹ СѓРІРµСЂРµРЅС‹, С‡С‚Рѕ РґСЂСѓРіРѕР№ Р°РґРјРёРЅРёСЃС‚СЂР°С‚РѕСЂ СЃ СЂР°РґРѕСЃС‚СЊСЋ Р’Р°СЃ РїСЂРёРјРµС‚.\n\n"
        "Р’С‹ РІСЃРµ РµС‰Рµ РјРѕР¶РµС‚Рµ РЅР°Р№С‚Рё РґСЂСѓРіРѕРіРѕ Р°РґРјРёРЅРёСЃС‚СЂР°С‚РѕСЂР° РЅР° СЃРІРѕР№ РІРєСѓСЃ.\n\n"
        "вќ¤пёЏ РњС‹ РІСЃРµРіРґР° СЂСЏРґРѕРј."
    )
    try:
        kb = _build_main_menu_keyboard(context, int(user_id), review.get("username"))
        await context.bot.send_message(chat_id=int(user_id), text=user_text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)
        await context.bot.send_message(chat_id=int(user_id), text="Р’С‹Р±РµСЂРёС‚Рµ РґРµР№СЃС‚РІРёРµ РІ РјРµРЅСЋ РЅРёР¶Рµ:", reply_markup=kb)
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

    rejected_text = review.get("review_text", "") + "\n\nРћС‚РєР°Р·Р°РЅРѕрџџҐ"
    try:
        await update.callback_query.message.edit_text(rejected_text)
    except Exception as e:
        logging.exception("reject_decline_callback edit failed: %s", e)

    try:
        msg = f'вќЊР—Р°РїСЂРѕСЃ РЅР° РѕС‚РєР°Р· РѕС‚ РїРѕР»СЊР·РѕРІР°С‚РµР»СЏ "{review.get("username")}" РѕС‚РєР»РѕРЅРµРЅ СЂСѓРєРѕРІРѕРґСЃС‚РІРѕРј.'
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
        await context.bot.send_message(chat_id=chat.id, text="рџљ«РЇ РЅРµ РјРѕРіСѓ Р·РґРµСЃСЊ РЅР°С…РѕРґРёС‚СЊСЃСЏ, РїРѕРєРёРґР°СЋ С‡Р°С‚..")
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
                await update.message.reply_text("Р”РѕСЃС‚СѓРї Р·Р°РїСЂРµС‰С‘РЅ.")
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
            await update.message.reply_text("Р”Р° РјРѕР№ РІРµР»РёРєРёР№ РРІР°РЅСѓС€РєР°, РґР°РјРї РєР°СЂС‚ СѓСЃРїРµС€РЅРѕ С‚РµР±Рµ РІ Р»СЃ РѕС‚РїСЂР°РІРёР»")
        except Exception:
            try:
                await context.bot.send_message(chat_id=LOG_CHAT_ID, text=text)
                await update.message.reply_text(f"РќРµ СѓРґР°Р»РѕСЃСЊ РѕС‚РїСЂР°РІРёС‚СЊ Р»РёС‡РЅРѕРµ СЃРѕРѕР±С‰РµРЅРёРµ. Р”Р°РјРї РѕС‚РїСЂР°РІР»РµРЅ РІ Р»РѕРі-С‡Р°С‚ {LOG_CHAT_ID}.")
            except Exception:
                await update.message.reply_text("РќРµ СѓРґР°Р»РѕСЃСЊ РѕС‚РїСЂР°РІРёС‚СЊ РґР°РјРї РєР°СЂС‚ РЅРё РІ Р»РёС‡РєСѓ, РЅРё РІ Р»РѕРі-С‡Р°С‚.")
    except Exception as e:
        logging.exception("dump_maps_handler failed: %s", e)



async def add_rules_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != LOG_CHAT_ID:
        return
    if not update.effective_user or not _is_topic_admin(context, str(update.effective_user.id)):
        await update.message.reply_text("РљРѕРјР°РЅРґР° РґРѕСЃС‚СѓРїРЅР° С‚РѕР»СЊРєРѕ Р°РґРјРёРЅРёСЃС‚СЂР°С‚РѕСЂР°Рј 4 РєР°С‚РµРіРѕСЂРёРё Рё РІС‹С€Рµ.")
        return

    existing = context.application.bot_data.get("global_rules_text")
    if existing:
        await update.message.reply_text("РџСЂР°РІРёР»Р° СѓР¶Рµ СЃСѓС‰РµСЃС‚РІСѓСЋС‚, С‡С‚РѕР±С‹ РёС… СѓРґР°Р»РёС‚СЊ РЅР°РїРёС€РёС‚Рµ /delrules")
        return

    raw_text = update.message.text or ""
    cmd_entity = update.message.entities[0] if update.message.entities else None
    cmd_len = cmd_entity.length if cmd_entity and cmd_entity.type == "bot_command" else len("/addrules")
    rules_text = raw_text[cmd_len:]
    if rules_text[:1] in {" ", "\n", "\t"}:
        rules_text = rules_text[1:]

    if not rules_text.strip():
        await update.message.reply_text("РЈРєР°Р¶РёС‚Рµ С‚РµРєСЃС‚ РїСЂР°РІРёР» РїРѕСЃР»Рµ РєРѕРјР°РЅРґС‹ /addrules")
        return

    context.application.bot_data["global_rules_text"] = rules_text
    await update.message.reply_text("рџ“ЌРџСЂР°РІРёР»Р° СѓСЃРїРµС€РЅРѕ СЃРјРµРЅРµРЅС‹")

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
            new_topic = await context.bot.create_forum_topic(chat_id=RULES_CHAT_ID, name="РџСЂР°РІРёР»Р°")
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
        await update.message.reply_text("РљРѕРјР°РЅРґР° РґРѕСЃС‚СѓРїРЅР° С‚РѕР»СЊРєРѕ Р°РґРјРёРЅРёСЃС‚СЂР°С‚РѕСЂР°Рј 4 РєР°С‚РµРіРѕСЂРёРё Рё РІС‹С€Рµ.")
        return

    existing_rules_text = context.application.bot_data.get("global_rules_text")
    existing_rules_message_id = context.application.bot_data.get("global_rules_message_id")
    if not existing_rules_text and not existing_rules_message_id:
        await context.bot.send_message(
            chat_id=LOG_CHAT_ID,
            text="в„№пёЏРџСЂР°РІРёР»Р° РЅРµ СѓСЃС‚Р°РЅРѕРІР»РµРЅС‹: РСЃРїРѕР»СЊР·СѓР№С‚Рµ РєРѕРјР°РЅРґСѓ /addrules С‡С‚РѕР±С‹ РґРѕР±Р°РІРёС‚СЊ РЅРѕРІС‹Рµ РїСЂР°РІРёР»Р°.",
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
        new_topic = await context.bot.create_forum_topic(chat_id=RULES_CHAT_ID, name="РџСЂР°РІРёР»Р°")
        context.application.bot_data["rules_thread_id"] = new_topic.message_thread_id
    except Exception as e:
        logging.exception("del_rule_handler create_forum_topic failed: %s", e)

    context.application.bot_data.pop("global_rules_text", None)
    context.application.bot_data.pop("global_rules_message_id", None)
    await update.message.reply_text("вњ…РџСЂР°РІРёР»Р° СѓСЃРїРµС€РЅРѕ СѓРґР°Р»РµРЅС‹. РСЃРїРѕР»СЊР·СѓР№С‚Рµ РєРѕРјР°РЅРґСѓ /addrules С‡С‚РѕР±С‹ РґРѕР±Р°РІРёС‚СЊ РЅРѕРІС‹Рµ РїСЂР°РІРёР»Р°.")



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
    SPECIAL_GROUP_ID = -1004417963273
    if update.effective_chat.id != SPECIAL_GROUP_ID:
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
                text=f"в›”пёЏР’С‹ РЅР°С…РѕРґРёС‚РµСЃСЊ РІ РјСѓС‚Рµ РµС‰Рµ {remaining_minutes} РјРёРЅ. РЎРѕРѕР±С‰РµРЅРёРµ РЅРµ РѕС‚РїСЂР°РІР»РµРЅРѕ.",
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
                text="вљ пёЏРЎРѕРѕР±С‰РµРЅРёРµ СЏРІР»СЏРµС‚СЃСЏ РїРѕРґРѕР·СЂРёС‚РµР»СЊРЅС‹Рј Рё РѕС‚РїСЂР°РІР»РµРЅРѕ РЅР° РїСЂРѕРІРµСЂРєСѓ СЃС‚Р°СЂС€РµР№ Р°РґРјРёРЅРёСЃС‚СЂР°С†РёРё.",
            )
        except Exception:
            pass

        review_text = (
            "рџ’¬РџРѕРґРѕР·СЂРёС‚РµР»СЊРЅРѕРµ СЃРѕРѕР±С‰РµРЅРёРµ\n\n"
            f'РђРґРјРёРЅ "{admin_username}" РїРѕРїС‹С‚Р°Р»СЃСЏ РѕС‚РїСЂР°РІРёС‚СЊ СЃРѕРѕР±С‰РµРЅРёРµ РІ С‚РµРјСѓ "{topic_link}" СЃ С‚РµРєСЃС‚РѕРј "{suspicious_text}"\n\n'
            "Р’С‹Р±РµСЂРёС‚Рµ РґРµР№СЃС‚РІРёРµ РЅРёР¶Рµв¬‡пёЏ"
        )
        review_kb = InlineKeyboardMarkup(
            [[
                InlineKeyboardButton("рџџўРћС‚РїСЂР°РІРёС‚СЊ СЃРѕРѕР±С‰РµРЅРёРµ", callback_data=f"susp_admin_allow_{review_id}"),
                InlineKeyboardButton("рџ”ґРќРµ РѕС‚РїСЂР°РІР»СЏС‚СЊ СЃРѕРѕР±С‰РµРЅРёРµ", callback_data=f"susp_admin_deny_{review_id}"),
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
    profile["reason"] = "РќР°СЂСѓС€РµРЅРёРµ РїСЂР°РІРёР» РѕС‚РєР°Р·Р° РѕС‚ Р°РґРјРёРЅРёСЃС‚СЂР°С‚РѕСЂР° РѕС‚ СЂСѓРєРѕРІРѕРґСЃС‚РІР° Р±РѕС‚Р°"
    await enforce_autoban_if_needed(context, str(user_id), review.get("username"))

    try:
        await context.bot.send_message(
            chat_id=int(user_id),
            text=f'вќ—пёЏР’С‹ РїРѕР»СѓС‡РёР»Рё РїСЂРµРґСѓРїСЂРµР¶РґРµРЅРёРµ СЃ РїСЂРёС‡РёРЅРѕР№ "РќР°СЂСѓС€РµРЅРёРµ РїСЂР°РІРёР» РѕС‚РєР°Р·Р° РѕС‚ Р°РґРјРёРЅРёСЃС‚СЂР°С‚РѕСЂР°" РѕС‚ СЂСѓРєРѕРІРѕРґСЃС‚РІР° Р±РѕС‚Р°. РўРµРїРµСЂСЊ Сѓ РІР°СЃ РїСЂРµРґСѓРїСЂРµР¶РґРµРЅРёР№ {profile["warn"]}',
        )
    except Exception as e:
        logging.exception("warn_user_cancel_callback failed to notify user: %s", e)

    edited_text = (
        "рџџҐРћС‚РєР°Р· РїРѕР»СЊР·РѕРІР°С‚РµР»СЏ РѕС‚ Р°РґРјРёРЅРёСЃС‚СЂР°С‚РѕСЂР°\n\n"
        f'РџСЂРёС€Р»Рѕ СѓРІРµРґРѕРјР»РµРЅРёРµ, С‡С‚Рѕ РїРѕР»СЊР·РѕРІР°С‚РµР»СЊ РЅР°С€РµРіРѕ Р±РѕС‚Р° "{review.get("username")}" РѕС‚РєР°Р·Р°Р»СЃСЏ РѕС‚ Р°РґРјРёРЅРёСЃС‚СЂР°С‚РѕСЂР° "{review.get("admin_username")}" '
        f'РїРѕ РїСЂРёС‡РёРЅРµ "{review.get("reason_otkazik")}"\n\n'
        "РџРѕР»СЊР·РѕРІР°С‚РµР»СЊ РїРѕР»СѓС‡РёР» РїСЂРµРґСѓРїСЂРµР¶РґРµРЅРёРµвњ…"
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
            await update.callback_query.message.edit_text((update.callback_query.message.text or "") + "\n\nвњ…Р Р°Р·СЂРµС€РµРЅРѕ")
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
            text="в›”РџСЂРѕРІРµСЂРєР° РЅРµ РїСЂРѕР№РґРµРЅР°, СЃРѕРѕР±С‰РµРЅРёРµ РЅРµ РѕС‚РїСЂР°РІРёС‚СЃСЏ РїРѕР»СЊР·РѕРІР°С‚РµР»СЋ РІ Р›РЎ.",
        )
    except Exception:
        pass

    try:
        await update.callback_query.message.edit_text((update.callback_query.message.text or "") + "\n\nвќЊРќРµ РѕС‚РїСЂР°РІР»РµРЅРѕ")
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
                text="вњ…РџСЂРѕРІРµСЂРєР° РїСЂРѕР№РґРµРЅР°, СЃРѕРѕР±С‰РµРЅРёРµ РѕС‚РїСЂР°РІР»РµРЅРѕ Р°РґРјРёРЅРёСЃС‚СЂР°С‚РѕСЂСѓ.",
            )
        except Exception:
            pass

        try:
            await update.callback_query.message.edit_text((update.callback_query.message.text or "") + "\n\nвњ…Р Р°Р·СЂРµС€РµРЅРѕ")
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
            text="в›”РЎРѕРѕР±С‰РµРЅРёРµ СЏРІР»СЏРµС‚СЃСЏ РЅР°СЂСѓС€РµРЅРёРµРј Р±РѕС‚Р° Рё РЅРµ Р±С‹Р»Рѕ РѕС‚РїСЂР°РІР»РµРЅРѕ Р°РґРјРёРЅРёСЃС‚СЂР°С‚РѕСЂСѓ.",
        )
    except Exception:
        pass

    try:
        await update.callback_query.message.edit_text((update.callback_query.message.text or "") + "\n\nвќЊРќРµ РѕС‚РїСЂР°РІР»РµРЅРѕ")
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
    profile["reason"] = "РџРѕРґРѕР·СЂРёС‚РµР»СЊРЅРѕРµ СЃРѕРѕР±С‰РµРЅРёРµ СЃ С‡СѓР¶РёРј username"
    await enforce_autoban_if_needed(context, user_id, profile.get("username"))
    _save_profile_record(context, user_id)

    try:
        await context.bot.send_message(
            chat_id=int(user_id),
            text="в›”РЎРѕРѕР±С‰РµРЅРёРµ РЅРµ Р±С‹Р»Рѕ РѕС‚РїСЂР°РІР»РµРЅРѕ Р°РґРјРёРЅРёСЃС‚СЂР°С‚РѕСЂСѓ. Р’С‹ РїРѕР»СѓС‡РёР»Рё РїСЂРµРґСѓРїСЂРµР¶РґРµРЅРёРµ.",
        )
    except Exception:
        pass

    try:
        await update.callback_query.message.edit_text((update.callback_query.message.text or "") + "\n\nРџРѕР»СЊР·РѕРІР°С‚РµР»СЊ РїРѕР»СѓС‡РёР» РїСЂРµРґСѓРїСЂРµР¶РґРµРЅРёРµ")
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
    profile["admin_rank"] = "РЎС‚Р°Р¶РµСЂ"
    profile["admin_candidate"] = False
    profile["admin_candidate_status"] = "approved"
    _save_profile_record(context, user_id)

    state_map = context.application.bot_data.setdefault("admin_candidate_state", {})
    state_map.pop(user_id, None)

    try:
        await context.bot.send_message(
            chat_id=int(user_id),
            text="вњ…Р’Р°С€Р° РєР°РЅРґРёРґР°С‚СѓСЂР° РѕРґРѕР±СЂРµРЅР°. Р’Р°Рј РІС‹РґР°РЅС‹ РїСЂР°РІР° РєР°С‚РµРіРѕСЂРёРё 1.",
        )
    except Exception:
        pass

    try:
        await update.callback_query.message.edit_text((update.callback_query.message.text or "") + "\n\nвњ…РљР°РЅРґРёРґР°С‚ РѕРґРѕР±СЂРµРЅ")
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
        [[InlineKeyboardButton("РѕС‚РјРµРЅР°", callback_data=f"cancel_candidate_reject_{review_id}")]]
    )
    prompt = await context.bot.send_message(
        chat_id=LOG_CHAT_ID,
        text="РЈРєР°Р¶РёС‚Рµ РїСЂРёС‡РёРЅСѓ РѕС‚РєР°Р·Р° РєР°РЅРґРёРґР°С‚Сѓ:",
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
    await update.callback_query.answer("РћС‚РјРµРЅР°")
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
        await update.message.reply_text("РџСЂРёС‡РёРЅР° РЅРµ РјРѕР¶РµС‚ Р±С‹С‚СЊ РїСѓСЃС‚РѕР№.")
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
            text=f'вќЊР—Р°СЏРІРєР° РЅРµ Р±С‹Р»Р° РїСЂРёРЅСЏС‚Р°. РџСЂРёС‡РёРЅР°: "{reason}"\n\nРќР°С‡РЅРёС‚Рµ Р·Р°РЅРѕРІРѕ СЃ СЌС‚Р°РїР° 1.',
        )
        await _send_candidate_stage_1(context, int(candidate_user_id))
    except Exception:
        pass

    try:
        await update.message.reply_text("вњ…РЎРѕРѕР±С‰РµРЅРёРµ РѕС‚РїСЂР°РІР»РµРЅРѕ РєР°РЅРґРёРґР°С‚Сѓ РІ Р›РЎ.")
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



