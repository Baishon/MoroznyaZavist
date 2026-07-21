"""Admin-candidate onboarding flow (private-chat form + inline callbacks).

Kept separate from `app.handlers.user` and `app.handlers.admin` because both of
those modules need to trigger/react to this flow, which would otherwise create
a circular import between them.
"""
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from app.config import LOG_CHAT_ID
from app.database.requests import _save_profile_record, _save_runtime_snapshot
from app.keyboards.inline import _build_candidate_gender_keyboard, _build_candidate_tip_keyboard
from app.services.profiles import _candidate_tip_value, _ensure_profile
from app.states.form import (
    STAGE_AWAIT_ADMIN_GENDER,
    STAGE_AWAIT_BIO,
    STAGE_AWAIT_REJECT_RESTART,
    STAGE_AWAIT_TAG,
    STAGE_AWAIT_TIP_ADMIN,
    STAGE_PENDING_REVIEW,
    TEXT_INPUT_STAGES,
)


async def _send_candidate_stage_1(context: ContextTypes.DEFAULT_TYPE, user_id: int):
    await context.bot.send_message(
        chat_id=user_id,
        text=(
            "📇Придумайте для своего персонажа уникальный админ тег, которые пользователи в будущем будут видеть и могут выбрать вас.\n"
            "Пример тега (муж) #неистовый (жен) #акира"
        ),
    )


async def admin_candidate_private_flow(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    if not update.message or not update.effective_user:
        return False
    user_id = str(update.effective_user.id)
    state_map = context.application.bot_data.setdefault("admin_candidate_state", {})
    state = state_map.get(user_id)
    if not state:
        return False

    stage = state.get("stage")
    if stage not in TEXT_INPUT_STAGES:
        return False

    profile = _ensure_profile(context, user_id, update.effective_user.username or f"id{user_id}")
    text = (update.message.text or "").strip()

    if stage == STAGE_AWAIT_TAG:
        if "#" not in text:
            await update.message.reply_text("Тег написан неправильно. Пример: #акира")
            return True
        profile["tag_admin"] = text
        state["stage"] = STAGE_AWAIT_BIO
        await update.message.reply_text("🔮Теперь напишите биографию своего персонажа (пример: любимое хобби, вредные привычки, распорядок дня )")
        return True

    if stage == STAGE_AWAIT_BIO:
        if not text:
            await update.message.reply_text("Биография не может быть пустой.")
            return True
        profile["biography_admin"] = text
        state["stage"] = STAGE_AWAIT_TIP_ADMIN
        state["tip_admin_selected"] = []

        await update.message.reply_text(
            "💬Теперь укажите тип общения которые вы больше всего можете обсуждать с будущими пользователями (можно выбрать все три)",
            reply_markup=_build_candidate_tip_keyboard(user_id, []),
        )
        return True

    if stage == STAGE_AWAIT_TIP_ADMIN:
        await update.message.reply_text("Выберите типы общения кнопками под сообщением.")
        return True

    if stage == STAGE_AWAIT_ADMIN_GENDER:
        await update.message.reply_text("Выберите пол кнопками под сообщением.")
        return True

    if stage == STAGE_AWAIT_REJECT_RESTART:
        if "#" not in text:
            await update.message.reply_text("Тег написан неправильно. Пример: #акира")
            return True
        profile["tag_admin"] = text
        state["stage"] = STAGE_AWAIT_BIO
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
    if state.get("stage") != STAGE_AWAIT_TIP_ADMIN:
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
    if state.get("stage") != STAGE_AWAIT_TIP_ADMIN:
        await update.callback_query.answer("Этап выбора типов общения уже завершен", show_alert=True)
        return

    selected = list(state.get("tip_admin_selected") or [])
    if not selected:
        await update.callback_query.answer("Выберите хотя бы один тип общения", show_alert=True)
        return

    profile = _ensure_profile(context, str(user_id), update.effective_user.username or f"id{user_id}")
    profile["tip_admin"] = _candidate_tip_value(selected)
    state["stage"] = STAGE_AWAIT_ADMIN_GENDER
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
    if state.get("stage") != STAGE_AWAIT_ADMIN_GENDER:
        await update.callback_query.answer("Этап выбора пола уже завершен", show_alert=True)
        return

    profile = _ensure_profile(context, str(user_id), update.effective_user.username or f"id{user_id}")
    profile["admin_gender"] = "👱🏻‍♂️Мальчик" if gender_key == "male" else "🙍‍♀️Девочка"
    state["stage"] = STAGE_PENDING_REVIEW

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
