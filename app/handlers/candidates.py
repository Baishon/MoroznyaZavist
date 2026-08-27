"""Admin-candidate onboarding flow (private-chat form + inline callbacks).

Kept separate from `app.handlers.user` and `app.handlers.admin` because both of
those modules need to trigger/react to this flow, which would otherwise create
a circular import between them.
"""
import asyncio

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from app.config import LOG_CHAT_ID
from app.database.requests import _save_profile_record, _save_runtime_snapshot
from app.keyboards.inline import (
    _build_candidate_gender_keyboard,
    _build_candidate_character_confirm_keyboard,
    _build_candidate_character_edit_keyboard,
    _build_candidate_images_confirm_keyboard,
    _build_candidate_images_preview_keyboard,
    _build_candidate_profile_keyboard,
    _build_candidate_tip_keyboard,
)
from app.services.profiles import _candidate_tip_value, _ensure_profile
from app.states.form import (
    STAGE_AWAIT_ADMIN_GENDER,
    STAGE_AWAIT_CANDIDATE_IMAGES,
    STAGE_AWAIT_CHARACTER,
    STAGE_AWAIT_BIO,
    STAGE_CANDIDATE_PROFILE,
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

    if stage == STAGE_AWAIT_CHARACTER:
        if not text:
            await update.message.reply_text("⚠️Характер не должен быть пустым. Напишите его текстом.")
            return True
        state["candidate_character_draft"] = text
        await update.message.reply_text(
            f"💬Вы указали характер:\n{text}\n\nВсё верно?",
            reply_markup=_build_candidate_character_confirm_keyboard(user_id),
        )
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
    state["stage"] = STAGE_CANDIDATE_PROFILE
    state.setdefault("candidate_image_ids", [])
    _save_profile_record(context, str(user_id))
    await update.callback_query.message.reply_text(
        "🧾Теперь вам нужно заполнить свою анкету которая будет видима всем пользователя на канале бота. Используйте кнопки ниже",
        reply_markup=_build_candidate_profile_keyboard(
            str(user_id),
            bool(state["candidate_image_ids"]),
            bool(profile.get("candidate_character")),
        ),
    )
    _save_profile_record(context, str(user_id))
    _save_runtime_snapshot(context)


async def candidate_profile_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data or ""
    parts = data.split("_")
    user_id = parts[-1]
    if not update.effective_user or str(update.effective_user.id) != user_id:
        await query.answer("Кнопка доступна только владельцу анкеты", show_alert=True)
        return
    state = context.application.bot_data.setdefault("admin_candidate_state", {}).get(user_id)
    if not state or state.get("stage") not in {
        STAGE_CANDIDATE_PROFILE,
        STAGE_AWAIT_CANDIDATE_IMAGES,
        STAGE_AWAIT_CHARACTER,
    }:
        await query.answer("Этап заполнения анкеты уже завершен", show_alert=True)
        return
    profile = _ensure_profile(context, user_id, update.effective_user.username or f"id{user_id}")
    state["candidate_image_ids"] = list(state.get("candidate_image_ids") or profile.get("candidate_image_ids") or [])
    state["stage"] = STAGE_CANDIDATE_PROFILE
    await query.message.edit_text(
        "🧾Теперь вам нужно заполнить свою анкету которая будет видима всем пользователя на канале бота. Используйте кнопки ниже",
        reply_markup=_build_candidate_profile_keyboard(
            user_id,
            bool(state.get("candidate_image_ids")),
            bool(profile.get("candidate_character")),
        ),
    )


async def candidate_images_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = (query.data or "").split("_")[-1]
    if not update.effective_user or str(update.effective_user.id) != user_id:
        await query.answer("Кнопка доступна только владельцу анкеты", show_alert=True)
        return
    state = context.application.bot_data.setdefault("admin_candidate_state", {}).get(user_id)
    if not state or state.get("stage") not in {STAGE_CANDIDATE_PROFILE, STAGE_AWAIT_CANDIDATE_IMAGES}:
        await query.answer("Этап заполнения анкеты уже завершен", show_alert=True)
        return
    profile = _ensure_profile(context, user_id, update.effective_user.username or f"id{user_id}")
    image_ids = list(state.get("candidate_image_ids") or profile.get("candidate_image_ids") or [])
    state["candidate_image_ids"] = image_ids
    if image_ids:
        await context.bot.send_media_group(
            chat_id=int(user_id),
            media=[InputMediaPhoto(photo_id) for photo_id in image_ids],
        )
        await query.message.edit_text(
            "🖼Ваши изображения анкеты уже сохранены.",
            reply_markup=_build_candidate_images_preview_keyboard(user_id),
        )
        return
    state["stage"] = STAGE_AWAIT_CANDIDATE_IMAGES
    state["candidate_image_ids"] = []
    state.pop("candidate_image_group_id", None)
    state["candidate_image_processing"] = False
    await query.message.edit_text(
        "🔗Отправьте 3 изображения щит-поста\nОдним сообщением-альбомом.",
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("отмена", callback_data=f"candidate_profile_{user_id}")]]
        ),
    )


