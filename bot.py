"""
Minimal Telegram bot using python-telegram-bot v20+ (async).
Set the environment variable `TELEGRAM_TOKEN` before running.
Install dependencies: `pip install -r requirements.txt`
"""

import os
import asyncio
import time
import re
import shlex
import sqlite3
import html
from datetime import datetime, timedelta
import logging
import json
try:
    import requests
    def _http_post(url, data, timeout=5):
        return requests.post(url, data=data, timeout=timeout)
except Exception:
    import urllib.request
    import urllib.parse
    class _DummyResponse:
        def __init__(self, code, text):
            self.status_code = code
            self.text = text

    def _http_post(url, data, timeout=5):
        encoded = urllib.parse.urlencode(data).encode()
        req = urllib.request.Request(url, data=encoded)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return _DummyResponse(resp.getcode(), resp.read().decode(errors='ignore'))
from telegram import BotCommand, ChatPermissions, Update, InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.error import BadRequest, Forbidden
from telegram.constants import ChatType, ParseMode
from telegram.ext import ApplicationBuilder, ApplicationHandlerStop, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

# Explicit token value.
TOKEN = "8664552259:AAEg3DStbZBtOMYpijNglBMycqy9U9NexuY"

logging.basicConfig(level=logging.INFO)

# Chat for bot log messages
LOG_CHAT_ID = -1004344722099
# Rules are published into this forum chat/topic
RULES_CHAT_ID = -1004417963273
RULES_THREAD_ID = 209
# Owner allowed to run debug commands
OWNER_ID = 7545068007
WORK_CHAT_ID = -1004417963273
COOPERATION_CHAT_ID = -1003918101019
OFFICIAL_CHANNEL_ID = -1003784351983
ADMIN_LEVEL_TITLES = {
    1: "🧸Стажер",
    2: "🦅Младший админ",
    3: "🐶Админ",
    4: "👮‍♀️Старший админ",
    5: "Руководство",
}
STATE_DIR = os.path.join(os.path.dirname(__file__), "bot_storage")
STATE_DB_PATH = os.path.join(STATE_DIR, "bot_state.sqlite3")


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


class TelegramLogHandler(logging.Handler):
    def __init__(self, token: str, chat_id: int):
        super().__init__()
        self.url = f"https://api.telegram.org/bot{token}/sendMessage"
        self.chat_id = chat_id
        self._last_http_request_time = 0.0
        self._throttle_window_seconds = 30 * 60

    def _should_emit(self, msg: str) -> bool:
        if "HTTP Request:" not in msg:
            return True
        now = time.time()
        if now - self._last_http_request_time < self._throttle_window_seconds:
            return False
        self._last_http_request_time = now
        return True

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            if len(msg) > 3500:
                msg = msg[:3500] + "..."
            payload = {"chat_id": self.chat_id, "text": msg}
            _http_post(self.url, payload, timeout=5)
        except Exception:
            pass


# attach handler to root logger
try:
    t_handler = TelegramLogHandler(TOKEN, LOG_CHAT_ID)
    t_handler.setLevel(logging.WARNING)
    t_handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
    logging.getLogger().addHandler(t_handler)
    # Emit a startup log so it appears in the special log chat (verifies handler works)
    logging.info(f"TelegramLogHandler initialized, logs will be sent to chat {LOG_CHAT_ID}")
except Exception:
    pass


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user:
        if _is_active_admin_candidate(context, str(user.id)):
            await update.message.reply_text(_candidate_block_text())
            return
        if is_user_banned(context, str(user.id)):
            await update.message.reply_text("⛔ Доступ к функциям бота для вашего аккаунта приостановлен на неопределённый срок.")
            return

        active = context.application.bot_data.get("active_chats", {}).get(str(user.id))
        if active and active.get("active", False):
            await update.message.reply_text(
                "🙂 **Вы уже в диалоге с администратором**\n\n"
                "На данный момент функция поиска нового собеседника недоступна, так как Вы уже общаетесь с нашим специалистом.\n\n"
                "Мы хотим, чтобы Вы получили максимум внимания и помощи от текущего администратора. Если у Вас возникнут вопросы или пожелания — Вы всегда можете обсудить их с ним напрямую.\n\n"
                "Если по какой-то причине Вы хотите завершить текущий диалог и начать новый — просто скажите об этом администратору. Он поможет Вам корректно завершить общение и при необходимости передаст Вас другому специалисту.",
                parse_mode=ParseMode.MARKDOWN,
            )
            return

    text = (
        "☀️ <b>Лучик, привет! Я так ждал тебя!</b>\n\n"
        "Посмотри на себя — ты уже сделал(а) большое дело! Ты пришел(ла) в место, где тебя поймут. "
        "Здесь нет оценок, нет \"правильных\" и \"неправильных\" ответов. Есть только ты и твой собеседник, "
        "и вы вместе разберете все твои чувства по полочкам.\n\n"
        "Иногда наши эмоции похожи на воздушные шарики: если их слишком много внутри, они могут лопнуть или улететь. "
        "А если их выпускать по одному и рассматривать, то становится легко и даже весело! "
        "Ты будешь учиться выпускать их с помощью общения или игр!\n\n"
        "Торопись быстрее, ведь администратор уже заждался тебя! Вы сможете:\n"
        "🧸 Послать тебе обнимашку в ответ на твою грусть;\n"
        "🌬️ Показать, как дышать, чтобы улетучилась вся тревога;\n"
        "💬 Или просто поговорить о твоем дне. Им правда-правда интересно!\n\n"
        "А еще, смотри, какие здесь кнопки Они как порталы в разные волшебные миры. Попробуй нажать на одну из них, "
        "и я сразу покажу тебе что-то интересное. Помни: ты самый важный человек для админов. Все будет супер! 🦋"
    )
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("⭐ Канал бота", url="https://t.me/Icyenvy_channel"),
                InlineKeyboardButton("❓ Помощь", callback_data="help")
            ]
        ]
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)

    menu_keyboard = _build_main_menu_keyboard(context, user.id, user.username)
    await update.message.reply_text(
        "Выберите действие в меню ниже:",
        reply_markup=menu_keyboard,
    )

async def help_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await block_if_banned(update, context):
        return
    await update.callback_query.answer()
    await update.callback_query.message.reply_text(
        "Если нужна помощь, просто напиши сюда, и я постараюсь помочь!"
    )

async def find_admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await block_if_banned(update, context):
        return
    await update.callback_query.answer()
    await update.callback_query.message.reply_text(
        "Админ уже в пути! Подождите немного, и мы обязательно с вами свяжемся."
    )


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


def _state_db_connection(context: ContextTypes.DEFAULT_TYPE):
    return context.application.bot_data.get("_state_db_connection")


def _json_safe(value):
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _save_runtime_snapshot(context: ContextTypes.DEFAULT_TYPE) -> None:
    conn = _state_db_connection(context)
    if conn is None:
        return
    snapshot = {
        key: _json_safe(value)
        for key, value in context.application.bot_data.items()
        if key != "_state_db_connection"
    }
    try:
        conn.execute(
            "INSERT OR REPLACE INTO runtime_state (id, data) VALUES (1, ?)",
            (json.dumps(snapshot, ensure_ascii=False),),
        )
        conn.commit()
    except Exception:
        logging.exception("Failed to persist runtime snapshot")


def _save_profile_seq(context: ContextTypes.DEFAULT_TYPE, seq: int | None = None) -> None:
    conn = _state_db_connection(context)
    if conn is None:
        return
    value = int(seq if seq is not None else context.application.bot_data.get("profile_seq", 0) or 0)
    try:
        conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
            ("profile_seq", str(value)),
        )
        conn.commit()
        _save_runtime_snapshot(context)
    except Exception:
        logging.exception("Failed to persist profile_seq")


def _save_profile_record(context: ContextTypes.DEFAULT_TYPE, user_id: str | int) -> None:
    conn = _state_db_connection(context)
    if conn is None:
        return
    profile = (context.application.bot_data.setdefault("profiles", {}) or {}).get(str(user_id))
    try:
        if profile is None:
            conn.execute("DELETE FROM profiles WHERE user_id = ?", (str(user_id),))
        else:
            conn.execute(
                "INSERT OR REPLACE INTO profiles (user_id, data) VALUES (?, ?)",
                (str(user_id), json.dumps(profile, ensure_ascii=False)),
            )
        conn.commit()
        _save_runtime_snapshot(context)
    except Exception:
        logging.exception("Failed to persist profile %s", user_id)


def _save_ban_record(context: ContextTypes.DEFAULT_TYPE, user_id: str | int) -> None:
    conn = _state_db_connection(context)
    if conn is None:
        return
    banned = (context.application.bot_data.setdefault("banned_users", {}) or {}).get(str(user_id))
    try:
        if banned is None:
            conn.execute("DELETE FROM banned_users WHERE user_id = ?", (str(user_id),))
        else:
            conn.execute(
                "INSERT OR REPLACE INTO banned_users (user_id, data) VALUES (?, ?)",
                (str(user_id), json.dumps(banned, ensure_ascii=False)),
            )
        conn.commit()
        _save_runtime_snapshot(context)
    except Exception:
        logging.exception("Failed to persist ban record %s", user_id)


def _init_persistent_storage(app) -> None:
    os.makedirs(STATE_DIR, exist_ok=True)
    conn = sqlite3.connect(STATE_DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    conn.execute("CREATE TABLE IF NOT EXISTS profiles (user_id TEXT PRIMARY KEY, data TEXT NOT NULL)")
    conn.execute("CREATE TABLE IF NOT EXISTS banned_users (user_id TEXT PRIMARY KEY, data TEXT NOT NULL)")
    conn.execute("CREATE TABLE IF NOT EXISTS runtime_state (id INTEGER PRIMARY KEY CHECK (id = 1), data TEXT NOT NULL)")

    app.bot_data["_state_db_connection"] = conn
    profiles = app.bot_data.setdefault("profiles", {})
    banned_users = app.bot_data.setdefault("banned_users", {})

    try:
        row = conn.execute("SELECT data FROM runtime_state WHERE id = 1").fetchone()
        if row and row[0]:
            try:
                snapshot = json.loads(row[0])
                for key, value in snapshot.items():
                    if key != "_state_db_connection":
                        app.bot_data[key] = value
            except Exception:
                logging.exception("Failed to load runtime snapshot")

        for user_id, data in conn.execute("SELECT user_id, data FROM profiles"):
            try:
                profiles[str(user_id)] = json.loads(data)
            except Exception:
                continue
        for user_id, data in conn.execute("SELECT user_id, data FROM banned_users"):
            try:
                banned_users[str(user_id)] = json.loads(data)
            except Exception:
                continue

        row = conn.execute("SELECT value FROM meta WHERE key = ?", ("profile_seq",)).fetchone()
        if row and str(row[0]).isdigit():
            app.bot_data["profile_seq"] = int(row[0])
    except Exception:
        logging.exception("Failed to load persistent state")


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


def _get_admin_mute_until(profile: dict) -> float:
    try:
        return float(profile.get("mute_until", 0) or 0)
    except Exception:
        return 0.0


def _clear_admin_mute(profile: dict) -> None:
    profile.pop("mute_until", None)
    profile.pop("mute_reason", None)
    profile.pop("mute_set_by", None)


def _is_admin_muted(profile: dict) -> bool:
    return _get_admin_mute_until(profile) > time.time()


def _mute_remaining_minutes(profile: dict) -> int:
    remaining_seconds = max(0, int(_get_admin_mute_until(profile) - time.time()))
    return max(1, (remaining_seconds + 59) // 60) if remaining_seconds else 0


def _admin_mute_permissions() -> ChatPermissions:
    return ChatPermissions(
        can_send_messages=False,
        can_send_polls=False,
        can_send_other_messages=False,
        can_add_web_page_previews=False,
        can_send_audios=False,
        can_send_documents=False,
        can_send_photos=False,
        can_send_videos=False,
        can_send_video_notes=False,
        can_send_voice_notes=False,
    )


def _admin_unmute_permissions() -> ChatPermissions:
    return ChatPermissions(
        can_send_messages=True,
        can_send_polls=True,
        can_send_other_messages=True,
        can_add_web_page_previews=True,
        can_send_audios=True,
        can_send_documents=True,
        can_send_photos=True,
        can_send_videos=True,
        can_send_video_notes=True,
        can_send_voice_notes=True,
    )


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


def _is_active_admin_candidate(context: ContextTypes.DEFAULT_TYPE, user_id: str) -> bool:
    state = (context.application.bot_data.get("admin_candidate_state", {}) or {}).get(str(user_id))
    if not state:
        return False
    return bool(
        state.get("stage") in {
            "await_tag",
            "await_bio",
            "await_tip_admin",
            "await_admin_gender",
            "pending_review",
            "await_reject_restart",
        }
    )


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


def _build_candidate_tip_keyboard(user_id: str, selected_keys: list[str]) -> InlineKeyboardMarkup:
    selected_set = set(selected_keys)
    tip_buttons = []
    for key, label in _candidate_tip_choices():
        prefix = "✅" if key in selected_set else ""
        tip_buttons.append(
            InlineKeyboardButton(
                f"{prefix}{label}",
                callback_data=f"candidate_tip_toggle_{user_id}_{key}",
            )
        )

    return InlineKeyboardMarkup(
        [
            tip_buttons,
            [InlineKeyboardButton("▶️Далее", callback_data=f"candidate_tip_next_{user_id}")],
        ]
    )


def _build_candidate_gender_keyboard(user_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[
            InlineKeyboardButton("👱🏻‍♂️Мальчик", callback_data=f"candidate_gender_{user_id}_male"),
            InlineKeyboardButton("🙍‍♀️Девочка", callback_data=f"candidate_gender_{user_id}_female"),
        ]]
    )


async def _post_init(app) -> None:
    try:
        await app.bot.set_my_commands(
            [
                BotCommand("start", "Запустить бота"),
                BotCommand("restart", "Обновить текущее подменю"),
            ]
        )
    except Exception:
        logging.exception("Failed to set bot commands")


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


def _admin_default_prefix(level: int) -> str | None:
    return {
        1: "🧸Стажер",
        2: "🦅Младший админ",
        3: "🐶Админ",
        4: "👮‍♀️Старший админ",
        5: "Руководство",
    }.get(int(level))


def _build_main_menu_keyboard(context: ContextTypes.DEFAULT_TYPE, user_id: int | str, username_hint: str | None = None) -> ReplyKeyboardMarkup:
    profile = _ensure_profile(context, str(user_id), username_hint or f"id{user_id}")
    profile_button = "🔰Админ-профиль" if _has_admin_rights_level_1_5(profile) else "👤 Профиль"
    return ReplyKeyboardMarkup(
        [[KeyboardButton("👤 Найти админа")], [KeyboardButton(profile_button)], [KeyboardButton("⚙️Настройки")]],
        resize_keyboard=True,
        one_time_keyboard=False,
    )


def _build_settings_menu_keyboard(profile: dict | None = None) -> ReplyKeyboardMarkup:
    profile_data = profile or {}
    ad_disabled = bool(profile_data.get("ad_disable_enabled", False))

    rows = [[KeyboardButton("💬Отправить жалобу")]]
    if ad_disabled:
        rows.append([KeyboardButton("🔔Включить рекламу")])
    else:
        rows.append([KeyboardButton("🔕Отключить рекламу")])
    rows.append([KeyboardButton("↩️Назад")])

    return ReplyKeyboardMarkup(
        rows,
        resize_keyboard=True,
        one_time_keyboard=False,
    )


def _build_complaint_menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton("👨‍🔧Сообщить о баге")],
            [KeyboardButton("👮‍♀️Пожаловаться на админа")],
            [KeyboardButton("↩️В настройки")],
        ],
        resize_keyboard=True,
        one_time_keyboard=False,
    )


def _build_active_dialog_admin_keyboard(request_user_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("⛔️ Выдать предупреждение", callback_data=f"warn_user_{request_user_id}"),
                InlineKeyboardButton("❌ Отказаться от пользователя", callback_data=f"decline_user_{request_user_id}"),
            ],
            [
                InlineKeyboardButton("📄Профиль пользователя", callback_data=f"show_user_profile_{request_user_id}"),
            ],
        ]
    )


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


async def topic_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_user:
        return

    allowed_topic_chats = {WORK_CHAT_ID, LOG_CHAT_ID, COOPERATION_CHAT_ID}
    if update.effective_chat.id not in allowed_topic_chats:
        await update.message.reply_text("Команда /topic доступна только в спец-группах.")
        return

    if not _is_topic_admin(context, str(update.effective_user.id)):
        await update.message.reply_text("Команда доступна только администраторам 4 категории и выше.")
        return

    raw_text = update.message.text or ""
    cmd_entity = update.message.entities[0] if update.message.entities else None
    cmd_len = cmd_entity.length if cmd_entity and cmd_entity.type == "bot_command" else len("/topic")
    args_text = raw_text[cmd_len:].strip()
    if not args_text:
        await update.message.reply_text('Используйте: /topic "url" del|close|open')
        return

    try:
        parts = shlex.split(args_text)
    except ValueError:
        await update.message.reply_text('Некорректный формат. Используйте: /topic "url" del|close|open')
        return

    if len(parts) < 2:
        await update.message.reply_text('Используйте: /topic "url" del|close|open')
        return

    topic_url = parts[0]
    action = parts[1].strip().lower()
    if action not in {"del", "close", "open"}:
        await update.message.reply_text('Доступные действия: del, close, open.')
        return

    chat_id, topic_id = _parse_topic_url(topic_url)
    if chat_id is None or topic_id is None:
        await update.message.reply_text('Некорректный URL темы. Нужен формат вида https://t.me/c/<chat_id>/<topic_id>.')
        return

    if chat_id not in allowed_topic_chats:
        await update.message.reply_text("Эта команда управляет только темами в спец-группах.")
        return

    state = _get_topic_state(context, chat_id, topic_id)

    if action == "del":
        try:
            await context.bot.delete_forum_topic(chat_id=chat_id, message_thread_id=topic_id)
            _set_topic_state(context, chat_id, topic_id, "deleted")
            await update.message.reply_text(f"✅Тема {topic_url} удалена.")
        except BadRequest as e:
            text = str(e).lower()
            if "not found" in text or "message thread" in text or "topic" in text:
                await update.message.reply_text("Тема не найдена или уже удалена.")
            else:
                await update.message.reply_text(f"Не удалось удалить тему: {e}")
        except Exception as e:
            await update.message.reply_text(f"Не удалось удалить тему: {e}")
        return

    if action == "close":
        if state == "closed":
            await update.message.reply_text("Тема уже закрыта.")
            return
        if state == "deleted":
            await update.message.reply_text("Тема уже удалена.")
            return

        close_method = getattr(context.bot, "close_forum_topic", None)
        if close_method is None:
            await update.message.reply_text("В этой версии бота закрытие тем недоступно.")
            return

        try:
            await close_method(chat_id=chat_id, message_thread_id=topic_id)
            _set_topic_state(context, chat_id, topic_id, "closed")
            await update.message.reply_text(f"✅Тема {topic_url} закрыта.")
        except BadRequest as e:
            text = str(e).lower()
            if "already closed" in text or "closed" in text:
                _set_topic_state(context, chat_id, topic_id, "closed")
                await update.message.reply_text("Тема уже закрыта.")
            elif "not found" in text or "message thread" in text or "topic" in text:
                await update.message.reply_text("Тема не найдена.")
            else:
                await update.message.reply_text(f"Не удалось закрыть тему: {e}")
        except Exception as e:
            await update.message.reply_text(f"Не удалось закрыть тему: {e}")
        return

    if action == "open":
        if state == "open":
            await update.message.reply_text("Тема уже открыта.")
            return
        if state == "deleted":
            await update.message.reply_text("Тема уже удалена.")
            return

        reopen_method = getattr(context.bot, "reopen_forum_topic", None)
        if reopen_method is None:
            await update.message.reply_text("В этой версии бота открытие тем недоступно.")
            return

        try:
            await reopen_method(chat_id=chat_id, message_thread_id=topic_id)
            _set_topic_state(context, chat_id, topic_id, "open")
            await update.message.reply_text(f"✅Тема {topic_url} открыта.")
        except BadRequest as e:
            text = str(e).lower()
            if "already open" in text or "not closed" in text or "open" in text:
                _set_topic_state(context, chat_id, topic_id, "open")
                await update.message.reply_text("Тема уже открыта.")
            elif "not found" in text or "message thread" in text or "topic" in text:
                await update.message.reply_text("Тема не найдена.")
            else:
                await update.message.reply_text(f"Не удалось открыть тему: {e}")
        except Exception as e:
            await update.message.reply_text(f"Не удалось открыть тему: {e}")
        return


async def _is_member_of_chat(context: ContextTypes.DEFAULT_TYPE, chat_id: int, user_id: int) -> bool:
    try:
        member = await context.bot.get_chat_member(chat_id=chat_id, user_id=user_id)
        status = getattr(member, "status", "")
        return status not in {"left", "kicked"}
    except Exception:
        return False


