"""Generic message-forwarding helpers shared by user- and admin-side handlers."""
import logging

from telegram.ext import ContextTypes

from app.services.profiles import _set_user_blocked_bot_state


async def deliver_message_to_user(bot, message, user_chat_id: int):
    """Fallback delivery for various content types when copy_message fails.
    Sends the appropriate send_* request based on message attributes.
    """
    try:
        # text (includes caption-only cases handled via media blocks below)
        if getattr(message, "text", None):
            await bot.send_message(chat_id=user_chat_id, text=message.text)
            return

        # photo
        if getattr(message, "photo", None):
            photo = message.photo[-1].file_id
            await bot.send_photo(chat_id=user_chat_id, photo=photo, caption=getattr(message, "caption", None))
            return

        # video
        if getattr(message, "video", None):
            vid = message.video.file_id
            await bot.send_video(chat_id=user_chat_id, video=vid, caption=getattr(message, "caption", None))
            return

        # audio
        if getattr(message, "audio", None):
            aid = message.audio.file_id
            await bot.send_audio(chat_id=user_chat_id, audio=aid, caption=getattr(message, "caption", None))
            return

        # voice
        if getattr(message, "voice", None):
            vid = message.voice.file_id
            await bot.send_voice(chat_id=user_chat_id, voice=vid, caption=getattr(message, "caption", None))
            return

        # animation (gif)
        if getattr(message, "animation", None):
            aid = message.animation.file_id
            await bot.send_animation(chat_id=user_chat_id, animation=aid, caption=getattr(message, "caption", None))
            return

        # document
        if getattr(message, "document", None):
            did = message.document.file_id
            await bot.send_document(chat_id=user_chat_id, document=did, caption=getattr(message, "caption", None))
            return

        # sticker
        if getattr(message, "sticker", None):
            await bot.send_sticker(chat_id=user_chat_id, sticker=message.sticker.file_id)
            return

        # location
        if getattr(message, "location", None):
            loc = message.location
            await bot.send_location(chat_id=user_chat_id, latitude=loc.latitude, longitude=loc.longitude)
            return

        # contact
        if getattr(message, "contact", None):
            c = message.contact
            await bot.send_contact(chat_id=user_chat_id, phone_number=c.phone_number, first_name=c.first_name, last_name=getattr(c, "last_name", None))
            return

        # dice
        if getattr(message, "dice", None):
            await bot.send_dice(chat_id=user_chat_id)
            return

        # polls and other unsupported types: notify user
        await bot.send_message(chat_id=user_chat_id, text="[Неподдерживаемый тип сообщения — пересылка не выполнена]")
    except Exception:
        logging.exception("deliver_message_to_user failed for user %s message_id=%s", user_chat_id, getattr(message, "message_id", None))


async def notify_blocked_user_in_topic(context: ContextTypes.DEFAULT_TYPE, chat_id: int, topic_id: int | None, target_user: str) -> None:
    _set_user_blocked_bot_state(context, str(target_user), True)

    display_name = None
    try:
        req = (context.application.bot_data.get("admin_requests", {}) or {}).get(str(target_user))
        if req:
            display_name = req.get("username")
    except Exception:
        pass
    if not display_name:
        try:
            profile = (context.application.bot_data.get("profiles", {}) or {}).get(str(target_user))
            if profile:
                display_name = profile.get("username")
        except Exception:
            pass
    if not display_name:
        display_name = f"id{target_user}"

    try:
        if topic_id is not None:
            await context.bot.edit_forum_topic(
                chat_id=chat_id,
                message_thread_id=topic_id,
                name=f"{display_name} (Закрыто системой)",
            )
    except Exception:
        pass

    # Stop active session to avoid repeated forwarding errors after user blocks the bot.
    try:
        active = context.application.bot_data.setdefault("active_chats", {})
        active.pop(str(target_user), None)
    except Exception:
        pass

    try:
        app_requests = context.application.bot_data.setdefault("admin_requests", {})
        app_requests.pop(str(target_user), None)
    except Exception:
        pass

    try:
        topic_map = context.application.bot_data.setdefault("topic_user_map", {})
        if topic_id is not None:
            topic_map.pop(topic_id, None)
            topic_map.pop(str(topic_id), None)
    except Exception:
        pass

    try:
        if topic_id is not None:
            await context.bot.send_message(chat_id=chat_id, message_thread_id=topic_id, text="Bot was blocked by the user.")
        else:
            await context.bot.send_message(chat_id=chat_id, text="Bot was blocked by the user.")
    except Exception:
        pass