async def candidate_character_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = (query.data or "").split("_")[-1]
    if not update.effective_user or str(update.effective_user.id) != user_id:
        await query.answer("Кнопка доступна только владельцу анкеты", show_alert=True)
        return
    state = context.application.bot_data.setdefault("admin_candidate_state", {}).get(user_id)
    if not state or state.get("stage") not in {STAGE_CANDIDATE_PROFILE, STAGE_AWAIT_CHARACTER}:
        await query.answer("Этап заполнения анкеты уже завершен", show_alert=True)
        return
    profile = _ensure_profile(context, user_id, update.effective_user.username or f"id{user_id}")
    character = str(state.get("candidate_character") or profile.get("candidate_character") or "").strip()
    if character:
        await query.message.edit_text(
            f"💬Ваш характер:\n{character}",
            reply_markup=_build_candidate_character_edit_keyboard(user_id),
        )
        return
    state["stage"] = STAGE_AWAIT_CHARACTER
    await query.message.edit_text(
        "💭Напишите свой характер. Так пользователям будет легче общаться с вами💝\nПример: Ревнивый, добрый",
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("отмена", callback_data=f"candidate_profile_{user_id}")]]
        ),
    )


async def candidate_character_edit_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = (query.data or "").split("_")[-1]
    if not update.effective_user or str(update.effective_user.id) != user_id:
        await query.answer("Кнопка доступна только владельцу анкеты", show_alert=True)
        return
    state = context.application.bot_data.setdefault("admin_candidate_state", {}).get(user_id)
    if not state:
        return
    state["stage"] = STAGE_AWAIT_CHARACTER
    await query.message.edit_text(
        "💭Напишите свой характер. Так пользователям будет легче общаться с вами💝\nПример: Ревнивый, добрый",
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("отмена", callback_data=f"candidate_profile_{user_id}")]]
        ),
    )


async def candidate_character_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = (query.data or "").split("_")
    user_id = data[-1]
    answer = data[-2]
    if not update.effective_user or str(update.effective_user.id) != user_id:
        await query.answer("Кнопка доступна только владельцу анкеты", show_alert=True)
        return
    state = context.application.bot_data.setdefault("admin_candidate_state", {}).get(user_id)
    if not state:
        return
    if answer == "no":
        state.pop("candidate_character_draft", None)
        state["stage"] = STAGE_AWAIT_CHARACTER
        await query.message.edit_text(
            "💭Напишите свой характер. Так пользователям будет легче общаться с вами💝\nПример: Ревнивый, добрый",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("отмена", callback_data=f"candidate_profile_{user_id}")]]
            ),
        )
        return
    character = str(state.get("candidate_character_draft") or "").strip()
    if not character:
        await query.answer("Сначала укажите характер", show_alert=True)
        return
    state["candidate_character"] = character
    state.pop("candidate_character_draft", None)
    state["stage"] = STAGE_CANDIDATE_PROFILE
    profile = _ensure_profile(context, user_id, update.effective_user.username or f"id{user_id}")
    profile["candidate_character"] = character
    _save_profile_record(context, user_id)
    await query.message.edit_text(
        "✅Характер сохранён.",
        reply_markup=_build_candidate_profile_keyboard(
            user_id,
            bool(state.get("candidate_image_ids")),
            True,
        ),
    )
    _save_runtime_snapshot(context)