async def _send_candidate_stage_1(context: ContextTypes.DEFAULT_TYPE, user_id: int):
    await context.bot.send_message(
        chat_id=user_id,
        text=(
            "📇Придумайте для своего персонажа уникальный админ тег, которые пользователи в будущем будут видеть и могут выбрать вас.\n"
            "Пример тега (муж) #неистовый (жен) #акира"
        ),
    )


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
            await update.message.reply_text("Команда доступна только администраторам 4 категории и выше.")
            return

    raw_text = update.message.text or ""
    cmd_entity = update.message.entities[0] if update.message.entities else None
    cmd_len = cmd_entity.length if cmd_entity and cmd_entity.type == "bot_command" else len("/makeadmin")
    args_text = raw_text[cmd_len:].strip()
    if not args_text:
        await update.message.reply_text(
            'prefix_text: устанавливается после назначения (через /setprefix)\n'
            'Используйте: /makeadmin "id_profile" "lvl"'
        )
        return

    try:
        parts = shlex.split(args_text)
    except ValueError:
        await update.message.reply_text(
            'prefix_text: устанавливается после назначения (через /setprefix)\n'
            'Некорректный формат. Используйте: /makeadmin "id_profile" "lvl"'
        )
        return

    if len(parts) < 2:
        await update.message.reply_text(
            'prefix_text: устанавливается после назначения (через /setprefix)\n'
            'Используйте: /makeadmin "id_profile" "lvl"'
        )
        return

    target_identifier = parts[0]
    lvl_raw = parts[1].strip()
    if not lvl_raw.isdigit():
        await update.message.reply_text(
            "prefix_text: устанавливается после назначения (через /setprefix)\n"
            "lvl должен быть числом от 0 до 5."
        )
        return
    lvl = int(lvl_raw)
    rank_title = ADMIN_LEVEL_TITLES.get(lvl)
    if lvl != 0 and not rank_title:
        await update.message.reply_text("Доступные уровни: 0, 1, 2, 3, 4, 5.")
        return
    if issuer_level == 4 and lvl not in {0, 1, 2, 3}:
        await update.message.reply_text("Администратор 4 категории может выдавать только уровни 1, 2, 3 (или снимать права в 0).")
        return

    target_user_id, profile = _resolve_warn_target(context, target_identifier)
    if not profile:
        await update.message.reply_text(f'Пользователь с id_profile "{target_identifier}" не найден.')
        return

    preview_prefix = str(profile.get("prefix") or "").strip()
    if not preview_prefix and lvl > 0:
        preview_prefix = str(_admin_default_prefix(lvl) or "").strip()
    preview_prefix_text = preview_prefix or "не установлен"

    target_user_id_int = int(target_user_id)
    in_work_chat = await _is_member_of_chat(context, WORK_CHAT_ID, target_user_id_int)
    if not in_work_chat:
        await update.message.reply_text("Выдача невозможна: добавьте пользователя в рабочий чат.")
        return

    in_channel = await _is_member_of_chat(context, OFFICIAL_CHANNEL_ID, target_user_id_int)
    if not in_channel:
        await update.message.reply_text("Выдача невозможна: пользователь не подписан на официальный тгк бота.")
        return

    if lvl > 1:
        candidate_status = str(profile.get("admin_candidate_status") or "")
        tag_admin = str(profile.get("tag_admin") or "").strip()
        biography_admin = str(profile.get("biography_admin") or "").strip()
        if candidate_status != "approved" or not tag_admin or not biography_admin:
            await update.message.reply_text(
                f"Префикс: {preview_prefix_text}\n"
                "Выдача невозможна: сначала пользователь должен пройти кандидатуру 1 уровня и заполнить анкету (тег и биография)."
            )
            return

    active = context.application.bot_data.setdefault("active_chats", {})
    active.pop(str(target_user_id), None)
    admin_username = update.effective_user.username or f"id{update.effective_user.id}"

    if lvl == 0:
        current_admin_level = int(profile.get("admin_level", 0) or 0)
        if current_admin_level <= 0:
            await update.message.reply_text(
                f'Снятие невозможно: у пользователя "{target_identifier}" нет активных админ-прав.'
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
            f'☑️Пользователь "{target_identifier}" был разжалован, админ-права сняты.'
        )

        try:
            await context.bot.send_message(
                chat_id=target_user_id_int,
                text=(
                    "⚠️Ваши админ-права были сняты.\n\n"
                    f'Решение принял: "{admin_username}".'
                ),
            )
        except Forbidden:
            await update.message.reply_text("Пользователь заблокировал бота. Уведомление о разжаловании отправить не удалось.")
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
    prefix_text = str(profile.get("prefix") or "не установлен")

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
            f'☑️Пользователь "{target_identifier}" был успешно назначен на админ-права, инструктаж ему отправлен в ЛС.\n'
            f'Префикс: {prefix_text}\n'
            'Уровень: 1'
        )

        try:
            await context.bot.send_message(chat_id=target_user_id_int, text="⚙️Клавиатура обновлена.", reply_markup=ReplyKeyboardRemove())
            await context.bot.send_message(
                chat_id=target_user_id_int,
                text=(
                    f"Префикс: {prefix_text}\n"
                    "❤️‍🔥Поздравляем! Вы были назначены администратором 1 уровня. "
                    f'Назначил вас "{admin_username}"\n\n'
                    "📝Перед началой работы вам нужно заполнить информацию о себе..."
                ),
            )
            await asyncio.sleep(2)
            await _send_candidate_stage_1(context, target_user_id_int)
        except Forbidden:
            await update.message.reply_text("Пользователь заблокировал бота. Инструктаж в ЛС отправить не удалось.")
        except Exception as e:
            logging.exception("makeadmin onboarding send failed: %s", e)
        return

    profile["admin_candidate"] = False
    profile["admin_candidate_status"] = "approved"
    state_map = context.application.bot_data.setdefault("admin_candidate_state", {})
    state_map.pop(str(target_user_id), None)

    await update.message.reply_text(
        f'☑️Пользователь "{target_identifier}" был успешно назначен.\n'
        f'Префикс: {prefix_text}\n'
        f'Уровень: {lvl} ({rank_title}).'
    )

    try:
        await context.bot.send_message(
            chat_id=target_user_id_int,
            text=(
                f"❤️‍🔥Поздравляем! Вы были назначены: {rank_title}. "
                f'Назначил вас "{admin_username}"'
            ),
        )
    except Forbidden:
        await update.message.reply_text("Пользователь заблокировал бота. Уведомление отправить не удалось.")
    except Exception as e:
        logging.exception("makeadmin notify failed: %s", e)
    _save_profile_record(context, str(target_user_id))


async def makeadmin_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _makeadmin_impl(update, context, owner_bypass=False)


async def prava_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or update.effective_user.id != OWNER_ID:
        if update.message:
            await update.message.reply_text("Команда доступна только владельцу бота.")
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
        await update.message.reply_text("✅Права 5 категории выданы пользователю с Telegram ID 7545068007.")


