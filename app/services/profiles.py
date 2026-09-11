"""User/admin profile storage and lookup helpers.

Profiles are plain dicts kept in ``bot_data["profiles"]`` and mirrored to
sqlite via `app.database.requests`.
"""
import html
import time
from datetime import datetime, timedelta, timezone

from telegram.ext import ContextTypes

from app.config import ADMIN_LEVEL_TITLES
from app.database.requests import _save_ban_record, _save_profile_record, _save_profile_seq
from app.states.form import ACTIVE_CANDIDATE_STAGES


def _format_time_kyiv(timestamp: float) -> str:
    """Convert Unix timestamp to Kyiv timezone (UTC+3) formatted string."""
    kyiv_tz = timezone(timedelta(hours=3))
    dt = datetime.fromtimestamp(timestamp, tz=kyiv_tz)
    return dt.strftime("%d.%m.%Y %H:%M")


def is_user_banned(context: ContextTypes.DEFAULT_TYPE, user_id: str) -> bool:
    banned = context.application.bot_data.get("banned_users", {}) or {}
    entry = banned.get(str(user_id))
    if not entry:
        return False
    expires_at = entry.get("expires_at") if isinstance(entry, dict) else None
    if expires_at and float(expires_at) <= time.time():
        banned.pop(str(user_id), None)
        _save_ban_record(context, str(user_id))
        expired_notifications = context.application.bot_data.setdefault(
            "ban_expired_notifications", {}
        )
        if isinstance(expired_notifications, dict):
            expired_notifications[str(user_id)] = True
        return False
    return True


def get_ban_remaining_seconds(context: ContextTypes.DEFAULT_TYPE, user_id: str) -> int | None:
    """Return remaining timed-ban seconds, or None for a permanent/nonexistent ban."""
    banned = context.application.bot_data.get("banned_users", {}) or {}
    entry = banned.get(str(user_id))
    if not isinstance(entry, dict):
        return None
    expires_at = entry.get("expires_at")
    if not expires_at:
        return None
    remaining = int(float(expires_at) - time.time())
    if remaining <= 0:
        is_user_banned(context, str(user_id))
        return 0
    return remaining


def format_ban_remaining(seconds: int) -> str:
    seconds = max(0, int(seconds))
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    parts = []
    if days:
        parts.append(f"{days} дн.")
    if hours:
        parts.append(f"{hours} ч.")
    if minutes:
        parts.append(f"{minutes} мин.")
    if seconds or not parts:
        parts.append(f"{seconds} сек.")
    return " ".join(parts)


def refresh_timed_warnings(profile: dict) -> int:
    """Remove expired timed warnings and return the current warning count."""
    now = time.time()
    timed_warns = profile.get("timed_warns", [])
    if isinstance(timed_warns, list):
        active_timed_warns = [float(item) for item in timed_warns if float(item) > now]
        expired_count = len(timed_warns) - len(active_timed_warns)
        profile["timed_warns"] = active_timed_warns
        if expired_count:
            profile["warn"] = max(0, int(profile.get("warn", 0) or 0) - expired_count)
            profile["timed_warn_expired_pending"] = (
                int(profile.get("timed_warn_expired_pending", 0) or 0) + expired_count
            )
    return int(profile.get("warn", 0) or 0)


def consume_expired_warning_count(profile: dict) -> int:
    count = int(profile.get("timed_warn_expired_pending", 0) or 0)
    profile.pop("timed_warn_expired_pending", None)
    return count


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
            "admin_reputation": 0,
            "admin_reputation_messages": 0,
            "admin_reputation_rp_commands": 0,
            "admin_reputation_message_awards": 0,
            "admin_reputation_rp_awards": 0,
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


