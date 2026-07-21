"""Ban / warning enforcement helpers.

`_apply_ban` does the actual ban (profile update + persistence + log entry);
`block_if_banned` and `check_active_chat_block` are guard checks handlers call
before processing a message; `enforce_autoban_if_needed` applies the
warn-count-based autoban rule. Persists through `app.database.requests`.
"""
from datetime import datetime

from telegram import Update
from telegram.ext import ContextTypes

from app.config import LOG_CHAT_ID
from app.database.requests import _save_ban_record, _save_profile_record
from app.services.profiles import _has_admin_ban_immunity, is_user_banned


async def _apply_ban(context: ContextTypes.DEFAULT_TYPE, user_id: str, username_hint: str | None, reason: str, log_title: str) -> bool:
    profiles = context.application.bot_data.setdefault("profiles", {})
    profile = profiles.get(str(user_id))
    if not profile:
        return False

    if _has_admin_ban_immunity(profile):
        return False

    banned_users = context.application.bot_data.setdefault("banned_users", {})
    if banned_users.get(str(user_id)):
        return True

    username = username_hint or profile.get("username") or f"id{user_id}"
    banned_users[str(user_id)] = {
        "banned_at": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
        "reason": reason,
        "source": log_title,
    }
    profile["reason"] = reason
    _save_profile_record(context, str(user_id))
    _save_ban_record(context, str(user_id))

    try:
        await context.bot.send_message(
            chat_id=int(user_id),
            text=f'⛔Вы были заблокированы в нашем боте. Причина: "{reason}"',
        )
    except Exception:
        pass

    topic_targets = []
    active_chats = context.application.bot_data.setdefault("active_chats", {})
    active_session = active_chats.pop(str(user_id), None)
    if active_session:
        topic_targets.append((active_session.get("chat_id"), active_session.get("topic_id")))

    admin_requests = context.application.bot_data.setdefault("admin_requests", {})
    req_info = admin_requests.pop(str(user_id), None)
    if req_info:
        topic_targets.append((req_info.get("chat_id"), req_info.get("topic_id")))

    topic_map = context.application.bot_data.setdefault("topic_user_map", {})
    unique_targets = set((c, t) for c, t in topic_targets if c and t is not None)
    for chat_id, topic_id in unique_targets:
        try:
            await context.bot.edit_forum_topic(
                chat_id=chat_id,
                message_thread_id=topic_id,
                name=f"{username} (Закрыта системой)",
            )
        except Exception:
            pass
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                message_thread_id=topic_id,
                text=f'🚫Пользователь "{username}" был заблокирован в нашем боте. Причина: "{reason}"',
            )
        except Exception:
            pass
        topic_map.pop(topic_id, None)
        topic_map.pop(str(topic_id), None)

    try:
        await context.bot.send_message(
            chat_id=LOG_CHAT_ID,
            text=f'⛔️{log_title}\n\nПользователь "{username}" был заблокирован. Причина: "{reason}"',
        )
    except Exception:
        pass

    return True


async def block_if_banned(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    user = update.effective_user
    if not user:
        return False
    if not is_user_banned(context, str(user.id)):
        return False

    text = "⛔ Доступ к функциям бота для вашего аккаунта приостановлен на неопределённый срок."
    if getattr(update, "message", None) is not None:
        await update.message.reply_text(text)
    elif getattr(update, "callback_query", None) is not None:
        await update.callback_query.message.reply_text(text)
    return True


async def enforce_autoban_if_needed(context: ContextTypes.DEFAULT_TYPE, user_id: str, username_hint: str | None = None) -> bool:
    profiles = context.application.bot_data.setdefault("profiles", {})
    profile = profiles.get(str(user_id))
    if not profile:
        return False

    if _has_admin_ban_immunity(profile):
        return False

    warn_count = int(profile.get("warn", 0) or 0)
    if warn_count > 3:
        profile["warn"] = 3
        warn_count = 3
    if warn_count < 3:
        return False

    profile["warn"] = 0

    banned_users = context.application.bot_data.setdefault("banned_users", {})
    if banned_users.get(str(user_id)):
        return True
    result = await _apply_ban(context, str(user_id), username_hint, "3 warnings", "AUTOBAN")
    _save_profile_record(context, str(user_id))
    return result


async def check_active_chat_block(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    user_id = str(update.effective_user.id)
    if is_user_banned(context, user_id):
        blocked_text = "⛔ Доступ к функции поиска администратора для вашего аккаунта приостановлен на неопределённый срок."
        if getattr(update, "message", None) is not None:
            await update.message.reply_text(blocked_text)
        elif getattr(update, "callback_query", None) is not None:
            await update.callback_query.message.reply_text(blocked_text)
        return True

    active = context.application.bot_data.get("active_chats", {}).get(user_id)
    if active and active.get("active", False):
        if getattr(update, "message", None) is not None:
            await update.message.reply_text("У вас уже есть активный собеседник, Поиск недоступен.")
        elif getattr(update, "callback_query", None) is not None:
            await update.callback_query.message.reply_text("У вас уже есть активный собеседник, Поиск недоступен.")
        return True
    return False