async def admin_candidate_private_flow(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    if not update.message or not update.effective_user:
        return False
    user_id = str(update.effective_user.id)
    state_map = context.application.bot_data.setdefault("admin_candidate_state", {})
    state = state_map.get(user_id)
    if not state:
        return False

    stage = state.get("stage")
    if stage not in {"await_tag", "await_bio", "await_tip_admin", "await_admin_gender", "await_reject_restart"}:
        return False

    profile = _ensure_profile(context, user_id, update.effective_user.username or f"id{user_id}")
    text = (update.message.text or "").strip()

    if stage == "await_tag":
        if "#" not in text:
            await update.message.reply_text("Тег написан неправильно. Пример: #акира")
            return True
        profile["tag_admin"] = text
        state["stage"] = "await_bio"
        await update.message.reply_text("🔮Теперь напишите биографию своего персонажа (пример: любимое хобби, вредные привычки, распорядок дня )")
        return True

    if stage == "await_bio":
        if not text:
            await update.message.reply_text("Биография не может быть пустой.")
            return True
        profile["biography_admin"] = text
        state["stage"] = "await_tip_admin"
        state["tip_admin_selected"] = []

        await update.message.reply_text(
            "💬Теперь укажите тип общения которые вы больше всего можете обсуждать с будущими пользователями (можно выбрать все три)",
            reply_markup=_build_candidate_tip_keyboard(user_id, []),
        )
        return True

    if stage == "await_tip_admin":
        await update.message.reply_text("Выберите типы общения кнопками под сообщением.")
        return True

    if stage == "await_admin_gender":
        await update.message.reply_text("Выберите пол кнопками под сообщением.")
        return True

    if stage == "await_reject_restart":
        if "#" not in text:
            await update.message.reply_text("Тег написан неправильно. Пример: #акира")
            return True
        profile["tag_admin"] = text
        state["stage"] = "await_bio"
        await update.message.reply_text("🔮Теперь напишите биографию своего персонажа (пример: любимое хобби, вредные привычки, распорядок дня )")
        return True

    return False


async def candidate_tip_toggle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    data = update.callback_query.data or ""
    parts = data.split("_")
    if len(parts) < 5:
        return

    user_id = parts[3]
    tip_key = parts[4]
    if not update.effective_user or str(update.effective_user.id) != str(user_id):
        await update.callback_query.answer("Кнопка доступна только владельцу анкеты", show_alert=True)
        return

    if tip_key not in {"chat", "support", "flirt"}:
        return

    state_map = context.application.bot_data.setdefault("admin_candidate_state", {})
    state = state_map.get(str(user_id)) or {}
    if state.get("stage") != "await_tip_admin":
        await update.callback_query.answer("Этап выбора типов общения уже завершен", show_alert=True)
        return

    selected = list(state.get("tip_admin_selected") or [])
    if tip_key in selected:
        selected.remove(tip_key)
    else:
        selected.append(tip_key)
    state["tip_admin_selected"] = selected

    try:
        await update.callback_query.message.edit_reply_markup(
            reply_markup=_build_candidate_tip_keyboard(str(user_id), selected)
        )
    except Exception:
        pass


async def candidate_tip_next_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    data = update.callback_query.data or ""
    parts = data.split("_")
    if len(parts) < 4:
        return

    user_id = parts[3]
    if not update.effective_user or str(update.effective_user.id) != str(user_id):
        await update.callback_query.answer("Кнопка доступна только владельцу анкеты", show_alert=True)
        return

    state_map = context.application.bot_data.setdefault("admin_candidate_state", {})
    state = state_map.get(str(user_id)) or {}
    if state.get("stage") != "await_tip_admin":
        await update.callback_query.answer("Этап выбора типов общения уже завершен", show_alert=True)
        return

    selected = list(state.get("tip_admin_selected") or [])
    if not selected:
        await update.callback_query.answer("Выберите хотя бы один тип общения", show_alert=True)
        return

    profile = _ensure_profile(context, str(user_id), update.effective_user.username or f"id{user_id}")
    profile["tip_admin"] = _candidate_tip_value(selected)
    state["stage"] = "await_admin_gender"
    _save_profile_record(context, str(user_id))

    await update.callback_query.message.reply_text(
        "**Пожалуйста, укажите Ваш пол**\nЭто надо чтобы пользователи знали с кем будут вести диалог",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=_build_candidate_gender_keyboard(str(user_id)),
    )


async def candidate_gender_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    data = update.callback_query.data or ""
    parts = data.split("_")
    if len(parts) < 4:
        return

    user_id = parts[2]
    gender_key = parts[3]
    if not update.effective_user or str(update.effective_user.id) != str(user_id):
        await update.callback_query.answer("Кнопка доступна только владельцу анкеты", show_alert=True)
        return

    if gender_key not in {"male", "female"}:
        return

    state_map = context.application.bot_data.setdefault("admin_candidate_state", {})
    state = state_map.get(str(user_id)) or {}
    if state.get("stage") != "await_admin_gender":
        await update.callback_query.answer("Этап выбора пола уже завершен", show_alert=True)
        return

    profile = _ensure_profile(context, str(user_id), update.effective_user.username or f"id{user_id}")
    profile["admin_gender"] = "👱🏻‍♂️Мальчик" if gender_key == "male" else "🙍‍♀️Девочка"
    state["stage"] = "pending_review"

    await update.callback_query.message.reply_text(
        "📌Обратите внимания, после того как вы вступили в ряды администрации нашего бота, то вы обязаны выполнять дневную норму сообщений и слушать указания старшей администрации и не нарушать правила анонимности пользователей и админов, в плохом случае вы не пробудите у нас долго."
    )
    await update.callback_query.message.reply_text("✅Заявка отправлена на обработку старшей администрации.")

    review_id = str(int(context.application.bot_data.get("admin_candidate_review_seq", 0) or 0) + 1)
    context.application.bot_data["admin_candidate_review_seq"] = int(review_id)
    pending_reviews = context.application.bot_data.setdefault("pending_admin_candidate_reviews", {})
    pending_reviews[review_id] = {
        "user_id": str(user_id),
        "username": profile.get("username") or f"id{user_id}",
        "tag_admin": profile.get("tag_admin", ""),
        "biography_admin": profile.get("biography_admin", ""),
        "tip_admin": profile.get("tip_admin", "не указано"),
        "admin_gender": profile.get("admin_gender", "не указан"),
    }

    review_text = (
        f'📁Кандидат {pending_reviews[review_id]["username"]} отправил свою заявку на обработку.\n'
        f'📎Тег: {pending_reviews[review_id]["tag_admin"]}\n\n'
        f'📝Биография: {pending_reviews[review_id]["biography_admin"]}\n\n'
        f'💕Тип диалогов: {pending_reviews[review_id]["tip_admin"]}\n'
        f'👨‍👩‍👦Пол: {pending_reviews[review_id]["admin_gender"]}'
    )
    kb = InlineKeyboardMarkup(
        [[
            InlineKeyboardButton("✅Одобрить кандидата", callback_data=f"approve_candidate_{review_id}"),
            InlineKeyboardButton("❌Отказать кандидату", callback_data=f"reject_candidate_{review_id}"),
        ]]
    )
    await context.bot.send_message(chat_id=LOG_CHAT_ID, text=review_text, reply_markup=kb)
    _save_profile_record(context, str(user_id))
    _save_runtime_snapshot(context)


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


async def amute_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != WORK_CHAT_ID or not update.message:
        return

    if not _can_use_moderation_commands(context, str(update.effective_user.id)):
        await update.message.reply_text("Команда доступна только администраторам 2 категории и выше.")
        return

    raw_text = update.message.text or ""
    cmd_entity = update.message.entities[0] if update.message.entities else None
    cmd_len = cmd_entity.length if cmd_entity and cmd_entity.type == "bot_command" else len("/amute")
    args_text = raw_text[cmd_len:].strip()
    if not args_text:
        await update.message.reply_text('Укажите id_profile и minute: /amute "id_profile" "minute"')
        return

    try:
        parts = shlex.split(args_text)
    except ValueError:
        await update.message.reply_text('Некорректный формат. Используйте: /amute "id_profile" "minute"')
        return

    if len(parts) < 2:
        await update.message.reply_text('Укажите id_profile и minute: /amute "id_profile" "minute"')
        return

    target_identifier = parts[0]
    minute_raw = parts[1].strip()
    if not minute_raw.isdigit():
        await update.message.reply_text("minute должен быть целым числом больше 0.")
        return

    minutes = int(minute_raw)
    if minutes <= 0:
        await update.message.reply_text("minute должен быть целым числом больше 0.")
        return

    target_user_id, profile = _resolve_warn_target(context, target_identifier)
    if not profile:
        await update.message.reply_text(f'Пользователь с id_profile "{target_identifier}" не найден.')
        return

    if _has_admin_rights_level_1_5(profile):
        await update.message.reply_text(f'Невозможно выдать мут: id_profile #{profile.get("id_profile")} имеет админ-права 1-5 уровня.')
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
        await update.message.reply_text("Не удалось выдать мут: у бота нет прав ограничивать участников в этой группе.")
        return
    except Exception:
        await update.message.reply_text("Не удалось выдать мут через настройки Telegram.")
        return

    profile["mute_until"] = time.time() + minutes * 60
    profile["mute_reason"] = f"mute for {minutes} minutes"
    profile["mute_set_by"] = str(update.effective_user.id)

    await update.message.reply_text(f'✅Администратор id_profile #{profile.get("id_profile")} получил мут на {minutes} мин.')

    try:
        await context.bot.send_message(
            chat_id=int(target_user_id),
            text=f'⛔️Вам выдан мут на {minutes} мин. В это время ваши сообщения в теме не будут отправляться пользователю.',
        )
    except Exception:
        pass
    _save_profile_record(context, str(target_user_id))


async def unmute_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != WORK_CHAT_ID or not update.message:
        return

    if not _can_use_moderation_commands(context, str(update.effective_user.id)):
        await update.message.reply_text("Команда доступна только администраторам 2 категории и выше.")
        return

    raw_text = update.message.text or ""
    cmd_entity = update.message.entities[0] if update.message.entities else None
    cmd_len = cmd_entity.length if cmd_entity and cmd_entity.type == "bot_command" else len("/aunmute")
    args_text = raw_text[cmd_len:].strip()
    if not args_text:
        await update.message.reply_text('Укажите id_profile: /aunmute "id_profile"')
        return

    try:
        parts = shlex.split(args_text)
    except ValueError:
        await update.message.reply_text('Некорректный формат. Используйте: /aunmute "id_profile"')
        return

    if len(parts) < 1:
        await update.message.reply_text('Укажите id_profile: /aunmute "id_profile"')
        return

    target_identifier = parts[0]
    target_user_id, profile = _resolve_warn_target(context, target_identifier)
    if not profile:
        await update.message.reply_text(f'Пользователь с id_profile "{target_identifier}" не найден.')
        return

    try:
        await context.bot.restrict_chat_member(
            chat_id=WORK_CHAT_ID,
            user_id=int(target_user_id),
            permissions=_admin_unmute_permissions(),
            use_independent_chat_permissions=True,
        )
    except Forbidden:
        await update.message.reply_text("Не удалось снять мут: у бота нет прав изменять ограничения участников в этой группе.")
        return
    except Exception:
        await update.message.reply_text("Не удалось снять мут через настройки Telegram.")
        return

    _clear_admin_mute(profile)
    await update.message.reply_text(f'✅С администратора id_profile #{profile.get("id_profile")} снят мут.')

    try:
        await context.bot.send_message(
            chat_id=int(target_user_id),
            text="✅С вас снят мут. Теперь ваши сообщения снова будут отправляться пользователю.",
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
        await update.message.reply_text(f"⛔️Вы находитесь в муте еще {remaining_minutes} мин.")
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
        await message.reply_text("В этом чате админские команды недоступны.")
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
        await update.message.reply_text("Команда /sendpiar доступна только в чате сотрудничества.")
        return

    bot_data = context.application.bot_data
    now_ts = time.time()
    cooldown_until = float(bot_data.get("sendpiar_cooldown_until", 0) or 0)
    cooldown_was_active = bool(bot_data.get("sendpiar_cooldown_was_active", False))

    if cooldown_until > now_ts:
        remaining_seconds = int(cooldown_until - now_ts)
        remaining_minutes = max(1, (remaining_seconds + 59) // 60)
        await update.message.reply_text(
            f"⏳Команда /sendpiar на кулдауне. Подождите {remaining_minutes} мин."
        )
        return

    if cooldown_until and cooldown_was_active and now_ts >= cooldown_until:
        await update.message.reply_text("✅Кулдаун завершен. Команда /sendpiar снова доступна.")
        bot_data["sendpiar_cooldown_was_active"] = False

    issuer_profile = _ensure_profile(
        context,
        str(update.effective_user.id),
        update.effective_user.username or f"id{update.effective_user.id}",
    )
    issuer_prefix = str(issuer_profile.get("prefix") or "").strip()
    if issuer_prefix != "💎Сотрудничество":
        await update.message.reply_text("Нельзя выполнить команду: нужен префикс 💎Сотрудничество.")
        return

    cmd_entities = update.message.entities if update.message.text else (update.message.caption_entities or [])
    cmd_entity = cmd_entities[0] if cmd_entities else None
    cmd_len = cmd_entity.length if cmd_entity and cmd_entity.type == "bot_command" else len("/sendpiar")
    args_text = source_text[cmd_len:]

    if not str(args_text).strip():
        await update.message.reply_text('Используйте: /sendpiar "текст" (можно с фото или видео).')
        return

    quoted = re.match(r'^\s*"([\s\S]*)"\s*$', args_text)
    broadcast_text = quoted.group(1) if quoted else args_text.strip()
    if not broadcast_text:
        await update.message.reply_text("Текст рассылки не должен быть пустым.")
        return

    payload_text = broadcast_text
    if not payload_text.lower().startswith("#реклама"):
        payload_text = f"#реклама\n\n{payload_text}"

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
        await update.message.reply_text("В базе нет пользователей для рассылки.")
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

    report_text = f"✅Рассылка отправлена всем пользователям бота.\nУспешно: {success_count}\nОшибок: {failed_count}"
    if immune_profiles:
        immune_profiles = sorted([pid for pid in immune_profiles if int(pid or 0) > 0])
        immune_lines = "\n".join([f"id_profile #{pid} имеет иммунитет к рекламе" for pid in immune_profiles])
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
        await update.message.reply_text("Команда доступна только администраторам 3 категории и выше.")
        return

    raw_text = update.message.text or ""
    cmd_entity = update.message.entities[0] if update.message.entities else None
    cmd_len = cmd_entity.length if cmd_entity and cmd_entity.type == "bot_command" else len("/anpiar")
    args_text = raw_text[cmd_len:].strip()
    if not args_text:
        await update.message.reply_text('Используйте: /anpiar "id_profile" "1-0"')
        return

    try:
        parts = shlex.split(args_text)
    except ValueError:
        await update.message.reply_text('Некорректный формат. Используйте: /anpiar "id_profile" "1-0"')
        return

    if len(parts) < 2:
        await update.message.reply_text('Используйте: /anpiar "id_profile" "1-0"')
        return

    target_identifier = parts[0]
    mode_raw = str(parts[1]).strip()
    if mode_raw not in {"1", "0"}:
        await update.message.reply_text('Второй аргумент должен быть "1" или "0".')
        return

    target_user_id, target_profile = _resolve_warn_target(context, target_identifier)
    if not target_profile:
        await update.message.reply_text(f'Пользователь с id_profile "{target_identifier}" не найден.')
        return

    has_access = bool(target_profile.get("ad_disable_access", False))
    target_id_profile = int(target_profile.get("id_profile", 0) or 0)

    if mode_raw == "1":
        if has_access:
            await update.message.reply_text(f"У пользователя id_profile #{target_id_profile} уже есть права на отключение рекламы.")
            return
        target_profile["ad_disable_access"] = True
        _save_profile_record(context, str(target_user_id))
        await update.message.reply_text(f"✅Права на отключение рекламы выданы пользователю id_profile #{target_id_profile}.")
        try:
            await context.bot.send_message(
                chat_id=int(target_user_id),
                text="✅Вам выданы права на отключение рекламы. Откройте ⚙️Настройки и нажмите 🔕Отключить рекламу.",
            )
        except Exception:
            pass
        return

    if not has_access:
        await update.message.reply_text(f"У пользователя id_profile #{target_id_profile} нет прав на отключение рекламы.")
        return

    target_profile["ad_disable_access"] = False
    target_profile["ad_disable_enabled"] = False
    _save_profile_record(context, str(target_user_id))
    await update.message.reply_text(f"✅Права на отключение рекламы сняты у пользователя id_profile #{target_id_profile}.")
    try:
        await context.bot.send_message(
            chat_id=int(target_user_id),
            text="⚠️Права на отключение рекламы были отозваны.",
        )
    except Exception:
        pass


async def warn_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != LOG_CHAT_ID or not update.message:
        return

    if not _can_use_moderation_commands(context, str(update.effective_user.id)):
        await update.message.reply_text("Команда доступна только администраторам 2 категории и выше.")
        return

    raw_text = update.message.text or ""
    cmd_entity = update.message.entities[0] if update.message.entities else None
    cmd_len = cmd_entity.length if cmd_entity and cmd_entity.type == "bot_command" else len("/warn")
    args_text = raw_text[cmd_len:].strip()
    if not args_text:
        await update.message.reply_text("Укажите id_profile и причину: /warn \"id_profile\" \"reason\"")
        return

    try:
        parts = shlex.split(args_text)
    except ValueError:
        await update.message.reply_text("Некорректный формат. Используйте: /warn \"id_profile\" \"reason\"")
        return

    if len(parts) < 2:
        await update.message.reply_text("Укажите id_profile и причину: /warn \"id_profile\" \"reason\"")
        return

    target_identifier = parts[0]
    reason = " ".join(parts[1:]).strip()
    if not reason:
        await update.message.reply_text("Укажите причину предупреждения.")
        return

    target_user_id, profile = _resolve_warn_target(context, target_identifier)
    if not profile:
        await update.message.reply_text(f'Пользователь с id_profile "{target_identifier}" не найден.')
        return

    if _has_admin_rights_level_1_5(profile):
        await update.message.reply_text(
            f'Невозможно выдать предупреждение: id_profile #{profile.get("id_profile")} имеет админ-права 1-5 уровня.'
        )
        return

    current_warn = int(profile.get("warn", 0) or 0)
    profile["warn"] = current_warn + 1
    profile["reason"] = reason
    await enforce_autoban_if_needed(context, str(target_user_id), profile.get("username"))

    await update.message.reply_text(
        f'⚠️Пользователю id_profile #{profile.get("id_profile")} выдано предупреждение. Теперь у него {profile["warn"]} warn(-ов). Причина: "{reason}"'
    )

    try:
        await context.bot.send_message(
            chat_id=int(target_user_id),
            text=f'❗️Вы получили предупреждение с причиной "{reason}" от руководства бота. Теперь у вас {profile["warn"]} предупреждений',
        )
    except Exception:
        pass
    _save_profile_record(context, str(target_user_id))


async def stats_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    allowed_special_chats = _special_admin_chat_ids()
    if update.effective_chat.id not in allowed_special_chats or not update.message:
        return

    if not _can_use_moderation_commands(context, str(update.effective_user.id)):
        await update.message.reply_text("Команда доступна только администраторам 2 категории и выше.")
        return

    raw_text = update.message.text or ""
    cmd_entity = update.message.entities[0] if update.message.entities else None
    cmd_len = cmd_entity.length if cmd_entity and cmd_entity.type == "bot_command" else len("/stats")
    args_text = raw_text[cmd_len:].strip()
    if not args_text:
        await update.message.reply_text('Используйте: /stats "id_profile"')
        return

    try:
        parts = shlex.split(args_text)
    except ValueError:
        await update.message.reply_text('Некорректный формат. Используйте: /stats "id_profile"')
        return

    if len(parts) < 1:
        await update.message.reply_text('Используйте: /stats "id_profile"')
        return

    target_identifier = parts[0]
    target_user_id, profile = _resolve_warn_target(context, target_identifier)
    if not profile:
        await update.message.reply_text(f'Пользователь с id_profile "{target_identifier}" не найден.')
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
        await update.message.reply_text("Команда доступна только администраторам 4 категории и выше.")
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
        "🔍Полная статистика базы данных морозной зависти\n\n"
        f"Зарегистрировано пользователей: {users_regist}\n"
        f"Пользователей которые заблокировали бота: {banned_users_regist}\n"
        f"Администраторов: {admins_regist}\n"
        f"Активных сессий: {session_regist}\n"
        f"Отправленных сообщений: {send_regist}\n"
        f"Полученных ответов: {otvet_regist}"
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
        await update.message.reply_text("Команда доступна только администраторам 4 категории и выше.")
        return

    raw_text = update.message.text or ""
    cmd_entity = update.message.entities[0] if update.message.entities else None
    cmd_len = cmd_entity.length if cmd_entity and cmd_entity.type == "bot_command" else len("/astats")
    args_text = raw_text[cmd_len:].strip()
    if not args_text:
        await update.message.reply_text('Используйте: /astats "id_profile"')
        return

    try:
        parts = shlex.split(args_text)
    except ValueError:
        await update.message.reply_text('Некорректный формат. Используйте: /astats "id_profile"')
        return

    if len(parts) < 1:
        await update.message.reply_text('Используйте: /astats "id_profile"')
        return

    target_identifier = parts[0]
    target_user_id, profile = _resolve_warn_target(context, target_identifier)
    if not profile:
        await update.message.reply_text(f'Пользователь с id_profile "{target_identifier}" не найден.')
        return

    if not _has_admin_rights_level_1_5(profile):
        await update.message.reply_text(
            f'Нельзя использовать /astats: id_profile #{profile.get("id_profile")} не является администратором.'
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


def _build_astats_profile_keyboard(session_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🎖Сменить тег", callback_data=f"astats_tag_change_{session_id}")],
            [InlineKeyboardButton("🔁Сменить пол", callback_data=f"astats_gender_menu_{session_id}")],
            [InlineKeyboardButton("📖Изменить биографию", callback_data=f"astats_bio_change_{session_id}")],
            [
                InlineKeyboardButton("💕Тип диалога", callback_data=f"astats_tip_menu_{session_id}"),
                InlineKeyboardButton("👤Активные ПЗ", callback_data=f"astats_active_pz_{session_id}"),
            ],
        ]
    )


def _build_astats_gender_editor_keyboard(session_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🙎‍♂️Мальчик", callback_data=f"astats_gender_set_{session_id}_male"),
                InlineKeyboardButton("🙍‍♀️Девочка", callback_data=f"astats_gender_set_{session_id}_female"),
            ],
            [InlineKeyboardButton("↩️Назад", callback_data=f"astats_gender_back_{session_id}")],
        ]
    )


def _build_astats_tip_editor_keyboard(session_id: str, selected_keys: list[str] | None = None) -> InlineKeyboardMarkup:
    selected = set(selected_keys or [])
    choices = [
        ("chat", "🗣️**Общение**"),
        ("support", "❤️**Поддержка**"),
        ("flirt", "🔥**Флирт**"),
    ]
    rows = []
    for key, label in choices:
        prefix = "✅" if key in selected else ""
        rows.append([InlineKeyboardButton(f"{prefix}{label}", callback_data=f"astats_tip_toggle_{session_id}_{key}")])
    rows.append([InlineKeyboardButton("🔰Применить", callback_data=f"astats_tip_apply_{session_id}")])
    return InlineKeyboardMarkup(rows)


def _is_rp_action_text(text: str | None) -> bool:
    value = (text or "").strip().lower()
    return value.startswith("/me ") or value.startswith("*")


def _build_info_topic_keyboard(panel_id: str, paused: bool = False, pending_action: str | None = None) -> InlineKeyboardMarkup:
    if pending_action == "close":
        return InlineKeyboardMarkup(
            [[
                InlineKeyboardButton("✅Уверен", callback_data=f"info_topic_close_confirm_{panel_id}"),
                InlineKeyboardButton("❌Отмена", callback_data=f"info_topic_cancel_{panel_id}"),
            ]]
        )

    if pending_action == "stop":
        return InlineKeyboardMarkup(
            [[
                InlineKeyboardButton("✅Уверен", callback_data=f"info_topic_stop_confirm_{panel_id}"),
                InlineKeyboardButton("❌Отмена", callback_data=f"info_topic_cancel_{panel_id}"),
            ]]
        )

    second_label = "✅Возомновить сессию" if paused else "💤Остановить общение"
    second_callback = f"info_topic_resume_{panel_id}" if paused else f"info_topic_stop_{panel_id}"
    return InlineKeyboardMarkup(
        [[
            InlineKeyboardButton("❌Закрыть общение", callback_data=f"info_topic_close_{panel_id}"),
            InlineKeyboardButton(second_label, callback_data=second_callback),
        ]]
    )


def _build_info_topic_text(username_pz: str, admin_username: str, detect: int, msg_topic: int, rp_topic: int, date_value: str, topic_link: str) -> str:
    return (
        f"📋Информация о переписке {username_pz} с админом {admin_username}\n\n"
        f"⚠️Подозрительных сообщений: {detect}\n"
        f"📥Сообщений в теме: {msg_topic}\n"
        f"💞РП действий: {rp_topic}\n\n"
        f"🕘Регистрация запроса от пользователя: {date_value}"
    )


def _next_info_topic_panel_id(context: ContextTypes.DEFAULT_TYPE) -> str:
    seq = int(context.application.bot_data.get("info_topic_panel_seq", 0) or 0) + 1
    context.application.bot_data["info_topic_panel_seq"] = seq
    return str(seq)


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
        await update.callback_query.answer("Панель устарела", show_alert=True)
        return

    if str(update.effective_user.id) != str(session.get("issuer_user_id")):
        await update.callback_query.answer("Кнопка доступна только автору /astats", show_alert=True)
        return

    issuer_profile = _ensure_profile(
        context,
        str(update.effective_user.id),
        update.effective_user.username or f"id{update.effective_user.id}",
    )
    if int(issuer_profile.get("admin_level", 0) or 0) < 4:
        await update.callback_query.answer("Недостаточно прав", show_alert=True)
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
        "Отправьте новый тег (должен начинаться с #) ответом на это сообщение.",
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("отмена", callback_data=f"astats_tag_cancel_{session_id}")]]
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
        await update.callback_query.answer("Панель устарела", show_alert=True)
        return

    if str(update.effective_user.id) != str(session.get("issuer_user_id")):
        await update.callback_query.answer("Кнопка доступна только автору /astats", show_alert=True)
        return

    issuer_profile = _ensure_profile(
        context,
        str(update.effective_user.id),
        update.effective_user.username or f"id{update.effective_user.id}",
    )
    if int(issuer_profile.get("admin_level", 0) or 0) < 4:
        await update.callback_query.answer("Недостаточно прав", show_alert=True)
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
            "Отправьте новую биографию администратора.",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("отмена", callback_data=f"astats_bio_cancel_{session_id}")]]
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
        await update.callback_query.answer("Панель устарела", show_alert=True)
        return

    if str(update.effective_user.id) != str(session.get("issuer_user_id")):
        await update.callback_query.answer("Кнопка доступна только автору /astats", show_alert=True)
        return

    issuer_profile = _ensure_profile(
        context,
        str(update.effective_user.id),
        update.effective_user.username or f"id{update.effective_user.id}",
    )
    if int(issuer_profile.get("admin_level", 0) or 0) < 4:
        await update.callback_query.answer("Недостаточно прав", show_alert=True)
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
    if "🗣️" in existing_tip:
        selected_keys.append("chat")
    if "❤️" in existing_tip:
        selected_keys.append("support")
    if "🔥" in existing_tip:
        selected_keys.append("flirt")

    session["status"] = "astats_tip_edit"
    session["tip_selected_keys"] = selected_keys

    try:
        await update.callback_query.message.edit_text(
            f"☕️Панель редактирования диалогов у админа {username_text}",
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
        await update.callback_query.answer("Панель устарела", show_alert=True)
        return

    if str(update.effective_user.id) != str(session.get("issuer_user_id")):
        await update.callback_query.answer("Кнопка доступна только автору /astats", show_alert=True)
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
        await update.callback_query.answer("Панель устарела", show_alert=True)
        return

    if str(update.effective_user.id) != str(session.get("issuer_user_id")):
        await update.callback_query.answer("Кнопка доступна только автору /astats", show_alert=True)
        return

    issuer_profile = _ensure_profile(
        context,
        str(update.effective_user.id),
        update.effective_user.username or f"id{update.effective_user.id}",
    )
    if int(issuer_profile.get("admin_level", 0) or 0) < 4:
        await update.callback_query.answer("Недостаточно прав", show_alert=True)
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
            text=f"📍Ваш тип диалогов был изменен. Теперь вы можете общаться на: {tip_value}",
        )
    except Exception:
        pass

    session["status"] = "idle"
    await update.callback_query.answer("Смена диалогов успешно завершена", show_alert=True)

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
        await update.callback_query.answer("Панель устарела", show_alert=True)
        return

    if str(update.effective_user.id) != str(session.get("issuer_user_id")):
        await update.callback_query.answer("Кнопка доступна только автору /astats", show_alert=True)
        return

    issuer_profile = _ensure_profile(
        context,
        str(update.effective_user.id),
        update.effective_user.username or f"id{update.effective_user.id}",
    )
    if int(issuer_profile.get("admin_level", 0) or 0) < 4:
        await update.callback_query.answer("Недостаточно прав", show_alert=True)
        return

    target_user_id = str(session.get("target_user_id"))
    target_profile = _ensure_profile(context, target_user_id, f"id{target_user_id}")
    admin_username = str(target_profile.get("username") or f"id{target_user_id}")

    try:
        await update.callback_query.message.edit_text(
            f"👨‍👦Панель управления активными ПЗ админа {admin_username}",
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
        await update.callback_query.message.reply_text("Ничего не найдено")
        return

    for requester_user_id, active in found_sessions:
        requester_profile = _ensure_profile(context, requester_user_id, active.get("topic_base_name") or f"id{requester_user_id}")
        username_pz = str(requester_profile.get("username") or active.get("topic_base_name") or f"id{requester_user_id}")
        user_messages = int(active.get("msg_topic_user", 0) or 0)
        admin_messages = int(active.get("msg_topic_admin", 0) or 0)
        msg_topic = user_messages + admin_messages
        date_value = str(active.get("session_started_at") or "не указана")
        topic_link = _topic_url(active.get("chat_id"), active.get("topic_id"))

        await update.callback_query.message.reply_text(
            f"ℹ️Активное общение с {username_pz}\n"
            f"📥Сообщений: \"{msg_topic}\"\n\n"
            f"Регистрация общения: \"{date_value}\"\n"
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
        await update.message.reply_text("Команда доступна только администраторам 3 категории и выше.")
        return

    raw_text = update.message.text or ""
    cmd_entity = update.message.entities[0] if update.message.entities else None
    cmd_len = cmd_entity.length if cmd_entity and cmd_entity.type == "bot_command" else len("/info_topic")
    args_text = raw_text[cmd_len:].strip()
    if not args_text:
        await update.message.reply_text('Используйте: /info_topic "topic_link"')
        return

    try:
        parts = shlex.split(args_text)
    except ValueError:
        await update.message.reply_text('Некорректный формат. Используйте: /info_topic "topic_link"')
        return

    if len(parts) < 1:
        await update.message.reply_text('Используйте: /info_topic "topic_link"')
        return

    topic_link = parts[0]
    chat_id, topic_id = _parse_topic_url(topic_link)
    if chat_id is None or topic_id is None:
        await update.message.reply_text('Некорректный URL темы. Нужен формат вида https://t.me/c/<chat_id>/<topic_id>.')
        return

    topic_map = context.application.bot_data.setdefault("topic_user_map", {})
    target_user_id = topic_map.get(topic_id) or topic_map.get(str(topic_id))
    active_chats = context.application.bot_data.setdefault("active_chats", {})
    active = active_chats.get(str(target_user_id)) if target_user_id else None
    if not active or not active.get("active"):
        await update.message.reply_text("Тема не найдена или сессия уже завершена.")
        return

    if int(active.get("chat_id", 0) or 0) != int(chat_id) or int(active.get("topic_id", 0) or 0) != int(topic_id):
        await update.message.reply_text("Тема не найдена или уже неактуальна.")
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
    admin_username = str(active.get("admin_username") or "админ")
    username_pz = str(target_profile.get("username") or active.get("topic_base_name") or f"id{target_user_id}")
    detect = int(active.get("detect_topic", 0) or 0)
    msg_topic = int(active.get("msg_topic_user", 0) or 0) + int(active.get("msg_topic_admin", 0) or 0)
    rp_topic = int(active.get("rp_topic", 0) or 0)
    date_value = str(active.get("session_started_at") or "не указана")

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
        await update.callback_query.answer("Панель устарела", show_alert=True)
        return

    if str(update.effective_user.id) != str(panel.get("issuer_user_id")):
        await update.callback_query.answer("Кнопка доступна только автору /info_topic", show_alert=True)
        return

    issuer_profile = _ensure_profile(
        context,
        str(update.effective_user.id),
        update.effective_user.username or f"id{update.effective_user.id}",
    )
    if int(issuer_profile.get("admin_level", 0) or 0) < 3:
        await update.callback_query.answer("Недостаточно прав", show_alert=True)
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
        await update.callback_query.answer("Панель устарела", show_alert=True)
        return

    if str(update.effective_user.id) != str(panel.get("issuer_user_id")):
        await update.callback_query.answer("Кнопка доступна только автору /info_topic", show_alert=True)
        return

    issuer_profile = _ensure_profile(
        context,
        str(update.effective_user.id),
        update.effective_user.username or f"id{update.effective_user.id}",
    )
    if int(issuer_profile.get("admin_level", 0) or 0) < 3:
        await update.callback_query.answer("Недостаточно прав", show_alert=True)
        return

    panel["pending_action"] = "stop"
    try:
        await update.callback_query.message.edit_reply_markup(reply_markup=_build_info_topic_keyboard(panel_id, pending_action="stop"))
    except Exception:
        pass


async def info_topic_cancel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer("Отмена")
    panel_id = (update.callback_query.data or "").split("_")[-1]
    panels = context.application.bot_data.setdefault("info_topic_panels", {})
    panel = panels.get(panel_id)
    if not panel:
        return

    if str(update.effective_user.id) != str(panel.get("issuer_user_id")):
        await update.callback_query.answer("Кнопка доступна только автору /info_topic", show_alert=True)
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
    admin_username = str(active.get("admin_username") or "админ")

    if action == "close":
        active.pop("management_paused", None)
        active.pop("management_close_pending", None)
        try:
            await context.bot.edit_forum_topic(
                chat_id=chat_id,
                message_thread_id=topic_id,
                name=f"{active.get('topic_base_name') or username_pz} (Закрыто руководством)",
            )
        except Exception:
            pass
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                message_thread_id=topic_id,
                text="❗️Руководство бота закрыло сессию, Тема больше не актуальна",
            )
        except Exception:
            pass
        try:
            await context.bot.send_message(
                chat_id=int(target_user_id),
                text="❗️Ваша сессия была завершена руководством бота.",
            )
        except Exception:
            pass
        active["active"] = False
        context.application.bot_data.get("topic_user_map", {}).pop(topic_id, None)
        context.application.bot_data.get("topic_user_map", {}).pop(str(topic_id), None)
        context.application.bot_data.get("active_chats", {}).pop(target_user_id, None)
        try:
            await context.bot.send_message(chat_id=LOG_CHAT_ID, text=f"✅Сессия {username_pz} с админом {admin_username} закрыта руководством.")
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
                name=f"{active.get('topic_base_name') or username_pz} (Остановлено руководством)",
            )
        except Exception:
            pass
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                message_thread_id=topic_id,
                text="💤Руководство бота остановила общение в этой теме, сообщение отправляться не будут.",
            )
        except Exception:
            pass
        try:
            await context.bot.send_message(
                chat_id=int(target_user_id),
                text="💤Сессия была остановлена руководством бота. Сообщения временно не отправляются.",
            )
        except Exception:
            pass
        panel["pending_action"] = None
        try:
            await context.bot.send_message(chat_id=LOG_CHAT_ID, text=f"💤Сессия {username_pz} с админом {admin_username} остановлена руководством.")
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
                text="✅Тема снова актуальная.",
            )
        except Exception:
            pass
        try:
            await context.bot.send_message(
                chat_id=int(target_user_id),
                text="✅Тема снова актуальная. Пересылка сообщений восстановлена.",
            )
        except Exception:
            pass
        panel["pending_action"] = None
        try:
            await context.bot.send_message(chat_id=LOG_CHAT_ID, text=f"✅Сессия {username_pz} с админом {admin_username} возобновлена руководством.")
        except Exception:
            pass


