"""Generic message-forwarding helpers shared by user- and admin-side handlers."""
import logging

from telegram import ReactionTypeCustomEmoji, ReactionTypeEmoji
from telegram.ext import ContextTypes

from app.config import WORK_CHAT_ID
from app.database.requests import _save_runtime_snapshot
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
    # Runtime snapshots are JSON, so tuple keys and tuple values are converted
    # to strings/lists on restart. Keep a stable string key as well.
    mapping[f"{int(source_chat_id)}:{int(source_message_id)}"] = target_key
    mapping[f"{int(target_chat_id)}:{int(target_message_id)}"] = source_key
    logging.info(
        "session_message_map: %s:%s <-> %s:%s",
        int(source_chat_id),
        int(source_message_id),
        int(target_chat_id),
        int(target_message_id),
    )
    _save_runtime_snapshot(context)


def _get_session_message_pair(mapping, chat_id: int, message_id: int):
    """Read a pair from both live and JSON-restored session maps."""
    pair = (
        mapping.get((chat_id, message_id))
        or mapping.get((chat_id, str(message_id)))
        or mapping.get(f"{chat_id}:{message_id}")
        or mapping.get(f"({chat_id}, {message_id})")
    )
    if not isinstance(pair, (tuple, list)) or len(pair) != 2:
        return None
    try:
        return int(pair[0]), int(pair[1])
    except (TypeError, ValueError):
        return None


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
    counterpart = _get_session_message_pair(mapping, chat_id, message_id)
    logging.info("reaction_lookup: chat=%s msg=%s counterpart=%s", chat_id, message_id, counterpart)
    if not counterpart:
        return

    other_chat_id, other_message_id = counterpart
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
    """Mirror edits of an admin's forum-topic message to the user's existing PM."""
    edited = getattr(update, "edited_message", None)
    if edited is None:
        logging.warning("edited_message handler invoked without edited_message payload")
        return

    chat = getattr(edited, "chat", None)
    actor = getattr(edited, "from_user", None)
    chat_id = int(getattr(chat, "id", 0) or getattr(edited, "chat_id", 0) or 0)
    message_id = int(getattr(edited, "message_id", 0) or 0)
    thread_id = getattr(edited, "message_thread_id", None)
    logging.info(
        "edited_message received: chat_id=%s message_id=%s thread_id=%s "
        "from_user_id=%s from_user_is_bot=%s text=%r caption=%r",
        chat_id,
        message_id,
        thread_id,
        getattr(actor, "id", None),
        getattr(actor, "is_bot", None),
        getattr(edited, "text", None),
        getattr(edited, "caption", None),
    )
    if not chat_id or not message_id:
        logging.warning("edited_message ignored: missing chat_id or message_id")
        return
    if thread_id is None:
        logging.info("edited_message ignored: no forum message_thread_id")
        return
    if actor is None:
        logging.warning("edited_message ignored: Telegram payload has no from_user")
        return
    if actor.is_bot:
        logging.info("edited_message ignored: source message was sent by a bot")
        return

    # Only admin edits in a work-chat forum topic are mirrored. User edits in
    # private chat must not cause a new or reverse edit in the topic.
    if chat_id != int(WORK_CHAT_ID):
        logging.info("edited_message ignored: chat_id=%s is not WORK_CHAT_ID=%s", chat_id, WORK_CHAT_ID)
        return

    text_value = getattr(edited, "text", None)
    caption_value = getattr(edited, "caption", None)
    if text_value is None and caption_value is None:
        logging.info("edited_message ignored: no editable text or caption")
        return

    mapping = context.application.bot_data.get("session_message_map", {}) or {}
    logging.info(
        "edited_message mapping lookup: source_chat_id=%s source_message_id=%s "
        "thread_id=%s mapping_entries=%s",
        chat_id,
        message_id,
        thread_id,
        len(mapping),
    )
    counterpart = _get_session_message_pair(mapping, chat_id, message_id)
    logging.info(
        "edited_message mapping result: source=%s:%s counterpart=%s",
        chat_id,
        message_id,
        counterpart,
    )
    if not counterpart:
        logging.error(
            "edited_message mapping missing: admin_message=%s:%s thread_id=%s",
            chat_id,
            message_id,
            thread_id,
        )
        return

    other_chat_id, other_message_id = counterpart
    if other_chat_id == chat_id:
        logging.error(
            "edited_message mapping points back to work chat: source=%s:%s target=%s:%s",
            chat_id,
            message_id,
            other_chat_id,
            other_message_id,
        )
        return
    logging.info(
        "edited_message target resolved: user_dm_chat_id=%s user_dm_message_id=%s "
        "admin_message_id=%s thread_id=%s",
        other_chat_id,
        other_message_id,
        message_id,
        thread_id,
    )

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
            "attempting DM edit: source_chat_id=%s source_message_id=%s thread_id=%s "
            "target_chat_id=%s target_message_id=%s text=%r caption=%r",
            chat_id,
            message_id,
            thread_id,
            other_chat_id,
            other_message_id,
            text_value,
            caption_value,
        )
        if text_value is not None:
            edit_kwargs = {
                "chat_id": other_chat_id,
                "message_id": other_message_id,
                "text": text_value,
            }
            entities = getattr(edited, "entities", None)
            if entities:
                edit_kwargs["entities"] = entities
            result = await context.bot.edit_message_text(**edit_kwargs)
        elif caption_value is not None:
            edit_kwargs = {
                "chat_id": other_chat_id,
                "message_id": other_message_id,
                "caption": caption_value,
            }
            caption_entities = getattr(edited, "caption_entities", None)
            if caption_entities:
                edit_kwargs["caption_entities"] = caption_entities
            result = await context.bot.edit_message_caption(**edit_kwargs)
        logging.info(
            "DM edit succeeded: target_chat_id=%s target_message_id=%s result_message_id=%s",
            other_chat_id,
            other_message_id,
            getattr(result, "message_id", None),
        )
    except Exception as exc:
        logging.exception(
            "DM edit failed: source_chat_id=%s source_message_id=%s thread_id=%s "
            "target_chat_id=%s target_message_id=%s Telegram API error=%s",
            chat_id,
            message_id,
            thread_id,
            other_chat_id,
            other_message_id,
            exc,
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
            return await bot.send_message(chat_id=user_chat_id, text=message.text)

        # photo
        if getattr(message, "photo", None):
            photo = message.photo[-1].file_id
            return await bot.send_photo(chat_id=user_chat_id, photo=photo, caption=getattr(message, "caption", None))

        # video
        if getattr(message, "video", None):
            vid = message.video.file_id
            return await bot.send_video(chat_id=user_chat_id, video=vid, caption=getattr(message, "caption", None))

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
            return await bot.send_animation(chat_id=user_chat_id, animation=aid, caption=getattr(message, "caption", None))

        # document
        if getattr(message, "document", None):
            did = message.document.file_id
            return await bot.send_document(chat_id=user_chat_id, document=did, caption=getattr(message, "caption", None))

        # sticker
        if getattr(message, "sticker", None):
            return await bot.send_sticker(chat_id=user_chat_id, sticker=message.sticker.file_id)

        # location
        if getattr(message, "location", None):
            loc = message.location
            return await bot.send_location(chat_id=user_chat_id, latitude=loc.latitude, longitude=loc.longitude)

        # contact
        if getattr(message, "contact", None):
            c = message.contact
            return await bot.send_contact(chat_id=user_chat_id, phone_number=c.phone_number, first_name=c.first_name, last_name=getattr(c, "last_name", None))

        # dice
        if getattr(message, "dice", None):
            return await bot.send_dice(chat_id=user_chat_id)

        # polls and other unsupported types: notify user
        return await bot.send_message(chat_id=user_chat_id, text="[Неподдерживаемый тип сообщения — пересылка не выполнена]")
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
