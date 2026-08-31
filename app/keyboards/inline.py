"""Inline/reply keyboard builders used across handlers.

Every `_build_*_keyboard` function returns an `InlineKeyboardMarkup` or
`ReplyKeyboardMarkup` for one screen (main menu, settings, astats gender/tip
editors, info-topic and setprefix panels, candidate onboarding); the
`_next_*_panel_id` helpers generate the opaque ids those keyboards encode
into callback_data. Pure UI construction only — no bot_data mutation beyond
handing out sequence ids, no Telegram API calls.
"""
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup
from telegram.ext import ContextTypes

from app.services.profiles import _candidate_tip_choices, _ensure_profile, _has_admin_rights_level_1_5


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


def _build_candidate_profile_keyboard(
    user_id: str,
    images_saved: bool = False,
    character_saved: bool = False,
) -> InlineKeyboardMarkup:
    image_label = "✅🔖Изображения" if images_saved else "🔖Изображения"
    character_label = "✅💬Характер" if character_saved else "💬Характер"
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(image_label, callback_data=f"candidate_images_{user_id}")],
            [InlineKeyboardButton(character_label, callback_data=f"candidate_character_{user_id}")],
            [InlineKeyboardButton("↗️отправить заявку", callback_data=f"candidate_submit_{user_id}")],
        ]
    )


def _build_candidate_character_confirm_keyboard(user_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[
            InlineKeyboardButton("да", callback_data=f"candidate_character_yes_{user_id}"),
            InlineKeyboardButton("нет", callback_data=f"candidate_character_no_{user_id}"),
        ]]
    )


def _build_candidate_character_edit_keyboard(user_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("отредактировать", callback_data=f"candidate_character_edit_{user_id}")],
         [InlineKeyboardButton("отмена", callback_data=f"candidate_profile_{user_id}")]]
    )


def _build_candidate_images_confirm_keyboard(user_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("переделать", callback_data=f"candidate_images_redo_{user_id}"),
                InlineKeyboardButton("готово", callback_data=f"candidate_images_done_{user_id}"),
            ]
        ]
    )


def _build_candidate_images_preview_keyboard(user_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("↩️вернуться к заполнению анкеты", callback_data=f"candidate_profile_{user_id}")],
            [InlineKeyboardButton("отредактировать", callback_data=f"candidate_images_edit_{user_id}")],
        ]
    )


def _build_main_menu_keyboard(context: ContextTypes.DEFAULT_TYPE, user_id: int | str, username_hint: str | None = None) -> ReplyKeyboardMarkup:
    profile = _ensure_profile(context, str(user_id), username_hint or f"id{user_id}")
    profile_button = "🔰Админ-профиль" if _has_admin_rights_level_1_5(profile) else "👤 Профиль"
    rows = [
        [KeyboardButton("👤 Найти админа")],
        [KeyboardButton(profile_button)],
    ]
    active = (context.application.bot_data.get("active_chats", {}) or {}).get(str(user_id))
    if active and active.get("active"):
        rows.append([KeyboardButton("↩️Вернуться к кнопкам диалога")])
    rows.append([KeyboardButton("⚙️Настройки")])
    return ReplyKeyboardMarkup(
        rows,
        resize_keyboard=True,
        one_time_keyboard=False,
    )


def _build_active_session_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton("🤧Отказаться от админа")],
            [KeyboardButton("➕Настройки сессии")],
            [KeyboardButton("🕘Проверить онлайн админа")],
            [KeyboardButton("↪️Вернуться в главное меню")],
        ],
        resize_keyboard=True,
        one_time_keyboard=False,
    )


def _build_session_settings_keyboard(active: dict | None = None) -> ReplyKeyboardMarkup:
    session_data = active or {}
    rp_disabled = bool(session_data.get("rp_disabled", False))
    label = "💞Включить RP" if rp_disabled else "💔Отключить RP"
    rows = [[KeyboardButton(label), KeyboardButton("💤Приостановить общение")]]
    rows.append([KeyboardButton("↩️Вернуться к кнопкам диалога")])
    return ReplyKeyboardMarkup(
        rows,
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


def _build_complaint_admin_menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton("✏️Написать тег админа")],
            [KeyboardButton("🕓Выбрать последнего админа")],
            [KeyboardButton("↩️В раздел жалоб")],
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
        ],
        [
            InlineKeyboardButton("👁Logs", callback_data=f"info_topic_logs_{panel_id}"),
        ]]
    )


def _build_info_topic_text(
    username_pz: str,
    admin_username: str,
    detect: int,
    detect_last: str,
    msg_topic: int,
    rp_topic: int,
    rp_topic_last: str,
    date_value: str,
    topic_link: str,
) -> str:
    return (
        f"📕Full information about the session {username_pz} with the admin {admin_username}\n\n"
        f"🔍Suspicious messages: {detect} Last suspicious: {detect_last}\n"
        f"✉️Total posts in this topic: {msg_topic}\n"
        f"💓Action role-play: {rp_topic} Last RP: {rp_topic_last}\n\n"
        f"📂Time of session registration in the bot's database {date_value}"
    )


def _next_info_topic_panel_id(context: ContextTypes.DEFAULT_TYPE) -> str:
    seq = int(context.application.bot_data.get("info_topic_panel_seq", 0) or 0) + 1
    context.application.bot_data["info_topic_panel_seq"] = seq
    return str(seq)


def _prefix_options() -> list[tuple[str, str]]:
    return [
        ("logs", "👁Logs"),
        ("cooperation", "💎Сотрудничество"),
        ("tech", "👨‍💻Технический специалист"),
        ("montajer", "🔧Монтажер"),
        ("owner", "💋Владелец"),
        ("deputy", "💘Заместитель владельца"),
        ("queen", "👑Королева"),
        ("heel", "👠Каблук"),
        ("senior", "👮‍♀️Старший админ"),
        ("admin", "🐶Админ"),
        ("junior", "🦅Младший админ"),
        ("intern", "🧸Стажер"),
    ]


def _build_setprefix_keyboard(panel_id: str, selected_keys: list[str] | str | None) -> InlineKeyboardMarkup:
    if isinstance(selected_keys, str):
        selected_set = {selected_keys} if selected_keys else set()
    else:
        selected_set = set(selected_keys or [])
    buttons = []
    for key, label in _prefix_options():
        title = f"✅{label}" if key in selected_set else label
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