async def info_topic_close_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    panel_id = (update.callback_query.data or "").split("_")[-1]
    panel = context.application.bot_data.setdefault("info_topic_panels", {}).get(panel_id)
    if not panel:
        await update.callback_query.answer("Панель устарела", show_alert=True)
        return

    if str(update.effective_user.id) != str(panel.get("issuer_user_id")):
        await update.callback_query.answer("Кнопка доступна только автору /info_topic", show_alert=True)
        return

    issuer_profile = _ensure_profile(context, str(update.effective_user.id), update.effective_user.username or f"id{update.effective_user.id}")
    if int(issuer_profile.get("admin_level", 0) or 0) < 3:
        await update.callback_query.answer("Недостаточно прав", show_alert=True)
        return

    await _finish_info_topic_action(context, panel_id, "close")
    try:
        await update.callback_query.message.edit_text("✅Сессия закрыта руководством бота.")
    except Exception:
        pass


async def info_topic_stop_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    panel_id = (update.callback_query.data or "").split("_")[-1]
    panel = context.application.bot_data.setdefault("info_topic_panels", {}).get(panel_id)
    if not panel:
        await update.callback_query.answer("Панель устарела", show_alert=True)
        return

    if str(update.effective_user.id) != str(panel.get("issuer_user_id")):
        await update.callback_query.answer("Кнопка доступна только автору /info_topic", show_alert=True)
        return

    issuer_profile = _ensure_profile(context, str(update.effective_user.id), update.effective_user.username or f"id{update.effective_user.id}")
    if int(issuer_profile.get("admin_level", 0) or 0) < 3:
        await update.callback_query.answer("Недостаточно прав", show_alert=True)
        return

    await _finish_info_topic_action(context, panel_id, "stop")
    try:
        await update.callback_query.message.edit_text(
            "💤Сессия остановлена руководством бота.",
            reply_markup=_build_info_topic_keyboard(panel_id, paused=True),
        )
    except Exception:
        pass


async def info_topic_resume_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    panel_id = (update.callback_query.data or "").split("_")[-1]
    panel = context.application.bot_data.setdefault("info_topic_panels", {}).get(panel_id)
    if not panel:
        await update.callback_query.answer("Панель устарела", show_alert=True)
        return

    if str(update.effective_user.id) != str(panel.get("issuer_user_id")):
        await update.callback_query.answer("Кнопка доступна только автору /info_topic", show_alert=True)
        return

    issuer_profile = _ensure_profile(context, str(update.effective_user.id), update.effective_user.username or f"id{update.effective_user.id}")
    if int(issuer_profile.get("admin_level", 0) or 0) < 3:
        await update.callback_query.answer("Недостаточно прав", show_alert=True)
        return

    panel_text = str(panel.get("panel_text") or "✅Тема снова актуальная.")

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
        await update.callback_query.answer("Панель устарела", show_alert=True)
        return

    if str(update.effective_user.id) != str(session.get("issuer_user_id")):
        await update.callback_query.answer("Кнопка доступна только автору /astats", show_alert=True)
        return

    issuer_profile = _ensure_profile(
        context,
        str(update.effective_user.id),
        update.effective_user.username or f"id{update.effective_user.id}",
    )
    if int(issuer_profile.get("admin_level", 0) or 0) < 4:
        await update.callback_query.answer("Недостаточно прав", show_alert=True)
        return

    try:
        await update.callback_query.message.edit_text(
            "☕️Редактор смены пола администратору",
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
        await update.callback_query.answer("Панель устарела", show_alert=True)
        return

    if str(update.effective_user.id) != str(session.get("issuer_user_id")):
        await update.callback_query.answer("Кнопка доступна только автору /astats", show_alert=True)
        return

    issuer_profile = _ensure_profile(
        context,
        str(update.effective_user.id),
        update.effective_user.username or f"id{update.effective_user.id}",
    )
    if int(issuer_profile.get("admin_level", 0) or 0) < 4:
        await update.callback_query.answer("Недостаточно прав", show_alert=True)
        return

    target_user_id = str(session.get("target_user_id"))
    target_profile = _ensure_profile(context, target_user_id, f"id{target_user_id}")
    admin_gender = "🙎‍♂️Мальчик" if gender_key == "male" else "🙍‍♀️Девочка"
    target_profile["admin_gender"] = admin_gender
    _save_profile_record(context, target_user_id)

    try:
        await context.bot.send_message(
            chat_id=LOG_CHAT_ID,
            text=(
                f"✅Смена пола выполнена: id_profile #{target_profile.get('id_profile')} -> {admin_gender}. "
                f"Инициатор: id{update.effective_user.id}"
            ),
        )
    except Exception:
        pass

    try:
        await context.bot.send_message(
            chat_id=int(target_user_id),
            text=f"📍Ваш пол был изменен. Теперь вы {admin_gender}",
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
    await update.callback_query.answer("Отмена")
    data = update.callback_query.data or ""
    parts = data.split("_")
    if len(parts) < 4:
        return
    session_id = parts[3]

    sessions = context.application.bot_data.setdefault("astats_tag_sessions", {})
    session = sessions.get(session_id)
    if not session:
        await update.callback_query.answer("Панель устарела", show_alert=True)
        return

    if str(update.effective_user.id) != str(session.get("issuer_user_id")):
        await update.callback_query.answer("Кнопка доступна только автору /astats", show_alert=True)
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
    await update.callback_query.answer("Отмена")
    data = update.callback_query.data or ""
    parts = data.split("_")
    if len(parts) < 4:
        return
    session_id = parts[3]

    sessions = context.application.bot_data.setdefault("astats_tag_sessions", {})
    session = sessions.get(session_id)
    if not session:
        await update.callback_query.answer("Панель устарела", show_alert=True)
        return

    if str(update.effective_user.id) != str(session.get("issuer_user_id")):
        await update.callback_query.answer("Кнопка доступна только автору /astats", show_alert=True)
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
    await update.callback_query.answer("Отмена")
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
        await update.callback_query.answer("Кнопка доступна только автору /astats", show_alert=True)
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
        await update.callback_query.answer("Кнопка доступна только владельцу профиля", show_alert=True)
        return

    profile = _ensure_profile(context, target_user_id, f"id{target_user_id}")
    biography_text = str(profile.get("biography_admin") or "не заполнена")
    await update.callback_query.message.reply_text(f"☺️Ваша новая биография: {biography_text}")


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
            await update.message.reply_text("Эту смену тега запустил другой администратор.")
            raise ApplicationHandlerStop
        return

    if str(session.get("status")) != "await_tag":
        return

    if int(session.get("chat_id", 0) or 0) != int(update.effective_chat.id):
        await update.message.reply_text("Отправьте тег в тот же чат, где была нажата кнопка смены тега.")
        raise ApplicationHandlerStop

    if resolved_from != "reply" and not pending_by_admin.get(admin_user_id):
        await update.message.reply_text("Ответьте новым тегом на сообщение бота с просьбой отправить тег.")
        raise ApplicationHandlerStop

    new_tag = (update.message.text or "").strip()
    if not new_tag.startswith("#"):
        await update.message.reply_text("Тег должен начинаться с #.")
        raise ApplicationHandlerStop

    target_user_id = str(session.get("target_user_id"))
    target_profile = _ensure_profile(context, target_user_id, f"id{target_user_id}")
    old_tag = str(target_profile.get("tag_admin") or "не указан")
    target_profile["tag_admin_new"] = new_tag
    _save_profile_record(context, target_user_id)

    try:
        await context.bot.send_message(
            chat_id=int(target_user_id),
            text=f"📍Ваш уникальный тег {old_tag} был изменен на {new_tag}",
        )
    except Exception:
        pass

    target_profile["tag_admin"] = str(target_profile.get("tag_admin_new") or new_tag)
    target_profile.pop("tag_admin_new", None)
    _save_profile_record(context, target_user_id)

    await update.message.reply_text("✅Замена тега успешна.")

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
        await update.message.reply_text("Биография не должна быть пустой.")
        raise ApplicationHandlerStop

    target_user_id = str(session.get("target_user_id"))
    target_profile = _ensure_profile(context, target_user_id, f"id{target_user_id}")
    target_profile["biography_admin"] = biography_text
    _save_profile_record(context, target_user_id)

    try:
        await context.bot.send_message(
            chat_id=int(target_user_id),
            text="📍Ваша биография была изменена.",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("📖Посмотреть", callback_data=f"astats_bio_view_{target_user_id}")]]
            ),
        )
    except Exception:
        pass

    await update.message.reply_text("✅Биография успешно изменена.")

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
        await update.message.reply_text("Команда доступна только администраторам 1 категории и выше.")
        return

    raw_text = update.message.text or ""
    cmd_entity = update.message.entities[0] if update.message.entities else None
    cmd_len = cmd_entity.length if cmd_entity and cmd_entity.type == "bot_command" else len("/pm")
    args_text = raw_text[cmd_len:].strip()
    if not args_text:
        await update.message.reply_text('Используйте: /pm "id_profile" "текст"')
        return

    try:
        parts = shlex.split(args_text)
    except ValueError:
        await update.message.reply_text('Некорректный формат. Используйте: /pm "id_profile" "текст"')
        return

    if len(parts) < 2:
        await update.message.reply_text('Используйте: /pm "id_profile" "текст"')
        return

    target_identifier = parts[0]
    dm_text = " ".join(parts[1:]).strip()
    if not dm_text:
        await update.message.reply_text("Текст сообщения не должен быть пустым.")
        return

    if _has_suspicious_username(dm_text):
        await update.message.reply_text("Отправка отклонена: текст не должен содержать @username или ссылки t.me.")
        return

    target_user_id, profile = _resolve_warn_target(context, target_identifier)
    if not profile:
        await update.message.reply_text(f'Пользователь с id_profile "{target_identifier}" не найден.')
        return

    username = str(profile.get("username") or f"id{target_user_id}")
    try:
        await context.bot.send_message(chat_id=int(target_user_id), text=dm_text)
    except Forbidden:
        await update.message.reply_text(f'Не удалось отправить ЛС пользователю "{username}": пользователь заблокировал бота.')
        return
    except Exception:
        await update.message.reply_text(f'Не удалось отправить ЛС пользователю "{username}".')
        return

    await update.message.reply_text(f'✅ЛС успешно отправлено пользователю "{username}" (id_profile #{profile.get("id_profile")}).')


async def kus_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = getattr(update, "message", None)
    user = update.effective_user
    chat = update.effective_chat
    if message is None or not user or chat is None:
        return

    if chat.type == ChatType.PRIVATE:
        active = context.application.bot_data.get("active_chats", {}).get(str(user.id))
        if not active or not active.get("active"):
            await message.reply_text("У вас нет активной переписки.")
            raise ApplicationHandlerStop

        if active.get("paused") or active.get("management_paused"):
            await message.reply_text("Сессия сейчас остановлена. Сначала возобновите общение.")
            raise ApplicationHandlerStop

        admin_id = str(active.get("admin_id") or "")
        topic_id = active.get("topic_id")
        chat_id = active.get("chat_id")
        admin_profile = _ensure_profile(context, admin_id, active.get("admin_username") or f"id{admin_id}")
        user_profile = _ensure_profile(context, str(user.id), user.username or f"id{user.id}")

        admin_tag = str(admin_profile.get("tag_admin") or active.get("admin_tag") or active.get("admin_username") or f"id{admin_id}")
        username_pz = str(user_profile.get("username") or active.get("topic_base_name") or f"id{user.id}")

        try:
            await message.reply_text(f"Вы укусили {admin_tag}")
        except Exception:
            pass

        if chat_id and topic_id is not None:
            try:
                await context.bot.send_message(
                    chat_id=chat_id,
                    message_thread_id=topic_id,
                    text=f"#RP 💕Вас укусил(-а) {username_pz}",
                )
            except Exception:
                pass

        active["rp_topic"] = int(active.get("rp_topic", 0) or 0) + 1
        raise ApplicationHandlerStop

    if chat.id != WORK_CHAT_ID:
        return

    topic_id = getattr(message, "message_thread_id", None)
    if topic_id is None:
        await message.reply_text("Команда доступна только в теме активного диалога.")
        raise ApplicationHandlerStop

    topic_map = context.application.bot_data.get("topic_user_map", {}) or {}
    target_user_id = topic_map.get(topic_id) or topic_map.get(str(topic_id))
    active = (context.application.bot_data.get("active_chats", {}) or {}).get(str(target_user_id)) if target_user_id else None
    if not active or not active.get("active"):
        await message.reply_text("У вас нет активной переписки.")
        raise ApplicationHandlerStop

    if active.get("paused") or active.get("management_paused"):
        await message.reply_text("Сессия сейчас остановлена. Сначала возобновите общение.")
        raise ApplicationHandlerStop

    admin_profile = _ensure_profile(context, str(user.id), user.username or f"id{user.id}")
    target_profile = _ensure_profile(context, str(target_user_id), active.get("topic_base_name") or f"id{target_user_id}")

    admin_tag = str(admin_profile.get("tag_admin") or admin_profile.get("username") or f"id{user.id}")
    username_pz = str(target_profile.get("username") or active.get("topic_base_name") or f"id{target_user_id}")

    try:
        await message.reply_text(f"Вы укусили {username_pz}")
    except Exception:
        pass

    try:
        await context.bot.send_message(
            chat_id=int(target_user_id),
            text=f"Вас укусил(-а) {admin_tag}",
        )
    except Exception:
        pass

    active["rp_topic"] = int(active.get("rp_topic", 0) or 0) + 1
    raise ApplicationHandlerStop


def _prefix_options() -> list[tuple[str, str]]:
    return [
        ("logs", "👁Logs"),
        ("cooperation", "💎Сотрудничество"),
        ("tech", "👨‍💻Технический специалист"),
        ("owner", "💋Владелец"),
        ("deputy", "💘Заместитель владельца"),
        ("queen", "👑Королева"),
        ("heel", "👠Каблук"),
        ("senior", "👮‍♀️Старший админ"),
        ("admin", "🐶Админ"),
        ("junior", "🦅Младший админ"),
        ("intern", "🧸Стажер"),
    ]


def _build_setprefix_keyboard(panel_id: str, selected_key: str | None) -> InlineKeyboardMarkup:
    buttons = []
    for key, label in _prefix_options():
        title = f"✅{label}" if str(selected_key or "") == key else label
        buttons.append(InlineKeyboardButton(title, callback_data=f"prefix_select_{panel_id}_{key}"))

    rows = []
    for i in range(0, len(buttons), 2):
        rows.append(buttons[i:i + 2])
    rows.append([InlineKeyboardButton("↩️Применить", callback_data=f"prefix_apply_{panel_id}")])

    return InlineKeyboardMarkup(
        rows
    )


def _next_prefix_panel_id(context: ContextTypes.DEFAULT_TYPE) -> str:
    seq = int(context.application.bot_data.get("prefix_panel_seq", 0) or 0) + 1
    context.application.bot_data["prefix_panel_seq"] = seq
    return str(seq)


