"""Application assembly: builds the python-telegram-bot Application and registers
every handler. This module intentionally imports handler functions by name
(mirroring the original single-file bot.py) so the registration list below
stays a straightforward, auditable 1:1 mapping of command/callback -> handler.
"""
import asyncio
import logging
import os
import threading
from types import SimpleNamespace
from http.server import BaseHTTPRequestHandler, HTTPServer

from telegram import BotCommand
from telegram.ext import ApplicationBuilder, CallbackQueryHandler, CommandHandler, MessageHandler, MessageReactionHandler, filters

from app import logging_setup  # noqa: F401  (side effect: attaches Telegram log handler)
from app.config import COOPERATION_CHAT_ID, LOG_CHAT_ID, TOKEN, WORK_CHAT_ID
from app.database.requests import (
    _init_persistent_storage,
    _save_incoming_message,
    _save_runtime_snapshot,
)
from app.handlers.admin import (
    add_rules_handler,
    admin_group_message_handler,
    admin_candidate_command_guard,
    admin_mute_guard_handler,
    admin_take_callback,
    amute_command_handler,
    anpiar_command_handler,
    approve_candidate_callback,
    approve_decline_callback,
    astats_active_pz_callback,
    astats_bio_cancel_callback,
    astats_bio_change_callback,
    astats_bio_view_callback,
    astats_command_handler,
    astats_gender_back_callback,
    astats_gender_menu_callback,
    astats_gender_set_callback,
    astats_tag_cancel_callback,
    astats_tag_change_callback,
    astats_tip_apply_callback,
    astats_tip_menu_callback,
    astats_tip_toggle_callback,
    ban_command_handler,
    permanent_ban_command_handler,
    cancel_candidate_reject_callback,
    cancel_decline_request_callback,
    cancel_warn_callback,
    candidate_reject_reason_message_handler,
    confirm_warn_callback,
    cooperation_admin_command_guard,
    del_rule_handler,
    admin_decline_callback,
    admin_decline_request_callback,
    admins_command_handler,
    banlist_command_handler,
    dump_maps_handler,
    fullstats_command_handler,
    givetopic_command_handler,
    handle_astats_bio_input_message,
    handle_astats_tag_input_message,
    handle_decline_input_message,
    handle_warn_reason_message,
    info_topic_cancel_callback,
    info_topic_close_callback,
    info_topic_close_confirm_callback,
    info_topic_command_handler,
    info_topic_logs_callback,
    info_topic_resume_callback,
    info_topic_stats_callback,
    info_topic_stop_callback,
    info_topic_stop_confirm_callback,
    kus_command_handler,
    log_command_router,
    makeadmin_command_handler,
    pm_command_handler,
    prava_command_handler,
    reject_candidate_callback,
    reject_decline_callback,
    sendpiar_command_handler,
    sendpiar_media_router,
    searchuser_command_handler,
    setrep_command_handler,
    setprefix_apply_callback,
    setprefix_command_handler,
    setprefix_select_callback,
    show_user_profile_callback,
    stats_command_handler,
    taketopic_command_handler,
    suspicious_admin_allow_callback,
    suspicious_admin_deny_callback,
    suspicious_user_allow_callback,
    suspicious_user_deny_callback,
    suspicious_user_warn_callback,
    topic_command_handler,
    unban_command_handler,
    unknown_chat_guard,
    unmute_command_handler,
    unwarn_command_handler,
    warn_command_handler,
    warn_user_callback,
    warn_user_cancel_callback,
    warnlist_command_handler,
)
from app.handlers.candidates import (
    candidate_gender_callback,
    candidate_application_callback,
    candidate_character_callback,
    candidate_character_confirm_callback,
    candidate_character_edit_callback,
    candidate_images_callback,
    candidate_images_done_callback,
    candidate_images_edit_callback,
    candidate_images_photo_handler,
    candidate_images_redo_callback,
    candidate_profile_callback,
    candidate_submit_callback,
    candidate_tip_next_callback,
    candidate_tip_toggle_callback,
)
from app.handlers.user import (
    admin_cancel_confirm_callback,
    admin_cancel_deny_callback,
    admin_complaint_cancel_callback,
    admin_complaint_confirm_callback,
    admin_complaint_photo_input_handler,
    admin_complaint_text_input_handler,
    ask_cancel_admin,
    bug_report_cancel_callback,
    bug_report_confirm_callback,
    bug_report_photo_input_handler,
    bug_report_reject_callback,
    bug_report_text_input_handler,
    cancel_nickname_change_callback,
    cancel_search_callback,
    change_nickname_callback,
    choose_admin_gender_callback,
    choose_mood_callback,
    mood_selection_callback,
    mood_selection_next_callback,
    check_session_admin_online_handler,
    complaint_admin_back_handler,
    complaint_admin_last_admin_handler,
    complaint_admin_menu_handler,
    complaint_admin_write_tag_handler,
    complaint_bug_menu_handler,
    confirm_cancel_callback,
    deny_cancel_callback,
    find_admin_menu_callback,
    handle_decline_reason_reply,
    pause_session_cancel_callback,
    pause_session_confirm_callback,
    pause_session_request,
    resume_session_callback,
    restart_command_handler,
    return_to_dialog_menu_handler,
    return_to_main_menu_handler,
    send_admin_profile,
    send_profile,
    session_rp_disable_cancel_callback,
    session_rp_disable_confirm_callback,
    session_rp_disable_prompt,
    session_rp_enable_handler,
    session_settings_menu_handler,
    settings_back_handler,
    settings_complaint_menu_handler,
    settings_disable_ad_handler,
    settings_enable_ad_handler,
    settings_menu_handler,
    show_admin_bio_callback,
    start,
    thanks_command_handler,
    thanks_cancel_callback,
    thanks_confirm_callback,
    thanks_from_session_prompt,
    user_private_message_handler,
    # Agreement handlers
    agreement_message_handler,
    agreement_callback_guard,
    agreement_accept_callback,
    admin_search_command_guard,
    subscription_message_handler,
    subscription_callback_guard,
    subscription_check_callback,
)
from app.services.messaging import mirror_session_message_edit, mirror_session_message_reaction
from app.telegram_bot import HistoryBot
from app.admin_api import create_admin_api