async def candidate_images_photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_user or not update.message.photo:
        return
    user_id = str(update.effective_user.id)
    state = context.application.bot_data.setdefault("admin_candidate_state", {}).get(user_id)
    if not state or state.get("stage") != STAGE_AWAIT_CANDIDATE_IMAGES:
        return
    media_group_id = update.message.media_group_id
    if not media_group_id:
        await update.message.reply_text("⚠️Отправьте все 3 изображения одним сообщением-альбомом.")
        return

    group_id = state.setdefault("candidate_image_group_id", media_group_id)
    if group_id != media_group_id:
        await update.message.reply_text("⚠️Отправьте все 3 изображения одним сообщением-альбомом.")
        return

    image_ids = state.setdefault("candidate_image_ids", [])
    file_id = update.message.photo[-1].file_id
    if file_id not in image_ids:
        image_ids.append(file_id)

    if state.get("candidate_image_processing"):
        return
    state["candidate_image_processing"] = True
    context.application.create_task(_finish_candidate_images_after_delay(context, int(user_id), media_group_id))


async def _finish_candidate_images_after_delay(
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int,
    media_group_id: str,
) -> None:
    await asyncio.sleep(3)
    state = context.application.bot_data.setdefault("admin_candidate_state", {}).get(str(user_id))
    if not state or state.get("stage") != STAGE_AWAIT_CANDIDATE_IMAGES:
        return
    if state.get("candidate_image_group_id") != media_group_id:
        return

    image_ids = list(state.get("candidate_image_ids") or [])
    state["candidate_image_processing"] = False
    if len(image_ids) != 3:
        state["candidate_image_ids"] = []
        state.pop("candidate_image_group_id", None)
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=f"⚠️В альбоме найдено изображений: {len(image_ids)} из 3. Отправьте ровно 3 изображения одним сообщением-альбомом.",
            )
        except Exception:
            pass
        return

    try:
        await context.bot.send_message(
            chat_id=user_id,
            text="Вы уверены, что хотите добавить эти изображения в анкету?",
            reply_markup=_build_candidate_images_confirm_keyboard(str(user_id)),
        )
    except Exception:
        state["candidate_image_processing"] = False


async def candidate_images_redo_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = (query.data or "").split("_")[-1]
    if not update.effective_user or str(update.effective_user.id) != user_id:
        await query.answer("Кнопка доступна только владельцу анкеты", show_alert=True)
        return
    state = context.application.bot_data.setdefault("admin_candidate_state", {}).get(user_id)
    if not state:
        return
    state["stage"] = STAGE_AWAIT_CANDIDATE_IMAGES
    state["candidate_image_ids"] = []
    state.pop("candidate_image_group_id", None)
    state["candidate_image_processing"] = False
    await query.message.edit_text(
        "🔗Отправьте 3 изображения щит-поста\nОдним сообщением-альбомом.",
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("отмена", callback_data=f"candidate_profile_{user_id}")]]
        ),
    )


async def candidate_images_done_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = (query.data or "").split("_")[-1]
    if not update.effective_user or str(update.effective_user.id) != user_id:
        await query.answer("Кнопка доступна только владельцу анкеты", show_alert=True)
        return
    state = context.application.bot_data.setdefault("admin_candidate_state", {}).get(user_id)
    if not state or len(state.get("candidate_image_ids") or []) != 3:
        await query.answer("Нужно отправить ровно 3 изображения", show_alert=True)
        return
    state["stage"] = STAGE_CANDIDATE_PROFILE
    state.pop("candidate_image_group_id", None)
    state["candidate_image_processing"] = False
    profile = _ensure_profile(context, user_id, update.effective_user.username or f"id{user_id}")
    profile["candidate_image_ids"] = list(state["candidate_image_ids"])
    _save_profile_record(context, user_id)
    await query.message.edit_text(
        "✅Аватарки анкеты сохранены.",
        reply_markup=_build_candidate_profile_keyboard(user_id, True),
    )
    _save_runtime_snapshot(context)


