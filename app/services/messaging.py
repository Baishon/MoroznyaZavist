"""Generic message-forwarding helpers shared by user- and admin-side handlers."""
import logging

from telegram import ReactionTypeCustomEmoji, ReactionTypeEmoji
from telegram.ext import ContextTypes

from app.services.profiles import _set_user_blocked_bot_state


def _track_session_message_pair(context: ContextTypes.DEFAULT_TYPE, source_chat_id: int, source_message_id: int, target_chat_id: int, target_message_id: int) -> None:
    """Store a bidirectional mapping between mirrored messages in both session chats."""
    mapping = context.application.bot_data.setdefault("session_message_map", {})
    source_key = (int(source_chat_id), int(source_message_id))
    target_key = (int(target_chat_id), int(target_message_id))
    mapping[source_key] = target_key
    mapping[target_key] = source_key
    mapping[(int(source_chat_id), str(source_message_id))] = target_key
    mapping[(int(target_chat_id), str(target_message_id))] = source_key
    logging.info(
        "session_message_map: %s:%s <-> %s:%s",
        int(source_chat_id),
        int(source_message_id),
        int(target_chat_id),
        int(target_message_id),
    )


def _extract_reaction_payload(reaction_value):
    """Convert Telegram reaction payloads to a set_message_reaction-compatible list."""
    if reaction_value is None:
        return None

    items = reaction_value if isinstance(reaction_value, (list, tuple, set)) else [reaction_value]
    cleaned = []
    for item in items:
        if item is None:
            continue
        if isinstance(item, str):
            cleaned.append(item)
        elif hasattr(item, "emoji") and getattr(item, "emoji", None):
            cleaned.append(ReactionTypeEmoji(emoji=item.emoji))
        elif hasattr(item, "custom_emoji_id") and getattr(item, "custom_emoji_id", None):
            cleaned.append(ReactionTypeCustomEmoji(custom_emoji_id=item.custom_emoji_id))
    return cleaned if cleaned else []


async def mirror_session_message_reaction(update, context: ContextTypes.DEFAULT_TYPE):
    """Mirror a reaction from one side of a session to the other side in the same session."""
    reaction = getattr(update, "message_reaction", None)
    if reaction is None:
        return

    chat_id = int(getattr(update.effective_chat, "id", 0) or 0)
    if not chat_id:
        return

    actor = getattr(reaction, "user", None) or getattr(reaction, "actor_chat", None)
    if actor is not None and getattr(actor, "is_bot", False):
        return

    message_id = int(getattr(reaction, "message_id", 0) or 0)
    if not message_id:
        return

    target_reaction = _extract_reaction_payload(getattr(reaction, "new_reaction", None))
    if target_reaction is None:
        return

    logging.info(
        "reaction_update: chat=%s msg=%s by=%s new_reaction=%s",
        chat_id,
        message_id,
        getattr(actor, "id", None),
        target_reaction,
    )

    mapping = context.application.bot_data.get("session_message_map", {}) or {}
    counterpart = mapping.get((chat_id, message_id)) or mapping.get((chat_id, str(message_id)))
    logging.info("reaction_lookup: chat=%s msg=%s counterpart=%s", chat_id, message_id, counterpart)
    if not counterpart or not isinstance(counterpart, tuple) or len(counterpart) != 2:
        return

    other_chat_id, other_message_id = int(counterpart[0]), int(counterpart[1])
    if other_chat_id == chat_id and other_message_id == message_id:
        return

    lock = context.application.bot_data.get("session_reaction_lock")
    if not isinstance(lock, set):
        lock = set()
        context.application.bot_data["session_reaction_lock"] = lock

    lock_key = tuple(sorted(((chat_id, message_id), (other_chat_id, other_message_id))))
    if lock_key in lock:
        return
    lock.add(lock_key)

    try:
        logging.info(
            "set_message_reaction: chat=%s msg=%s reaction=%s -> chat=%s msg=%s",
            chat_id,
            message_id,
            target_reaction,
            other_chat_id,
            other_message_id,
        )
        await context.bot.set_message_reaction(chat_id=other_chat_id, message_id=other_message_id, reaction=target_reaction)
    except Exception as exc:
        logging.exception(
            "Failed to mirror reaction from chat %s msg %s to chat %s msg %s. Telegram API error: %s",
            chat_id,
            message_id,
            other_chat_id,
            other_message_id,
            exc,
        )
    finally:
        lock.discard(lock_key)


async def mirror_session_message_edit(update, context: ContextTypes.DEFAULT_TYPE):
    """Mirror text/caption edits from one side of a session to the paired message on the other side."""
    edited = getattr(update, "edited_message", None)
    if edited is None:
        return

    chat_id = int(getattr(edited, "chat_id", 0) or 0)
    message_id = int(getattr(edited, "message_id", 0) or 0)
    if not chat_id or not message_id:
        return

    text_value = getattr(edited, "text", None)
    caption_value = getattr(edited, "caption", None)
    if text_value is None and caption_value is None:
        return

    mapping = context.application.bot_data.get("session_message_map", {}) or {}
    counterpart = mapping.get((chat_id, message_id)) or mapping.get((chat_id, str(message_id)))
    if not counterpart or not isinstance(counterpart, tuple) or len(counterpart) != 2:
        return

    other_chat_id, other_message_id = int(counterpart[0]), int(counterpart[1])
    if other_chat_id == chat_id and other_message_id == message_id:
        return

    lock = context.application.bot_data.get("session_edit_lock")
    if not isinstance(lock, set):
        lock = set()
        context.application.bot_data["session_edit_lock"] = lock

    lock_key = tuple(sorted(((chat_id, message_id), (other_chat_id, other_message_id))))
    if lock_key in lock:
        return
    lock.add(lock_key)

    try:
        logging.info(
            "edit_update: source_chat=%s source_msg=%s paired_chat=%s paired_msg=%s text=%r caption=%r",
            chat_id,
            message_id,
            other_chat_id,
            other_message_id,
            text_value,
            caption_value,
        )
        if text_value is not None:
            await context.bot.edit_message_text(
                chat_id=other_chat_id,
                message_id=other_message_id,
                text=text_value,
                parse_mode=getattr(edited, "parse_mode", None),
                entities=getattr(edited, "entities", None) or None,
            )
        elif caption_value is not None:
            await context.bot.edit_message_caption(
                chat_id=other_chat_id,
                message_id=other_message_id,
                caption=caption_value,
                parse_mode=getattr(edited, "parse_mode", None),
                caption_entities=getattr(edited, "caption_entities", None) or None,
            )
    except Exception:
        logging.exception(
            "Failed to mirror edited message from chat %s msg %s to chat %s msg %s. Telegram API error:",
            chat_id,
            message_id,
            other_chat_id,
            other_message_id,
        )
    finally:
        lock.discard(lock_key)


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
