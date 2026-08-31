"""Small stateless helpers: chat/topic checks, text parsing, topic-state cache."""
import re

from telegram.ext import ContextTypes

from app.config import COOPERATION_CHAT_ID, LOG_CHAT_ID, NORMAL_CHAT_ID, RULES_CHAT_ID, TRUSTED_ADMIN_CHAT_ID, WORK_CHAT_ID


def _special_admin_chat_ids() -> set[int]:
    return {LOG_CHAT_ID, WORK_CHAT_ID, RULES_CHAT_ID, COOPERATION_CHAT_ID, TRUSTED_ADMIN_CHAT_ID, NORMAL_CHAT_ID}


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


def _rp_action_templates() -> dict[str, str]:
    return {
        "поздороваться": "{name_admin} дружески помахал рукой и поздоровался с {name_user}",
        "обнять": "{name_admin} крепко и со всей теплотой обнял {name_user}",
        "поцеловать": "{name_admin} нежно поцеловал в щеку {name_user}",
        "улыбнуться": "{name_admin} искренне и тепло улыбнулся {name_user}",
        "пожать руку": "{name_admin} уверенно и крепко пожал руку {name_user}",
        "дать пять": "{name_admin} звонко дал «пять» {name_user}",
        "подмигнуть": "{name_admin} задорно подмигнул {name_user}",
        "похвалить": "{name_admin} искренне похвалил за отличную работу {name_user}",
        "налить чай": "{name_admin} налил чашечку горячего ароматного чая для {name_user}",
        "угостить кофе": "{name_admin} приготовил крепкий бодрящий кофе для {name_user}",
        "поделиться печеньем": "{name_admin} протянул самую вкусную шоколадную печеньку {name_user}",
        "укрыть пледом": "{name_admin} заботливо укутал в теплый мягкий плед {name_user}",
        "позвать гулять": "{name_admin} предложил {name_user} бросить все дела и пойти прогуляться",
        "включить фильм": "{name_admin} притащил попкорн и включил интересный фильм вместе с {name_user}",
        "сделать кусь": "{name_admin} тихонько подкрался и сделал кусь за ушко {name_user}",
        "погладить по голове": "{name_admin} ласково потрепал по волосам и погладил по голове {name_user}",
        "пощекотать": "{name_admin} внезапно напал и начал нещадно щекотать {name_user}",
        "дать леща": "{name_admin} выдал профилактический отрезвляющий подзатыльник {name_user}",
        "оседлать": "{name_admin} с разбегу запрыгнул на спину и оседлал {name_user}",
        "спрятаться": "{name_admin} испуганно залез под диван и спрятался от {name_user}",
        "кинуть снежок": "{name_admin} идеально слепил снежок и запустил точно в {name_user}",
        "подарить звезду": "{name_admin} достал с неба самую яркую звезду и вручил {name_user}",
        "вызвать на дуэль": "{name_admin} пафосно бросил перчатку и вызвал на дуэль {name_user}",
        "связать": "{name_admin} аккуратно, но крепко связал шелковой веревкой {name_user}",
        "взять в заложники": "{name_admin} внезапно застал врасплох и взял в заложники {name_user}",
        "ограбить": "{name_admin} незаметно проверил карманы и мастерски ограбил {name_user}",
        "поставить в угол": "{name_admin} строго нахмурился и отправил стоять в угол {name_user}",
        "наколдовать котика": "{name_admin} взмахнул волшебной палочкой и призвал пушистого кота на колени до {name_user}",
        "страстно поцеловать": "{name_admin} резко притянул к себе и страстно поцеловал в губы {name_user}",
        "повалить на кровать": "{name_admin} внезапно толкнул на мягкую кровать и навис сверху над {name_user}",
        "прижать к стене": "{name_admin} грубо заблокировал руками все пути к отступлению и прижал к стене {name_user}",
        "укусить за губу": "{name_admin} во время поцелуя слегка и игриво укусил за нижнюю губу {name_user}",
        "сорвать одежду": "{name_admin} резким движением в порыве страсти сорвал верхнюю одежду с {name_user}",
        "посадить на колени": "{name_admin} уверенно взял за талию и усадил к себе на колени {name_user}",
        "шептать на ушко": "{name_admin} медленно подошел со спины и горячо прошептал пошлую шутку на ушко {name_user}",
        "наказать": "{name_admin} строго посмотрел в глаза и сурово наказал за непослушание {name_user}",
        "приказать": "{name_admin} властным тоном отдал жесткий приказ, который должен выполнить {name_user}",
        "заставить подчиниться": "{name_admin} применил силу и полностью подчинил своей воле {name_user}",
        "шлепнуть": "{name_admin} замахнулся и звонко шлепнул по мягкому месту {name_user}",
        "изнасиловать": "{name_admin} жестко надругался над {name_user}",
        "выебать": "{name_admin} принудил к жёсткому интиму {name_user}",
        "трахнуть": "{name_admin} принудил к интиму {name_user}",
    }


def _normalize_rp_trigger_text(text: str | None) -> str:
    value = (text or "").strip()
    return re.sub(r"\s+", " ", value).lower()


def _parse_rp_trigger_text(text: str | None) -> tuple[str, str] | None:
    normalized = _normalize_rp_trigger_text(text)
    if not normalized:
        return None
    template = _rp_action_templates().get(normalized)
    if not template:
        return None
    return (normalized, template)


def _rp_display_name_admin(profile: dict | None, fallback_username: str | None = None, fallback_user_id: str | int | None = None) -> str:
    tag_value = str((profile or {}).get("tag_admin") or "").strip()
    if tag_value:
        return tag_value.lstrip("@")

    fallback = str(fallback_username or "").strip()
    if fallback:
        return fallback if fallback.startswith("@") else f"@{fallback}"
    if fallback_user_id is not None:
        return f"id{fallback_user_id}"
    return "админ"


def _rp_display_name_user(profile: dict | None, fallback_username: str | None = None, fallback_user_id: str | int | None = None) -> str:
    nickname = str((profile or {}).get("user_nickname") or "").strip()
    if nickname:
        return nickname

    fallback = str(fallback_username or "").strip()
    if fallback:
        return fallback if fallback.startswith("@") else f"@{fallback}"
    if fallback_user_id is not None:
        return f"id{fallback_user_id}"
    return "пользователь"


def _render_rp_action_message(template: str, sender_is_admin: bool, name_admin: str, name_user: str, trigger_name: str | None = None) -> str:
    display_name_admin = str(name_admin or "админ").strip().lstrip("@")
    display_name_user = str(name_user or "пользователь").strip().lstrip("@")

    text = template.replace("{name_admin}", "__ADMIN__").replace("{name_user}", "__USER__")
    if sender_is_admin:
        return text.replace("__ADMIN__", display_name_admin).replace("__USER__", display_name_user)

    swapped = text.replace("__ADMIN__", "__SWAP__").replace("__USER__", "__ADMIN__").replace("__SWAP__", "__USER__")
    return swapped.replace("__ADMIN__", display_name_admin).replace("__USER__", display_name_user)


def _is_rp_action_text(text: str | None) -> bool:
    value = _normalize_rp_trigger_text(text)
    if not value:
        return False
    if value.startswith("/me ") or value.startswith("*"):
        return True
    return value in _rp_action_templates()
