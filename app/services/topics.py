"""Small stateless helpers: chat/topic checks, text parsing, topic-state cache."""
import re

from telegram.ext import ContextTypes

from app.config import COOPERATION_CHAT_ID, LOG_CHAT_ID, RULES_CHAT_ID, WORK_CHAT_ID


def _special_admin_chat_ids() -> set[int]:
    return {LOG_CHAT_ID, WORK_CHAT_ID, RULES_CHAT_ID, COOPERATION_CHAT_ID}


def _is_special_admin_chat(chat_id: int) -> bool:
    return chat_id in _special_admin_chat_ids()


def _is_admin_command_text(text: str | None) -> bool:
    value = (text or "").strip()
    if not value.startswith("/"):
        return False
    command = value.split(maxsplit=1)[0].split("@")[0].lower()
    blocked_commands = {
        "/addrules",
        "/delrules",
        "/makeadmin",
        "/setprefix",
        "/anpiar",
        "/fullstats",
        "/pm",
        "/prava",
        "/ban",
        "/unban",
        "/warn",
        "/unwarn",
        "/stats",
        "/astats",
        "/info_topic",
        "/topic",
        "/amute",
        "/aunmute",
        "/dump_maps",
    }
    return command in blocked_commands


def _build_paused_topic_name(base_name: str) -> str:
    suffix = "(приостановлено пользователем)"
    clean = (base_name or "").strip() or "тема"
    if clean.endswith(suffix):
        return clean
    return f"{clean} {suffix}"


def _extract_usernames_from_text(text: str) -> list[str]:
    if not text:
        return []
    return [m.group(1).lower() for m in re.finditer(r"@([A-Za-z0-9_]{5,32})", text)]


def _has_suspicious_username(text: str, allowed_username: str | None = None) -> bool:
    found = _extract_usernames_from_text(text)
    if re.search(r"https?://t\.me(?:/|\b)", text or "", flags=re.IGNORECASE):
        return True
    if not found:
        return False
    if not allowed_username:
        return True
    allowed = str(allowed_username).strip().lstrip("@").lower()
    return any(name != allowed for name in found)


def _topic_url(chat_id: int | None, topic_id: int | None) -> str:
    if not chat_id or topic_id is None:
        return "(тема недоступна)"
    chat_str = str(chat_id)
    if chat_str.startswith("-100"):
        chat_str = chat_str[4:]
    elif chat_str.startswith("-"):
        chat_str = chat_str[1:]
    return f"https://t.me/c/{chat_str}/{topic_id}"


def _next_suspicious_review_id(context: ContextTypes.DEFAULT_TYPE) -> str:
    seq = int(context.application.bot_data.get("suspicious_review_seq", 0) or 0) + 1
    context.application.bot_data["suspicious_review_seq"] = seq
    return str(seq)


def _parse_topic_url(topic_url: str):
    value = (topic_url or "").strip().strip('"').strip("'")
    match = re.search(r"(?:https?://)?t\.me/c/(?P<chat_id>\d+)/(?P<topic_id>\d+)(?:\?.*)?$", value, flags=re.IGNORECASE)
    if not match:
        return None, None

    chat_id = int(f"-100{match.group('chat_id')}")
    topic_id = int(match.group("topic_id"))
    return chat_id, topic_id


def _topic_state_key(chat_id: int, topic_id: int) -> str:
    return f"{chat_id}:{topic_id}"


def _get_topic_state(context: ContextTypes.DEFAULT_TYPE, chat_id: int, topic_id: int) -> str | None:
    topic_states = context.application.bot_data.setdefault("topic_states", {})
    return topic_states.get(_topic_state_key(chat_id, topic_id))


def _set_topic_state(context: ContextTypes.DEFAULT_TYPE, chat_id: int, topic_id: int, state: str) -> None:
    topic_states = context.application.bot_data.setdefault("topic_states", {})
    topic_states[_topic_state_key(chat_id, topic_id)] = state


def _clear_topic_state(context: ContextTypes.DEFAULT_TYPE, chat_id: int, topic_id: int) -> None:
    topic_states = context.application.bot_data.setdefault("topic_states", {})
    topic_states.pop(_topic_state_key(chat_id, topic_id), None)


async def _is_member_of_chat(context: ContextTypes.DEFAULT_TYPE, chat_id: int, user_id: int) -> bool:
    try:
        member = await context.bot.get_chat_member(chat_id=chat_id, user_id=user_id)
        status = getattr(member, "status", "")
        return status not in {"left", "kicked"}
    except Exception:
        return False


def _is_rp_action_text(text: str | None) -> bool:
    value = (text or "").strip().lower()
    return value.startswith("/me ") or value.startswith("*")