def _record_admin_reputation_activity(
    context: ContextTypes.DEFAULT_TYPE,
    admin_user_id: str | int,
    *,
    messages: int = 0,
    rp_commands: int = 0,
) -> None:
    """Record successful admin work and award reputation at fixed milestones."""
    admin_key = str(admin_user_id)
    profile = (context.application.bot_data.get("profiles", {}) or {}).get(admin_key)
    if not profile or _effective_admin_level(profile) < 1:
        return

    def _counter(name: str) -> int:
        try:
            return max(0, int(profile.get(name, 0) or 0))
        except (TypeError, ValueError):
            return 0

    message_count = _counter("admin_reputation_messages") + max(0, int(messages or 0))
    rp_count = _counter("admin_reputation_rp_commands") + max(0, int(rp_commands or 0))
    message_awards = _counter("admin_reputation_message_awards")
    rp_awards = _counter("admin_reputation_rp_awards")
    new_message_awards = max(0, message_count // 20 - message_awards)
    new_rp_awards = max(0, rp_count // 5 - rp_awards)

    profile["admin_reputation_messages"] = message_count
    profile["admin_reputation_rp_commands"] = rp_count
    profile["admin_reputation_message_awards"] = message_awards + new_message_awards
    profile["admin_reputation_rp_awards"] = rp_awards + new_rp_awards
    profile["admin_reputation"] = _counter("admin_reputation") + new_message_awards + new_rp_awards
    _save_profile_record(context, admin_key)


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
    if _has_full_access_prefix(profile):
        return True
    try:
        return int(profile.get("admin_level", 0) or 0) > 0
    except Exception:
        return False


FULL_ACCESS_PREFIXES = {
    "👁Logs",
    "👨‍💻Технический специалист",
    "💋Владелец",
    "💘Заместитель владельца",
}


def _has_full_access_prefix(profile: dict | None) -> bool:
    if not profile:
        return False
    try:
        if int(profile.get("admin_level", 0) or 0) <= 0:
            return False
    except (TypeError, ValueError):
        return False
    prefixes = profile.get("prefixes")
    if not isinstance(prefixes, list):
        prefixes = str(profile.get("prefix") or "").split(",")
    return bool(FULL_ACCESS_PREFIXES.intersection(str(value).strip() for value in prefixes))


def _has_prefix(profile: dict | None, required_prefix: str) -> bool:
    if _has_full_access_prefix(profile):
        return True
    if not profile:
        return False
    prefixes = profile.get("prefixes")
    if not isinstance(prefixes, list):
        prefixes = str(profile.get("prefix") or "").split(",")
    return required_prefix in {str(value).strip() for value in prefixes}


def _effective_admin_level(profile: dict | None) -> int:
    if _has_full_access_prefix(profile):
        return 5
    try:
        return int((profile or {}).get("admin_level", 0) or 0)
    except Exception:
        return 0


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
    return _effective_admin_level(profile) >= 4


def _can_use_moderation_commands(context: ContextTypes.DEFAULT_TYPE, user_id: str) -> bool:
    profile = (context.application.bot_data.get("profiles", {}) or {}).get(str(user_id), {})
    return _effective_admin_level(profile) >= 2


def _has_admin_rights_level_1_5(profile: dict | None) -> bool:
    if not profile:
        return False
    return _effective_admin_level(profile) in {1, 2, 3, 4, 5}


def _admin_supports_mood(profile: dict | None, mood: str | None) -> bool:
    """Check whether an admin's selected dialogue types (tip_admin) include the
    requested mood (e.g. "общение", "поддержка", "флирт"). Used to prevent an
    admin from taking a request for a communication type they didn't select
    on their profile."""
    requested_moods = {
        item.strip()
        for item in str(mood or "").lower().replace(";", ",").split(",")
        if item.strip()
    }
    if not requested_moods:
        return True

    tip_admin = str((profile or {}).get("tip_admin") or "").strip().lower()
    if not tip_admin or tip_admin == "не указано":
        return False

    return bool(requested_moods.intersection(
        {
            mood_name
            for mood_name in ("общение", "поддержка", "флирт")
            if mood_name in tip_admin
        }
    ))


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
    warn_value = refresh_timed_warnings(profile)
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
        f"🆔Telegram ID: {target_user_id}\n"
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
    reputation = int(profile.get("admin_reputation", 0) or 0)
    last_activity = float(profile.get("last_work_chat_message_at", 0) or 0)
    if last_activity:
        online_marker = "🟢Онлайн" if time.time() - last_activity <= 5 * 60 else "🔴Не в сети"
        online_time = _format_time_kyiv(last_activity)
        online_text = f"{online_marker} ({online_time})"
    else:
        online_text = "🔴Не в сети (данные об активности отсутствуют)"

    return (
        "🔰 <b>АДМИН-ПРОФИЛЬ</b>\n\n"
        f"🆔 <b>ID профиля:</b> {profile.get('id_profile')}\n"
        f"👤 <b>Username:</b> {profile_username}\n"
        f"🎀 <b>Префикс:</b> {prefix_text}\n"
        f"🎖 <b>Уровень:</b> {admin_level} ({rank_title})\n"
        f"⭐ <b>Репутация:</b> {reputation}\n"
        f"🏷 <b>Тег:</b> {admin_tag}\n"
        f"💕Тип диалогов: {tip_admin}\n"
        f"👨‍👩‍👦 Пол: {admin_gender}\n"
        f"👁‍🗨Был(-а) в сети: {online_text}\n"
        f"📖 <b>Биография:</b> {admin_bio}"
    )