async def _runtime_snapshot_loop(app) -> None:
    while True:
        try:
            _save_runtime_snapshot(SimpleNamespace(application=app))
        except Exception:
            logging.exception("Failed to autosave runtime snapshot")
        await asyncio.sleep(10)


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
    app.create_task(_runtime_snapshot_loop(app))


async def _message_history_tracker(update, context) -> None:
    """Record incoming messages/callbacks before the normal handlers process them."""
    message = update.effective_message
    callback_query = update.callback_query
    if message is None and callback_query is not None:
        message = callback_query.message
    if message is None:
        return

    _save_incoming_message(
        context,
        update_id=update.update_id,
        message=message,
        actor=update.effective_user,
        callback_data=callback_query.data if callback_query is not None else None,
    )


def build_application():
    app = ApplicationBuilder().bot(HistoryBot(token=TOKEN)).post_init(_post_init).build()
    _init_persistent_storage(app)
    app.bot._message_history_connection = app.bot_data.get("_state_db_connection")

    # Agreement enforcement: block users who haven't accepted terms (default: 0)
    # Runs early to prevent other handlers from executing when user hasn't agreed.
    # Callback guard applies globally (anywhere a user clicks a callback) and will send PM with agreement.
    app.add_handler(CallbackQueryHandler(subscription_callback_guard, pattern=r".*"), group=-8)
    app.add_handler(CallbackQueryHandler(subscription_check_callback, pattern=r"^subscription_check$"), group=-6)
    app.add_handler(CallbackQueryHandler(agreement_callback_guard, pattern=r".*"), group=-7)
    # Specific accept handler (should be after the guard so it can be handled)
    app.add_handler(CallbackQueryHandler(agreement_accept_callback, pattern=r"^agreement_accept$"), group=-5)
    # Keep an audit copy of incoming interactions without changing handler flow.
    app.add_handler(MessageHandler(filters.ALL, _message_history_tracker), group=-20)
    app.add_handler(CallbackQueryHandler(_message_history_tracker, pattern=r".*"), group=-20)
    # Message guard for all chat types: intercepts any message from a user who hasn't accepted the current agreement.
    app.add_handler(MessageHandler(filters.ALL, subscription_message_handler), group=-8)
    app.add_handler(MessageHandler(filters.ALL, agreement_message_handler), group=-7)
    app.add_handler(
        MessageHandler(filters.COMMAND & filters.ChatType.PRIVATE, admin_search_command_guard),
        group=-6,
    )

    app.add_handler(
        MessageHandler(
            (filters.PHOTO | filters.VIDEO)
            & filters.CaptionRegex(r"^/sp(?:@[\w_]+)?(?:\s+.*)?$"),
            sendpiar_media_router,
        ),
        group=-3,
    )
    app.add_handler(MessageHandler(filters.ALL & (filters.ChatType.GROUP | filters.ChatType.SUPERGROUP), unknown_chat_guard), group=-2)
    app.add_handler(
        MessageHandler(
            filters.TEXT
            & ~filters.Regex(r"^/sp(?:@[\w_]+)?(?:\s+.*)?$")
            & filters.Chat(COOPERATION_CHAT_ID),
            cooperation_admin_command_guard,
        ),
        group=-1,
    )
    app.add_handler(MessageHandler(filters.TEXT & filters.Chat(LOG_CHAT_ID), log_command_router), group=-1)
    app.add_handler(
        MessageHandler(
            filters.COMMAND & (filters.ChatType.GROUP | filters.ChatType.SUPERGROUP | filters.ChatType.CHANNEL),
            admin_candidate_command_guard,
        ),
        group=-2,
    )
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND & (filters.ChatType.GROUP | filters.ChatType.SUPERGROUP), admin_mute_guard_handler), group=-1)
    app.add_handler(CommandHandler("addrules", add_rules_handler), group=-1)
    app.add_handler(CommandHandler("delrules", del_rule_handler), group=-1)
    app.add_handler(CommandHandler("makeadmin", makeadmin_command_handler, filters=filters.ChatType.PRIVATE | filters.ChatType.GROUP | filters.ChatType.SUPERGROUP), group=-1)
    app.add_handler(CommandHandler("setprefix", setprefix_command_handler, filters=filters.ChatType.PRIVATE | filters.ChatType.GROUP | filters.ChatType.SUPERGROUP), group=-1)
    app.add_handler(CommandHandler("setrep", setrep_command_handler, filters=filters.ChatType.PRIVATE | filters.ChatType.GROUP | filters.ChatType.SUPERGROUP), group=-1)
    app.add_handler(CommandHandler("searchuser", searchuser_command_handler, filters=filters.ChatType.PRIVATE | filters.ChatType.GROUP | filters.ChatType.SUPERGROUP), group=-1)
    app.add_handler(CommandHandler("anpiar", anpiar_command_handler, filters=filters.ChatType.PRIVATE | filters.ChatType.GROUP | filters.ChatType.SUPERGROUP), group=-1)
    app.add_handler(CommandHandler("fullstats", fullstats_command_handler, filters=filters.ChatType.PRIVATE | filters.ChatType.GROUP | filters.ChatType.SUPERGROUP), group=-1)
    app.add_handler(CommandHandler("admins", admins_command_handler, filters=filters.ChatType.PRIVATE | filters.ChatType.GROUP | filters.ChatType.SUPERGROUP), group=-1)
    app.add_handler(CommandHandler("banlist", banlist_command_handler, filters=filters.ChatType.PRIVATE | filters.ChatType.GROUP | filters.ChatType.SUPERGROUP), group=-1)
    app.add_handler(CommandHandler("warnlist", warnlist_command_handler, filters=filters.ChatType.PRIVATE | filters.ChatType.GROUP | filters.ChatType.SUPERGROUP), group=-1)
    app.add_handler(CommandHandler("sp", sendpiar_command_handler, filters=filters.ChatType.PRIVATE | filters.ChatType.GROUP | filters.ChatType.SUPERGROUP), group=-1)
    app.add_handler(CommandHandler("pm", pm_command_handler, filters=filters.ChatType.PRIVATE | filters.ChatType.GROUP | filters.ChatType.SUPERGROUP), group=-1)
    app.add_handler(CommandHandler("prava", prava_command_handler, filters=filters.ChatType.PRIVATE | filters.ChatType.GROUP | filters.ChatType.SUPERGROUP), group=-1)
    app.add_handler(CommandHandler("ban", ban_command_handler, filters=filters.ChatType.PRIVATE | filters.ChatType.GROUP | filters.ChatType.SUPERGROUP), group=-1)
    app.add_handler(CommandHandler("pban", permanent_ban_command_handler, filters=filters.ChatType.PRIVATE | filters.ChatType.GROUP | filters.ChatType.SUPERGROUP), group=-1)
    app.add_handler(CommandHandler("unban", unban_command_handler, filters=filters.ChatType.PRIVATE | filters.ChatType.GROUP | filters.ChatType.SUPERGROUP), group=-1)
    app.add_handler(CommandHandler("amute", amute_command_handler, filters=filters.ChatType.GROUP | filters.ChatType.SUPERGROUP), group=-1)
    app.add_handler(CommandHandler("aunmute", unmute_command_handler, filters=filters.ChatType.GROUP | filters.ChatType.SUPERGROUP), group=-1)
    app.add_handler(CommandHandler("warn", warn_command_handler, filters=filters.ChatType.PRIVATE | filters.ChatType.GROUP | filters.ChatType.SUPERGROUP), group=-1)
    app.add_handler(CommandHandler("unwarn", unwarn_command_handler, filters=filters.ChatType.PRIVATE | filters.ChatType.GROUP | filters.ChatType.SUPERGROUP), group=-1)
    app.add_handler(CommandHandler("stats", stats_command_handler, filters=filters.ChatType.PRIVATE | filters.ChatType.GROUP | filters.ChatType.SUPERGROUP), group=-1)
    app.add_handler(CommandHandler("astats", astats_command_handler, filters=filters.ChatType.PRIVATE | filters.ChatType.GROUP | filters.ChatType.SUPERGROUP), group=-1)
    app.add_handler(CommandHandler("info_topic", info_topic_command_handler, filters=filters.ChatType.GROUP | filters.ChatType.SUPERGROUP | filters.ChatType.CHANNEL), group=-1)
    app.add_handler(CommandHandler("givetopic", givetopic_command_handler, filters=filters.ChatType.GROUP | filters.ChatType.SUPERGROUP), group=-1)
    app.add_handler(CommandHandler("taketopic", taketopic_command_handler, filters=filters.ChatType.GROUP | filters.ChatType.SUPERGROUP), group=-1)
    app.add_handler(CommandHandler("topic", topic_command_handler, filters=filters.ChatType.GROUP | filters.ChatType.SUPERGROUP), group=-1)
    app.add_handler(CommandHandler("start", start, filters=filters.ChatType.PRIVATE))
    app.add_handler(CommandHandler("thanks", thanks_command_handler, filters=filters.ChatType.PRIVATE))
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
    app.add_handler(CallbackQueryHandler(mood_selection_callback, pattern=r"^mood_toggle_\d+_(chat|support|flirt)$"))
    app.add_handler(CallbackQueryHandler(mood_selection_next_callback, pattern=r"^mood_next_\d+$"))
    app.add_handler(CallbackQueryHandler(candidate_gender_callback, pattern=r"^candidate_gender_\d+_(male|female)$"))
    app.add_handler(CallbackQueryHandler(candidate_application_callback, pattern=r"^candidate_application_\d+$"))
    app.add_handler(CallbackQueryHandler(candidate_character_callback, pattern=r"^candidate_character_\d+$"))
    app.add_handler(CallbackQueryHandler(candidate_character_edit_callback, pattern=r"^candidate_character_edit_\d+$"))
    app.add_handler(CallbackQueryHandler(candidate_character_confirm_callback, pattern=r"^candidate_character_(yes|no)_\d+$"))
    app.add_handler(CallbackQueryHandler(candidate_images_callback, pattern=r"^candidate_images_\d+$"))
    app.add_handler(CallbackQueryHandler(candidate_images_redo_callback, pattern=r"^candidate_images_redo_\d+$"))
    app.add_handler(CallbackQueryHandler(candidate_images_done_callback, pattern=r"^candidate_images_done_\d+$"))
    app.add_handler(CallbackQueryHandler(candidate_images_edit_callback, pattern=r"^candidate_images_edit_\d+$"))
    app.add_handler(CallbackQueryHandler(candidate_profile_callback, pattern=r"^candidate_profile_\d+$"))
    app.add_handler(CallbackQueryHandler(candidate_submit_callback, pattern=r"^candidate_submit_\d+$"))
    app.add_handler(CallbackQueryHandler(cancel_candidate_reject_callback, pattern=r"^cancel_candidate_reject_\d+$"))
    app.add_handler(CallbackQueryHandler(suspicious_admin_allow_callback, pattern=r"^susp_admin_allow_\d+$"))
    app.add_handler(CallbackQueryHandler(suspicious_admin_deny_callback, pattern=r"^susp_admin_deny_\d+$"))
    app.add_handler(CallbackQueryHandler(suspicious_user_allow_callback, pattern=r"^susp_user_allow_\d+$"))
    app.add_handler(CallbackQueryHandler(suspicious_user_deny_callback, pattern=r"^susp_user_deny_\d+$"))
    app.add_handler(CallbackQueryHandler(suspicious_user_warn_callback, pattern=r"^susp_user_warn_\d+$"))
    app.add_handler(CallbackQueryHandler(pause_session_confirm_callback, pattern=r"^pause_confirm_\d+$"))
    app.add_handler(CallbackQueryHandler(pause_session_cancel_callback, pattern=r"^pause_cancel_\d+$"))
    app.add_handler(CallbackQueryHandler(resume_session_callback, pattern=r"^resume_session_\d+$"))
    app.add_handler(CallbackQueryHandler(session_rp_disable_confirm_callback, pattern=r"^session_rp_disable_confirm_\d+$"))
    app.add_handler(CallbackQueryHandler(session_rp_disable_cancel_callback, pattern=r"^session_rp_disable_cancel_\d+$"))
    app.add_handler(CallbackQueryHandler(thanks_confirm_callback, pattern=r"^thanks_confirm_\d+$"))
    app.add_handler(CallbackQueryHandler(thanks_cancel_callback, pattern=r"^thanks_cancel_\d+$"))
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
    app.add_handler(CallbackQueryHandler(info_topic_logs_callback, pattern=r"^info_topic_logs_\d+$"))
    app.add_handler(CallbackQueryHandler(info_topic_stats_callback, pattern=r"^info_topic_stats_\d+$"))
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
    app.add_handler(CallbackQueryHandler(admin_complaint_cancel_callback, pattern=r"^admin_complaint_cancel_\d+$"))
    app.add_handler(CallbackQueryHandler(admin_complaint_confirm_callback, pattern=r"^admin_complaint_confirm_\d+$"))
    app.add_handler(MessageHandler(filters.Regex("^👤 Найти админа$") & filters.ChatType.PRIVATE, find_admin_menu_callback))
    app.add_handler(MessageHandler(filters.Regex("^⚙️Настройки$") & filters.ChatType.PRIVATE, settings_menu_handler))
    app.add_handler(MessageHandler(filters.Regex("^💬Отправить жалобу$") & filters.ChatType.PRIVATE, settings_complaint_menu_handler))
    app.add_handler(MessageHandler(filters.Regex("^👨‍🔧Сообщить о баге$") & filters.ChatType.PRIVATE, complaint_bug_menu_handler))
    app.add_handler(MessageHandler(filters.Regex("^👮‍♀️Пожаловаться на админа$") & filters.ChatType.PRIVATE, complaint_admin_menu_handler))
    app.add_handler(MessageHandler(filters.Regex("^✏️Написать тег админа$") & filters.ChatType.PRIVATE, complaint_admin_write_tag_handler))
    app.add_handler(MessageHandler(filters.Regex("^🕓Выбрать последнего админа$") & filters.ChatType.PRIVATE, complaint_admin_last_admin_handler))
    app.add_handler(MessageHandler(filters.Regex("^↩️В раздел жалоб$") & filters.ChatType.PRIVATE, complaint_admin_back_handler))
    app.add_handler(MessageHandler(filters.Regex("^↩️В настройки$") & filters.ChatType.PRIVATE, settings_menu_handler))
    app.add_handler(MessageHandler(filters.Regex("^🔕Отключить рекламу$") & filters.ChatType.PRIVATE, settings_disable_ad_handler))
    app.add_handler(MessageHandler(filters.Regex("^🔔Включить рекламу$") & filters.ChatType.PRIVATE, settings_enable_ad_handler))
    app.add_handler(MessageHandler(filters.Regex("^↩️Назад$") & filters.ChatType.PRIVATE, settings_back_handler))
    app.add_handler(MessageHandler(filters.Regex("^👤 Профиль$"), send_profile))
    app.add_handler(MessageHandler(filters.Regex("^🔰Админ-профиль$") & filters.ChatType.PRIVATE, send_admin_profile))
    app.add_handler(MessageHandler(filters.Regex("^🤧Отказаться от админа$") & filters.ChatType.PRIVATE, ask_cancel_admin))
    app.add_handler(MessageHandler(filters.Regex("^💤Приостановить общение$") & filters.ChatType.PRIVATE, pause_session_request))
    app.add_handler(MessageHandler(filters.Regex("^➕Настройки сессии$") & filters.ChatType.PRIVATE, session_settings_menu_handler))
    app.add_handler(MessageHandler(filters.Regex("^💔Отключить RP$") & filters.ChatType.PRIVATE, session_rp_disable_prompt))
    app.add_handler(MessageHandler(filters.Regex("^💛Отблагодарить админа$") & filters.ChatType.PRIVATE, thanks_from_session_prompt))
    app.add_handler(MessageHandler(filters.Regex("^💞Включить RP$") & filters.ChatType.PRIVATE, session_rp_enable_handler))
    app.add_handler(MessageHandler(filters.Regex("^🕘Проверить онлайн админа$") & filters.ChatType.PRIVATE, check_session_admin_online_handler))
    app.add_handler(MessageHandler(filters.Regex("^↩️Вернуться к кнопкам диалога$") & filters.ChatType.PRIVATE, return_to_dialog_menu_handler))
    app.add_handler(MessageHandler(filters.Regex("^↪️Вернуться в главное меню$") & filters.ChatType.PRIVATE, return_to_main_menu_handler))
    app.add_handler(MessageHandler(filters.Regex("^👨 Мальчик$|^👩 Девочка$|^◀️ Назад$") & filters.ChatType.PRIVATE, choose_admin_gender_callback))
    app.add_handler(MessageHandler(filters.Regex("^🗣️ Общение$|^❤️ Поддержка$|^🔥 Флирт$|^◀️ Назад$") & filters.ChatType.PRIVATE, choose_mood_callback))
    app.add_handler(MessageHandler(filters.PHOTO & filters.ChatType.PRIVATE, bug_report_photo_input_handler), group=-2)
    app.add_handler(MessageHandler(filters.PHOTO & filters.ChatType.PRIVATE, admin_complaint_photo_input_handler), group=-6)
    app.add_handler(MessageHandler(filters.PHOTO & filters.ChatType.PRIVATE, candidate_images_photo_handler), group=-5)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE, bug_report_text_input_handler), group=-2)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE, admin_complaint_text_input_handler), group=-6)
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
    app.add_handler(MessageHandler(filters.ALL & ~filters.REPLY & filters.ChatType.PRIVATE, user_private_message_handler))
    # Handler for admin replies to the bot's "Введите причину отклонения запроса" prompt
    app.add_handler(MessageHandler(filters.TEXT & filters.REPLY & (filters.ChatType.GROUP | filters.ChatType.SUPERGROUP), handle_decline_reason_reply))
    # Edited messages must be handled before ordinary message handlers. Several
    # group handlers use filters.ALL/TEXT and would otherwise consume the only
    # handler slot for their group before the mirror handler is reached.
    app.add_handler(
        MessageHandler(filters.UpdateType.EDITED_MESSAGE & filters.ALL, mirror_session_message_edit),
        group=-10,
    )
    app.add_handler(MessageReactionHandler(mirror_session_message_reaction))
    app.add_handler(CommandHandler("dump_maps", dump_maps_handler))
    return app


class _HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

    def log_message(self, format: str, *args) -> None:  # noqa: A002 - stdlib signature
        pass


def _start_health_check_server() -> None:
    """Start the admin API and health endpoint on the deployment port."""
    import uvicorn

    port = int(os.environ.get("PORT", "10000"))
    api = create_admin_api()
    config = uvicorn.Config(api, host="0.0.0.0", port=port, log_level="info")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()


def main() -> None:
    _start_health_check_server()
    app = build_application()
    app.run_polling(
        allowed_updates=[
            "message",
            "edited_message",
            "callback_query",
            "message_reaction",
            "message_reaction_count",
        ],
        close_loop=False,
    )
