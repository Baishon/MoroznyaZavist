"""User/admin profile storage and lookup helpers.

Profiles are plain dicts kept in ``bot_data["profiles"]`` and mirrored to
sqlite via `app.database.requests`.
"""
import html
from datetime import datetime

from telegram.ext import ContextTypes

from app.config import ADMIN_LEVEL_TITLES
from app.database.requests import _save_profile_record, _save_profile_seq
from app.states.form import ACTIVE_CANDIDATE_STAGES


def is_user_banned(context: ContextTypes.DEFAULT_TYPE, user_id: str) -> bool:
    banned = context.application.bot_data.get("banned_users", {}) or {}
    return bool(banned.get(str(user_id)))


def _normalize_username(value: str) -> str:
    return value.strip().lstrip("@").lower()


def _find_profile_by_identifier(context: ContextTypes.DEFAULT_TYPE, identifier: str):
    normalized = identifier.strip().lstrip("@").lower()
    profiles = context.application.bot_data.setdefault("profiles", {})
    for user_id, profile in profiles.items():
        if str(user_id) == normalized:
            return str(user_id), profile
        stored_username = str(profile.get("username") or "")
        if _normalize_username(stored_username) == normalized:
            return str(user_id), profile
    return None, None


def _is_user_nickname_taken(context: ContextTypes.DEFAULT_TYPE, owner_user_id: str, nickname: str) -> bool:
    normalized = str(nickname or "").strip().lower()
    if not normalized:
        return False

    profiles = context.application.bot_data.setdefault("profiles", {})
    for user_id, profile in profiles.items():
        if str(user_id) == str(owner_user_id):
            continue
        existing = str((profile or {}).get("user_nickname") or "").strip().lower()
        if existing and existing == normalized:
            return True
    return False


def _allocate_next_profile_id(context: ContextTypes.DEFAULT_TYPE) -> int:
    profiles = context.application.bot_data.setdefault("profiles", {})
    seq = context.application.bot_data.get("profile_seq")
    if not isinstance(seq, int) or seq < 0:
        max_id = 0
        for profile in profiles.values():
            try:
                current = int(profile.get("id_profile", 0) or 0)
                if current > max_id:
                    max_id = current
            except Exception:
                continue
        seq = max_id
    seq += 1
    context.application.bot_data["profile_seq"] = seq
    _save_profile_seq(context, seq)
    return seq


def _ensure_profile(context: ContextTypes.DEFAULT_TYPE, user_id: str, username_hint: str | None = None):
    profiles = context.application.bot_data.setdefault("profiles", {})
    profile = profiles.get(str(user_id))
    if not profile:
        profile = {
            "id_profile": _allocate_next_profile_id(context),
            "username": username_hint or f"id{user_id}",
            "message_user": 0,
            "last_admin_tag": "не указан",
            "warn": 0,
            "reason": "нет причин",
            "coin": 0,
            "date_registration": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
        }
        profiles[str(user_id)] = profile
        _save_profile_record(context, str(user_id))
        return profile

    changed = False
    if username_hint and not profile.get("username"):
        profile["username"] = username_hint
        changed = True

    try:
        int(profile.get("id_profile", 0) or 0)
    except Exception:
        profile["id_profile"] = 0
        changed = True
    if int(profile.get("id_profile", 0) or 0) <= 0:
        profile["id_profile"] = _allocate_next_profile_id(context)
        changed = True

    if not str(profile.get("last_admin_tag") or "").strip():
        profile["last_admin_tag"] = "не указан"
        changed = True

    if changed:
        _save_profile_record(context, str(user_id))

    return profile


def _find_admin_profile_by_tag_admin(context: ContextTypes.DEFAULT_TYPE, tag: str):
    normalized = str(tag or "").strip().lower()
    if not normalized:
        return None, None

    profiles = context.application.bot_data.setdefault("profiles", {})
    for user_id, profile in profiles.items():
        stored_tag = str(profile.get("tag_admin") or "").strip().lower()
        if stored_tag and stored_tag == normalized:
            return str(user_id), profile
    return None, None


def _set_last_admin_tag_for_user(context: ContextTypes.DEFAULT_TYPE, user_id: str | int, admin_tag: str | None) -> None:
    value = str(admin_tag or "").strip()
    if not value:
        return
    profile = _ensure_profile(context, str(user_id), f"id{user_id}")
    profile["last_admin_tag"] = value
    _save_profile_record(context, str(user_id))