async def setprefix_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not _is_special_admin_chat(update.effective_chat.id):
        return

    issuer_profile = _ensure_profile(
        context,
        str(update.effective_user.id),
        update.effective_user.username or f"id{update.effective_user.id}",
    )
    if int(issuer_profile.get("admin_level", 0) or 0) != 5:
        await update.message.reply_text("Команда доступна только администраторам 5 категории.")
        return

    raw_text = update.message.text or ""
    cmd_entity = update.message.entities[0] if update.message.entities else None
    cmd_len = cmd_entity.length if cmd_entity and cmd_entity.type == "bot_command" else len("/setprefix")
    args_text = raw_text[cmd_len:].strip()
    if not args_text:
        await update.message.reply_text('Используйте: /setprefix "id_profile"')
        return

    try:
        parts = shlex.split(args_text)
    except ValueError:
        await update.message.reply_text('Некорректный формат. Используйте: /setprefix "id_profile"')
        return

    if len(parts) < 1:
        await update.message.reply_text('Используйте: /setprefix "id_profile"')
        return

    target_identifier = parts[0]
    target_user_id, profile = _resolve_warn_target(context, target_identifier)
    if not profile:
        await update.message.reply_text(f'Пользователь с id_profile "{target_identifier}" не найден.')
        return
    if not _has_admin_rights_level_1_5(profile):
        await update.message.reply_text(
            f'Нельзя открыть панель: id_profile #{profile.get("id_profile")} не имеет админ-прав 1-5 уровня.'
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
        f"☕️Открыта панель редактирования префикса человека {target_id_profile}",
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
        await update.callback_query.answer("Панель редактирования устарела", show_alert=True)
        return

    if str(update.effective_user.id) != str(panel.get("issuer_user_id")):
        await update.callback_query.answer("Кнопка доступна только автору панели", show_alert=True)
        return

    issuer_profile = _ensure_profile(
        context,
        str(update.effective_user.id),
        update.effective_user.username or f"id{update.effective_user.id}",
    )
    if int(issuer_profile.get("admin_level", 0) or 0) != 5:
        await update.callback_query.answer("Недостаточно прав", show_alert=True)
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
        await update.callback_query.answer("Панель редактирования устарела", show_alert=True)
        return

    if str(update.effective_user.id) != str(panel.get("issuer_user_id")):
        await update.callback_query.answer("Кнопка доступна только автору панели", show_alert=True)
        return

    issuer_profile = _ensure_profile(
        context,
        str(update.effective_user.id),
        update.effective_user.username or f"id{update.effective_user.id}",
    )
    if int(issuer_profile.get("admin_level", 0) or 0) != 5:
        await update.callback_query.answer("Недостаточно прав", show_alert=True)
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
    prefix_text = str(target_profile.get("prefix") or "не установлен")
    try:
        await update.callback_query.message.edit_text(
            f"✅Префикс для id_profile #{target_id_profile} применен: {prefix_text}"
        )
    except Exception:
        pass

    try:
        await context.bot.send_message(
            chat_id=int(target_user_id),
            text=f"✅Ваш админ-префикс был отредактирован. Теперь ваш префикс: {prefix_text}",
        )
    except Exception:
        pass

    pending_panels.pop(panel_id, None)


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


async def unwarn_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != LOG_CHAT_ID or not update.message:
        return

    if not _can_use_moderation_commands(context, str(update.effective_user.id)):
        await update.message.reply_text("Команда доступна только администраторам 2 категории и выше.")
        return

    raw_text = update.message.text or ""
    cmd_entity = update.message.entities[0] if update.message.entities else None
    cmd_len = cmd_entity.length if cmd_entity and cmd_entity.type == "bot_command" else len("/unwarn")
    args_text = raw_text[cmd_len:].strip()
    if not args_text:
        await update.message.reply_text('Укажите id_profile: /unwarn "id_profile"')
        return

    try:
        parts = shlex.split(args_text)
    except ValueError:
        await update.message.reply_text('Некорректный формат. Используйте: /unwarn "id_profile"')
        return

    if len(parts) < 1:
        await update.message.reply_text('Укажите id_profile: /unwarn "id_profile"')
        return

    target_identifier = parts[0]
    target_user_id, profile = _resolve_warn_target(context, target_identifier)
    if not profile:
        await update.message.reply_text(f'Пользователь с id_profile "{target_identifier}" не найден.')
        return

    current_warn = int(profile.get("warn", 0) or 0)
    if current_warn <= 0:
        await update.message.reply_text(f'У пользователя id_profile #{profile.get("id_profile")} нет предупреждений для снятия.')
        return

    profile["warn"] = current_warn - 1
    if profile["warn"] <= 0:
        profile["reason"] = "нет причин"
    _save_profile_record(context, str(target_user_id))

    await update.message.reply_text(
        f'✅Пользователю id_profile #{profile.get("id_profile")} снято предупреждение. Теперь у него {profile["warn"]} warn(-ов).'
    )

    try:
        await context.bot.send_message(
            chat_id=int(target_user_id),
            text=f'✅С Вас сняли предупреждение. Теперь у вас {profile["warn"]} предупреждений',
        )
    except Exception:
        pass


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


async def ban_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not _is_special_admin_chat(update.effective_chat.id):
        return

    if not _can_use_moderation_commands(context, str(update.effective_user.id)):
        await update.message.reply_text("Команда доступна только администраторам 2 категории и выше.")
        return

    raw_text = update.message.text or ""
    cmd_entity = update.message.entities[0] if update.message.entities else None
    cmd_len = cmd_entity.length if cmd_entity and cmd_entity.type == "bot_command" else len("/ban")
    args_text = raw_text[cmd_len:].strip()
    if not args_text:
        await update.message.reply_text('Укажите id_profile и причину: /ban "id_profile" "reason"')
        return

    try:
        parts = shlex.split(args_text)
    except ValueError:
        await update.message.reply_text('Некорректный формат. Используйте: /ban "id_profile" "reason"')
        return

    if len(parts) < 2:
        await update.message.reply_text('Укажите id_profile и причину: /ban "id_profile" "reason"')
        return

    target_identifier = parts[0]
    reason = " ".join(parts[1:]).strip()
    if not reason:
        await update.message.reply_text("Укажите причину блокировки.")
        return

    target_user_id, profile = _resolve_ban_target(context, target_identifier)
    if not profile:
        await update.message.reply_text(f'Пользователь с id_profile "{target_identifier}" не найден.')
        return

    if _has_admin_rights_level_1_5(profile) or _has_admin_ban_immunity(profile):
        await update.message.reply_text(
            f'Невозможно выдать бан: id_profile #{profile.get("id_profile")} имеет админ-права 1-5 уровня.'
        )
        return

    await _apply_ban(context, str(target_user_id), profile.get("username"), reason, "BAN")
    await update.message.reply_text(
        f'⛔Пользователь id_profile #{profile.get("id_profile")} заблокирован. Причина: "{reason}"'
    )


async def unban_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not _is_special_admin_chat(update.effective_chat.id):
        return

    if not _can_use_moderation_commands(context, str(update.effective_user.id)):
        await update.message.reply_text("Команда доступна только администраторам 2 категории и выше.")
        return

    raw_text = update.message.text or ""
    cmd_entity = update.message.entities[0] if update.message.entities else None
    cmd_len = cmd_entity.length if cmd_entity and cmd_entity.type == "bot_command" else len("/unban")
    args_text = raw_text[cmd_len:].strip()
    if not args_text:
        await update.message.reply_text('Укажите id_profile: /unban "id_profile"')
        return

    try:
        parts = shlex.split(args_text)
    except ValueError:
        await update.message.reply_text('Некорректный формат. Используйте: /unban "id_profile"')
        return

    if len(parts) < 1:
        await update.message.reply_text('Укажите id_profile: /unban "id_profile"')
        return

    target_identifier = parts[0]
    target_user_id, profile = _resolve_ban_target(context, target_identifier)
    if not profile:
        await update.message.reply_text(f'Пользователь с id_profile "{target_identifier}" не найден.')
        return

    banned_users = context.application.bot_data.setdefault("banned_users", {})
    if not banned_users.pop(str(target_user_id), None):
        await update.message.reply_text(f'Пользователь id_profile #{profile.get("id_profile")} не находится в бане.')
        return

    await update.message.reply_text(f'✅Пользователь id_profile #{profile.get("id_profile")} разбанен.')

    try:
        await context.bot.send_message(
            chat_id=int(target_user_id),
            text="✅С вашей учетной записи снята блокировка. Функции бота снова доступны.",
        )
    except Exception:
        pass

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

async def send_admin_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await check_active_chat_block(update, context):
        return
    context.user_data["last_category_step"] = 1

    text = (
        "› Пожалуйста, выберите какой пол собеседника вы желаете чтобы нам было проще отфильтровать ваш запрос.\n\n"
        "Мы ценим ваш комфорт и хотим, чтобы диалог приносило только положительные эмоции.\n\n"
        "🙎‍♂️<b>Мальчик</b> - Они ориентированы на решение проблемы (результат). "
        "Если мальчик жалуется, он чаще ждет не сочувствия (\"Бедненький\"), "
        "а конкретного совета или инструкции, <b>как это исправить</b>. "
        "Говорите короче и по делу.\n\n"
        "🙍‍♀️<b>Девочка</b> - Часто важен процесс общения, обмен эмоциями, сопереживание. "
        "Можно говорить «по кругу», возвращаясь к теме, чтобы выплеснуть чувства\n"
        "—\n"
        "› Заявки, поступившие в ночное время (с 0:00 до 09:30 по МСК), будут обрабатываться с начала рабочего дня."
    )
    menu_keyboard = ReplyKeyboardMarkup(
        [
            [KeyboardButton("👨 Мальчик"), KeyboardButton("👩 Девочка")],
            [KeyboardButton("◀️ Назад")]
        ],
        resize_keyboard=True,
        one_time_keyboard=False,
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=menu_keyboard)


async def send_main_submenu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["last_category_step"] = 0
    menu_keyboard = _build_main_menu_keyboard(
        context,
        update.effective_user.id,
        update.effective_user.username if update.effective_user else None,
    )
    await update.message.reply_text("Выберите действие в меню ниже:", reply_markup=menu_keyboard)


async def settings_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or update.effective_chat.type != ChatType.PRIVATE:
        return
    if await block_if_banned(update, context):
        return
    if _is_active_admin_candidate(context, str(update.effective_user.id)):
        await update.message.reply_text(_candidate_block_text())
        return

    user_id = str(update.effective_user.id)
    profile = _ensure_profile(context, user_id, update.effective_user.username or f"id{user_id}")
    await update.message.reply_text("⚙️Раздел настроек", reply_markup=_build_settings_menu_keyboard(profile))


async def settings_back_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or update.effective_chat.type != ChatType.PRIVATE:
        return
    if await block_if_banned(update, context):
        return
    await send_main_submenu(update, context)


async def settings_disable_ad_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or update.effective_chat.type != ChatType.PRIVATE:
        return
    if await block_if_banned(update, context):
        return
    if _is_active_admin_candidate(context, str(update.effective_user.id)):
        await update.message.reply_text(_candidate_block_text())
        return

    user_id = str(update.effective_user.id)
    profile = _ensure_profile(context, user_id, update.effective_user.username or f"id{user_id}")
    if not bool(profile.get("ad_disable_access", False)):
        await update.message.reply_text(
            "⚠️У вас нет доступа к отключению рекламы. Чтобы получить права купите их за 15 звезд у @baish0n"
        )
        return

    profile["ad_disable_enabled"] = True
    _save_profile_record(context, user_id)
    await update.message.reply_text(
        "✅Вы успешно отключили рекламу у себя, больше вас не потревожат рассылки.",
        reply_markup=_build_settings_menu_keyboard(profile),
    )


async def settings_enable_ad_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or update.effective_chat.type != ChatType.PRIVATE:
        return
    if await block_if_banned(update, context):
        return
    if _is_active_admin_candidate(context, str(update.effective_user.id)):
        await update.message.reply_text(_candidate_block_text())
        return

    user_id = str(update.effective_user.id)
    profile = _ensure_profile(context, user_id, update.effective_user.username or f"id{user_id}")
    if not bool(profile.get("ad_disable_enabled", False)):
        await update.message.reply_text("ℹ️Реклама у вас уже включена.")
        return

    profile["ad_disable_enabled"] = False
    _save_profile_record(context, user_id)
    await update.message.reply_text(
        "✅Вы снова будете получать рассылки.",
        reply_markup=_build_settings_menu_keyboard(profile),
    )


async def settings_complaint_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or update.effective_chat.type != ChatType.PRIVATE:
        return
    if await block_if_banned(update, context):
        return
    if _is_active_admin_candidate(context, str(update.effective_user.id)):
        await update.message.reply_text(_candidate_block_text())
        return

    await update.message.reply_text("💬Раздел жалоб", reply_markup=_build_complaint_menu_keyboard())


async def complaint_bug_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or update.effective_chat.type != ChatType.PRIVATE:
        return
    if await block_if_banned(update, context):
        return

    user_id = str(update.effective_user.id)
    pending = context.application.bot_data.setdefault("pending_bug_reports", {})
    pending[user_id] = {
        "state": "await_text",
    }

    # Hide the reply submenu while the bug report flow is active.
    await update.message.reply_text("🛠Режим отправки бага активирован.", reply_markup=ReplyKeyboardRemove())

    await update.message.reply_text(
        "Спасибо, что помогаете нам стать лучше. Чтобы мы быстро решили проблему, опишите ситуацию максимально конкретно.\n\n"
        "**Что считается багом (ошибкой в коде):**\n"
        "Функция работает не так, как описано в инструкции (крашится, зависает, выдает неверный результат).\n"
        "Интерфейс отображается криво (наложение элементов, съехавшие кнопки, нечитаемый текст).\n"
        "Данные сохраняются/загружаются с ошибками или теряются.\n"
        "Действие не выполняется, хотя нет блокировок (нет интернета, нет прав).\n"
        "Грамматические ошибки в боте при нажатии кнопок",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("отмена", callback_data=f"bug_report_cancel_{user_id}")]]
        ),
    )


async def complaint_admin_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or update.effective_chat.type != ChatType.PRIVATE:
        return
    if await block_if_banned(update, context):
        return

    await update.message.reply_text("📝Раздел 'Пожаловаться на админа' открыт. Опишите жалобу следующим сообщением.")


async def bug_report_cancel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer("Отмена")
    user_id = (update.callback_query.data or "").split("_")[-1]
    if str(update.effective_user.id) != str(user_id):
        await update.callback_query.answer("Кнопка доступна только владельцу запроса", show_alert=True)
        return

    pending = context.application.bot_data.setdefault("pending_bug_reports", {})
    pending.pop(str(user_id), None)

    profile = _ensure_profile(context, str(user_id), update.effective_user.username or f"id{user_id}")
    try:
        await update.callback_query.message.reply_text(
            "Отменено. Возврат в раздел настроек.",
            reply_markup=_build_settings_menu_keyboard(profile),
        )
    except Exception:
        pass


async def bug_report_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    user_id = (update.callback_query.data or "").split("_")[-1]
    if str(update.effective_user.id) != str(user_id):
        await update.callback_query.answer("Кнопка доступна только владельцу запроса", show_alert=True)
        return

    pending = context.application.bot_data.setdefault("pending_bug_reports", {})
    entry = pending.get(str(user_id))
    if not entry or str(entry.get("state")) != "await_confirm":
        await update.callback_query.answer("Заявка устарела", show_alert=True)
        return

    report_text = str(entry.get("draft_text") or "").strip()
    if not report_text:
        pending.pop(str(user_id), None)
        await update.callback_query.answer("Текст жалобы пуст", show_alert=True)
        return

    profile = _ensure_profile(context, str(user_id), update.effective_user.username or f"id{user_id}")
    id_profile = int(profile.get("id_profile", 0) or 0)
    username = str(profile.get("username") or f"id{user_id}")

    try:
        await context.bot.send_message(
            chat_id=LOG_CHAT_ID,
            text=(
                "🛠Новая жалоба о баге\n\n"
                f"id_profile: #{id_profile}\n"
                f"username: {username}\n"
                f"user_id: {user_id}\n\n"
                f"Текст: {report_text}"
            ),
        )
    except Exception:
        pass

    pending.pop(str(user_id), None)
    try:
        await update.callback_query.message.reply_text(
            "✅Ваше сообщение отправлено в технический раздел.",
            reply_markup=_build_settings_menu_keyboard(profile),
        )
    except Exception:
        pass


async def bug_report_reject_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer("Отмена")
    user_id = (update.callback_query.data or "").split("_")[-1]
    if str(update.effective_user.id) != str(user_id):
        await update.callback_query.answer("Кнопка доступна только владельцу запроса", show_alert=True)
        return

    pending = context.application.bot_data.setdefault("pending_bug_reports", {})
    entry = pending.get(str(user_id))
    if not entry:
        return

    entry["state"] = "await_text"
    entry.pop("draft_text", None)

    try:
        await update.callback_query.message.reply_text(
            "Отправка отменена. Опишите текст заново или нажмите отмена.",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("отмена", callback_data=f"bug_report_cancel_{user_id}")]]
            ),
        )
    except Exception:
        pass


async def bug_report_text_input_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or update.effective_chat.type != ChatType.PRIVATE or not update.effective_user:
        return
    if update.effective_user.is_bot:
        return

    pending = context.application.bot_data.setdefault("pending_bug_reports", {})
    user_id = str(update.effective_user.id)
    entry = pending.get(user_id)
    if not entry:
        return

    if str(entry.get("state")) != "await_text":
        return

    report_text = (update.message.text or "").strip()
    if not report_text:
        await update.message.reply_text("Текст не должен быть пустым.")
        raise ApplicationHandlerStop

    entry["state"] = "await_confirm"
    entry["draft_text"] = report_text
    await update.message.reply_text(
        "🛠Вы уверены что хотите отправить этот текст техническому специалисту?",
        reply_markup=InlineKeyboardMarkup(
            [[
                InlineKeyboardButton("уверен", callback_data=f"bug_report_confirm_{user_id}"),
                InlineKeyboardButton("не уверен", callback_data=f"bug_report_reject_{user_id}"),
            ]]
        ),
    )
    raise ApplicationHandlerStop


async def restart_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await block_if_banned(update, context):
        return
    if not update.message or not update.effective_user:
        return

    user_id = str(update.effective_user.id)
    active = (context.application.bot_data.get("active_chats", {}) or {}).get(user_id)
    if active and active.get("active"):
        if active.get("paused"):
            resume_kb = InlineKeyboardMarkup(
                [[InlineKeyboardButton("Возомновить общение", callback_data=f"resume_session_{user_id}")]]
            )
            await update.message.reply_text(
                "💤Сессия сейчас на паузе. Нажмите кнопку ниже, чтобы возобновить общение.",
                reply_markup=resume_kb,
            )
            return

        session_kb = ReplyKeyboardMarkup(
            [[KeyboardButton("🤧Отказаться от админа"), KeyboardButton("💤Приостановить общение")]],
            resize_keyboard=True,
            one_time_keyboard=False,
        )
        await update.message.reply_text(
            "Вы в активной сессии. Используйте кнопки ниже для управления текущим диалогом.",
            reply_markup=session_kb,
        )
        return

    state = (context.application.bot_data.get("admin_candidate_state", {}) or {}).get(user_id)
    if state:
        stage = str(state.get("stage") or "")
        if stage in {"await_tag", "await_reject_restart"}:
            await _send_candidate_stage_1(context, int(user_id))
            return
        if stage == "await_bio":
            await update.message.reply_text("🔮Теперь напишите биографию своего персонажа (пример: любимое хобби, вредные привычки, распорядок дня )")
            return
        if stage == "await_tip_admin":
            selected = list(state.get("tip_admin_selected") or [])
            await update.message.reply_text(
                "💬Теперь укажите тип общения которые вы больше всего можете обсуждать с будущими пользователями (можно выбрать все три)",
                reply_markup=_build_candidate_tip_keyboard(user_id, selected),
            )
            return
        if stage == "await_admin_gender":
            await update.message.reply_text(
                "**Пожалуйста, укажите Ваш пол**\nЭто надо чтобы пользователи знали с кем будут вести диалог",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=_build_candidate_gender_keyboard(user_id),
            )
            return
        if stage == "pending_review":
            await update.message.reply_text("✅Ваша заявка уже отправлена на обработку старшей администрации.")
            return

    step = context.user_data.get("profile", 0)
    last_step = context.user_data.get("last_category_step")
    if last_step in {0, 1, 2}:
        step = last_step
    if step == 1:
        await send_admin_menu(update, context)
        return
    if step == 2:
        await send_mood_menu(update, context)
        return

    await send_main_submenu(update, context)

async def choose_admin_gender_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await block_if_banned(update, context):
        return
    if _is_active_admin_candidate(context, str(update.effective_user.id)):
        await update.message.reply_text(_candidate_block_text())
        return

    choice = update.message.text
    flow_step = context.user_data.get("profile")
    if choice in {"👨 Мальчик", "👩 Девочка", "◀️ Назад"} and flow_step != 1:
        await update.message.reply_text("Используйте подменю кнопки")
        context.user_data["profile"] = 0
        await send_main_submenu(update, context)
        return

    if choice == "👨 Мальчик":
        context.user_data["admin_gender"] = "male"
        context.user_data["profile"] = 2
        await send_mood_menu(update, context)
    elif choice == "👩 Девочка":
        context.user_data["admin_gender"] = "female"
        context.user_data["profile"] = 2
        await send_mood_menu(update, context)
    elif choice == "◀️ Назад":
        context.user_data.clear()
        await start(update, context)
    elif choice == "👤 Профиль":
        await send_profile(update, context)
    elif choice == "🔰Админ-профиль":
        await send_admin_profile(update, context)
    else:
        await update.message.reply_text(
            "Пожалуйста, выберите одного из администраторов ниже."
        )

async def send_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await block_if_banned(update, context):
        return
    if _is_active_admin_candidate(context, str(update.effective_user.id)):
        await update.message.reply_text(_candidate_block_text())
        return

    user = update.effective_user
    user_id = str(user.id)
    profile = _ensure_profile(context, user_id, user.username or f"id{user.id}")

    if _has_admin_rights_level_1_5(profile):
        await update.message.reply_text("⛔ Для администраторов кнопка '👤 Профиль' недоступна.")
        return

    profile_username = html.escape(str(profile.get("username", "")))
    user_nickname = html.escape(str(profile.get("user_nickname") or "не указан"))
    profile_last_admin = html.escape(str(profile.get("last_admin_tag", "не указан")))
    profile_reason = html.escape(str(profile.get("reason", "")))
    profile_date = html.escape(str(profile.get("date_registration", "")))

    text = (
        "📋 <b>ПРОФИЛЬ ПОЛЬЗОВАТЕЛЯ</b>\n\n"
        "--------------------\n"
        f"🆔 <b>ID профиля:</b> {profile.get('id_profile')}\n"
        f"👤 <b>Статистика Number:</b> {profile_username}\n"
        f"🍓Никнейм: {user_nickname}\n\n"
        f"✉️ <b>Сообщений:</b> {profile.get('message_user')}\n"
        f"👨‍💼 <b>Последний админ:</b> {profile_last_admin}\n"
        f"⚠️ <b>Предупреждений:</b> {profile.get('warn')} | <b>Последняя причина:</b> {profile_reason}\n"
        f"🪙 <b>Админ-коинов:</b> {profile.get('coin')}\n\n"
        "--------------------\n\n"
        f"📅 <b>В боте с:</b> {profile_date}"
    )

    nickname_kb = InlineKeyboardMarkup(
        [[InlineKeyboardButton("🧸Сменить никнейм", callback_data=f"change_nickname_{user_id}")]]
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=nickname_kb)


async def change_nickname_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    data = update.callback_query.data or ""
    parts = data.split("_")
    if len(parts) < 3:
        return

    user_id = int(parts[2])
    if not update.effective_user or update.effective_user.id != user_id:
        await update.callback_query.answer("Кнопка доступна только владельцу профиля", show_alert=True)
        return

    cancel_kb = InlineKeyboardMarkup(
        [[InlineKeyboardButton("отмена", callback_data=f"cancel_nickname_change_{user_id}")]]
    )
    sent = await update.callback_query.message.reply_text(
        "╭─ 🏓 Смена никнейма ─╮\n\n"
        "Укажите новый никнейм (от 3 до 13 символов).\n\n"
        "╰────────────────╯",
        reply_markup=cancel_kb,
    )
    context.user_data["pending_nickname_change"] = {
        "active": True,
        "prompt_message_id": sent.message_id,
    }


async def cancel_nickname_change_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer("Отмена")
    data = update.callback_query.data or ""
    parts = data.split("_")
    if len(parts) < 4:
        return

    user_id = int(parts[3])
    if not update.effective_user or update.effective_user.id != user_id:
        await update.callback_query.answer("Кнопка доступна только владельцу профиля", show_alert=True)
        return

    context.user_data.pop("pending_nickname_change", None)
    try:
        await update.callback_query.message.delete()
    except Exception:
        pass


async def send_admin_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await block_if_banned(update, context):
        return
    if _is_active_admin_candidate(context, str(update.effective_user.id)):
        await update.message.reply_text(_candidate_block_text())
        return

    user = update.effective_user
    user_id = str(user.id)
    profile = _ensure_profile(context, user_id, user.username or f"id{user.id}")

    if not _has_admin_rights_level_1_5(profile):
        await update.message.reply_text("⛔ Кнопка '🔰Админ-профиль' доступна только администраторам.")
        return

    admin_level = int(profile.get("admin_level", 0) or 0)
    rank_title = ADMIN_LEVEL_TITLES.get(admin_level, "Не назначен")
    prefix_text = html.escape(str(profile.get("prefix") or "не установлен"))
    admin_tag = html.escape(str(profile.get("tag_admin") or "не указан"))
    tip_admin = html.escape(str(profile.get("tip_admin") or "не указано"))
    admin_gender = html.escape(str(profile.get("admin_gender") or "не указан"))
    admin_bio = html.escape(str(profile.get("biography_admin") or "не заполнена"))
    profile_username = html.escape(str(profile.get("username") or ""))

    text = (
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
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)

async def track_user_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user:
        return
    user_id = str(user.id)
    profile = _ensure_profile(context, user_id, user.username or f"id{user.id}")
    profile["message_user"] += 1

async def send_mood_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["last_category_step"] = 2
    text = (
        "›› Теперь выберите тип запроса\n\n"
        "<b>Зачем выбирать?</b> Подстройка экономит ваши силы. Если вы с самого начала поняли, что нужно от вас, "
        "вы решаете вопрос за 5 минут, а не за час нервотрепки.\n\n"
        "🗣️<b>Общение</b> - Обо всем и ни о чем, свободный разговор без обязательств.\n\n"
        "❤️<b>Поддержка</b> - если накопилось обиды или гнева и хочется, чтобы кто то послушал и пожалел.\n\n"
        "🔥<b>Флирт</b> - Для тех, кто любит поролить и называть милыми словами"
    )
    menu_keyboard = ReplyKeyboardMarkup(
        [
            [KeyboardButton("🗣️ Общение"), KeyboardButton("❤️ Поддержка")],
            [KeyboardButton("🔥 Флирт")],
            [KeyboardButton("◀️ Назад")]
        ],
        resize_keyboard=True,
        one_time_keyboard=False,
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=menu_keyboard)

