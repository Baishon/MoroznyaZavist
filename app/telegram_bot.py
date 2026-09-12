"""Telegram bot client that records successfully sent messages."""
from telegram.ext import ExtBot

from app.database.requests import _save_outgoing_message, _save_outgoing_reference


class HistoryBot(ExtBot):
    """Keep outgoing message auditing in one place for all existing handlers."""

    def _record_sent_message(self, message, chat_id=None):
        _save_outgoing_message(
            getattr(self, "_message_history_connection", None),
            message,
            recipient_chat_id=chat_id,
        )
        return message

    def _sent_chat_id(self, args, kwargs):
        return kwargs.get("chat_id", args[0] if args else None)

    def _record_reference(self, result, chat_id, message_type):
        connection = getattr(self, "_message_history_connection", None)
        if isinstance(result, (list, tuple)):
            for item in result:
                self._record_reference(item, chat_id, message_type)
            return result
        message_id = getattr(result, "message_id", result)
        _save_outgoing_reference(connection, message_id, chat_id, message_type)
        return result

    async def send_message(self, *args, **kwargs):
        message = await super().send_message(*args, **kwargs)
        return self._record_sent_message(message, self._sent_chat_id(args, kwargs))

    async def send_photo(self, *args, **kwargs):
        message = await super().send_photo(*args, **kwargs)
        return self._record_sent_message(message, self._sent_chat_id(args, kwargs))

    async def send_video(self, *args, **kwargs):
        message = await super().send_video(*args, **kwargs)
        return self._record_sent_message(message, self._sent_chat_id(args, kwargs))

    async def send_audio(self, *args, **kwargs):
        message = await super().send_audio(*args, **kwargs)
        return self._record_sent_message(message, self._sent_chat_id(args, kwargs))

    async def send_voice(self, *args, **kwargs):
        message = await super().send_voice(*args, **kwargs)
        return self._record_sent_message(message, self._sent_chat_id(args, kwargs))

    async def send_document(self, *args, **kwargs):
        message = await super().send_document(*args, **kwargs)
        return self._record_sent_message(message, self._sent_chat_id(args, kwargs))

    async def send_animation(self, *args, **kwargs):
        message = await super().send_animation(*args, **kwargs)
        return self._record_sent_message(message, self._sent_chat_id(args, kwargs))

    async def send_sticker(self, *args, **kwargs):
        message = await super().send_sticker(*args, **kwargs)
        return self._record_sent_message(message, self._sent_chat_id(args, kwargs))

    async def send_location(self, *args, **kwargs):
        message = await super().send_location(*args, **kwargs)
        return self._record_sent_message(message, self._sent_chat_id(args, kwargs))

    async def send_contact(self, *args, **kwargs):
        message = await super().send_contact(*args, **kwargs)
        return self._record_sent_message(message, self._sent_chat_id(args, kwargs))

    async def send_dice(self, *args, **kwargs):
        message = await super().send_dice(*args, **kwargs)
        return self._record_sent_message(message, self._sent_chat_id(args, kwargs))

    async def send_media_group(self, *args, **kwargs):
        messages = await super().send_media_group(*args, **kwargs)
        return self._record_sent_message_group(messages, self._sent_chat_id(args, kwargs))

    def _record_sent_message_group(self, messages, chat_id):
        for message in messages:
            self._record_sent_message(message, chat_id)
        return messages

    async def copy_message(self, *args, **kwargs):
        result = await super().copy_message(*args, **kwargs)
        return self._record_reference(result, self._sent_chat_id(args, kwargs), "copied_message")