def _set_user_blocked_bot_state(context: ContextTypes.DEFAULT_TYPE, user_id: str | int, is_blocked: bool) -> None:
    profile = _ensure_profile(context, str(user_id), f"id{user_id}")
    profile["bot_blocked_by_user"] = bool(is_blocked)
    _save_profile_record(context, str(user_id))


def _resolve_warn_target(context: ContextTypes.DEFAULT_TYPE, identifier: str):
    normalized = identifier.strip().lower()
    if not normalized.isdigit():
        return None, None

    profiles = context.application.bot_data.setdefault("profiles", {})
    admin_requests = context.application.bot_data.setdefault("admin_requests", {})
    active_chats = context.application.bot_data.setdefault("active_chats", {})

    # direct profile-id/user-id lookup
    for user_id, profile in profiles.items():
        ensured = _ensure_profile(context, str(user_id), profile.get("username"))
        profile_id = int(ensured.get("id_profile", 0) or 0)
        if str(profile_id) == normalized or str(user_id) == normalized:
            return str(user_id), ensured

    # lookup by telegram id in pending requests
    for user_id, req_info in admin_requests.items():
        if str(user_id) == normalized:
            return str(user_id), _ensure_profile(context, str(user_id), req_info.get("username"))

    # lookup by telegram id in active chats
    for user_id, session in active_chats.items():
        if str(user_id) == normalized:
            return str(user_id), _ensure_profile(context, str(user_id), session.get("username") or session.get("admin_username"))

    return None, None


def _resolve_ban_target(context: ContextTypes.DEFAULT_TYPE, identifier: str):
    target_user_id, profile = _resolve_warn_target(context, identifier)
    if target_user_id and profile:
        return target_user_id, profile

    return None, None


def _has_admin_ban_immunity(profile: dict) -> bool:
    try:
        return int(profile.get("admin_level", 0) or 0) > 0
    except Exception:
        return False


def _is_active_admin_candidate(context: ContextTypes.DEFAULT_TYPE, user_id: str) -> bool:
    state = (context.application.bot_data.get("admin_candidate_state", {}) or {}).get(str(user_id))
    if not state:
        return False
    return bool(state.get("stage") in ACTIVE_CANDIDATE_STAGES)


def _candidate_block_text() -> str:
    return "⚠️Сначала завершите анкету кандидата администратора."


def _candidate_tip_choices() -> list[tuple[str, str]]:
    return [
        ("chat", "🗣️Общение"),
        ("support", "❤️Поддержка"),
        ("flirt", "🔥Флирт"),
    ]


def _candidate_tip_value(selected_keys: list[str]) -> str:
    labels = {key: label for key, label in _candidate_tip_choices()}
    ordered_labels = [labels[key] for key, _ in _candidate_tip_choices() if key in set(selected_keys)]
    return ", ".join(ordered_labels) if ordered_labels else "не указано"


def _is_topic_admin(context: ContextTypes.DEFAULT_TYPE, user_id: str) -> bool:
    profile = (context.application.bot_data.get("profiles", {}) or {}).get(str(user_id), {})
    try:
        return int(profile.get("admin_level", 0) or 0) >= 4
    except Exception:
        return False


def _can_use_moderation_commands(context: ContextTypes.DEFAULT_TYPE, user_id: str) -> bool:
    profile = (context.application.bot_data.get("profiles", {}) or {}).get(str(user_id), {})
    try:
        return int(profile.get("admin_level", 0) or 0) >= 2
    except Exception:
        return False


def _has_admin_rights_level_1_5(profile: dict | None) -> bool:
    if not profile:
        return False
    try:
        return int(profile.get("admin_level", 0) or 0) in {1, 2, 3, 4, 5}
    except Exception:
        return False


def _admin_supports_mood(profile: dict | None, mood: str | None) -> bool:
    """Check whether an admin's selected dialogue types (tip_admin) include the
    requested mood (e.g. "общение", "поддержка", "флирт"). Used to prevent an
    admin from taking a request for a communication type they didn't select
    on their profile."""
    mood_normalized = str(mood or "").strip().lower()
    if not mood_normalized:
        return True

    tip_admin = str((profile or {}).get("tip_admin") or "").strip().lower()
    if not tip_admin or tip_admin == "не указано":
        return False

    return mood_normalized in tip_admin