async def choose_mood_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await block_if_banned(update, context):
        return
    if _is_active_admin_candidate(context, str(update.effective_user.id)):
        await update.message.reply_text(_candidate_block_text())
        return

    choice = update.message.text
    flow_step = context.user_data.get("profile")
    mood_map = {
        "🗣️ Общение": "общение",
        "❤️ Поддержка": "поддержка",
        "🔥 Флирт": "флирт",
    }
    if (choice in mood_map or choice == "◀️ Назад") and flow_step != 2:
        await update.message.reply_text("Используйте подменю кнопки")
        context.user_data["profile"] = 0
        await send_main_submenu(update, context)
        return

    if choice in mood_map:
        if await check_active_chat_block(update, context):
            return
        context.user_data["mood"] = mood_map[choice]
        context.user_data["profile"] = 0
        # Remove the reply keyboard and send a confirmation with a single inline "Отменить" button
        first_confirmation = await update.message.reply_text(
            "››› Запрос успешно создан 🔎\n"
            "Все свободные администраторы уведомлены, ожидайте от 10 до 120 минут❤️\n\n"
            "Если ваш запрос не был рассмотрен больше 2 часов, пожалуйста отправьте сообщение в техническую поддержку а мы разберемся с админами.",
            reply_markup=ReplyKeyboardRemove(),
        )

        user = update.effective_user
        user_confirmation = await update.message.reply_text(
            "Если хотите отменить поиск администратора — нажмите кнопку ниже.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Отменить", callback_data=f"cancel_search_{user.id}")]]),
        )

        chat_id = -1004417963273
        username = f"@{user.username}" if user.username else f"id{user.id}"
        admin_gender = "Мальчик" if context.user_data.get("admin_gender") == "male" else "Девочка"
        mood = context.user_data.get("mood", "не выбран")
        topic_name = username if user.username else f"user_{user.id}"

        try:
            topic = await context.bot.create_forum_topic(chat_id=chat_id, name=topic_name)
            topic_id = topic.message_thread_id
        except BadRequest:
            topic = None
            topic_id = None

        topic_message = (
            "❗️Новый пользователь\n"
            f"Пол: {admin_gender}\n"
            f"Тип общения: {mood}\n"
            f"Юзернейм пользователя: {username}"
        )
        buttons = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("✅ Взять пользователя", callback_data=f"take_user_{user.id}"),
                    InlineKeyboardButton("❌Отказать запрос", callback_data=f"decline_request_{user.id}")
                ]
            ]
        )
        if topic is not None:
            sent = await context.bot.send_message(
                chat_id=chat_id,
                text=topic_message,
                message_thread_id=topic_id,
                reply_markup=buttons,
            )
        else:
            # fallback: send a normal message to the group
            sent = await context.bot.send_message(chat_id=chat_id, text=topic_message, reply_markup=buttons)

        # Map the topic message id to the requesting user so admin replies to the panel route correctly
        try:
            group_map = context.application.bot_data.setdefault("group_message_map", {})
            group_map[sent.message_id] = str(user.id)
            group_map[str(sent.message_id)] = str(user.id)
        except Exception:
            pass
        # Save info to allow cancellation later (both per-user and application-wide)
        context.user_data.setdefault("admin_request", {})
        context.user_data["admin_request"][str(user.id)] = {
            "chat_id": chat_id,
            "topic_id": topic_id,
            "topic_message_id": sent.message_id,
            "user_confirmation_message_id": user_confirmation.message_id,
            "first_confirmation_message_id": first_confirmation.message_id,
            "username": username,
            "mood": mood,
        }

        app_requests = context.application.bot_data.setdefault("admin_requests", {})
        app_requests[str(user.id)] = {
            "chat_id": chat_id,
            "topic_id": topic_id,
            "topic_message_id": sent.message_id,
            "user_confirmation_message_id": user_confirmation.message_id,
            "first_confirmation_message_id": first_confirmation.message_id,
            "username": username,
            "mood": mood,
        }
        _save_runtime_snapshot(context)
    elif choice == "◀️ Назад":
        context.user_data.clear()
        await start(update, context)
    else:
        await update.message.reply_text(
            "Пожалуйста, выберите один из вариантов ниже."
        )

async def admin_take_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user:
        return
    admin_profile = _ensure_profile(
        context,
        str(update.effective_user.id),
        update.effective_user.username or f"id{update.effective_user.id}",
    )
    if not _has_admin_rights_level_1_5(admin_profile):
        await update.callback_query.answer("Действие доступно только администраторам 1 категории и выше.", show_alert=True)
        return
    await update.callback_query.answer("Пользователь принят")
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
        f"📊Вы находитесь в переписке на тему {mood}\n"
        f"Имя пользователя: {username}\n"
        f"Айди профиля: {requester_id_profile}\n"
        f"Администратор {admin_username}"
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
        "╭─ ❀ 𝓢𝔂𝓼𝓽𝓮𝓶 ─╮\n\n"
        f'✅ "{admin_tag}" принял ваш запрос.\n\n'
        "💭 Администратор уже подключается к чату и совсем скоро начнёт диалог с вами.\n\n"
        "📨 Вся указанная вами информация уже передана ему, поэтому он немного знаком с вашей ситуацией.\n\n"
        "⏳ Пожалуйста, оставайтесь в чате.\n\n"
        "💌 Если ожидание немного затянется — просто отправьте любое сообщение. Администратор обязательно ответит.\n\n"
        "╰────────────────╯"
    )
    user_text_2 = (
        "╭─ 📌 𝓘𝓷𝓯𝓸 ─╮\n\n"
        "⚙️ Временно недоступна пересылка:\n\n"
        "• 📷 Фото\n"
        "• 🎥 Видео\n"
        "• 🎞 GIF\n"
        "• 😊 Стикеров\n"
        "• 📁 Файлов\n\n"
        "Это связано с технической ошибкой на стороне сервера.\n\n"
        "🛠 Мы уже занимаемся её устранением. Спасибо за терпение!\n\n"
        "╰────────────────╯"
    )
    bio_button = InlineKeyboardMarkup(
        [[InlineKeyboardButton("🔭Биография админа", callback_data=f"show_admin_bio_{request_user_id}")]]
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
            [[KeyboardButton("🤧Отказаться от админа"), KeyboardButton("💤Приостановить общение")]],
            resize_keyboard=True,
            one_time_keyboard=False,
        )
        await context.bot.send_message(chat_id=requester_chat_id, text="Если вам администратор не понравился, то вы можете отменить его кнопкой ниже.", reply_markup=kb)
    except Exception:
        pass

    _save_runtime_snapshot(context)


async def pause_session_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await block_if_banned(update, context):
        return

    user = update.effective_user
    if not user or not update.message:
        return

    active = context.application.bot_data.get("active_chats", {}).get(str(user.id))
    if not active or not active.get("active"):
        await update.message.reply_text("У вас нет активной сессии с администратором.")
        return

    if active.get("paused"):
        resume_kb = InlineKeyboardMarkup(
            [[InlineKeyboardButton("Возомновить общение", callback_data=f"resume_session_{user.id}")]]
        )
        await update.message.reply_text("💤Сессия уже приостановлена.", reply_markup=resume_kb)
        return

    confirm_kb = InlineKeyboardMarkup(
        [[
            InlineKeyboardButton("Уверен(-а)", callback_data=f"pause_confirm_{user.id}"),
            InlineKeyboardButton("Не уверен(-а)", callback_data=f"pause_cancel_{user.id}"),
        ]]
    )
    await update.message.reply_text(
        "╭─ 💤Режим сна ─╮\n\n"
        "Уверены что хотите включить режим сна? Вы и ваш собеседник не будет получать сообщения пока будет активирован режим сна.\n\n"
        "❗️Не злоупотребляйте командой, это запрещено правилами бота\n\n"
        "╰────────────────╯",
        reply_markup=confirm_kb,
    )


async def show_admin_bio_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    data = update.callback_query.data or ""
    parts = data.split("_")
    if len(parts) < 4:
        return

    request_user_id = parts[3]
    if not update.effective_user or str(update.effective_user.id) != str(request_user_id):
        await update.callback_query.answer("Кнопка доступна только владельцу диалога", show_alert=True)
        return

    active = context.application.bot_data.get("active_chats", {}).get(str(request_user_id)) or {}
    admin_bio = str(active.get("admin_biography") or "").strip()
    admin_tag = str(active.get("admin_tag") or active.get("admin_username") or "админ")

    if not admin_bio:
        await update.callback_query.message.reply_text("Биография администратора пока не заполнена.")
        return

    await update.callback_query.message.reply_text(
        f'🔭Биография администратора "{admin_tag}":\n\n{admin_bio}'
    )


async def show_user_profile_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _can_use_moderation_commands(context, str(update.effective_user.id)):
        await update.callback_query.answer("Действие доступно только администраторам 2 категории и выше.", show_alert=True)
        return

    await update.callback_query.answer()
    data = update.callback_query.data or ""
    parts = data.split("_")
    if len(parts) < 4:
        return

    request_user_id = parts[3]
    active = (context.application.bot_data.get("active_chats", {}) or {}).get(str(request_user_id))
    if not active or not active.get("active"):
        await update.callback_query.answer("Тема больше неактуальна", show_alert=True)
        return

    topic_id = getattr(update.callback_query.message, "message_thread_id", None)
    active_topic_id = active.get("topic_id")
    if topic_id is not None and active_topic_id is not None and str(topic_id) != str(active_topic_id):
        await update.callback_query.answer("Тема больше неактуальна", show_alert=True)
        return

    profile = _ensure_profile(context, str(request_user_id), active.get("topic_base_name") or f"id{request_user_id}")
    await update.callback_query.message.reply_text(_build_user_stats_text(context, str(request_user_id), profile))


async def pause_session_cancel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    data = update.callback_query.data or ""
    parts = data.split("_")
    if len(parts) < 3:
        return
    user_id = int(parts[2])
    if not update.effective_user or update.effective_user.id != user_id:
        await update.callback_query.answer("Только владелец сессии может это сделать", show_alert=True)
        return

    try:
        await update.callback_query.message.delete()
    except Exception:
        pass


async def pause_session_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    data = update.callback_query.data or ""
    parts = data.split("_")
    if len(parts) < 3:
        return
    user_id = int(parts[2])
    if not update.effective_user or update.effective_user.id != user_id:
        await update.callback_query.answer("Только владелец сессии может это сделать", show_alert=True)
        return

    active = context.application.bot_data.get("active_chats", {}).get(str(user_id))
    if not active or not active.get("active"):
        await update.callback_query.answer("Активная сессия не найдена", show_alert=True)
        return

    if active.get("paused"):
        await update.callback_query.answer("Сессия уже приостановлена", show_alert=True)
        return

    active["paused"] = True
    chat_id = active.get("chat_id")
    topic_id = active.get("topic_id")

    base_name = active.get("topic_base_name")
    if not base_name:
        profile = (context.application.bot_data.get("profiles", {}) or {}).get(str(user_id), {})
        base_name = profile.get("username") or f"id{user_id}"
        active["topic_base_name"] = base_name

    if chat_id and topic_id is not None:
        try:
            await context.bot.edit_forum_topic(
                chat_id=chat_id,
                message_thread_id=topic_id,
                name=_build_paused_topic_name(base_name),
            )
        except Exception as e:
            logging.exception("pause_session_confirm_callback rename failed: %s", e)

        try:
            pause_msg = await context.bot.send_message(
                chat_id=chat_id,
                message_thread_id=topic_id,
                text="💤Пользователь приостановил сессию, сообщение отправляться не будут.",
            )
            active["pause_topic_message_id"] = pause_msg.message_id
            try:
                await context.bot.pin_chat_message(chat_id=chat_id, message_id=pause_msg.message_id, disable_notification=True)
            except Exception:
                pass
        except Exception as e:
            logging.exception("pause_session_confirm_callback topic notify failed: %s", e)

    try:
        await update.callback_query.message.delete()
    except Exception:
        pass

    resume_kb = InlineKeyboardMarkup(
        [[InlineKeyboardButton("Возомновить общение", callback_data=f"resume_session_{user_id}")]]
    )
    try:
        await context.bot.send_message(
            chat_id=user_id,
            text="💤Сессия приостановлена.",
            reply_markup=resume_kb,
        )
    except Exception:
        pass


async def resume_session_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    data = update.callback_query.data or ""
    parts = data.split("_")
    if len(parts) < 3:
        return
    user_id = int(parts[2])
    if not update.effective_user or update.effective_user.id != user_id:
        await update.callback_query.answer("Только владелец сессии может это сделать", show_alert=True)
        return

    active = context.application.bot_data.get("active_chats", {}).get(str(user_id))
    if not active or not active.get("active"):
        await update.callback_query.answer("Активная сессия не найдена", show_alert=True)
        return

    if not active.get("paused"):
        await update.callback_query.answer("Сессия уже активна", show_alert=True)
        return

    active["paused"] = False
    chat_id = active.get("chat_id")
    topic_id = active.get("topic_id")
    base_name = active.get("topic_base_name") or f"id{user_id}"

    if chat_id and topic_id is not None:
        try:
            await context.bot.edit_forum_topic(
                chat_id=chat_id,
                message_thread_id=topic_id,
                name=base_name,
            )
        except Exception as e:
            logging.exception("resume_session_callback rename failed: %s", e)

        try:
            await context.bot.send_message(
                chat_id=chat_id,
                message_thread_id=topic_id,
                text="✅Пользователь вернулся в чат, тема снова актуальная.",
            )
        except Exception as e:
            logging.exception("resume_session_callback topic notify failed: %s", e)

    try:
        await update.callback_query.message.edit_text("✅Сессия снова активна, пересылка сообщений восстановлена.")
    except Exception:
        pass

async def warn_user_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _can_use_moderation_commands(context, str(update.effective_user.id)):
        await update.callback_query.answer("Действие доступно только администраторам 2 категории и выше.", show_alert=True)
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
        await update.callback_query.answer("Тема больше неактуальна", show_alert=True)
        return
    active_topic_id = active.get("topic_id")
    if topic_id is not None and active_topic_id is not None and str(topic_id) != str(active_topic_id):
        await update.callback_query.answer("Тема больше неактуальна", show_alert=True)
        return

    app_requests = context.application.bot_data.setdefault("admin_requests", {})
    req_info = app_requests.get(str(request_user_id))
    username = req_info.get("username") if req_info else f"id{request_user_id}"

    thread_id = getattr(update.callback_query.message, "message_thread_id", None)
    prompt_text = f'Вы собираетесь предупредить своего пользователя "{username}"?'
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🔁 Отмена", callback_data=f"cancel_warn_{request_user_id}"),
                InlineKeyboardButton("Выдать предупреждение", callback_data=f"confirm_warn_{request_user_id}"),
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
    await update.callback_query.answer("Отмена")
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
        "📌Укажите причину выдачи предупреждения пользователю (пунктом)\n"
        "Правила можно посмотреть здесь https://t.me/c/4417963273/209"
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

    reason = getattr(update.message, "text", None) or getattr(update.message, "caption", None) or "(без причины)"
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
                    text="❌У этого пользователя больше 3 предупреждений, больше выдать нельзя.",
                )
            else:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text="❌У этого пользователя больше 3 предупреждений, больше выдать нельзя.",
                )
        except Exception:
            pass
        pending_warns.pop(key, None)
        raise ApplicationHandlerStop

    profile["warn"] += 1
    profile["reason"] = reason
    await enforce_autoban_if_needed(context, str(request_user_id), username)

    success_text = (
        f'✅Вы успешно выдали предупреждение пользователю "{username}" с причиной "{reason}". '
        f'Теперь у него {profile["warn"]} предупреждений'
    )
    try:
        if thread_id is not None:
            await context.bot.send_message(chat_id=chat_id, text=success_text, message_thread_id=thread_id)
        else:
            await context.bot.send_message(chat_id=chat_id, text=success_text)
    except Exception as e:
        logging.exception("handle_warn_reason_message success message failed: %s", e)

    user_text = (
        "╭─ 🚨 𝓝𝓸𝓽𝓲𝓬𝓮 ─╮\n\n"
        "❗️Вы получили предупреждение от своего администратора.\n\n"
        f"✦ Причина: {reason}\n"
        f"✦ Всего предупреждений: {profile['warn']}\n\n"
        "╰────────────────╯"
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
        await update.callback_query.answer("Действие доступно только администраторам 2 категории и выше.", show_alert=True)
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
        await update.callback_query.answer("Тема больше неактуальна", show_alert=True)
        return
    active_topic_id = active.get("topic_id")
    if topic_id is not None and active_topic_id is not None and str(topic_id) != str(active_topic_id):
        await update.callback_query.answer("Тема больше неактуальна", show_alert=True)
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
                text="💬Пожалуйста, укажите причину почему вы собираетесь отказаться от пользователя",
            )
        else:
            prompt = await context.bot.send_message(
                chat_id=chat_id,
                text="💬Пожалуйста, укажите причину почему вы собираетесь отказаться от пользователя",
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
        await update.callback_query.answer("Действие доступно только администраторам 2 категории и выше.", show_alert=True)
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
        await update.callback_query.answer("Тема больше неактуальна", show_alert=True)
        return
    req_topic_id = req_check.get("topic_id")
    if topic_id is not None and req_topic_id is not None and str(topic_id) != str(req_topic_id):
        await update.callback_query.answer("Тема больше неактуальна", show_alert=True)
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
            [[InlineKeyboardButton("отмена", callback_data=f"cancel_decline_request_{request_user_id}")]]
        )
        if thread_id is not None:
            prompt = await context.bot.send_message(
                chat_id=chat_id,
                message_thread_id=thread_id,
                text="ℹ️Введите причину чтобы отказать запрос в поиске админа",
                reply_markup=cancel_button,
            )
        else:
            prompt = await context.bot.send_message(
                chat_id=chat_id,
                text="ℹ️Введите причину чтобы отказать запрос в поиске админа",
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
    await update.callback_query.answer("Отмена")
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
                    text="Такое нельзя назвать причиной. Отправьте только текст без ссылок.",
                )
            else:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text="Такое нельзя назвать причиной. Отправьте только текст без ссылок.",
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
                    name=f"{username} (Закрыто системой)",
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
                    text="✅Пользователь успешно получил уведомление о отказе в поиске админа",
                )
            except Exception:
                pass
        else:
            try:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text="✅Пользователь успешно получил уведомление о отказе в поиске админа",
                )
            except Exception:
                pass

        try:
            await context.bot.send_message(
                chat_id=LOG_CHAT_ID,
                text=f'⭕️Администратор "{pending.get("admin_username")}" закрыл запрос о поиске админа пользователю "{username}"',
            )
        except Exception:
            pass

        try:
            await context.bot.send_message(
                chat_id=int(request_user_id),
                text=(
                    "🌸 **Запрос был отказан**\n\n"
                    "К сожалению, сейчас мы не можем найти для Вас администратора.\n\n"
                    f'Это не связано с Вами лично — просто в данный момент ваш запрос был отклонен по причине "{text_reason}"\n\n'
                    "Пожалуйста, попробуйте снова через некоторое время. Возможно, через час или 2.\n\n"
                    "Берегите себя. Мы помним о Вас! 💛"
                ),
                parse_mode=ParseMode.MARKDOWN,
            )
        except Exception as e:
            logging.exception("failed to notify user about declined search request: %s", e)

        try:
            kb = _build_main_menu_keyboard(context, int(request_user_id), profile.get("username"))
            await context.bot.send_message(chat_id=int(request_user_id), text="Выберите действие в меню ниже:", reply_markup=kb)
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
                text="✅Ваш запрос на отказ от пользователя был отправлен старшей администрации, ожидайте.",
            )
        else:
            await context.bot.send_message(
                chat_id=chat_id,
                text="✅Ваш запрос на отказ от пользователя был отправлен старшей администрации, ожидайте.",
            )
    except Exception:
        pass

    seq = context.application.bot_data.get("decline_review_seq", 0) + 1
    context.application.bot_data["decline_review_seq"] = seq
    review_id = str(seq)

    review_text = (
        f'⚠️Запрос на отказ пользователя от "{pending.get("admin_username")}"\n\n'
        f'Пришло уведомление, что админ "{pending.get("admin_username")}" хочет отказаться от своего пользователя "{pending.get("username")}" по причине "{pending.get("reason_otkaz_ot_username")}".\n\n'
        "Выберите кнопку ниже⬇️"
    )
    review_kb = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🟢Одобрить", callback_data=f"approve_decline_{review_id}"),
                InlineKeyboardButton("🔴Отказать", callback_data=f"reject_decline_{review_id}"),
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

    approved_text = review.get("review_text", "") + "\n\nОдобрено🟩"
    try:
        await update.callback_query.message.edit_text(approved_text)
    except Exception as e:
        logging.exception("approve_decline_callback edit failed: %s", e)

    chat_id = review.get("chat_id")
    thread_id = review.get("thread_id")
    try:
        msg = f'💔Запрос на отказ от пользователя "{review.get("username")}" был успешно одобрен руководством бота, он больше не будет получать от вас сообщение.'
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
                name=f'{review.get("username") or f"id{user_id}"} (Закрыто админом)',
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
        "🙏 **Администратор отказался от вас.**\n\n"
        "К сожалению, наш специалист не смог продолжить общение с Вами.\n\n"
        "Это случается в работе - иногда просто не совпал характер, или вы нарушали правила анонимности или другое.\n\n"
        "Не принимайте это на свой счёт. Вы — замечательный собеседник, и мы уверены, что другой администратор с радостью Вас примет.\n\n"
        "Вы все еще можете найти другого администратора на свой вкус.\n\n"
        "❤️ Мы всегда рядом."
    )
    try:
        kb = _build_main_menu_keyboard(context, int(user_id), review.get("username"))
        await context.bot.send_message(chat_id=int(user_id), text=user_text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)
        await context.bot.send_message(chat_id=int(user_id), text="Выберите действие в меню ниже:", reply_markup=kb)
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

    rejected_text = review.get("review_text", "") + "\n\nОтказано🟥"
    try:
        await update.callback_query.message.edit_text(rejected_text)
    except Exception as e:
        logging.exception("reject_decline_callback edit failed: %s", e)

    try:
        msg = f'❌Запрос на отказ от пользователя "{review.get("username")}" отклонен руководством.'
        if review.get("thread_id") is not None:
            await context.bot.send_message(chat_id=review.get("chat_id"), message_thread_id=review.get("thread_id"), text=msg)
        else:
            await context.bot.send_message(chat_id=review.get("chat_id"), text=msg)
    except Exception:
        pass

    reviews.pop(review_id, None)


