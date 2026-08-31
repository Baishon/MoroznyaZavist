"""User-facing command/callback/message handlers (private chat with the bot).

Covers: /start and the main/settings menus, bug reports, profile display and
nickname changes, and mood/"find an admin" requests that open a work-chat
forum topic for the user to talk to staff through. Actual message relay
between a user and their assigned admin lives in `app.services.messaging`.
Admin-candidate approval UI lives in `app.handlers.candidates` instead,
shared with `admin.py`.
"""
import asyncio
import html
import logging
import time
from datetime import datetime, timedelta, timezone

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    Update,
)
from telegram.constants import ChatType, ParseMode
from telegram.error import BadRequest
from telegram.ext import ApplicationHandlerStop, ContextTypes

from app.config import ADMIN_LEVEL_TITLES, LOG_CHAT_ID, TRUSTED_ADMIN_CHAT_ID, WORK_CHAT_ID
from app.database.requests import _save_profile_record, _save_runtime_snapshot
from app.handlers.candidates import _send_candidate_stage_1, admin_candidate_private_flow
from app.keyboards.inline import (
    _build_active_session_keyboard,
    _build_candidate_gender_keyboard,
    _build_candidate_tip_keyboard,
    _build_complaint_admin_menu_keyboard,
    _build_complaint_menu_keyboard,
    _build_main_menu_keyboard,
    _build_session_settings_keyboard,
    _build_settings_menu_keyboard,
)
from app.services.bans import block_if_banned, check_active_chat_block, enforce_autoban_if_needed
from app.services.messaging import deliver_message_to_user
from app.services.profiles import (
    _candidate_block_text,
    _ensure_profile,
    _find_admin_profile_by_tag_admin,
    _has_admin_rights_level_1_5,
    _is_active_admin_candidate,
    _is_user_nickname_taken,
    _set_last_admin_tag_for_user,
    consume_expired_warning_count,
    refresh_timed_warnings,
)
from app.services.topics import (
    _build_paused_topic_name,
    _has_suspicious_username,
    _is_rp_action_text,
    _next_suspicious_review_id,
    _parse_rp_trigger_text,
    _render_rp_action_message,
    _rp_display_name_admin,
    _rp_display_name_user,
    _topic_url,
)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user:
        if _is_active_admin_candidate(context, str(user.id)):
            await update.message.reply_text(_candidate_block_text())
            return
        if await block_if_banned(update, context):
            return
        profile = _ensure_profile(context, str(user.id), user.username or f"id{user.id}")
        refresh_timed_warnings(profile)
        expired_warns = consume_expired_warning_count(profile)
        if expired_warns:
            _save_profile_record(context, str(user.id))
            await update.message.reply_text(
                f"✅Срок действия предупреждени{'я' if expired_warns == 1 else 'й'} истёк. "
                f"Истёкших предупреждений: {expired_warns}. "
                "Теперь учитываются только действующие предупреждения."
            )

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
                InlineKeyboardButton("⭐ Канал бота", url="https://t.me/berlogaAskly"),
                InlineKeyboardButton("❓ Помощь", url="https://t.me/berlogaAskly/58")
            ]
        ]
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)

    menu_keyboard = _build_main_menu_keyboard(context, user.id, user.username)
    await update.message.reply_text(
        "Выберите действие в меню ниже:",
        reply_markup=menu_keyboard,
    )


# ==== Agreement enforcement helpers ====
AGREEMENT_VERSION = 1  # bump this to force all users to re-accept

AGREEMENT_TEXT = (
    "╭─────────────── ✦ ───────────────╮\n"
    "          🤍 ДОБРО ПОЖАЛОВАТЬ\n"
    "╰─────────────── ✦ ───────────────╯\n\n"
    "Прежде чем продолжить знакомство с ботом, пожалуйста, ознакомьтесь с **Пользовательским соглашением** и **Политикой обработки персональных данных**. 📖\n\n"
    "Здесь собраны основные правила использования сервиса, права и обязанности сторон, а также информация об ответственности.\n\n"
    "𓂃 ˖ ☁️ Чтобы продолжить, внимательно ознакомьтесь с документами и подтвердите своё согласие, нажав кнопку **«Принять»**.\n\n"
    "⚠️ Если вы не принимаете условия, к функционалу бота получить доступ не получится.\n\n"
    "Нажимая **«Принять»**, вы подтверждаете, что ознакомились с текстом Соглашения и Политики обработки персональных данных в полном объёме и принимаете их условия."
)


def _format_time_kyiv(timestamp: float) -> str:
    """Convert Unix timestamp to Kyiv timezone (UTC+3) formatted string."""
    kyiv_tz = timezone(timedelta(hours=3))
    dt = datetime.fromtimestamp(timestamp, tz=kyiv_tz)
    return dt.strftime("%d.%m.%Y %H:%M")


def _agreement_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("✅ Принять", callback_data="agreement_accept")]])