def _admin_matches_gender(profile: dict | None, requested_gender: str | None) -> bool:
    """Check whether the requesting user's desired admin gender ("male"/"female")
    matches the admin's own gender set on their profile (admin_gender). Used to
    prevent e.g. a male admin from taking a request meant for a female admin."""
    requested_normalized = str(requested_gender or "").strip().lower()
    if requested_normalized not in {"male", "female"}:
        return True

    admin_gender = str((profile or {}).get("admin_gender") or "").strip().lower()
    if not admin_gender:
        return False

    if requested_normalized == "male":
        return "мальчик" in admin_gender
    return "девочка" in admin_gender


def _admin_default_prefix(level: int) -> str | None:
    return {
        1: "🧸Стажер",
        2: "🦅Младший админ",
        3: "🐶Админ",
        4: "👮‍♀️Старший админ",
        5: "Руководство",
    }.get(int(level))


def _build_user_stats_text(context: ContextTypes.DEFAULT_TYPE, target_user_id: str, profile: dict) -> str:
    username = str(profile.get("username") or f"id{target_user_id}")
    user_nickname = str(profile.get("user_nickname") or "не указан")
    message_user = int(profile.get("message_user", 0) or 0)
    warn_value = int(profile.get("warn", 0) or 0)
    profile_reason = str(profile.get("reason") or "нет причин")
    admin_level = int(profile.get("admin_level", 0) or 0)
    rank_title = ADMIN_LEVEL_TITLES.get(admin_level, "Нет админ-прав")
    date_value = str(profile.get("date_registration") or "не указана")

    active_session = (context.application.bot_data.get("active_chats", {}) or {}).get(str(target_user_id)) or {}
    session = "активна" if active_session.get("active") else "не активна"
    admin_tag = str(active_session.get("admin_tag") or active_session.get("admin_username") or profile.get("last_admin_tag") or "не указан")

    extra_status_lines = []
    if bool(profile.get("bot_blocked_by_user", False)):
        extra_status_lines.append("🚫Пользователь заблокировал у себя бота")

    banned_info = (context.application.bot_data.get("banned_users", {}) or {}).get(str(target_user_id))
    if banned_info:
        ban_reason = str(banned_info.get("reason") or "не указана")
        extra_status_lines.append(f"📛Пользователь находится в списке заблокированных / Причина: {ban_reason}")

    extra_status_text = ""
    if extra_status_lines:
        extra_status_text = "\n\n" + "\n".join(extra_status_lines)

    return (
        f"📈Профиль пользователя {username}\n\n"
        f"📩Сообщений: {message_user}\n"
        f"🤹‍♀️Юзернейм: {username}\n"
        f"🍓Никнейм: {user_nickname}\n"
        f"⚠️Предупреждений: {warn_value} // Последняя причина: {profile_reason}\n"
        f"🔰Уровень админ-прав: {rank_title}\n"
        f"📲Активная сессия: {session} // Последний админ: {admin_tag}\n\n"
        f"💿Регистрация в базе данных: {date_value}"
        f"{extra_status_text}"
    )


def _build_admin_stats_text(target_user_id: str, profile: dict) -> str:
    admin_level = int(profile.get("admin_level", 0) or 0)
    rank_title = ADMIN_LEVEL_TITLES.get(admin_level, "Не назначен")
    prefix_text = html.escape(str(profile.get("prefix") or "не установлен"))
    admin_tag = html.escape(str(profile.get("tag_admin") or "не указан"))
    tip_admin = html.escape(str(profile.get("tip_admin") or "не указано"))
    admin_gender = html.escape(str(profile.get("admin_gender") or "не указан"))
    admin_bio = html.escape(str(profile.get("biography_admin") or "не заполнена"))
    profile_username = html.escape(str(profile.get("username") or f"id{target_user_id}"))

    return (
        "🔰 <b>АДМИН-ПРОФИЛЬ</b>\n\n"
        f"🆔 <b>ID профиля:</b> {profile.get('id_profile')}\n"
        f"👤 <b>Username:</b> {profile_username}\n"
        f"🎀 <b>Префикс:</b> {prefix_text}\n"
        f"🎖 <b>Уровень:</b> {admin_level} ({rank_title})\n"
        f"🏷 <b>Тег:</b> {admin_tag}\n"
        f"💕Тип диалогов: {tip_admin}\n"
        f"👨‍👩‍👦 Пол: {admin_gender}\n"
        f"📖 <b>Биография:</b> {admin_bio}"
    )