async def cancel_search_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await block_if_banned(update, context):
        return
    await update.callback_query.answer()
    data = update.callback_query.data  # e.g. "cancel_search_12345"
    parts = data.split("_")
    if len(parts) < 3:
        return
    user_id = int(parts[2])
    # Only the original user may cancel their request
    if update.effective_user.id != user_id:
        await update.callback_query.answer("Только запросивший пользователь может отменить поиск", show_alert=True)
        return

    # Ask for confirmation (edit the confirmation message)
    confirm_kb = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Да", callback_data=f"confirm_cancel_{user_id}"),
                InlineKeyboardButton("Нет", callback_data=f"deny_cancel_{user_id}"),
            ]
        ]
    )
    try:
        await update.callback_query.message.edit_text("Вы уверены что хотите отменить поиск нового администратора?", reply_markup=confirm_kb)
    except BadRequest:
        await update.callback_query.message.reply_text("Вы уверены что хотите отменить поиск нового администратора?", reply_markup=confirm_kb)


async def deny_cancel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await block_if_banned(update, context):
        return
    await update.callback_query.answer()
    data = update.callback_query.data
    parts = data.split("_")
    if len(parts) < 3:
        return
    user_id = int(parts[2])
    if update.effective_user.id != user_id:
        await update.callback_query.answer("Только запросивший пользователь может подтвердить", show_alert=True)
        return

    # Restore the single "Отменить" button
    try:
        await update.callback_query.message.edit_text(
            "Если хотите отменить поиск администратора — нажмите кнопку ниже.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Отменить", callback_data=f"cancel_search_{user_id}")]]),
        )
    except BadRequest:
        pass


async def confirm_cancel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await block_if_banned(update, context):
        return
    await update.callback_query.answer()
    data = update.callback_query.data
    parts = data.split("_")
    if len(parts) < 3:
        return
    user_id = int(parts[2])
    if update.effective_user.id != user_id:
        await update.callback_query.answer("Только запросивший пользователь может подтвердить", show_alert=True)
        return

    # Edit the confirmation message to show cancellation text and remove buttons
    try:
        await update.callback_query.message.edit_text(
            "Вы отменили поиск администратора. Если захотите начать заново, нажмите кнопку \"👤 Найти админа\"."
        )
    except BadRequest:
        pass

    # Send a short notification to the group/topic if the request exists
    user_requests = context.user_data.get("admin_request", {})
    req_info = user_requests.get(str(user_id)) if user_requests else None
    if req_info:
        chat_id = req_info.get("chat_id")
        topic_id = req_info.get("topic_id")
        try:
            if topic_id:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text="😢 Пользователь отменил поиск администратора.",
                    message_thread_id=topic_id,
                )
            else:
                await context.bot.send_message(chat_id=chat_id, text="😢 Пользователь отменил поиск администратора.")
        except Exception:
            pass

        if topic_id:
            async def _delete_topic():
                await asyncio.sleep(60)
                try:
                    await context.bot.delete_forum_topic(chat_id=chat_id, message_thread_id=topic_id)
                except Exception:
                    pass

            asyncio.create_task(_delete_topic())

    # Clear saved request state for this user
    if user_requests and str(user_id) in user_requests:
        del user_requests[str(user_id)]
    app_requests = context.application.bot_data.get("admin_requests", {})
    if str(user_id) in app_requests:
        del app_requests[str(user_id)]

    # Restore keyboard for the user
    try:
        kb = _build_main_menu_keyboard(context, user_id, update.effective_user.username if update.effective_user else None)
        await context.bot.send_message(chat_id=user_id, text="Поиск администратора отменен.", reply_markup=kb)
    except Exception:
        pass

async def find_admin_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    requester_profile = _ensure_profile(
        context,
        str(update.effective_user.id),
        update.effective_user.username or f"id{update.effective_user.id}",
    )
    if _has_admin_rights_level_1_5(requester_profile):
        await update.message.reply_text("⛔ Администратору нельзя искать общение для начала сессии.")
        return
    if await check_active_chat_block(update, context):
        return
    context.user_data["profile"] = 1
    await send_admin_menu(update, context)


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
        await context.bot.send_message(chat_id=chat.id, text="🚫Я не могу здесь находиться, покидаю чат..")
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
                await update.message.reply_text("Доступ запрещён.")
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
            await update.message.reply_text("Да мой великий Иванушка, дамп карт успешно тебе в лс отправил")
        except Exception:
            try:
                await context.bot.send_message(chat_id=LOG_CHAT_ID, text=text)
                await update.message.reply_text(f"Не удалось отправить личное сообщение. Дамп отправлен в лог-чат {LOG_CHAT_ID}.")
            except Exception:
                await update.message.reply_text("Не удалось отправить дамп карт ни в личку, ни в лог-чат.")
    except Exception as e:
        logging.exception("dump_maps_handler failed: %s", e)


async def add_rules_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != LOG_CHAT_ID:
        return
    if not update.effective_user or not _is_topic_admin(context, str(update.effective_user.id)):
        await update.message.reply_text("Команда доступна только администраторам 4 категории и выше.")
        return

    existing = context.application.bot_data.get("global_rules_text")
    if existing:
        await update.message.reply_text("Правила уже существуют, чтобы их удалить напишите /delrules")
        return

    raw_text = update.message.text or ""
    cmd_entity = update.message.entities[0] if update.message.entities else None
    cmd_len = cmd_entity.length if cmd_entity and cmd_entity.type == "bot_command" else len("/addrules")
    rules_text = raw_text[cmd_len:]
    if rules_text[:1] in {" ", "\n", "\t"}:
        rules_text = rules_text[1:]

    if not rules_text.strip():
        await update.message.reply_text("Укажите текст правил после команды /addrules")
        return

    context.application.bot_data["global_rules_text"] = rules_text
    await update.message.reply_text("📍Правила успешно сменены")

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
            new_topic = await context.bot.create_forum_topic(chat_id=RULES_CHAT_ID, name="Правила")
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
        await update.message.reply_text("Команда доступна только администраторам 4 категории и выше.")
        return

    existing_rules_text = context.application.bot_data.get("global_rules_text")
    existing_rules_message_id = context.application.bot_data.get("global_rules_message_id")
    if not existing_rules_text and not existing_rules_message_id:
        await context.bot.send_message(
            chat_id=LOG_CHAT_ID,
            text="ℹ️Правила не установлены: Используйте команду /addrules чтобы добавить новые правила.",
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
        new_topic = await context.bot.create_forum_topic(chat_id=RULES_CHAT_ID, name="Правила")
        context.application.bot_data["rules_thread_id"] = new_topic.message_thread_id
    except Exception as e:
        logging.exception("del_rule_handler create_forum_topic failed: %s", e)

    context.application.bot_data.pop("global_rules_text", None)
    context.application.bot_data.pop("global_rules_message_id", None)
    await update.message.reply_text("✅Правила успешно удалены. Используйте команду /addrules чтобы добавить новые правила.")


async def handle_decline_reason_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # This handler catches admin replies to the bot's "Введите причину..." prompt
    if not update.message.reply_to_message:
        return
    replied_id = update.message.reply_to_message.message_id
    pending = context.application.bot_data.get("pending_reasons", {}).get(replied_id)
    if not pending:
        return

    reason = update.message.text or "(без причины)"
    request_user_id = pending.get("request_user_id")
    chat_id = pending.get("group_chat_id")
    thread_id = pending.get("thread_id")

    # delete the bot's prompt message
    try:
        await context.bot.delete_message(chat_id=chat_id, message_id=replied_id)
    except Exception:
        pass

    # announce in group/topic
    result_text = f'Вы успешно отказали запрос об поиске админа пользователю {request_user_id} с причиной "{reason}"'
    try:
        if thread_id:
            await context.bot.send_message(chat_id=chat_id, text=result_text, message_thread_id=thread_id)
        else:
            await context.bot.send_message(chat_id=chat_id, text=result_text)
    except Exception:
        pass

    # notify requester in private: delete confirmation messages and send rejection message
    app_reqs = context.application.bot_data.get("admin_requests", {})
    req_info = app_reqs.pop(str(request_user_id), None)
    if req_info:
        requester_chat_id = int(request_user_id)
        topic_id = req_info.get("topic_id")
        if topic_id:
            topic_map = context.application.bot_data.get("topic_user_map", {})
            if topic_map is not None:
                topic_map.pop(str(topic_id), None)
        # Attempt to delete stored messages in user's chat
        try:
            if req_info.get("user_confirmation_message_id"):
                await context.bot.delete_message(chat_id=requester_chat_id, message_id=req_info.get("user_confirmation_message_id"))
        except Exception:
            pass
        try:
            if req_info.get("first_confirmation_message_id"):
                await context.bot.delete_message(chat_id=requester_chat_id, message_id=req_info.get("first_confirmation_message_id"))
        except Exception:
            pass

        # Increment warn count and set last reason in profile data
        profile = _ensure_profile(context, str(request_user_id), f"id{request_user_id}")
        profile["warn"] += 1
        profile["reason"] = reason
        await enforce_autoban_if_needed(context, str(request_user_id), profile.get("username"))

        # send rejection DM with keyboard restored
        sad_text = (
            f"😢 К сожалению, ваш запрос на поиск админа был отклонен по причине \"{reason}\"\n\n"
            "Ваш профиль был отмечен как нарушающий правила нашего сервиса. Мы не можем продолжить общение, так как это противоречит нашим правилам бота."
        )
        try:
            kb = _build_main_menu_keyboard(context, requester_chat_id, profile.get("username"))
            await context.bot.send_message(chat_id=requester_chat_id, text=sad_text, reply_markup=kb)
        except Exception:
            pass

    # clean up pending state
    try:
        del context.application.bot_data["pending_reasons"][replied_id]
    except Exception:
        pass


async def user_private_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user:
        return
    user_id = str(user.id)

    if is_user_banned(context, user_id):
        return

    if await admin_candidate_private_flow(update, context):
        return

    pending_nickname = context.user_data.get("pending_nickname_change")
    if pending_nickname and pending_nickname.get("active"):
        nickname_text = (update.message.text or "").strip()
        if len(nickname_text) < 3:
            await update.message.reply_text("Слишком короткий никнейм. Минимум 3 символа.")
            return
        if len(nickname_text) > 13:
            await update.message.reply_text("Слишком длинный никнейм. Максимум 13 символов.")
            return

        if not nickname_text:
            await update.message.reply_text("Никнейм не должен быть пустым.")
            return

        if _is_user_nickname_taken(context, user_id, nickname_text):
            await update.message.reply_text("Этот никнейм уже занят. Укажите другой.")
            return

        profile = _ensure_profile(context, user_id, update.effective_user.username or f"id{user_id}")
        profile["user_nickname"] = nickname_text
        _save_profile_record(context, user_id)

        prompt_message_id = pending_nickname.get("prompt_message_id")
        if prompt_message_id:
            try:
                await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=prompt_message_id)
            except Exception:
                pass

        context.user_data.pop("pending_nickname_change", None)
        await update.message.reply_text(f"✅Никнейм обновлен: {nickname_text}")
        return

    # If user is in pending cancel flow, accept the text as reason
    if context.user_data.get("pending_admin_cancel"):
        reason = update.message.text or "(без причины)"
        reason_otkazik = reason
        user_id_int = update.effective_user.id

        profile = _ensure_profile(context, str(user_id_int), update.effective_user.username or f"id{user_id_int}")
        profile["reason_otkazik"] = reason_otkazik

        context.user_data.pop("pending_admin_cancel", None)

        user_sessions = context.application.bot_data.setdefault("active_chats", {})
        active_session = user_sessions.get(user_id)
        if active_session:
            chat_id = active_session.get("chat_id")
            topic_id = active_session.get("topic_id")
            admin_username = active_session.get("admin_username", "unknown")
            _set_last_admin_tag_for_user(
                context,
                user_id_int,
                active_session.get("admin_tag") or active_session.get("admin_username"),
            )
            username = profile.get("username") or (update.effective_user.username or f"id{user_id_int}")

            if topic_id is not None:
                try:
                    await context.bot.edit_forum_topic(
                        chat_id=chat_id,
                        message_thread_id=topic_id,
                        name=f"{username} (отказано пользователем)",
                    )
                except Exception as e:
                    logging.exception("failed to rename declined topic: %s", e)

            try:
                if topic_id is not None:
                    await context.bot.send_message(
                        chat_id=chat_id,
                        message_thread_id=topic_id,
                        text="💔Пользователь этой темы к сожалению отказался от администратора. Тема больше неактуальна.",
                    )
                else:
                    await context.bot.send_message(
                        chat_id=chat_id,
                        text="💔Пользователь этой темы к сожалению отказался от администратора. Тема больше неактуальна.",
                    )
            except Exception as e:
                logging.exception("failed to notify topic about user cancel: %s", e)

            cancel_review_text = (
                "🟥Отказ пользователя от администратора\n\n"
                f"Пришло уведомление, что пользователь нашего бота \"{username}\" отказался от администратора \"{admin_username}\" "
                f"по причине \"{reason_otkazik}\"\n\n"
                "Выберите кнопки ниже⬇️"
            )

            seq = context.application.bot_data.get("user_cancel_review_seq", 0) + 1
            context.application.bot_data["user_cancel_review_seq"] = seq
            review_id = str(seq)

            try:
                review_msg = await context.bot.send_message(
                    chat_id=LOG_CHAT_ID,
                    text=cancel_review_text,
                    reply_markup=InlineKeyboardMarkup(
                        [[InlineKeyboardButton("🔺Предупредить пользователя", callback_data=f"warn_user_cancel_{review_id}")]]
                    ),
                )
                pending = context.application.bot_data.setdefault("pending_user_cancel_reviews", {})
                pending[review_id] = {
                    "review_text": cancel_review_text,
                    "review_message_id": review_msg.message_id,
                    "user_id": str(user_id_int),
                    "username": username,
                    "admin_username": admin_username,
                    "reason_otkazik": reason_otkazik,
                }
            except Exception as e:
                logging.exception("failed to send user-cancel review to log chat: %s", e)

            try:
                del user_sessions[user_id]
            except Exception:
                pass

            topic_map = context.application.bot_data.setdefault("topic_user_map", {})
            if topic_id is not None:
                topic_map.pop(topic_id, None)
                topic_map.pop(str(topic_id), None)

        await update.message.reply_text(
            f"🙏 **Диалог завершён по Вашему желанию с причиной \"{reason_otkazik}\"**\n\n"
            "Мы приняли Ваш запрос на завершение общения. Администратор уже знает, что Вы закончили беседу.\n\n"
            "Вы всегда можете вернуться в любое время - мы будем рады Вас слышать.\n\n"
            "Если захотите оставить отзыв или задать вопрос, кнопки ниже помогут Вам.",
            parse_mode=ParseMode.MARKDOWN,
        )
        try:
            kb = _build_main_menu_keyboard(context, user_id_int, profile.get("username"))
            await context.bot.send_message(chat_id=user_id_int, text="Выберите действие в меню ниже:", reply_markup=kb)
        except Exception:
            pass
        return

    # Forward user messages to special group topic if active chat exists
    user_sessions = context.application.bot_data.get("active_chats", {})
    active = user_sessions.get(user_id)
    if not active or not active.get("active"):
        return

    if active.get("paused") or active.get("management_paused"):
        resume_kb = InlineKeyboardMarkup(
            [[InlineKeyboardButton("Возомновить общение", callback_data=f"resume_session_{user_id}")]]
        )
        await update.message.reply_text("💤Включен режим остановки. Администратору не отправится сообщение пока общение не будет возобновлено.", reply_markup=resume_kb)
        return

    suspicious_text = (getattr(update.message, "text", None) or getattr(update.message, "caption", None) or "").strip()
    bypass_until = float(active.get("suspicious_bypass_user_to_admin_until", 0) or 0)
    if _has_suspicious_username(suspicious_text) and time.time() >= bypass_until:
        active["detect_topic"] = int(active.get("detect_topic", 0) or 0) + 1
        chat_id = active.get("chat_id")
        topic_id = active.get("topic_id")
        topic_link = _topic_url(chat_id, topic_id)
        username = update.effective_user.username or f"id{update.effective_user.id}"
        review_id = _next_suspicious_review_id(context)

        pending = context.application.bot_data.setdefault("pending_suspicious_user_msgs", {})
        pending[review_id] = {
            "user_id": str(user.id),
            "username": username,
            "chat_id": chat_id,
            "topic_id": topic_id,
            "source_chat_id": update.message.chat_id,
            "source_message_id": update.message.message_id,
            "text": suspicious_text,
        }

        try:
            await update.message.reply_text(
                "╭─ 🛡Анти-реклама ─╮\n\n"
                "Ваше сообщения является подозрительным и не будет отправлено администратору до тех пор пока не рассмотрит руководство бота.\n\n"
                "╰────────────────╯"
            )
        except Exception:
            pass

        review_text = (
            "💬Подозрительное сообщение\n\n"
            f'Пользователь "{username}" попытался отправить админу сообщение в тему "{topic_link}" с текстом "{suspicious_text}"\n\n'
            "Выберите действие ниже⬇️"
        )
        review_kb = InlineKeyboardMarkup(
            [[
                InlineKeyboardButton("🟢Отправить сообщение админу", callback_data=f"susp_user_allow_{review_id}"),
                InlineKeyboardButton("🛑Не доставлять сообщения админу", callback_data=f"susp_user_deny_{review_id}"),
            ], [
                InlineKeyboardButton("➖Предупредить пользователя", callback_data=f"susp_user_warn_{review_id}"),
            ]]
        )
        try:
            await context.bot.send_message(chat_id=LOG_CHAT_ID, text=review_text, reply_markup=review_kb)
        except Exception as e:
            logging.exception("user suspicious review send failed: %s", e)
        return

    chat_id = active.get("chat_id")
    topic_id = active.get("topic_id")
    profile = _ensure_profile(context, user_id, update.effective_user.username or f"id{update.effective_user.id}")

    try:
        copied = await context.bot.copy_message(
            chat_id=chat_id,
            from_chat_id=update.message.chat_id,
            message_id=update.message.message_id,
            message_thread_id=topic_id if topic_id else None,
        )
        profile["message_user"] = int(profile.get("message_user", 0) or 0) + 1
        active["msg_topic_user"] = int(active.get("msg_topic_user", 0) or 0) + 1
        if _is_rp_action_text(update.message.text or update.message.caption):
            active["rp_topic"] = int(active.get("rp_topic", 0) or 0) + 1
        group_map = context.application.bot_data.setdefault("group_message_map", {})
        group_map[copied.message_id] = user_id
        group_map[str(copied.message_id)] = user_id
    except Exception:
        try:
            await deliver_message_to_user(context.bot, update.message, int(chat_id))
            profile["message_user"] = int(profile.get("message_user", 0) or 0) + 1
            active["msg_topic_user"] = int(active.get("msg_topic_user", 0) or 0) + 1
            if _is_rp_action_text(update.message.text or update.message.caption):
                active["rp_topic"] = int(active.get("rp_topic", 0) or 0) + 1
        except Exception:
            pass


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
                text=f"⛔️Вы находитесь в муте еще {remaining_minutes} мин. Сообщение не отправлено.",
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
                text="⚠️Сообщение является подозрительным и отправлено на проверку старшей администрации.",
            )
        except Exception:
            pass

        review_text = (
            "💬Подозрительное сообщение\n\n"
            f'Админ "{admin_username}" попытался отправить сообщение в тему "{topic_link}" с текстом "{suspicious_text}"\n\n'
            "Выберите действие ниже⬇️"
        )
        review_kb = InlineKeyboardMarkup(
            [[
                InlineKeyboardButton("🟢Отправить сообщение", callback_data=f"susp_admin_allow_{review_id}"),
                InlineKeyboardButton("🔴Не отправлять сообщение", callback_data=f"susp_admin_deny_{review_id}"),
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

async def ask_cancel_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await block_if_banned(update, context):
        return

    await update.message.reply_text(
        "╭─ 🚨 Меню отказа ─╮\n"
        "Вы уверены что хотите отказаться от администратора? Он будет скучать за вами..😢\n"
        "╰────────────────╯",
        reply_markup=InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("Уверен(-а)", callback_data=f"confirm_admin_cancel_{update.effective_user.id}"),
                    InlineKeyboardButton("Не уверен(-а)", callback_data=f"deny_admin_cancel_{update.effective_user.id}"),
                ]
            ]
        ),
    )