async def candidate_images_edit_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await candidate_images_redo_callback(update, context)


async def candidate_submit_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = (query.data or "").split("_")[-1]
    if not update.effective_user or str(update.effective_user.id) != user_id:
        await query.answer("Кнопка доступна только владельцу анкеты", show_alert=True)
        return
    state = context.application.bot_data.setdefault("admin_candidate_state", {}).get(user_id)
    profile = _ensure_profile(context, user_id, update.effective_user.username or f"id{user_id}")
    image_ids = list(state.get("candidate_image_ids") or profile.get("candidate_image_ids") or [])
    character = str(state.get("candidate_character") or profile.get("candidate_character") or "").strip()
    if len(image_ids) != 3 or not character:
        missing = []
        if len(image_ids) != 3:
            missing.append("изображения")
        if not character:
            missing.append("характер")
        await query.answer(f"Заполните: {', '.join(missing)}", show_alert=True)
        return
    state["candidate_image_ids"] = image_ids
    state["stage"] = STAGE_PENDING_REVIEW
    review_id = str(int(context.application.bot_data.get("admin_candidate_review_seq", 0) or 0) + 1)
    context.application.bot_data["admin_candidate_review_seq"] = int(review_id)
    pending_reviews = context.application.bot_data.setdefault("pending_admin_candidate_reviews", {})
    pending_reviews[review_id] = {
        "user_id": user_id,
        "username": profile.get("username") or f"id{user_id}",
        "tag_admin": profile.get("tag_admin", ""),
        "biography_admin": profile.get("biography_admin", ""),
        "tip_admin": profile.get("tip_admin", "не указано"),
        "admin_gender": profile.get("admin_gender", "не указан"),
        "candidate_image_ids": list(state["candidate_image_ids"]),
        "candidate_character": character,
    }
    review = pending_reviews[review_id]
    review_text = (
        f'📁Кандидат {review["username"]} отправил свою заявку на обработку.\n'
        f'📎Тег: {review["tag_admin"]}\n\n'
        f'📝Биография: {review["biography_admin"]}\n\n'
        f'💕Тип диалогов: {review["tip_admin"]}\n'
        f'👨‍👩‍👦Пол: {review["admin_gender"]}\n'
        f'💬Характер: {review["candidate_character"]}'
    )
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅Одобрить кандидата", callback_data=f"approve_candidate_{review_id}"),
        InlineKeyboardButton("❌Отказать кандидату", callback_data=f"reject_candidate_{review_id}"),
        InlineKeyboardButton("🔍Анкета кандидата", callback_data=f"candidate_application_{review_id}"),
    ]])
    await context.bot.send_message(chat_id=LOG_CHAT_ID, text=review_text, reply_markup=kb)
    await query.message.edit_text("✅Заявка отправлена на обработку старшей администрации.")
    _save_profile_record(context, user_id)
    _save_runtime_snapshot(context)


async def candidate_application_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    review_id = (query.data or "").split("_")[-1]
    review = context.application.bot_data.setdefault("pending_admin_candidate_reviews", {}).get(review_id)
    if not review:
        await query.answer("Анкета уже недоступна", show_alert=True)
        return
    image_ids = list(review.get("candidate_image_ids") or [])
    if len(image_ids) != 3:
        await query.answer("Изображения анкеты не найдены", show_alert=True)
        return
    media = [
        InputMediaPhoto(
            image_ids[0],
            caption=f"💬Характер кандидата:\n{review.get('candidate_character') or 'не указан'}",
        ),
        *[InputMediaPhoto(photo_id) for photo_id in image_ids[1:]],
    ]
    await context.bot.send_media_group(
        chat_id=query.message.chat_id,
        media=media,
    )