async def _send_agreement_message_for_user(chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    # Sends the agreement text as a private message (tries markdown then plain)
    try:
        await context.bot.send_message(chat_id=chat_id, text=AGREEMENT_TEXT, parse_mode=ParseMode.MARKDOWN, reply_markup=_agreement_markup())
    except Exception:
        try:
            # fallback without markdown
            await context.bot.send_message(chat_id=chat_id, text=AGREEMENT_TEXT, reply_markup=_agreement_markup())
        except Exception:
            pass


async def agreement_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Intercepts any message (all chat types) when user hasn't accepted the current agreement version
    if not update.message or not update.effective_user:
        return

    # Ignore messages coming from bots (including our own) or from anonymous/service sources
    from_user = update.message.from_user
    if not from_user:
        return
    if getattr(from_user, "is_bot", False):
        # do not prompt the bot or other bots
        return

    # Skip Telegram "service" messages (new_chat_members, left_chat_member, pinned_message, etc.)
    _service_attrs = [
        "new_chat_members",
        "left_chat_member",
        "new_chat_title",
        "new_chat_photo",
        "delete_chat_photo",
        "group_chat_created",
        "supergroup_chat_created",
        "channel_chat_created",
        "pinned_message",
        "migrate_to_chat_id",
        "migrate_from_chat_id",
        "voice_chat_scheduled",
        "voice_chat_started",
        "voice_chat_ended",
        "poll",
    ]
    for _attr in _service_attrs:
        if hasattr(update.message, _attr) and getattr(update.message, _attr):
            return

    user_id = str(update.effective_user.id)
    profile = _ensure_profile(context, user_id, update.effective_user.username or f"id{user_id}")
    agreed = int(profile.get("agreed_terms", 0) or 0)
    agreed_version = int(profile.get("agreed_terms_version", 0) or 0)
    if agreed == 1 and agreed_version == AGREEMENT_VERSION:
        return

    # Keep the trusted admin chat quiet for users who have not accepted the terms.
    if update.effective_chat and update.effective_chat.id == TRUSTED_ADMIN_CHAT_ID:
        raise ApplicationHandlerStop

    # Send agreement as a private message
    await _send_agreement_message_for_user(int(user_id), context)

    # If the trigger happened in a non-private chat, notify user briefly there
    try:
        if update.effective_chat and update.effective_chat.type != ChatType.PRIVATE:
            await update.message.reply_text(
                "⚠️ Для продолжения пользования ботом требуется принять Пользовательское соглашение. "
                "Проверьте личные сообщения и нажмите «Принять»."
            )
    except Exception:
        pass

    # Stop further processing of this message
    raise ApplicationHandlerStop


async def agreement_callback_guard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Guard for any callback query: if user hasn't accepted current agreement, present agreement
    query = update.callback_query
    if not query or not update.effective_user:
        return

    # Ignore interactions from bots
    from_user = query.from_user
    if not from_user:
        return
    if getattr(from_user, "is_bot", False):
        return

    data = (query.data or "").strip()
    # Let the accept callback be handled by its specific handler
    if data == "agreement_accept":
        return

    user_id = str(update.effective_user.id)
    profile = _ensure_profile(context, user_id, update.effective_user.username or f"id{user_id}")
    agreed = int(profile.get("agreed_terms", 0) or 0)
    agreed_version = int(profile.get("agreed_terms_version", 0) or 0)
    if agreed == 1 and agreed_version == AGREEMENT_VERSION:
        return

    if query.message and query.message.chat.id == TRUSTED_ADMIN_CHAT_ID:
        raise ApplicationHandlerStop

    # Notify user and send PM with agreement
    try:
        await query.answer("Примите соглашение в личных сообщениях", show_alert=True)
    except Exception:
        pass
    await _send_agreement_message_for_user(int(user_id), context)
    raise ApplicationHandlerStop


async def agreement_accept_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # User pressed Accept — set flag and send /start welcome messages
    query = update.callback_query
    if not query or not update.effective_user:
        return
    user_id = str(update.effective_user.id)
    profile = _ensure_profile(context, user_id, update.effective_user.username or f"id{user_id}")
    profile["agreed_terms"] = 1
    profile["agreed_terms_version"] = int(AGREEMENT_VERSION)
    try:
        _save_profile_record(context, user_id)
    except Exception:
        pass

    # delete the agreement message if possible
    try:
        if query.message:
            await query.message.delete()
    except Exception:
        pass

    # send the same initial /start content
    try:
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
                    InlineKeyboardButton("⭐ Канал бота", url="https://t.me/berlogaAskly"),
                    InlineKeyboardButton("❓ Помощь", url="https://t.me/berlogaAskly/58")
                ]
            ]
        )
        await context.bot.send_message(chat_id=int(user_id), text=text, parse_mode=ParseMode.HTML, reply_markup=keyboard)

        menu_keyboard = _build_main_menu_keyboard(context, int(user_id), update.effective_user.username)
        await context.bot.send_message(chat_id=int(user_id), text="Выберите действие в меню ниже:", reply_markup=menu_keyboard)
    except Exception:
        pass

    try:
        await query.answer("Спасибо! Вы приняли условия.")
    except Exception:
        pass

    raise ApplicationHandlerStop