async def admin_cancel_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    data = update.callback_query.data
    parts = data.split("_")
    if len(parts) < 4:
        return
    user_id = int(parts[3])
    if update.effective_user.id != user_id:
        await update.callback_query.answer("Только владелец запроса может подтвердить", show_alert=True)
        return

    try:
        await update.callback_query.message.edit_text(
            "╭─ 🚨 Меню отказа / 2 пункт  ─╮\n"
            "Введите пожалуйста более подробную причину что вам не понравилось в общении с администратором, а мы постараемся это больше не совершать🥺\n"
            "╰────────────────╯"
        )
    except BadRequest:
        await update.callback_query.message.reply_text(
            "╭─ 🚨 Меню отказа / 2 пункт  ─╮\n"
            "Введите пожалуйста более подробную причину что вам не понравилось в общении с администратором, а мы постараемся это больше не совершать🥺\n"
            "╰────────────────╯"
        )

    context.user_data["pending_admin_cancel"] = True

async def admin_cancel_deny_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    data = update.callback_query.data
    parts = data.split("_")
    if len(parts) < 4:
        return
    user_id = int(parts[3])
    if update.effective_user.id != user_id:
        await update.callback_query.answer("Только владелец запроса может подтвердить", show_alert=True)
        return

    try:
        await update.callback_query.message.edit_text("Отмена отменена. Если потребуется, нажмите кнопку снова.")
    except BadRequest:
        pass


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
    profile["reason"] = "Нарушение правил отказа от администратора от руководства бота"
    await enforce_autoban_if_needed(context, str(user_id), review.get("username"))

    try:
        await context.bot.send_message(
            chat_id=int(user_id),
            text=f'❗️Вы получили предупреждение с причиной "Нарушение правил отказа от администратора" от руководства бота. Теперь у вас предупреждений {profile["warn"]}',
        )
    except Exception as e:
        logging.exception("warn_user_cancel_callback failed to notify user: %s", e)

    edited_text = (
        "🟥Отказ пользователя от администратора\n\n"
        f'Пришло уведомление, что пользователь нашего бота "{review.get("username")}" отказался от администратора "{review.get("admin_username")}" '
        f'по причине "{review.get("reason_otkazik")}"\n\n'
        "Пользователь получил предупреждение✅"
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
            await update.callback_query.message.edit_text((update.callback_query.message.text or "") + "\n\n✅Разрешено")
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
            text="⛔Проверка не пройдена, сообщение не отправится пользователю в ЛС.",
        )
    except Exception:
        pass

    try:
        await update.callback_query.message.edit_text((update.callback_query.message.text or "") + "\n\n❌Не отправлено")
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
                text="✅Проверка пройдена, сообщение отправлено администратору.",
            )
        except Exception:
            pass

        try:
            await update.callback_query.message.edit_text((update.callback_query.message.text or "") + "\n\n✅Разрешено")
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
            text="⛔Сообщение является нарушением бота и не было отправлено администратору.",
        )
    except Exception:
        pass

    try:
        await update.callback_query.message.edit_text((update.callback_query.message.text or "") + "\n\n❌Не отправлено")
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
    profile["reason"] = "Подозрительное сообщение с чужим username"
    await enforce_autoban_if_needed(context, user_id, profile.get("username"))
    _save_profile_record(context, user_id)

    try:
        await context.bot.send_message(
            chat_id=int(user_id),
            text="⛔Сообщение не было отправлено администратору. Вы получили предупреждение.",
        )
    except Exception:
        pass

    try:
        await update.callback_query.message.edit_text((update.callback_query.message.text or "") + "\n\nПользователь получил предупреждение")
    except Exception:
        pass

    pending.pop(review_id, None)

async def find_admin_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    requester_profile = _ensure_profile(
        context,
        str(update.effective_user.id),
        update.effective_user.username or f"id{update.effective_user.id}",
    )
    if _has_admin_rights_level_1_5(requester_profile):
        await update.message.reply_text("⛔ Администратору нельзя искать администратора для начала сессии.")
        return
    if _is_active_admin_candidate(context, str(update.effective_user.id)):
        await update.message.reply_text(_candidate_block_text())
        return
    if await check_active_chat_block(update, context):
        return
    context.user_data["profile"] = 1
    await send_admin_menu(update, context)


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
    profile["admin_rank"] = "Стажер"
    profile["admin_candidate"] = False
    profile["admin_candidate_status"] = "approved"
    _save_profile_record(context, user_id)

    state_map = context.application.bot_data.setdefault("admin_candidate_state", {})
    state_map.pop(user_id, None)

    try:
        await context.bot.send_message(
            chat_id=int(user_id),
            text="✅Ваша кандидатура одобрена. Вам выданы права категории 1.",
        )
    except Exception:
        pass

    try:
        await update.callback_query.message.edit_text((update.callback_query.message.text or "") + "\n\n✅Кандидат одобрен")
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
        [[InlineKeyboardButton("отмена", callback_data=f"cancel_candidate_reject_{review_id}")]]
    )
    prompt = await context.bot.send_message(
        chat_id=LOG_CHAT_ID,
        text="Укажите причину отказа кандидату:",
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
    await update.callback_query.answer("Отмена")
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
        await update.message.reply_text("Причина не может быть пустой.")
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
            text=f'❌Заявка не была принята. Причина: "{reason}"\n\nНачните заново с этапа 1.',
        )
        await _send_candidate_stage_1(context, int(candidate_user_id))
    except Exception:
        pass

    try:
        await update.message.reply_text("✅Сообщение отправлено кандидату в ЛС.")
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


def main() -> None:
    app = ApplicationBuilder().token(TOKEN).post_init(_post_init).build()
    _init_persistent_storage(app)
    app.add_handler(
        MessageHandler(
            (filters.PHOTO | filters.VIDEO)
            & filters.CaptionRegex(r"^/sendpiar(?:@[\w_]+)?(?:\s+.*)?$"),
            sendpiar_media_router,
        ),
        group=-3,
    )
    app.add_handler(MessageHandler(filters.ALL & (filters.ChatType.GROUP | filters.ChatType.SUPERGROUP), unknown_chat_guard), group=-2)
    app.add_handler(
        MessageHandler(
            filters.TEXT
            & ~filters.Regex(r"^/sendpiar(?:@[\w_]+)?(?:\s+.*)?$")
            & filters.Chat(COOPERATION_CHAT_ID),
            cooperation_admin_command_guard,
        ),
        group=-1,
    )
    app.add_handler(MessageHandler(filters.TEXT & filters.Chat(LOG_CHAT_ID), log_command_router), group=-1)
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND & (filters.ChatType.GROUP | filters.ChatType.SUPERGROUP), admin_mute_guard_handler), group=-1)
    app.add_handler(CommandHandler("addrules", add_rules_handler), group=-1)
    app.add_handler(CommandHandler("delrules", del_rule_handler), group=-1)
    app.add_handler(CommandHandler("makeadmin", makeadmin_command_handler, filters=filters.ChatType.PRIVATE | filters.ChatType.GROUP | filters.ChatType.SUPERGROUP), group=-1)
    app.add_handler(CommandHandler("setprefix", setprefix_command_handler, filters=filters.ChatType.PRIVATE | filters.ChatType.GROUP | filters.ChatType.SUPERGROUP), group=-1)
    app.add_handler(CommandHandler("anpiar", anpiar_command_handler, filters=filters.ChatType.PRIVATE | filters.ChatType.GROUP | filters.ChatType.SUPERGROUP), group=-1)
    app.add_handler(CommandHandler("fullstats", fullstats_command_handler, filters=filters.ChatType.PRIVATE | filters.ChatType.GROUP | filters.ChatType.SUPERGROUP), group=-1)
    app.add_handler(CommandHandler("sendpiar", sendpiar_command_handler, filters=filters.ChatType.PRIVATE | filters.ChatType.GROUP | filters.ChatType.SUPERGROUP), group=-1)
    app.add_handler(CommandHandler("pm", pm_command_handler, filters=filters.ChatType.PRIVATE | filters.ChatType.GROUP | filters.ChatType.SUPERGROUP), group=-1)
    app.add_handler(CommandHandler("prava", prava_command_handler, filters=filters.ChatType.PRIVATE | filters.ChatType.GROUP | filters.ChatType.SUPERGROUP), group=-1)
    app.add_handler(CommandHandler("ban", ban_command_handler, filters=filters.ChatType.PRIVATE | filters.ChatType.GROUP | filters.ChatType.SUPERGROUP), group=-1)
    app.add_handler(CommandHandler("unban", unban_command_handler, filters=filters.ChatType.PRIVATE | filters.ChatType.GROUP | filters.ChatType.SUPERGROUP), group=-1)
    app.add_handler(CommandHandler("amute", amute_command_handler, filters=filters.ChatType.GROUP | filters.ChatType.SUPERGROUP), group=-1)
    app.add_handler(CommandHandler("aunmute", unmute_command_handler, filters=filters.ChatType.GROUP | filters.ChatType.SUPERGROUP), group=-1)
    app.add_handler(CommandHandler("warn", warn_command_handler, filters=filters.ChatType.PRIVATE | filters.ChatType.GROUP | filters.ChatType.SUPERGROUP), group=-1)
    app.add_handler(CommandHandler("unwarn", unwarn_command_handler, filters=filters.ChatType.PRIVATE | filters.ChatType.GROUP | filters.ChatType.SUPERGROUP), group=-1)
    app.add_handler(CommandHandler("stats", stats_command_handler, filters=filters.ChatType.PRIVATE | filters.ChatType.GROUP | filters.ChatType.SUPERGROUP), group=-1)
    app.add_handler(CommandHandler("astats", astats_command_handler, filters=filters.ChatType.PRIVATE | filters.ChatType.GROUP | filters.ChatType.SUPERGROUP), group=-1)
    app.add_handler(CommandHandler("info_topic", info_topic_command_handler, filters=filters.ChatType.GROUP | filters.ChatType.SUPERGROUP | filters.ChatType.CHANNEL), group=-1)
    app.add_handler(CommandHandler("topic", topic_command_handler, filters=filters.ChatType.GROUP | filters.ChatType.SUPERGROUP), group=-1)
    app.add_handler(CommandHandler("start", start, filters=filters.ChatType.PRIVATE))
    app.add_handler(CommandHandler("restart", restart_command_handler, filters=filters.ChatType.PRIVATE))
    app.add_handler(CallbackQueryHandler(cancel_search_callback, pattern=r"^cancel_search_\d+$"))
    app.add_handler(CallbackQueryHandler(confirm_cancel_callback, pattern=r"^confirm_cancel_\d+$"))
    app.add_handler(CallbackQueryHandler(deny_cancel_callback, pattern=r"^deny_cancel_\d+$"))
    app.add_handler(CallbackQueryHandler(admin_take_callback, pattern=r"^take_user_\d+$"))
    app.add_handler(CallbackQueryHandler(warn_user_callback, pattern=r"^warn_user_\d+$"))
    app.add_handler(CallbackQueryHandler(cancel_warn_callback, pattern=r"^cancel_warn_\d+$"))
    app.add_handler(CallbackQueryHandler(confirm_warn_callback, pattern=r"^confirm_warn_\d+$"))
    app.add_handler(CallbackQueryHandler(show_admin_bio_callback, pattern=r"^show_admin_bio_\d+$"))
    app.add_handler(CallbackQueryHandler(show_user_profile_callback, pattern=r"^show_user_profile_\d+$"))
    app.add_handler(CallbackQueryHandler(admin_decline_request_callback, pattern=r"^decline_request_\d+$"))
    app.add_handler(CallbackQueryHandler(admin_decline_callback, pattern=r"^decline_user_\d+$"))
    app.add_handler(CallbackQueryHandler(cancel_decline_request_callback, pattern=r"^cancel_decline_request_\d+$"))
    app.add_handler(CallbackQueryHandler(approve_decline_callback, pattern=r"^approve_decline_\d+$"))
    app.add_handler(CallbackQueryHandler(reject_decline_callback, pattern=r"^reject_decline_\d+$"))
    app.add_handler(CallbackQueryHandler(warn_user_cancel_callback, pattern=r"^warn_user_cancel_\d+$"))
    app.add_handler(CallbackQueryHandler(approve_candidate_callback, pattern=r"^approve_candidate_\d+$"))
    app.add_handler(CallbackQueryHandler(reject_candidate_callback, pattern=r"^reject_candidate_\d+$"))
    app.add_handler(CallbackQueryHandler(candidate_tip_toggle_callback, pattern=r"^candidate_tip_toggle_\d+_(chat|support|flirt)$"))
    app.add_handler(CallbackQueryHandler(candidate_tip_next_callback, pattern=r"^candidate_tip_next_\d+$"))
    app.add_handler(CallbackQueryHandler(candidate_gender_callback, pattern=r"^candidate_gender_\d+_(male|female)$"))
    app.add_handler(CallbackQueryHandler(cancel_candidate_reject_callback, pattern=r"^cancel_candidate_reject_\d+$"))
    app.add_handler(CallbackQueryHandler(suspicious_admin_allow_callback, pattern=r"^susp_admin_allow_\d+$"))
    app.add_handler(CallbackQueryHandler(suspicious_admin_deny_callback, pattern=r"^susp_admin_deny_\d+$"))
    app.add_handler(CallbackQueryHandler(suspicious_user_allow_callback, pattern=r"^susp_user_allow_\d+$"))
    app.add_handler(CallbackQueryHandler(suspicious_user_deny_callback, pattern=r"^susp_user_deny_\d+$"))
    app.add_handler(CallbackQueryHandler(suspicious_user_warn_callback, pattern=r"^susp_user_warn_\d+$"))
    app.add_handler(CallbackQueryHandler(pause_session_confirm_callback, pattern=r"^pause_confirm_\d+$"))
    app.add_handler(CallbackQueryHandler(pause_session_cancel_callback, pattern=r"^pause_cancel_\d+$"))
    app.add_handler(CallbackQueryHandler(resume_session_callback, pattern=r"^resume_session_\d+$"))
    app.add_handler(CallbackQueryHandler(setprefix_select_callback, pattern=r"^prefix_select_\d+_[a-z]+$"))
    app.add_handler(CallbackQueryHandler(setprefix_apply_callback, pattern=r"^prefix_apply_\d+$"))
    app.add_handler(CallbackQueryHandler(astats_tag_change_callback, pattern=r"^astats_tag_change_\d+$"))
    app.add_handler(CallbackQueryHandler(astats_tag_cancel_callback, pattern=r"^astats_tag_cancel_\d+$"))
    app.add_handler(CallbackQueryHandler(astats_bio_change_callback, pattern=r"^astats_bio_change_\d+$"))
    app.add_handler(CallbackQueryHandler(astats_bio_cancel_callback, pattern=r"^astats_bio_cancel_\d+$"))
    app.add_handler(CallbackQueryHandler(astats_bio_view_callback, pattern=r"^astats_bio_view_\d+$"))
    app.add_handler(CallbackQueryHandler(astats_active_pz_callback, pattern=r"^astats_active_pz_\d+$"))
    app.add_handler(CallbackQueryHandler(astats_tip_menu_callback, pattern=r"^astats_tip_menu_\d+$"))
    app.add_handler(CallbackQueryHandler(astats_tip_toggle_callback, pattern=r"^astats_tip_toggle_\d+_(chat|support|flirt)$"))
    app.add_handler(CallbackQueryHandler(astats_tip_apply_callback, pattern=r"^astats_tip_apply_\d+$"))
    app.add_handler(CallbackQueryHandler(astats_gender_menu_callback, pattern=r"^astats_gender_menu_\d+$"))
    app.add_handler(CallbackQueryHandler(astats_gender_set_callback, pattern=r"^astats_gender_set_\d+_(male|female)$"))
    app.add_handler(CallbackQueryHandler(astats_gender_back_callback, pattern=r"^astats_gender_back_\d+$"))
    app.add_handler(CallbackQueryHandler(info_topic_close_callback, pattern=r"^info_topic_close_\d+$"))
    app.add_handler(CallbackQueryHandler(info_topic_stop_callback, pattern=r"^info_topic_stop_\d+$"))
    app.add_handler(CallbackQueryHandler(info_topic_cancel_callback, pattern=r"^info_topic_cancel_\d+$"))
    app.add_handler(CallbackQueryHandler(info_topic_close_confirm_callback, pattern=r"^info_topic_close_confirm_\d+$"))
    app.add_handler(CallbackQueryHandler(info_topic_stop_confirm_callback, pattern=r"^info_topic_stop_confirm_\d+$"))
    app.add_handler(CallbackQueryHandler(info_topic_resume_callback, pattern=r"^info_topic_resume_\d+$"))
    app.add_handler(CallbackQueryHandler(change_nickname_callback, pattern=r"^change_nickname_\d+$"))
    app.add_handler(CallbackQueryHandler(cancel_nickname_change_callback, pattern=r"^cancel_nickname_change_\d+$"))
    app.add_handler(CallbackQueryHandler(admin_cancel_confirm_callback, pattern=r"^confirm_admin_cancel_\d+$"))
    app.add_handler(CallbackQueryHandler(admin_cancel_deny_callback, pattern=r"^deny_admin_cancel_\d+$"))
    app.add_handler(CallbackQueryHandler(bug_report_cancel_callback, pattern=r"^bug_report_cancel_\d+$"))
    app.add_handler(CallbackQueryHandler(bug_report_confirm_callback, pattern=r"^bug_report_confirm_\d+$"))
    app.add_handler(CallbackQueryHandler(bug_report_reject_callback, pattern=r"^bug_report_reject_\d+$"))
    app.add_handler(MessageHandler(filters.Regex("^👤 Найти админа$") & filters.ChatType.PRIVATE, find_admin_menu_callback))
    app.add_handler(MessageHandler(filters.Regex("^⚙️Настройки$") & filters.ChatType.PRIVATE, settings_menu_handler))
    app.add_handler(MessageHandler(filters.Regex("^💬Отправить жалобу$") & filters.ChatType.PRIVATE, settings_complaint_menu_handler))
    app.add_handler(MessageHandler(filters.Regex("^👨‍🔧Сообщить о баге$") & filters.ChatType.PRIVATE, complaint_bug_menu_handler))
    app.add_handler(MessageHandler(filters.Regex("^👮‍♀️Пожаловаться на админа$") & filters.ChatType.PRIVATE, complaint_admin_menu_handler))
    app.add_handler(MessageHandler(filters.Regex("^↩️В настройки$") & filters.ChatType.PRIVATE, settings_menu_handler))
    app.add_handler(MessageHandler(filters.Regex("^🔕Отключить рекламу$") & filters.ChatType.PRIVATE, settings_disable_ad_handler))
    app.add_handler(MessageHandler(filters.Regex("^🔔Включить рекламу$") & filters.ChatType.PRIVATE, settings_enable_ad_handler))
    app.add_handler(MessageHandler(filters.Regex("^↩️Назад$") & filters.ChatType.PRIVATE, settings_back_handler))
    app.add_handler(MessageHandler(filters.Regex("^👤 Профиль$"), send_profile))
    app.add_handler(MessageHandler(filters.Regex("^🔰Админ-профиль$") & filters.ChatType.PRIVATE, send_admin_profile))
    app.add_handler(MessageHandler(filters.Regex("^🤧Отказаться от админа$") & filters.ChatType.PRIVATE, ask_cancel_admin))
    app.add_handler(MessageHandler(filters.Regex("^💤Приостановить общение$") & filters.ChatType.PRIVATE, pause_session_request))
    app.add_handler(MessageHandler(filters.Regex("^👨 Мальчик$|^👩 Девочка$|^◀️ Назад$") & filters.ChatType.PRIVATE, choose_admin_gender_callback))
    app.add_handler(MessageHandler(filters.Regex("^🗣️ Общение$|^❤️ Поддержка$|^🔥 Флирт$|^◀️ Назад$") & filters.ChatType.PRIVATE, choose_mood_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE, bug_report_text_input_handler), group=-2)
    # Moderation input handlers must run before generic forwarding.
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & (filters.ChatType.GROUP | filters.ChatType.SUPERGROUP), handle_astats_bio_input_message), group=-4)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & (filters.ChatType.GROUP | filters.ChatType.SUPERGROUP), handle_astats_tag_input_message), group=-3)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & (filters.ChatType.GROUP | filters.ChatType.SUPERGROUP), candidate_reject_reason_message_handler), group=0)
    app.add_handler(MessageHandler(filters.ALL & (filters.ChatType.GROUP | filters.ChatType.SUPERGROUP), handle_decline_input_message), group=0)
    app.add_handler(MessageHandler(filters.ALL & (filters.ChatType.GROUP | filters.ChatType.SUPERGROUP), handle_warn_reason_message), group=0)
    app.add_handler(MessageHandler(filters.ALL & (filters.ChatType.GROUP | filters.ChatType.SUPERGROUP), admin_group_message_handler), group=1)
    app.add_handler(MessageHandler(
        filters.TEXT & filters.Regex(r"^/кусь(?:@[\w_]+)?(?:\s+.*)?$") & (filters.ChatType.PRIVATE | filters.Chat(WORK_CHAT_ID)),
        kus_command_handler,
    ), group=-1)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.REPLY & filters.ChatType.PRIVATE, user_private_message_handler))
    # Handler for admin replies to the bot's "Введите причину отклонения запроса" prompt
    app.add_handler(MessageHandler(filters.TEXT & filters.REPLY & (filters.ChatType.GROUP | filters.ChatType.SUPERGROUP), handle_decline_reason_reply))
    app.add_handler(CommandHandler("dump_maps", dump_maps_handler))
    app.run_polling(close_loop=False)

if __name__ == "__main__":
    main()