async def admin_search_command_guard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Prevent private commands while the user is waiting for an administrator.

    Some system commands must remain available even while the search request is active
    (for example /admins, /restart, /start), otherwise bot navigation becomes blocked by
    stale search state.
    """
    if not update.message or not update.effective_user:
        return
    if not update.effective_chat or update.effective_chat.type != ChatType.PRIVATE:
        return

    raw_text = update.message.text or ""
    if raw_text:
        candidate = raw_text.split()[0].lower().split("@", 1)[0]
        if candidate in {"/admins", "/restart", "/start"}:
            return

    user_id = str(update.effective_user.id)
    active = (context.application.bot_data.get("active_chats", {}) or {}).get(user_id)
    if active and active.get("active"):
        return

    request = (context.application.bot_data.get("admin_requests", {}) or {}).get(user_id)
    if not request:
        return

    await update.message.reply_text(
        "⏳ Вы сейчас ожидаете администратора.\n\n"
        "Команды временно недоступны. Сначала отмените поиск администратора "
        "кнопкой «Отменить» в сообщении запроса."
    )
    raise ApplicationHandlerStop


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


async def send_active_session_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or update.effective_chat.type != ChatType.PRIVATE:
        return
    if await block_if_banned(update, context):
        return
    active = (context.application.bot_data.get("active_chats", {}) or {}).get(str(update.effective_user.id)) if update.effective_user else None
    if not active or not active.get("active"):
        await update.message.reply_text("У вас нет активной сессии с администратором.")
        await send_main_submenu(update, context)
        return
    await update.message.reply_text("Кнопки диалога:", reply_markup=_build_active_session_keyboard())


async def session_settings_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or update.effective_chat.type != ChatType.PRIVATE:
        return
    if await block_if_banned(update, context):
        return
    user_id = str(update.effective_user.id)
    active = (context.application.bot_data.get("active_chats", {}) or {}).get(user_id)
    if not active or not active.get("active"):
        await update.message.reply_text("У вас нет активной сессии с администратором.")
        return

    status_text = "💔RP-команды отключены в этой сессии." if active.get("rp_disabled") else "💞RP-команды включены в этой сессии."
    await update.message.reply_text(
        f"⚙️Настройки сессии\n\n{status_text}",
        reply_markup=_build_session_settings_keyboard(active),
    )


async def session_rp_disable_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or update.effective_chat.type != ChatType.PRIVATE:
        return
    if await block_if_banned(update, context):
        return
    user_id = str(update.effective_user.id)
    active = (context.application.bot_data.get("active_chats", {}) or {}).get(user_id)
    if not active or not active.get("active"):
        await update.message.reply_text("У вас нет активной сессии с администратором.")
        return
    if active.get("rp_disabled"):
        await update.message.reply_text(
            "💞RP-команды уже отключены в этой сессии.",
            reply_markup=_build_session_settings_keyboard(active),
        )
        return

    cooldown_until = float(active.get("rp_disable_cooldown_until", 0) or 0)
    if time.time() < cooldown_until:
        remaining = max(0, int(cooldown_until - time.time()))
        minutes, seconds = divmod(remaining, 60)
        remaining_text = f"{minutes} мин. {seconds} сек." if minutes else f"{seconds} сек."
        await update.message.reply_text(f"⏳Повторно отключить RP можно через {remaining_text}.")
        return

    user_id_int = int(user_id)
    confirm_kb = InlineKeyboardMarkup(
        [[
            InlineKeyboardButton("✅ Подтвердить", callback_data=f"session_rp_disable_confirm_{user_id_int}"),
            InlineKeyboardButton("❌ Отмена", callback_data=f"session_rp_disable_cancel_{user_id_int}"),
        ]]
    )
    await update.message.reply_text(
        "⚠️Вы уверены, что хотите отключить RP-команды в этой сессии?\n\nПосле отключения любые RP-команды будут недоступны и не будут отправляться администратору.",
        reply_markup=confirm_kb,
    )


async def session_rp_enable_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or update.effective_chat.type != ChatType.PRIVATE:
        return
    if await block_if_banned(update, context):
        return
    user_id = str(update.effective_user.id)
    active = (context.application.bot_data.get("active_chats", {}) or {}).get(user_id)
    if not active or not active.get("active"):
        await update.message.reply_text("У вас нет активной сессии с администратором.")
        return

    active["rp_disabled"] = False
    active["rp_disable_cooldown_until"] = time.time() + 30 * 60
    await update.message.reply_text(
        "✅RP-команды снова включены в этой сессии.",
        reply_markup=_build_session_settings_keyboard(active),
    )


async def session_rp_disable_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    data = update.callback_query.data or ""
    parts = data.split("_")
    if len(parts) < 5:
        return
    user_id = int(parts[4])
    if update.effective_user.id != user_id:
        await update.callback_query.answer("Только владелец сессии может это сделать", show_alert=True)
        return

    active = (context.application.bot_data.get("active_chats", {}) or {}).get(str(user_id))
    if not active or not active.get("active"):
        await update.callback_query.answer("Активная сессия не найдена", show_alert=True)
        return
    if active.get("rp_disabled"):
        await update.callback_query.answer("RP-команды уже отключены", show_alert=True)
        return

    active["rp_disabled"] = True
    active["rp_disable_cooldown_until"] = time.time() + 30 * 60

    chat_id = active.get("chat_id")
    topic_id = active.get("topic_id")
    notify_text = "💔RP-команды отключены в данной сессии. Чтобы включить их снова, откройте раздел настроек сессии."
    user_notice = "💔RP-команды отключены в этой сессии. Чтобы включить их снова, откройте раздел настроек сессии."

    try:
        await update.callback_query.message.delete()
    except Exception:
        pass

    try:
        await context.bot.send_message(chat_id=int(user_id), text=user_notice)
    except Exception:
        pass

    try:
        if chat_id is not None:
            if topic_id is not None:
                await context.bot.send_message(
                    chat_id=int(chat_id),
                    message_thread_id=topic_id,
                    text=f"💔RP-команды были отключены пользователем в этой сессии.",
                )
            else:
                await context.bot.send_message(chat_id=int(chat_id), text=notify_text)
    except Exception:
        pass

    admin_id = active.get("admin_id")
    if admin_id:
        try:
            await context.bot.send_message(chat_id=int(admin_id), text="💔Пользователь отключил RP-команды в этой сессии.")
        except Exception:
            pass


async def session_rp_disable_cancel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    data = update.callback_query.data or ""
    parts = data.split("_")
    if len(parts) < 5:
        return
    user_id = int(parts[4])
    if update.effective_user.id != user_id:
        await update.callback_query.answer("Только владелец сессии может это сделать", show_alert=True)
        return

    try:
        await update.callback_query.message.delete()
    except Exception:
        pass


async def check_session_admin_online_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or update.effective_chat.type != ChatType.PRIVATE:
        return
    if await block_if_banned(update, context):
        return

    user_id = str(update.effective_user.id)
    active = (context.application.bot_data.get("active_chats", {}) or {}).get(user_id)
    if not active or not active.get("active"):
        await update.message.reply_text("У вас нет активной сессии с администратором.")
        return

    admin_id = str(active.get("admin_id") or "")
    last_activity = float(
        (context.application.bot_data.get("admin_work_chat_activity", {}) or {}).get(admin_id, 0) or 0
    )
    if not last_activity:
        await update.message.reply_text("🔴Администратор не в сети. Данных о его активности пока нет.")
        return

    elapsed = max(0, int(time.time() - last_activity))
    last_activity_text = _format_time_kyiv(last_activity)
    status = "🟢Онлайн" if elapsed <= 5 * 60 else "🔴Не в сети"
    await update.message.reply_text(
        f"👁‍🗨Был(-а) в сети: {status}\nПоследнее сообщение: {last_activity_text}"
    )


async def return_to_main_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or update.effective_chat.type != ChatType.PRIVATE:
        return
    if await block_if_banned(update, context):
        return
    await send_main_submenu(update, context)


async def return_to_dialog_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_active_session_menu(update, context)


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



def _complaint_cooldown_duration(profile: dict) -> int:
    warn_count = refresh_timed_warnings(profile)
    return 30 * 60 + (3 * 60 * 60 if warn_count > 0 else 0)


def _format_cooldown_left(seconds_left: int) -> str:
    total = max(0, seconds_left)
    hours, remainder = divmod(total, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours} ч. {minutes} мин."
    if minutes:
        return f"{minutes} мин. {seconds} сек."
    return f"{seconds} сек."


def _check_admin_search_cooldown(context: ContextTypes.DEFAULT_TYPE, user_id: str, profile: dict):
    now = time.time()
    cooldown_until = float(profile.get("admin_search_cooldown_until", 0) or 0)
    if cooldown_until <= now:
        return False, ""

    remaining = max(0, int(cooldown_until - now))
    return True, f"⏳ Вы отменили поиск администратора недавно. Повторный поиск будет доступен через {_format_cooldown_left(remaining)}."


def _activate_admin_search_cooldown(context: ContextTypes.DEFAULT_TYPE, user_id: str, profile: dict) -> None:
    profile["admin_search_cooldown_until"] = time.time() + (15 * 60)
    _save_profile_record(context, user_id)


def _check_complaint_cooldown(context: ContextTypes.DEFAULT_TYPE, user_id: str, profile: dict, cooldown_key: str, label: str):
    now = time.time()
    cooldown_until = float(profile.get(cooldown_key, 0) or 0)
    if cooldown_until <= now:
        return False, ""

    warn_count = refresh_timed_warnings(profile)
    if warn_count > 0:
        profile[cooldown_key] = cooldown_until + (3 * 60 * 60)
        _save_profile_record(context, user_id)
    remaining = max(0, int(float(profile.get(cooldown_key, now)) - now))
    waiting_text = _format_cooldown_left(remaining)
    return True, f"⏳Вы сможете использовать «{label}» снова через {waiting_text}."


def _activate_complaint_cooldown(context: ContextTypes.DEFAULT_TYPE, user_id: str, profile: dict, cooldown_key: str) -> None:
    now = time.time()
    duration = _complaint_cooldown_duration(profile)
    profile[cooldown_key] = now + duration
    _save_profile_record(context, user_id)


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
    profile = _ensure_profile(context, user_id, update.effective_user.username or f"id{user_id}")
    blocked, message_text = _check_complaint_cooldown(context, user_id, profile, "bug_report_cooldown_until", "Сообщить о баге")
    if blocked:
        await update.message.reply_text(message_text)
        return

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

    user_id = str(update.effective_user.id)
    profile = _ensure_profile(context, user_id, update.effective_user.username or f"id{user_id}")
    blocked, message_text = _check_complaint_cooldown(context, user_id, profile, "admin_complaint_cooldown_until", "Пожаловаться на админа")
    if blocked:
        await update.message.reply_text(message_text)
        return

    pending = context.application.bot_data.setdefault("pending_admin_complaints", {})
    pending.pop(user_id, None)

    await update.message.reply_text(
        "👮‍♀️Раздел 'Пожаловаться на админа'\n\nВыберите способ подачи жалобы.",
        reply_markup=_build_complaint_admin_menu_keyboard(),
    )



async def complaint_admin_back_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or update.effective_chat.type != ChatType.PRIVATE:
        return
    if await block_if_banned(update, context):
        return

    user_id = str(update.effective_user.id)
    pending = context.application.bot_data.setdefault("pending_admin_complaints", {})
    pending.pop(user_id, None)

    await update.message.reply_text("💬Раздел жалоб", reply_markup=_build_complaint_menu_keyboard())



async def complaint_admin_write_tag_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or update.effective_chat.type != ChatType.PRIVATE:
        return
    if await block_if_banned(update, context):
        return

    user_id = str(update.effective_user.id)
    profile = _ensure_profile(context, user_id, update.effective_user.username or f"id{user_id}")
    blocked, message_text = _check_complaint_cooldown(context, user_id, profile, "admin_complaint_cooldown_until", "Пожаловаться на админа")
    if blocked:
        await update.message.reply_text(message_text)
        return

    pending = context.application.bot_data.setdefault("pending_admin_complaints", {})
    pending[user_id] = {"state": "await_tag"}

    await update.message.reply_text(
        "🏷Напишите тег администратора, на которого хотите пожаловаться.\n"
        "Тег должен начинаться с # в начале сообщения, например: #акира",
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("отмена", callback_data=f"admin_complaint_cancel_{user_id}")]]
        ),
    )



async def complaint_admin_last_admin_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or update.effective_chat.type != ChatType.PRIVATE:
        return
    if await block_if_banned(update, context):
        return

    user_id = str(update.effective_user.id)
    profile = _ensure_profile(context, user_id, update.effective_user.username or f"id{user_id}")
    blocked, message_text = _check_complaint_cooldown(context, user_id, profile, "admin_complaint_cooldown_until", "Пожаловаться на админа")
    if blocked:
        await update.message.reply_text(message_text)
        return

    last_tag = str(profile.get("last_admin_tag") or "").strip()
    if not last_tag or last_tag == "не указан":
        await update.message.reply_text("ℹ️У вас пока нет последнего администратора, на которого можно пожаловаться.")
        return

    pending = context.application.bot_data.setdefault("pending_admin_complaints", {})
    pending[user_id] = {
        "state": "await_complaint",
        "admin_tag": last_tag,
        "via_last_admin": True,
    }

    await update.message.reply_text(
        f"📝Опишите, в чем администратор {last_tag} не прав и почему он должен быть наказан. Отправьте это следующим сообщением.",
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("отмена", callback_data=f"admin_complaint_cancel_{user_id}")]]
        ),
    )



async def admin_complaint_cancel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer("Отмена")
    user_id = (update.callback_query.data or "").split("_")[-1]
    if str(update.effective_user.id) != str(user_id):
        await update.callback_query.answer("Кнопка доступна только владельцу запроса", show_alert=True)
        return

    pending = context.application.bot_data.setdefault("pending_admin_complaints", {})
    pending.pop(str(user_id), None)

    try:
        await update.callback_query.message.reply_text(
            "Отменено. Возврат в раздел 'Пожаловаться на админа'.",
            reply_markup=_build_complaint_admin_menu_keyboard(),
        )
    except Exception:
        pass



async def admin_complaint_text_input_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or update.effective_chat.type != ChatType.PRIVATE or not update.effective_user:
        return
    if update.effective_user.is_bot:
        return

    pending = context.application.bot_data.setdefault("pending_admin_complaints", {})
    user_id = str(update.effective_user.id)
    entry = pending.get(user_id)
    if not entry:
        return

    state = str(entry.get("state") or "")

    if state == "await_tag":
        text = (update.message.text or "").strip()
        if not text or not text.startswith("#"):
            await update.message.reply_text("⚠️Тег написан неправильно. В начале сообщения должен быть #. Пример: #акира")
            raise ApplicationHandlerStop

        target_user_id, target_profile = _find_admin_profile_by_tag_admin(context, text)
        if not target_profile:
            await update.message.reply_text("⚠️Администратор с таким тегом не найден. Проверьте тег и попробуйте снова.")
            raise ApplicationHandlerStop

        entry["state"] = "await_complaint"
        entry["admin_tag"] = text
        entry["admin_user_id"] = target_user_id
        entry["via_last_admin"] = False

        await update.message.reply_text(
            f"📝Опишите, в чем администратор {text} не прав и почему он должен быть наказан. Отправьте это следующим сообщением.",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("отмена", callback_data=f"admin_complaint_cancel_{user_id}")]]
            ),
        )
        raise ApplicationHandlerStop

    if state == "await_complaint":
        complaint_text = (update.message.text or "").strip()
        if not complaint_text:
            await update.message.reply_text("Текст жалобы не должен быть пустым.")
            raise ApplicationHandlerStop

        entry["draft_text"] = complaint_text
        entry["state"] = "await_proof"
        await update.message.reply_text(
            "📎Пришлите изображение-доказательство (скрин/фото) или нажмите «Далее», если доказательство не нужно.\n\n"
            "После этого жалоба будет отправлена руководству.",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("Далее", callback_data=f"admin_complaint_confirm_{user_id}")], [InlineKeyboardButton("отмена", callback_data=f"admin_complaint_cancel_{user_id}")]]
            ),
        )
        raise ApplicationHandlerStop

    return


async def admin_complaint_photo_input_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or update.effective_chat.type != ChatType.PRIVATE or not update.effective_user:
        return
    if update.effective_user.is_bot:
        return

    pending = context.application.bot_data.setdefault("pending_admin_complaints", {})
    user_id = str(update.effective_user.id)
    entry = pending.get(user_id)
    if not entry:
        return
    if str(entry.get("state")) not in {"await_proof", "await_confirm"}:
        return

    photo = update.message.photo[-1] if update.message.photo else None
    if photo is None:
        return

    entry["photo_file_id"] = photo.file_id
    entry["state"] = "await_confirm"
    await update.message.reply_text(
        "✅Изображение-доказательство добавлено. Нажмите «Далее», чтобы отправить жалобу.",
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("Далее", callback_data=f"admin_complaint_confirm_{user_id}")], [InlineKeyboardButton("отмена", callback_data=f"admin_complaint_cancel_{user_id}")]]
        ),
    )
    raise ApplicationHandlerStop


async def admin_complaint_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    user_id = (update.callback_query.data or "").split("_")[-1]
    if str(update.effective_user.id) != str(user_id):
        await update.callback_query.answer("Кнопка доступна только владельцу запроса", show_alert=True)
        return

    pending = context.application.bot_data.setdefault("pending_admin_complaints", {})
    entry = pending.get(str(user_id))
    if not entry or str(entry.get("state")) not in {"await_proof", "await_confirm"}:
        await update.callback_query.answer("Заявка устарела", show_alert=True)
        return

    profile = _ensure_profile(context, str(user_id), update.effective_user.username or f"id{user_id}")
    id_profile = int(profile.get("id_profile", 0) or 0)
    admin_tag = str(entry.get("admin_tag") or "не указан")
    via_last_admin = bool(entry.get("via_last_admin", False))
    complaint_text = str(entry.get("draft_text") or "").strip()
    photo_file_id = entry.get("photo_file_id")

    if via_last_admin:
        notification_text = (
            f"👮‍♀️Поступила жалоба на администратора {admin_tag} \n\n"
            f"От пользователя #{id_profile} (Являлась ПЗ этого админа)\n"
            f"Суть жалобы: {complaint_text}"
        )
    else:
        notification_text = (
            f"👮‍♀️Поступила жалоба на администратора {admin_tag}\n\n"
            f"От пользователя #{id_profile}\n"
            f"Суть жалобы: {complaint_text}"
        )

    try:
        if photo_file_id:
            await context.bot.send_photo(chat_id=LOG_CHAT_ID, photo=photo_file_id, caption=notification_text)
        else:
            await context.bot.send_message(chat_id=LOG_CHAT_ID, text=notification_text)
        _activate_complaint_cooldown(context, str(user_id), profile, "admin_complaint_cooldown_until")
    except Exception:
        logging.exception("admin complaint send failed")

    pending.pop(str(user_id), None)
    try:
        await update.callback_query.message.reply_text(
            "✅Жалоба отправлена руководству.",
            reply_markup=_build_complaint_menu_keyboard(),
        )
    except Exception:
        pass



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
    if not entry or str(entry.get("state")) not in {"await_proof", "await_confirm"}:
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
    photo_file_id = entry.get("photo_file_id")
    text_block = (
        "🛠Новая жалоба о баге\n\n"
        f"id_profile: #{id_profile}\n"
        f"username: {username}\n"
        f"user_id: {user_id}\n\n"
        f"Текст: {report_text}"
    )

    try:
        if photo_file_id:
            await context.bot.send_photo(
                chat_id=LOG_CHAT_ID,
                photo=photo_file_id,
                caption=text_block,
            )
        else:
            await context.bot.send_message(chat_id=LOG_CHAT_ID, text=text_block)
        _activate_complaint_cooldown(context, str(user_id), profile, "bug_report_cooldown_until")
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

    entry["state"] = "await_proof"
    entry["draft_text"] = report_text
    await update.message.reply_text(
        "📎Пришлите изображение-доказательство (скрин/фото) или нажмите «Далее», если доказательство не нужно.\n\n"
        "После этого жалоба будет отправлена в технический раздел.",
        reply_markup=InlineKeyboardMarkup(
            [[
                InlineKeyboardButton("Далее", callback_data=f"bug_report_confirm_{user_id}"),
            ], [
                InlineKeyboardButton("отмена", callback_data=f"bug_report_cancel_{user_id}"),
            ]]
        ),
    )
    raise ApplicationHandlerStop


async def bug_report_photo_input_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or update.effective_chat.type != ChatType.PRIVATE or not update.effective_user:
        return
    if update.effective_user.is_bot:
        return

    pending = context.application.bot_data.setdefault("pending_bug_reports", {})
    user_id = str(update.effective_user.id)
    entry = pending.get(user_id)
    if not entry:
        return
    if str(entry.get("state")) not in {"await_proof", "await_confirm"}:
        return

    photo = update.message.photo[-1] if update.message.photo else None
    if photo is None:
        return

    entry["photo_file_id"] = photo.file_id
    entry["state"] = "await_confirm"
    await update.message.reply_text(
        "✅Изображение-доказательство добавлено. Нажмите «Далее», чтобы отправить жалобу.",
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("Далее", callback_data=f"bug_report_confirm_{user_id}")], [InlineKeyboardButton("отмена", callback_data=f"bug_report_cancel_{user_id}")]]
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

        await send_active_session_menu(update, context)
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
    last_activity = float(
        (context.application.bot_data.get("admin_work_chat_activity", {}) or {}).get(user_id, 0) or 0
    )
    if last_activity:
        last_activity_text = _format_time_kyiv(last_activity)
        online_marker = "🟢Онлайн" if time.time() - last_activity <= 5 * 60 else "🔴Не в сети"
        online_text = f"{online_marker} ({last_activity_text})"
    else:
        online_text = "🔴Не в сети (данные об активности отсутствуют)"

    text = (
        "🔰 <b>АДМИН-ПРОФИЛЬ</b>\n\n"
        f"🆔 <b>ID профиля:</b> {profile.get('id_profile')}\n"
        f"👤 <b>Username:</b> {profile_username}\n"
        f"🎀 <b>Префикс:</b> {prefix_text}\n"
        f"🎖 <b>Уровень:</b> {admin_level} ({rank_title})\n"
        f"🏷 <b>Тег:</b> {admin_tag}\n"
        f"💕Тип диалогов: {tip_admin}\n"
        f"👨‍👩‍👦 Пол: {admin_gender}\n"
        f"👁‍🗨Был(-а) в сети: {online_text}\n"
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
        user_id = str(update.effective_user.id)
        profile = _ensure_profile(context, user_id, update.effective_user.username or f"id{user_id}")
        blocked, block_message = _check_admin_search_cooldown(context, user_id, profile)
        if blocked:
            await update.message.reply_text(block_message)
            return
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

        chat_id = WORK_CHAT_ID
        username = f"@{user.username}" if user.username else f"id{user.id}"
        gender_key = context.user_data.get("admin_gender")
        admin_gender = "Мальчик" if gender_key == "male" else "Девочка"
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
            "gender": gender_key,
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
            "gender": gender_key,
        }
        _save_runtime_snapshot(context)
    elif choice == "◀️ Назад":
        context.user_data.clear()
        await start(update, context)
    else:
        await update.message.reply_text(
            "Пожалуйста, выберите один из вариантов ниже."
        )


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

    if active.get("management_paused"):
        await update.message.reply_text(
            "🔇В вашем диалоге отключен режим общения руководством бота. "
            "Если вы считаете что это ошибка обратитесь в технический раздел"
        )
        return

    if active.get("paused"):
        resume_kb = InlineKeyboardMarkup(
            [[InlineKeyboardButton("Возомновить общение", callback_data=f"resume_session_{user.id}")]]
        )
        await update.message.reply_text("💤Сессия уже приостановлена.", reply_markup=resume_kb)
        return

    cooldown_until = float(active.get("pause_cooldown_until", 0) or 0)
    if time.time() < cooldown_until:
        remaining = int(cooldown_until - time.time()) + 1
        minutes, seconds = divmod(remaining, 60)
        remaining_text = f"{minutes} мин. {seconds} сек." if minutes else f"{seconds} сек."
        await update.message.reply_text(
            f"⏳Подождите {remaining_text} перед повторным использованием этой кнопки."
        )
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
    active["pause_cooldown_until"] = time.time() + 600
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

    profile = _ensure_profile(context, str(user_id), update.effective_user.username if update.effective_user else f"id{user_id}")
    _activate_admin_search_cooldown(context, str(user_id), profile)

    # Restore keyboard for the user
    try:
        kb = _build_main_menu_keyboard(context, user_id, update.effective_user.username if update.effective_user else None)
        await context.bot.send_message(chat_id=user_id, text="Поиск администратора отменен. На 15 минут будет установлен кулдаун перед повторным поиском.", reply_markup=kb)
    except Exception:
        pass


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

    if await block_if_banned(update, context):
        return

    profile = _ensure_profile(context, user_id, user.username or f"id{user_id}")
    refresh_timed_warnings(profile)
    expired_warns = consume_expired_warning_count(profile)
    if expired_warns:
        _save_profile_record(context, user_id)
        await update.message.reply_text(
            f"✅Срок действия предупреждени{'я' if expired_warns == 1 else 'й'} истёк. "
            f"Истёкших предупреждений: {expired_warns}. "
            "Теперь учитываются только действующие предупреждения."
        )

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

            admin_tag = str(active_session.get("admin_tag") or active_session.get("admin_username") or "админ")
            id_profile = profile.get("id_profile")
            msg_topic_closed = int(active_session.get("msg_topic_user", 0) or 0) + int(active_session.get("msg_topic_admin", 0) or 0)
            url_topic_closed = _topic_url(chat_id, topic_id) if chat_id and topic_id is not None else "тема недоступна"
            date_closed = active_session.get("closed_at") or datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
            active_session["closed_at"] = date_closed
            user_handle = f"@{username}" if username and not str(username).startswith("@") else str(username or "@unknown")
            cancel_review_text = (
                f"💔Пользователь отказался от администратора {admin_tag}\n\n"
                f"Сессия {url_topic_closed} была закрыта пользователем {user_handle} по причине {reason_otkazik}\n"
                f"🆔Айди пользователя: {id_profile}\n\n"
                "📌\n"
                f"Сообщений в теме: {msg_topic_closed}\n\n"
                f"🕑Дата закрытия сессии: {date_closed}"
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

    if active.get("management_paused"):
        await update.message.reply_text(
            "🔇В вашем диалоге отключен режим общения руководством бота. Если вы считаете что это ошибка обратитесь в технический раздел"
        )
        return

    if active.get("paused") or active.get("management_paused"):
        resume_kb = InlineKeyboardMarkup(
            [[InlineKeyboardButton("Возомновить общение", callback_data=f"resume_session_{user_id}")]]
        )
        await update.message.reply_text("💤Включен режим остановки. Администратору не отправится сообщение пока общение не будет возобновлено.", reply_markup=resume_kb)
        return

    if active.get("rp_disabled"):
        rp_trigger = _parse_rp_trigger_text(getattr(update.message, "text", None) or getattr(update.message, "caption", None))
        if rp_trigger:
            try:
                await update.message.delete()
            except Exception:
                pass
            await update.message.reply_text(
                "💔RP-команды отключены в данной сессии. Чтобы включить их снова, откройте раздел настроек сессии."
            )
            return

    suspicious_text = (getattr(update.message, "text", None) or getattr(update.message, "caption", None) or "").strip()
    bypass_until = float(active.get("suspicious_bypass_user_to_admin_until", 0) or 0)
    if _has_suspicious_username(suspicious_text) and time.time() >= bypass_until:
        active["detect_topic"] = int(active.get("detect_topic", 0) or 0) + 1
        active["last_suspicious_message"] = suspicious_text
        chat_id = active.get("chat_id")
        topic_id = active.get("topic_id")
        profile = _ensure_profile(context, user_id, update.effective_user.username or f"id{update.effective_user.id}")
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
    rp_trigger = _parse_rp_trigger_text(update.message.text or update.message.caption)
    if rp_trigger:
        trigger_name, template = rp_trigger
        admin_profile = _ensure_profile(context, str(active.get("admin_id")), active.get("admin_username") or f"id{active.get('admin_id')}")
        admin_name = _rp_display_name_admin(admin_profile, fallback_username=active.get("admin_username"), fallback_user_id=active.get("admin_id"))
        user_name = _rp_display_name_user(profile, fallback_username=update.effective_user.username, fallback_user_id=update.effective_user.id)
        rendered = _render_rp_action_message(
            template,
            sender_is_admin=False,
            name_admin=admin_name,
            name_user=user_name,
            trigger_name=trigger_name,
        )
        wrapped_text = f"💞RP : {rendered}"
        try:
            await context.bot.send_message(chat_id=int(chat_id), message_thread_id=topic_id if topic_id else None, text=wrapped_text)
        except Exception:
            try:
                await context.bot.send_message(chat_id=int(chat_id), text=wrapped_text)
            except Exception:
                pass
        admin_id = active.get("admin_id")
        if admin_id:
            try:
                await context.bot.send_message(chat_id=int(admin_id), text=wrapped_text)
            except Exception:
                pass
        profile["message_user"] = int(profile.get("message_user", 0) or 0) + 1
        active["msg_topic_user"] = int(active.get("msg_topic_user", 0) or 0) + 1
        active["rp_topic"] = int(active.get("rp_topic", 0) or 0) + 1
        active["last_rp_action"] = update.message.text or update.message.caption or "RP"
        return

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
            active["last_rp_action"] = update.message.text or update.message.caption or "RP"
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
        await update.callback_query.message.edit_text("Отмена успешна. Если потребуется, нажмите кнопку снова.")
    except BadRequest:
        pass



async def find_admin_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    requester_profile = _ensure_profile(
        context,
        user_id,
        update.effective_user.username or f"id{user_id}",
    )
    if _has_admin_rights_level_1_5(requester_profile):
        await update.message.reply_text("⛔ Администратору нельзя искать администратора для начала сессии.")
        return
    if _is_active_admin_candidate(context, user_id):
        await update.message.reply_text(_candidate_block_text())
        return
    blocked, block_message = _check_admin_search_cooldown(context, user_id, requester_profile)
    if blocked:
        await update.message.reply_text(block_message)
        return
    if await check_active_chat_block(update, context):
        return
    context.user_data["profile"] = 1
    await send_admin_menu(update, context)
