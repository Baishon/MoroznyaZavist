"""Application assembly: builds the python-telegram-bot Application and registers
every handler. This module intentionally imports handler functions by name
(mirroring the original single-file bot.py) so the registration list below
stays a straightforward, auditable 1:1 mapping of command/callback -> handler.
"""
import asyncio
import base64
import http.cookies
import json
import logging
import os
import threading
import time
from datetime import datetime, time as datetime_time
from pathlib import Path
from types import SimpleNamespace
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

from telegram import BotCommand
from telegram.error import TelegramError, TimedOut
from telegram.ext import ApplicationBuilder, CallbackQueryHandler, ChatMemberHandler, CommandHandler, MessageHandler, MessageReactionHandler, filters

from app import logging_setup  # noqa: F401  (side effect: attaches Telegram log handler)
from app.config import (
    ADMIN_LEVEL_TITLES,
    AUTO_ADMIN_CHAT_ID,
    COOPERATION_CHAT_ID,
    LOG_CHAT_ID,
    TOKEN,
    TRUSTED_ADMIN_CHAT_IDS,
    WEB_AUTH_BOT_USERNAME,
    WORK_CHAT_ID,
)
from app.database.requests import (
    _dashboard_message_action as _update_dashboard_message_history,
    _get_user_action_history,
    _get_dashboard_messages,
    _get_dashboard_users,
    _get_user_session_history,
    _get_user_session_messages,
    _mark_user_session_message_deleted,
    _record_user_action,
    _record_user_session,
    _record_user_session_message,
    _close_user_session,
    _save_profile_record,
    _init_persistent_storage,
    _save_incoming_message,
    _save_runtime_snapshot,
)
from app.handlers.admin import (
    _finish_admin_demotion,
    add_rules_handler,
    admin_group_message_handler,
    admin_mute_guard_handler,
    admin_period_callback,
    admin_period_menu_handler,
    auto_promote_trusted_group_admin,
    awarn_command_handler,
    aunwarn_command_handler,
    my_admin_norm_handler,
    weekly_admin_norm_report_job,
    weekly_admin_stats_reset_callback,
    weekly_admin_stats_message_handler,
    WEEKLY_STATS_TRIGGER_PATTERN,
    admin_take_callback,
    amute_command_handler,
    anpiar_command_handler,
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
    candidate_review_accept_callback,
    candidate_review_reject_callback,
    permanent_ban_command_handler,
    cancel_decline_request_callback,
    cancel_warn_callback,
    confirm_warn_callback,
    cooperation_admin_command_guard,
    del_rule_handler,
    admin_decline_callback,
    admin_decline_request_callback,
    admins_command_handler,
    addrest_command_handler,
    kd_command_handler,
    delrest_command_handler,
    restlist_command_handler,
    banlist_command_handler,
    dump_maps_handler,
    fullstats_command_handler,
    recinfo_command_handler,
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
    log_ping_handler,
    makeadmin_command_handler,
    makeadmin_demotion_callback,
    pm_command_handler,
    prava_command_handler,
    restart_chat_menu_handler,
    restart_log_chat_menu_handler,
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
    topic_mood_settings_callback_handler,
    topic_access_menu_callback_handler,
    session_mood_review_callback,
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
    candidate_application_guard,
    candidate_back_callback,
    candidate_confirm_callback,
    candidate_continue_callback,
    candidate_privacy_yes_callback,
    candidate_scope_continue_callback,
    candidate_scope_toggle_callback,
    candidate_submit_callback,
    ticket_accept_callback,
    ticket_reject_callback,
    ticket_close_callback,
    cancel_nickname_change_callback,
    cancel_search_callback,
    change_nickname_callback,
    choose_admin_gender_callback,
    choose_mood_callback,
    mood_selection_callback,
    mood_selection_next_callback,
    mood_aggressive_confirm_callback,
    mood_aggressive_cancel_callback,
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
    help_menu_handler,
    admin_shop_menu_handler,
    main_menu_callback,
    admin_shop_buy_reprimand_callback,
    admin_shop_confirm_reprimand_callback,
    admin_shop_cancel_callback,
    pause_session_cancel_callback,
    pause_session_confirm_callback,
    pause_session_request,
    resume_session_callback,
    restart_command_handler,
    return_to_dialog_menu_handler,
    return_to_main_menu_handler,
    request_gender_callback,
    send_admin_profile,
    send_profile,
    show_candidate_intro_callback,
    stop_candidate_command_handler,
    session_rp_disable_cancel_callback,
    session_rp_disable_confirm_callback,
    session_rp_disable_prompt,
    session_rp_enable_handler,
    session_settings_menu_handler,
    session_mood_back_callback,
    session_mood_open_callback,
    session_mood_submit_callback,
    session_mood_toggle_callback,
    settings_back_handler,
    settings_complaint_menu_handler,
    settings_disable_ad_handler,
    settings_enable_ad_handler,
    settings_menu_handler,
    show_admin_bio_callback,
    start,
    web_auth_confirmation_callback,
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
from app.services.owner_session import start_owner_session, stop_owner_session
from app.services.profiles import _effective_admin_level
from app.services.profiles import (
    _admin_default_prefix,
    _ensure_profile,
    _set_user_blocked_bot_state,
    is_user_banned,
)
from app.keyboards.inline import _prefix_options
from app.services.topics import _topic_url
from app.services.web_auth import (
    WEB_AUTH_BROWSER_COOKIE,
    WEB_AUTH_SESSION_COOKIE,
    WEB_AUTH_STORE,
)
from app.telegram_bot import HistoryBot

_BOT_STARTED_AT: float | None = None
_DASHBOARD_APP = None
_BOT_LOOP: asyncio.AbstractEventLoop | None = None
_TECHNICAL_MESSAGE_PREFIX = "Сообщение от технического раздела: "


async def _runtime_snapshot_loop(app) -> None:
    while True:
        try:
            _save_runtime_snapshot(SimpleNamespace(application=app))
        except Exception:
            logging.exception("Failed to autosave runtime snapshot")
        await asyncio.sleep(10)


async def _post_init(app) -> None:
    global _BOT_STARTED_AT, _DASHBOARD_APP, _BOT_LOOP
    _DASHBOARD_APP = app
    _BOT_LOOP = asyncio.get_running_loop()
    app.bot_data.pop("weekly_stats_reset_processing", None)
    await start_owner_session(app)
    try:
        bot_identity = await app.bot.get_me()
        if (bot_identity.username or "").casefold() != WEB_AUTH_BOT_USERNAME.casefold():
            raise RuntimeError(
                f"Website login is configured for @{WEB_AUTH_BOT_USERNAME}, "
                f"but TELEGRAM_TOKEN belongs to @{bot_identity.username or 'unknown'}."
            )
        WEB_AUTH_STORE.set_bot_username(WEB_AUTH_BOT_USERNAME)
    except Exception:
        logging.exception("Failed to initialize the website Telegram login link")
    try:
        await app.bot.set_my_commands(
            [
                BotCommand("start", "Запустить бота"),
                BotCommand("restart", "Обновить текущее подменю"),
                BotCommand("stop", "Остановить кандидатуру"),
            ]
        )
    except Exception:
        logging.exception("Failed to set bot commands")
    if app.job_queue:
        moscow_timezone = ZoneInfo("Europe/Moscow")
        app.job_queue.run_daily(
            weekly_admin_norm_report_job,
            time=datetime_time(8, 0, tzinfo=moscow_timezone),
            days=(0,),
            name="weekly_admin_norm_report",
        )
        now_moscow = datetime.now(moscow_timezone)
        if now_moscow.weekday() == 0 and (now_moscow.hour, now_moscow.minute) >= (8, 0):
            await weekly_admin_norm_report_job(SimpleNamespace(application=app, bot=app.bot))
    app.create_task(_runtime_snapshot_loop(app))
    _BOT_STARTED_AT = time.monotonic()


async def _post_shutdown(app) -> None:
    global _BOT_STARTED_AT, _DASHBOARD_APP, _BOT_LOOP
    _BOT_STARTED_AT = None
    _DASHBOARD_APP = None
    _BOT_LOOP = None
    await stop_owner_session(app)


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
    if callback_query is not None:
        return

    active_sessions = context.application.bot_data.get("active_chats", {}) or {}
    actor_id = str(getattr(update.effective_user, "id", "") or "")
    session = None
    session_user_id = actor_id
    if getattr(message.chat, "type", None) == "private":
        session = active_sessions.get(actor_id)
    elif getattr(message.chat, "type", None) in {"group", "supergroup"}:
        topic_id = getattr(message, "message_thread_id", None)
        if topic_id is not None:
            session_user_id = (
                context.application.bot_data.get("topic_user_map", {}) or {}
            ).get(topic_id) or (
                context.application.bot_data.get("topic_user_map", {}) or {}
            ).get(str(topic_id))
            session = active_sessions.get(str(session_user_id)) if session_user_id else None
            if session and str(session.get("chat_id")) != str(message.chat_id):
                session = None

    if session and session.get("active"):
        _record_user_session(context, session_user_id, session)
        _record_user_session_message(
            context,
            session,
            message,
            actor=update.effective_user,
        )


async def _application_error_handler(update, context) -> None:
    error = context.error
    if error is None or isinstance(error, TimedOut):
        return

    logging.error(
        "Unhandled exception while processing update",
        exc_info=(type(error), error, error.__traceback__),
    )


def build_application():
    app = (
        ApplicationBuilder()
        .bot(HistoryBot(token=TOKEN))
        .post_init(_post_init)
        .post_shutdown(_post_shutdown)
        .build()
    )
    _init_persistent_storage(app)
    app.bot._message_history_connection = app.bot_data.get("_state_db_connection")
    app.add_error_handler(_application_error_handler)

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
        CallbackQueryHandler(candidate_application_guard, pattern=r".*"),
        group=-4,
    )
    app.add_handler(
        MessageHandler(
            filters.COMMAND | (filters.ALL & filters.ChatType.PRIVATE),
            candidate_application_guard,
        ),
        group=-4,
    )

    app.add_handler(
        MessageHandler(
            (filters.PHOTO | filters.VIDEO)
            & filters.CaptionRegex(r"^/(?:sp|asp)(?:@[\w_]+)?(?:\s+.*)?$"),
            sendpiar_media_router,
        ),
        group=-3,
    )
    app.add_handler(MessageHandler(filters.ALL & (filters.ChatType.GROUP | filters.ChatType.SUPERGROUP), unknown_chat_guard), group=-2)
    app.add_handler(
        ChatMemberHandler(
            auto_promote_trusted_group_admin,
            chat_member_types=ChatMemberHandler.CHAT_MEMBER,
            chat_id=AUTO_ADMIN_CHAT_ID,
        ),
        group=-1,
    )
    app.add_handler(
        MessageHandler(
            filters.TEXT
            & ~filters.Regex(r"^/(?:sp|asp)(?:@[\w_]+)?(?:\s+.*)?$")
            & filters.Chat(COOPERATION_CHAT_ID),
            cooperation_admin_command_guard,
        ),
        group=-1,
    )
    app.add_handler(MessageHandler(filters.Regex("^💬Пинг$") & filters.Chat(LOG_CHAT_ID), log_ping_handler), group=-1)
    app.add_handler(
        MessageHandler(
            filters.Regex("^👥Список администрации$") & filters.Chat(LOG_CHAT_ID),
            admins_command_handler,
        ),
        group=-1,
    )
    app.add_handler(
        MessageHandler(
            filters.Regex("^🛥Список администраторов в ресте$") & filters.Chat(LOG_CHAT_ID),
            restlist_command_handler,
        ),
        group=-1,
    )
    app.add_handler(
        MessageHandler(
            filters.Regex("^📊Статистика администрации$") & filters.Chat(LOG_CHAT_ID),
            admin_period_menu_handler,
        ),
        group=-1,
    )
    app.add_handler(
        MessageHandler(
            filters.TEXT & filters.Chat([LOG_CHAT_ID, *TRUSTED_ADMIN_CHAT_IDS]),
            log_command_router,
        ),
        group=-1,
    )
    app.add_handler(
        MessageHandler(
            filters.Regex("^📊Моя норма$") & filters.Chat(list(TRUSTED_ADMIN_CHAT_IDS)),
            my_admin_norm_handler,
        ),
        group=-1,
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
    app.add_handler(CommandHandler("recinfo", recinfo_command_handler, filters=filters.Chat(WORK_CHAT_ID)), group=-1)
    app.add_handler(CommandHandler("admins", admins_command_handler, filters=filters.ChatType.PRIVATE | filters.ChatType.GROUP | filters.ChatType.SUPERGROUP), group=-1)
    app.add_handler(CommandHandler("addrest", addrest_command_handler, filters=filters.ChatType.PRIVATE | filters.ChatType.GROUP | filters.ChatType.SUPERGROUP), group=-1)
    app.add_handler(CommandHandler("kd", kd_command_handler, filters=filters.ChatType.PRIVATE | filters.ChatType.GROUP | filters.ChatType.SUPERGROUP), group=-1)
    app.add_handler(CommandHandler("delrest", delrest_command_handler, filters=filters.ChatType.PRIVATE | filters.ChatType.GROUP | filters.ChatType.SUPERGROUP), group=-1)
    app.add_handler(CommandHandler("restlist", restlist_command_handler, filters=filters.ChatType.PRIVATE | filters.ChatType.GROUP | filters.ChatType.SUPERGROUP), group=-1)
    app.add_handler(CommandHandler("banlist", banlist_command_handler, filters=filters.ChatType.PRIVATE | filters.ChatType.GROUP | filters.ChatType.SUPERGROUP), group=-1)
    app.add_handler(CommandHandler("warnlist", warnlist_command_handler, filters=filters.ChatType.PRIVATE | filters.ChatType.GROUP | filters.ChatType.SUPERGROUP), group=-1)
    app.add_handler(CommandHandler("sp", sendpiar_command_handler, filters=filters.ChatType.PRIVATE | filters.ChatType.GROUP | filters.ChatType.SUPERGROUP), group=-1)
    app.add_handler(CommandHandler("asp", sendpiar_command_handler, filters=filters.ChatType.PRIVATE | filters.ChatType.GROUP | filters.ChatType.SUPERGROUP), group=-1)
    app.add_handler(CommandHandler("pm", pm_command_handler, filters=filters.ChatType.PRIVATE | filters.ChatType.GROUP | filters.ChatType.SUPERGROUP), group=-1)
    app.add_handler(CommandHandler("prava", prava_command_handler, filters=filters.ChatType.PRIVATE | filters.ChatType.GROUP | filters.ChatType.SUPERGROUP), group=-1)
    app.add_handler(CommandHandler("ban", ban_command_handler, filters=filters.ChatType.PRIVATE | filters.ChatType.GROUP | filters.ChatType.SUPERGROUP), group=-1)
    app.add_handler(CommandHandler("pban", permanent_ban_command_handler, filters=filters.ChatType.PRIVATE | filters.ChatType.GROUP | filters.ChatType.SUPERGROUP), group=-1)
    app.add_handler(CommandHandler("unban", unban_command_handler, filters=filters.ChatType.PRIVATE | filters.ChatType.GROUP | filters.ChatType.SUPERGROUP), group=-1)
    app.add_handler(CommandHandler("amute", amute_command_handler, filters=filters.ChatType.GROUP | filters.ChatType.SUPERGROUP), group=-1)
    app.add_handler(CommandHandler("aunmute", unmute_command_handler, filters=filters.ChatType.GROUP | filters.ChatType.SUPERGROUP), group=-1)
    app.add_handler(CommandHandler("warn", warn_command_handler, filters=filters.ChatType.PRIVATE | filters.ChatType.GROUP | filters.ChatType.SUPERGROUP), group=-1)
    app.add_handler(CommandHandler("unwarn", unwarn_command_handler, filters=filters.ChatType.PRIVATE | filters.ChatType.GROUP | filters.ChatType.SUPERGROUP), group=-1)
    app.add_handler(CommandHandler("awarn", awarn_command_handler, filters=filters.ChatType.GROUP | filters.ChatType.SUPERGROUP), group=-1)
    app.add_handler(CommandHandler("aunwarn", aunwarn_command_handler, filters=filters.ChatType.GROUP | filters.ChatType.SUPERGROUP), group=-1)
    app.add_handler(CommandHandler("stats", stats_command_handler, filters=filters.ChatType.PRIVATE | filters.ChatType.GROUP | filters.ChatType.SUPERGROUP), group=-1)
    app.add_handler(CommandHandler("astats", astats_command_handler, filters=filters.ChatType.PRIVATE | filters.ChatType.GROUP | filters.ChatType.SUPERGROUP), group=-1)
    app.add_handler(CommandHandler("info_topic", info_topic_command_handler, filters=filters.ChatType.GROUP | filters.ChatType.SUPERGROUP | filters.ChatType.CHANNEL), group=-1)
    app.add_handler(CommandHandler("givetopic", givetopic_command_handler, filters=filters.ChatType.GROUP | filters.ChatType.SUPERGROUP), group=-1)
    app.add_handler(CommandHandler("taketopic", taketopic_command_handler, filters=filters.ChatType.GROUP | filters.ChatType.SUPERGROUP), group=-1)
    app.add_handler(CommandHandler("topic", topic_command_handler, filters=filters.ChatType.GROUP | filters.ChatType.SUPERGROUP), group=-1)
    app.add_handler(CommandHandler("start", start, filters=filters.ChatType.PRIVATE))
    app.add_handler(CommandHandler("thanks", thanks_command_handler, filters=filters.ChatType.PRIVATE))
    app.add_handler(CommandHandler("restart", restart_command_handler, filters=filters.ChatType.PRIVATE))
    app.add_handler(CommandHandler("stop", stop_candidate_command_handler, filters=filters.ChatType.PRIVATE))
    app.add_handler(
        MessageHandler(
            filters.Regex(r"^/restartchat(?:@[\w_]+)?$") & filters.Chat(list(TRUSTED_ADMIN_CHAT_IDS)),
            restart_chat_menu_handler,
        ),
        group=-1,
    )
    app.add_handler(CallbackQueryHandler(cancel_search_callback, pattern=r"^cancel_search_\d+$"))
    app.add_handler(CallbackQueryHandler(admin_period_callback, pattern=r"^admin_period_week$"))
    app.add_handler(
        CallbackQueryHandler(
            web_auth_confirmation_callback,
            pattern=r"^web_auth_(?:yes|no)_[A-Za-z0-9_-]{20,64}$",
        )
    )
    app.add_handler(
        CallbackQueryHandler(
            weekly_admin_stats_reset_callback,
            pattern=r"^weekly_reset_(?:request|yes|no)_\d+$",
        )
    )
    app.add_handler(CallbackQueryHandler(confirm_cancel_callback, pattern=r"^confirm_cancel_\d+$"))
    app.add_handler(CallbackQueryHandler(deny_cancel_callback, pattern=r"^deny_cancel_\d+$"))
    app.add_handler(
        CallbackQueryHandler(
            candidate_continue_callback,
            pattern=r"^candidate_continue$",
        )
    )
    app.add_handler(
        CallbackQueryHandler(
            candidate_back_callback,
            pattern=r"^candidate_back$",
        )
    )
    app.add_handler(
        CallbackQueryHandler(
            candidate_confirm_callback,
            pattern=r"^candidate_confirm$",
        )
    )
    app.add_handler(
        CallbackQueryHandler(
            candidate_scope_toggle_callback,
            pattern=r"^candidate_scope_toggle_\d+_(chat|support|flirt|aggressive)$",
        )
    )
    app.add_handler(
        CallbackQueryHandler(
            candidate_scope_continue_callback,
            pattern=r"^candidate_scope_continue_\d+$",
        )
    )
    app.add_handler(
        CallbackQueryHandler(
            candidate_privacy_yes_callback,
            pattern=r"^candidate_privacy_yes_\d+$",
        )
    )
    app.add_handler(
        CallbackQueryHandler(
            candidate_submit_callback,
            pattern=r"^candidate_submit_(yes|no)_\d+$",
        )
    )
    app.add_handler(
        CallbackQueryHandler(
            candidate_review_accept_callback,
            pattern=r"^candidate_review_accept_\d+$",
        )
    )
    app.add_handler(
        CallbackQueryHandler(
            candidate_review_reject_callback,
            pattern=r"^candidate_review_reject_\d+$",
        )
    )
    app.add_handler(
        CallbackQueryHandler(
            main_menu_callback,
            pattern=r"^main_menu_(find_admin|candidate|profile|admin_profile|admin_shop|help|settings|dialog|back)$",
        )
    )
    app.add_handler(CallbackQueryHandler(admin_take_callback, pattern=r"^take_user_\d+$"))
    app.add_handler(
        CallbackQueryHandler(
            topic_access_menu_callback_handler,
            pattern=r"^topic_(?:settings(?:_back)?|accesses|access_(?:back|list(?:_back)?|pick|confirm|cancel)):"
        )
    )
    app.add_handler(
        CallbackQueryHandler(
            topic_mood_settings_callback_handler,
            pattern=r"^topic_mood_(?:open|submit|back):\d+$|^topic_mood_toggle:\d+:(?:chat|support|flirt|aggressive)$",
        )
    )
    app.add_handler(CallbackQueryHandler(session_mood_open_callback, pattern=r"^session_mood_open_\d+$"))
    app.add_handler(CallbackQueryHandler(session_mood_toggle_callback, pattern=r"^session_mood_toggle_\d+_(chat|support|flirt|aggressive)$"))
    app.add_handler(CallbackQueryHandler(session_mood_submit_callback, pattern=r"^session_mood_submit_\d+$"))
    app.add_handler(CallbackQueryHandler(session_mood_back_callback, pattern=r"^session_mood_back_\d+$"))
    app.add_handler(CallbackQueryHandler(session_mood_review_callback, pattern=r"^session_mood_review_(approve|reject)_\d+$"))
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
    app.add_handler(CallbackQueryHandler(mood_selection_callback, pattern=r"^mood_toggle_\d+_(chat|support|flirt|aggressive)$"))
    app.add_handler(CallbackQueryHandler(mood_selection_next_callback, pattern=r"^mood_next_\d+$"))
    app.add_handler(CallbackQueryHandler(mood_aggressive_confirm_callback, pattern=r"^mood_aggressive_confirm_\d+$"))
    app.add_handler(CallbackQueryHandler(mood_aggressive_cancel_callback, pattern=r"^mood_aggressive_cancel_\d+$"))
    app.add_handler(CallbackQueryHandler(request_gender_callback, pattern=r"^request_gender_\d+_(male|female)$"))
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
    app.add_handler(CallbackQueryHandler(makeadmin_demotion_callback, pattern=r"^makeadmin_demote_(?:notify|silent|cancel)_[a-f0-9]{32}$"))
    app.add_handler(CallbackQueryHandler(setprefix_select_callback, pattern=r"^prefix_select_\d+_[a-z]+$"))
    app.add_handler(CallbackQueryHandler(setprefix_apply_callback, pattern=r"^prefix_apply_\d+$"))
    app.add_handler(CallbackQueryHandler(astats_tag_change_callback, pattern=r"^astats_tag_change_\d+$"))
    app.add_handler(CallbackQueryHandler(astats_tag_cancel_callback, pattern=r"^astats_tag_cancel_\d+$"))
    app.add_handler(CallbackQueryHandler(astats_bio_change_callback, pattern=r"^astats_bio_change_\d+$"))
    app.add_handler(CallbackQueryHandler(astats_bio_cancel_callback, pattern=r"^astats_bio_cancel_\d+$"))
    app.add_handler(CallbackQueryHandler(astats_bio_view_callback, pattern=r"^astats_bio_view_\d+$"))
    app.add_handler(CallbackQueryHandler(astats_active_pz_callback, pattern=r"^astats_active_pz_\d+$"))
    app.add_handler(CallbackQueryHandler(astats_tip_menu_callback, pattern=r"^astats_tip_menu_\d+$"))
    app.add_handler(CallbackQueryHandler(astats_tip_toggle_callback, pattern=r"^astats_tip_toggle_\d+_(chat|support|flirt|aggressive)$"))
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
    app.add_handler(CallbackQueryHandler(ticket_accept_callback, pattern=r"^ticket_accept_\d+$"))
    app.add_handler(CallbackQueryHandler(ticket_reject_callback, pattern=r"^ticket_reject_\d+$"))
    app.add_handler(CallbackQueryHandler(ticket_close_callback, pattern=r"^ticket_close_\d+$"))
    app.add_handler(CallbackQueryHandler(admin_shop_buy_reprimand_callback, pattern=r"^admin_shop_buy_reprimand$"))
    app.add_handler(CallbackQueryHandler(admin_shop_confirm_reprimand_callback, pattern=r"^admin_shop_confirm_reprimand$"))
    app.add_handler(CallbackQueryHandler(admin_shop_cancel_callback, pattern=r"^admin_shop_cancel$"))
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
    app.add_handler(MessageHandler(filters.Regex("^❓Помощь$") & filters.ChatType.PRIVATE, help_menu_handler))
    app.add_handler(MessageHandler(filters.Regex("^🛒Админ-магазин$") & filters.ChatType.PRIVATE, admin_shop_menu_handler))
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
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE, bug_report_text_input_handler), group=-2)
    app.add_handler(
        MessageHandler(
            filters.TEXT
            & filters.Regex(WEEKLY_STATS_TRIGGER_PATTERN)
            & filters.ChatType.PRIVATE,
            weekly_admin_stats_message_handler,
        ),
        group=-6,
    )
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE, admin_complaint_text_input_handler), group=-6)
    # Moderation input handlers must run before generic forwarding.
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & (filters.ChatType.GROUP | filters.ChatType.SUPERGROUP), handle_astats_bio_input_message), group=-4)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & (filters.ChatType.GROUP | filters.ChatType.SUPERGROUP), handle_astats_tag_input_message), group=-3)
    app.add_handler(
        MessageHandler(
            filters.TEXT
            & filters.Regex(WEEKLY_STATS_TRIGGER_PATTERN)
            & (filters.ChatType.GROUP | filters.ChatType.SUPERGROUP),
            weekly_admin_stats_message_handler,
        ),
        group=-5,
    )
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


async def _dashboard_users(query: str, offset: int) -> list[dict]:
    if _DASHBOARD_APP is None:
        raise RuntimeError("Bot application is not ready")
    return _get_dashboard_users(
        SimpleNamespace(application=_DASHBOARD_APP),
        query=query,
        offset=offset,
    )


async def _dashboard_messages(user_id: str) -> list[dict]:
    if _DASHBOARD_APP is None:
        raise RuntimeError("Bot application is not ready")
    messages = _get_dashboard_messages(
        SimpleNamespace(application=_DASHBOARD_APP),
        user_id,
        limit=100,
    )
    return [
        message
        for message in messages
        if message.get("chat_id") == user_id and message.get("chat_type") == "private"
    ]


async def _dashboard_actions(user_id: str) -> list[dict]:
    if _DASHBOARD_APP is None:
        raise RuntimeError("Bot application is not ready")
    context = SimpleNamespace(application=_DASHBOARD_APP)
    profiles = _DASHBOARD_APP.bot_data.get("profiles", {}) or {}
    if user_id not in profiles and not _get_dashboard_messages(context, user_id, limit=1):
        raise LookupError("User profile not found")
    return _get_user_action_history(context, user_id, limit=50)


async def _dashboard_user_details(user_id: str, actor_id: str) -> dict:
    if _DASHBOARD_APP is None:
        raise RuntimeError("Bot application is not ready")
    context = SimpleNamespace(application=_DASHBOARD_APP)
    profiles = _DASHBOARD_APP.bot_data.get("profiles", {}) or {}
    profile = profiles.get(user_id, profiles.get(int(user_id), {}))
    if not isinstance(profile, dict):
        profile = {}
    history = _get_dashboard_messages(context, user_id, limit=500)
    private_messages = [
        message
        for message in history
        if message.get("chat_id") == user_id and message.get("chat_type") == "private"
    ]
    if not profile and not private_messages:
        raise LookupError("User profile not found")

    latest = private_messages[0] if private_messages else {}
    username = str(profile.get("username") or latest.get("username") or "")
    if username.startswith("id"):
        username = ""
    first_name = str(latest.get("first_name") or "")
    last_name = str(latest.get("last_name") or "")
    avatar_data = None
    try:
        chat = await _DASHBOARD_APP.bot.get_chat(int(user_id))
        username = str(getattr(chat, "username", None) or username)
        first_name = str(getattr(chat, "first_name", None) or first_name)
        last_name = str(getattr(chat, "last_name", None) or last_name)
    except TelegramError:
        logging.warning("Could not load Telegram chat identity for dashboard user %s", user_id)
    try:
        photos = await _DASHBOARD_APP.bot.get_user_profile_photos(int(user_id), limit=1)
        if photos.photos:
            photo = photos.photos[0][-1]
            photo_file = await _DASHBOARD_APP.bot.get_file(photo.file_id)
            if photo_file.file_size is None or photo_file.file_size <= 1_000_000:
                avatar_bytes = bytes(await photo_file.download_as_bytearray())
                content_type = "image/png" if avatar_bytes.startswith(b"\x89PNG\r\n\x1a\n") else "image/jpeg"
                avatar_data = f"data:{content_type};base64,{base64.b64encode(avatar_bytes).decode('ascii')}"
            else:
                logging.warning("Dashboard user avatar exceeds size limit for user %s", user_id)
    except TelegramError:
        logging.warning("Could not load Telegram profile photo for dashboard user %s", user_id)

    display_name = str(
        profile.get("user_nickname")
        or " ".join(part for part in (first_name, last_name) if part)
        or (f"@{username}" if username else f"Пользователь {user_id}")
    )
    ban_info = (_DASHBOARD_APP.bot_data.get("banned_users", {}) or {}).get(user_id)
    effective_level = _effective_admin_level(profile)
    actor_profiles = _DASHBOARD_APP.bot_data.get("profiles", {}) or {}
    actor_profile = actor_profiles.get(actor_id, actor_profiles.get(int(actor_id), {}))
    actor_level = _effective_admin_level(actor_profile)
    prefix_values = profile.get("prefixes")
    if not isinstance(prefix_values, list):
        prefix_values = str(profile.get("prefix") or "").split(",")
    stored_prefixes = [str(prefix).strip() for prefix in prefix_values if str(prefix).strip()]
    prefix_options = _prefix_options()
    prefixes = [
        key
        for key, label in prefix_options
        if key in stored_prefixes or label in stored_prefixes
    ]
    return {
        "user_id": user_id,
        "username": username or None,
        "display_name": display_name,
        "avatar": avatar_data,
        "is_banned": is_user_banned(context, user_id),
        "ban_info": ban_info,
        "blocked_bot": bool(profile.get("bot_blocked_by_user", False)),
        "admin_level": effective_level,
        "admin_rank": str(profile.get("admin_rank") or ""),
        "has_admin_rights": effective_level > 0,
        "can_manage_levels": actor_level >= 4,
        "max_assignable_level": 5 if actor_level >= 5 else 3,
        "can_manage_prefixes": actor_level >= 5 and effective_level > 0,
        "prefixes": prefixes,
        "prefix_labels": stored_prefixes,
        "prefix_options": [{"value": value, "label": label} for value, label in prefix_options],
        "profile": {
            "id_profile": profile.get("id_profile"),
            "nickname": profile.get("user_nickname"),
            "registration_date": profile.get("date_registration"),
            "last_admin_tag": profile.get("last_admin_tag"),
        },
        "history": _get_user_action_history(context, user_id, limit=50),
        "sessions": _get_user_session_history(context, user_id=user_id, limit=500),
    }


async def _dashboard_sessions(query: str = "") -> list[dict]:
    if _DASHBOARD_APP is None:
        raise RuntimeError("Bot application is not ready")
    context = SimpleNamespace(application=_DASHBOARD_APP)
    profiles = _DASHBOARD_APP.bot_data.get("profiles", {}) or {}
    sessions = _get_user_session_history(context, limit=500)
    normalized_query = query.casefold().strip()
    result = []
    for session in sessions:
        profile = profiles.get(str(session["user_id"]), {}) or {}
        user_name = str(profile.get("user_nickname") or profile.get("username") or f"ID {session['user_id']}")
        if normalized_query and normalized_query not in " ".join(
            (str(session["user_id"]), user_name, str(session.get("admin_name") or ""))
        ).casefold():
            continue
        session["user_name"] = user_name
        session["topic_url"] = _topic_url(
            int(session["chat_id"]) if session.get("chat_id") else None,
            int(session["topic_id"]) if session.get("topic_id") else None,
        )
        result.append(session)
    return result


async def _dashboard_session_messages(session_id: str) -> dict:
    if _DASHBOARD_APP is None:
        raise RuntimeError("Bot application is not ready")
    context = SimpleNamespace(application=_DASHBOARD_APP)
    session = next(
        (
            item
            for item in _get_user_session_history(context, limit=500)
            if item["session_id"] == session_id
        ),
        None,
    )
    if not session:
        raise LookupError("Session not found")
    return {
        "session": session,
        "messages": _get_user_session_messages(context, session_id, limit=200),
    }


async def _dashboard_session_action(payload: dict) -> dict:
    if _DASHBOARD_APP is None:
        raise RuntimeError("Bot application is not ready")
    context = SimpleNamespace(application=_DASHBOARD_APP, bot=_DASHBOARD_APP.bot)
    session_id = payload["session_id"]
    session = next(
        (
            item
            for item in _get_user_session_history(context, limit=500)
            if item["session_id"] == session_id
        ),
        None,
    )
    if not session:
        raise LookupError("Session not found")
    active = (
        _DASHBOARD_APP.bot_data.get("active_chats", {}) or {}
    ).get(str(session["user_id"]))
    if (
        session["status"] != "active"
        or not active
        or not active.get("active")
        or str(active.get("session_id") or "") != session_id
    ):
        raise PermissionError("session_inactive")
    if session.get("chat_id") is None or session.get("topic_id") is None:
        raise ValueError("session_topic_unavailable")

    if payload["action"] == "send":
        text = payload["text"].strip()
        if not text or len(text) > 4000:
            raise ValueError("Message text must contain 1 to 4000 characters")
        message = await _DASHBOARD_APP.bot.send_message(
            chat_id=int(session["chat_id"]),
            message_thread_id=int(session["topic_id"]),
            text=f"{_TECHNICAL_MESSAGE_PREFIX}{text}",
        )
        _record_user_session_message(
            context,
            active,
            message,
            actor=SimpleNamespace(
                id=payload["_admin_id"],
                full_name=f"Технический раздел · ID {payload['_admin_id']}",
            ),
            direction="dashboard",
        )
        return {"message_id": str(message.message_id)}

    if payload["action"] == "delete":
        message_id = payload["message_id"]
        messages = _get_user_session_messages(context, session_id, limit=200)
        if not any(
            item["message_id"] == message_id and not item["deleted"]
            for item in messages
        ):
            raise LookupError("Session message not found")
        await _DASHBOARD_APP.bot.delete_message(
            chat_id=int(session["chat_id"]),
            message_id=int(message_id),
        )
        if not _mark_user_session_message_deleted(context, session_id, message_id):
            raise RuntimeError("Deleted session message was not updated in history")
        return {"message_id": message_id}
    raise ValueError("Unsupported session action")


async def _dashboard_database_action(payload: dict) -> dict:
    if _DASHBOARD_APP is None:
        raise RuntimeError("Bot application is not ready")
    context = SimpleNamespace(application=_DASHBOARD_APP, bot=_DASHBOARD_APP.bot)
    user_id = payload["user_id"]
    actor_id = payload["_admin_id"]
    profiles = _DASHBOARD_APP.bot_data.setdefault("profiles", {})
    profile = profiles.get(user_id, profiles.get(int(user_id)))
    if profile is not None:
        profiles[user_id] = profile
    if not profile and not any(
        item.get("chat_id") == user_id and item.get("chat_type") == "private"
        for item in _get_dashboard_messages(context, user_id, limit=500)
    ):
        raise LookupError("User profile not found")
    if str(user_id) == str(actor_id):
        raise PermissionError("self_management_not_allowed")

    actor_profile = profiles.get(actor_id, profiles.get(int(actor_id), {}))
    actor_level = _effective_admin_level(actor_profile)
    if payload["action"] == "change_level":
        profile = profile or _ensure_profile(context, user_id)
        level = payload["level"]
        if level not in range(0, 6):
            raise ValueError("invalid_admin_level")
        old_level = _effective_admin_level(profile)
        if actor_level < 4:
            raise PermissionError("insufficient_admin_level")
        if actor_level == 4 and level not in {0, 1, 2, 3}:
            raise PermissionError("level_four_limit")
        if level == 0:
            mode = payload.get("demotion_mode")
            if old_level <= 0 or mode not in {"silent", "notify_ban"}:
                raise ValueError("invalid_demotion")
            await _finish_admin_demotion(
                context,
                user_id,
                str(profile.get("username") or user_id),
                mode == "notify_ban",
                actor_id,
            )
            _record_user_action(
                context,
                user_id,
                "Снятие прав администратора",
                actor_id=actor_id,
                details=(
                    "С уведомлением и блокировкой на 14 дней"
                    if mode == "notify_ban"
                    else "Тихое снятие без блокировки"
                ),
            )
            return {"success": True, "message": "Админ-права сняты."}

        rank = ADMIN_LEVEL_TITLES.get(level)
        if not rank:
            raise ValueError("invalid_admin_level")
        profile["admin_level"] = level
        profile["admin_rank"] = rank
        if not str(profile.get("prefix") or "").strip():
            default_prefix = _admin_default_prefix(level)
            if default_prefix:
                profile["prefix"] = default_prefix
        _save_profile_record(context, user_id)
        _record_user_action(
            context,
            user_id,
            "Изменение уровня администратора",
            actor_id=actor_id,
            details=f"Уровень {old_level} → {level} ({rank})",
        )
        notification_warning = None
        try:
            await context.bot.send_message(
                chat_id=int(user_id),
                text=f"В техническом разделе изменён ваш уровень админ-прав: {level} ({rank}).",
            )
        except TelegramError:
            logging.exception("Could not notify user %s about admin level change", user_id)
            _set_user_blocked_bot_state(context, user_id, True)
            notification_warning = "Права изменены, но уведомление в ЛС отправить не удалось."

        if payload.get("send_briefing"):
            briefing = (
                "📇Придумайте для своего персонажа уникальный админ тег, который пользователи "
                "в будущем будут видеть и могут выбрать вас.\nПример тега: #неистовый или #акира"
                if level == 1
                else (
                    "Инструктаж администратора: соблюдайте правила бота, общайтесь уважительно "
                    "и обращайтесь в технический раздел, если возник вопрос по доступам."
                )
            )
            try:
                await context.bot.send_message(chat_id=int(user_id), text=briefing)
            except TelegramError:
                logging.exception("Could not send admin briefing to user %s", user_id)
                _set_user_blocked_bot_state(context, user_id, True)
                notification_warning = "Права изменены, но отправить инструктаж в ЛС не удалось."
        return {
            "success": True,
            "message": f"Уровень прав изменён на {level}.",
            "warning": notification_warning,
        }

    if payload["action"] == "set_prefixes":
        if actor_level < 5:
            raise PermissionError("prefix_management_requires_level_five")
        profile = profile or _ensure_profile(context, user_id)
        if _effective_admin_level(profile) <= 0:
            raise PermissionError("target_is_not_admin")
        options = dict(_prefix_options())
        selected = payload["prefixes"]
        if not isinstance(selected, list) or len(selected) > 3 or any(
            not isinstance(value, str) or value not in options for value in selected
        ):
            raise ValueError("invalid_prefixes")
        labels = [options[value] for value in dict.fromkeys(selected)]
        profile["prefixes"] = labels
        profile["prefix"] = ", ".join(labels)
        _save_profile_record(context, user_id)
        _record_user_action(
            context,
            user_id,
            "Изменение префикса",
            actor_id=actor_id,
            details=", ".join(labels) if labels else "Все префиксы сняты",
        )
        notification_warning = None
        try:
            await context.bot.send_message(
                chat_id=int(user_id),
                text=f"В техническом разделе изменён ваш список префиксов: {', '.join(labels) or 'не установлены'}.",
            )
        except TelegramError:
            logging.exception("Could not notify user %s about prefix change", user_id)
            _set_user_blocked_bot_state(context, user_id, True)
            notification_warning = "Префиксы изменены, но уведомление в ЛС отправить не удалось."
        return {"success": True, "message": "Префиксы сохранены.", "warning": notification_warning}

    raise ValueError("Unsupported database action")


async def _dashboard_message_action(payload: dict) -> dict:
    if _DASHBOARD_APP is None:
        raise RuntimeError("Bot application is not ready")
    user_id = payload["user_id"]
    profiles = _DASHBOARD_APP.bot_data.get("profiles", {}) or {}
    action = payload["action"]
    context = SimpleNamespace(application=_DASHBOARD_APP)
    if user_id not in profiles:
        known_private_chat = any(
            item.get("chat_id") == user_id and item.get("chat_type") == "private"
            for item in _get_dashboard_messages(context, user_id, limit=500)
        )
        if not known_private_chat:
            raise LookupError("User profile not found")

    if action == "send":
        text = payload["text"].strip()
        if text.startswith(_TECHNICAL_MESSAGE_PREFIX):
            text = text[len(_TECHNICAL_MESSAGE_PREFIX):].lstrip()
        if not text or len(text) > 4000:
            raise ValueError("Message text must contain 1 to 4000 characters")
        message = await _DASHBOARD_APP.bot.send_message(
            chat_id=int(user_id),
            text=f"{_TECHNICAL_MESSAGE_PREFIX}{text}",
        )
        logging.info(
            "Dashboard admin %s sent a technical message to user %s",
            payload["_admin_id"],
            user_id,
        )
        return {"message_id": message.message_id}

    message_id = payload["message_id"]
    history = _get_dashboard_messages(context, user_id, limit=500)
    record = next(
        (
            item
            for item in history
            if item.get("telegram_message_id") == str(message_id)
            and item.get("chat_id") == user_id
            and item.get("chat_type") == "private"
        ),
        None,
    )
    if not record:
        raise LookupError("Private chat message not found")

    if action == "delete":
        await _DASHBOARD_APP.bot.delete_message(
            chat_id=int(user_id),
            message_id=int(message_id),
        )
        updated = _update_dashboard_message_history(
            context,
            user_id,
            message_id,
            deleted=True,
        )
        if not updated:
            raise RuntimeError("Deleted message was not updated in history")
        logging.info(
            "Dashboard admin %s deleted a message for user %s",
            payload["_admin_id"],
            user_id,
        )
        return {"message_id": str(message_id)}

    raise ValueError("Unsupported message action")


def _run_dashboard_coroutine(coroutine):
    loop = _BOT_LOOP
    if loop is None or not loop.is_running():
        coroutine.close()
        raise RuntimeError("Bot is not running")
    future = asyncio.run_coroutine_threadsafe(coroutine, loop)
    return future.result(timeout=20)


def _authorized_dashboard_session(token: str | None) -> dict | None:
    session = WEB_AUTH_STORE.get_session(token)
    if not session or _DASHBOARD_APP is None:
        return None
    profiles = _DASHBOARD_APP.bot_data.get("profiles", {}) or {}
    user_id = str(session["telegram_user_id"])
    profile = profiles.get(user_id, profiles.get(session["telegram_user_id"], {}))
    if not isinstance(profile, dict):
        profile = {}
    prefixes = profile.get("prefixes")
    if not isinstance(prefixes, list):
        prefixes = str(profile.get("prefix") or "").split(",")
    if (
        _effective_admin_level(profile) < 4
        or "👁Logs" not in {str(prefix).strip() for prefix in prefixes}
    ):
        WEB_AUTH_STORE.revoke_session(token)
        return None
    return session


class _HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        parsed_path = urlsplit(self.path)
        if parsed_path.path == "/healthz":
            body = b"OK"
            content_type = "text/plain; charset=utf-8"
        elif parsed_path.path == "/":
            try:
                body = (Path(__file__).resolve().parents[1] / "site" / "index.html").read_bytes()
            except OSError:
                logging.exception("Failed to load website homepage")
                self.send_error(500, "Homepage unavailable")
                return
            content_type = "text/html; charset=utf-8"
        elif parsed_path.path == "/api/auth/status":
            query = parse_qs(parsed_path.query)
            request_id = query.get("id", [""])[0]
            if not request_id or len(request_id) > 64:
                self._send_json(400, {"error": "invalid_request"})
                return
            browser_token = self._cookie_value(WEB_AUTH_BROWSER_COOKIE)
            status, session_token, account = WEB_AUTH_STORE.poll_request(
                request_id,
                browser_token,
            )
            if status == "invalid":
                self._send_json(403, {"error": "invalid_request"})
                return
            if status == "expired":
                self._send_json(200, {"status": "expired"})
                return
            headers = {}
            if session_token:
                headers["Set-Cookie"] = self._cookie_header(
                    WEB_AUTH_SESSION_COOKIE,
                    session_token,
                    max_age=12 * 60 * 60,
                )
            self._send_json(200, {"status": status, "account": account}, headers=headers)
            return
        elif parsed_path.path == "/api/auth/session":
            session = _authorized_dashboard_session(
                self._cookie_value(WEB_AUTH_SESSION_COOKIE)
            )
            self._send_json(200, {"authenticated": session is not None, "account": session})
            return
        elif parsed_path.path == "/api/auth/avatar":
            session_token = self._cookie_value(WEB_AUTH_SESSION_COOKIE)
            if not _authorized_dashboard_session(session_token):
                self._send_json(401, {"error": "authentication_required"})
                return
            avatar = WEB_AUTH_STORE.get_session_avatar(session_token)
            if not avatar:
                self.send_error(404, "Avatar unavailable")
                return
            if avatar.startswith(b"\x89PNG\r\n\x1a\n"):
                content_type = "image/png"
            elif avatar.startswith(b"\xff\xd8\xff"):
                content_type = "image/jpeg"
            else:
                self.send_error(415, "Unsupported avatar format")
                return
            self._send_body(200, avatar, content_type)
            return
        elif parsed_path.path == "/api/dashboard/command":
            if not _authorized_dashboard_session(
                self._cookie_value(WEB_AUTH_SESSION_COOKIE)
            ):
                self._send_json(401, {"error": "authentication_required"})
                return
            command = parse_qs(parsed_path.query).get("command", [""])[0].strip().lower()
            if command == "help":
                output = [
                    "Доступно только чтение:",
                    "status — состояние бота",
                    "logs — последние предупреждения и ошибки",
                    "help — эта справка",
                ]
            elif command == "status":
                if _BOT_STARTED_AT is None:
                    output = ["Бот не запущен."]
                else:
                    uptime = int(time.monotonic() - _BOT_STARTED_AT)
                    hours, remainder = divmod(uptime, 3600)
                    minutes, seconds = divmod(remainder, 60)
                    output = [
                        "Бот запущен.",
                        f"Время работы: {hours:02}:{minutes:02}:{seconds:02}",
                    ]
            elif command == "logs":
                output = logging_setup.get_recent_dashboard_logs()
                if not output:
                    output = ["Предупреждений и ошибок пока нет."]
            else:
                self._send_json(400, {"error": "unsupported_command"})
                return
            self._send_json(200, {"command": command, "output": output})
            return
        elif parsed_path.path == "/api/dashboard/users":
            if not _authorized_dashboard_session(
                self._cookie_value(WEB_AUTH_SESSION_COOKIE)
            ):
                self._send_json(401, {"error": "authentication_required"})
                return
            query = parse_qs(parsed_path.query).get("query", [""])[0].strip()
            if len(query) > 100:
                self._send_json(400, {"error": "query_too_long"})
                return
            offset_text = parse_qs(parsed_path.query).get("offset", ["0"])[0]
            if not offset_text.isdigit() or len(offset_text) > 8:
                self._send_json(400, {"error": "invalid_offset"})
                return
            try:
                users = _run_dashboard_coroutine(
                    _dashboard_users(query, int(offset_text))
                )
            except TimeoutError:
                self._send_json(504, {"error": "dashboard_timeout"})
                return
            except Exception:
                logging.exception("Failed to load dashboard user list")
                self._send_json(503, {"error": "dashboard_unavailable"})
                return
            self._send_json(200, {"users": users})
            return
        elif parsed_path.path == "/api/dashboard/database/user":
            dashboard_session = _authorized_dashboard_session(
                self._cookie_value(WEB_AUTH_SESSION_COOKIE)
            )
            if not dashboard_session:
                self._send_json(401, {"error": "authentication_required"})
                return
            user_id = parse_qs(parsed_path.query).get("user_id", [""])[0].strip()
            if not user_id.isdigit() or len(user_id) > 20:
                self._send_json(400, {"error": "invalid_user_id"})
                return
            try:
                user = _run_dashboard_coroutine(
                    _dashboard_user_details(
                        user_id,
                        str(dashboard_session["telegram_user_id"]),
                    )
                )
            except LookupError:
                self._send_json(404, {"error": "user_not_found"})
                return
            except TimeoutError:
                self._send_json(504, {"error": "dashboard_timeout"})
                return
            except Exception:
                logging.exception("Failed to load dashboard database user %s", user_id)
                self._send_json(503, {"error": "dashboard_unavailable"})
                return
            self._send_json(200, {"user": user})
            return
        elif parsed_path.path == "/api/dashboard/sessions":
            if not _authorized_dashboard_session(
                self._cookie_value(WEB_AUTH_SESSION_COOKIE)
            ):
                self._send_json(401, {"error": "authentication_required"})
                return
            query = parse_qs(parsed_path.query).get("query", [""])[0].strip()
            if len(query) > 100:
                self._send_json(400, {"error": "query_too_long"})
                return
            try:
                sessions = _run_dashboard_coroutine(_dashboard_sessions(query))
            except TimeoutError:
                self._send_json(504, {"error": "dashboard_timeout"})
                return
            except Exception:
                logging.exception("Failed to load dashboard sessions")
                self._send_json(503, {"error": "dashboard_unavailable"})
                return
            self._send_json(200, {"sessions": sessions})
            return
        elif parsed_path.path.startswith("/api/dashboard/sessions/") and parsed_path.path.endswith("/messages"):
            if not _authorized_dashboard_session(
                self._cookie_value(WEB_AUTH_SESSION_COOKIE)
            ):
                self._send_json(401, {"error": "authentication_required"})
                return
            session_id = parsed_path.path.removeprefix("/api/dashboard/sessions/").removesuffix("/messages")
            if not session_id.isalnum() or len(session_id) > 64:
                self._send_json(400, {"error": "invalid_session_id"})
                return
            try:
                result = _run_dashboard_coroutine(
                    _dashboard_session_messages(session_id)
                )
            except LookupError:
                self._send_json(404, {"error": "session_not_found"})
                return
            except TimeoutError:
                self._send_json(504, {"error": "dashboard_timeout"})
                return
            except Exception:
                logging.exception("Failed to load dashboard session %s", session_id)
                self._send_json(503, {"error": "dashboard_unavailable"})
                return
            self._send_json(200, result)
            return
        elif parsed_path.path.startswith("/api/dashboard/users/") and parsed_path.path.endswith("/messages"):
            if not _authorized_dashboard_session(
                self._cookie_value(WEB_AUTH_SESSION_COOKIE)
            ):
                self._send_json(401, {"error": "authentication_required"})
                return
            user_id = parsed_path.path.removeprefix("/api/dashboard/users/").removesuffix("/messages")
            if not user_id.isdigit() or len(user_id) > 20:
                self._send_json(400, {"error": "invalid_user_id"})
                return
            try:
                messages = _run_dashboard_coroutine(_dashboard_messages(user_id))
            except TimeoutError:
                self._send_json(504, {"error": "dashboard_timeout"})
                return
            except Exception:
                logging.exception("Failed to load dashboard messages for user %s", user_id)
                self._send_json(503, {"error": "dashboard_unavailable"})
                return
            self._send_json(200, {"messages": messages})
            return
        elif parsed_path.path.startswith("/api/dashboard/users/") and parsed_path.path.endswith("/actions"):
            if not _authorized_dashboard_session(
                self._cookie_value(WEB_AUTH_SESSION_COOKIE)
            ):
                self._send_json(401, {"error": "authentication_required"})
                return
            user_id = parsed_path.path.removeprefix("/api/dashboard/users/").removesuffix("/actions")
            if not user_id.isdigit() or len(user_id) > 20:
                self._send_json(400, {"error": "invalid_user_id"})
                return
            try:
                actions = _run_dashboard_coroutine(_dashboard_actions(user_id))
            except LookupError:
                self._send_json(404, {"error": "chat_or_message_not_found"})
                return
            except TimeoutError:
                self._send_json(504, {"error": "dashboard_timeout"})
                return
            except Exception:
                logging.exception("Failed to load dashboard action history for user %s", user_id)
                self._send_json(503, {"error": "dashboard_unavailable"})
                return
            self._send_json(200, {"actions": actions})
            return
        else:
            self.send_error(404)
            return

        self._send_body(200, body, content_type)

    def do_POST(self) -> None:
        parsed_path = urlsplit(self.path)
        dashboard_message_action = parsed_path.path == "/api/dashboard/messages"
        dashboard_database_action = parsed_path.path == "/api/dashboard/database/actions"
        dashboard_session_action = parsed_path.path == "/api/dashboard/sessions/actions"
        dashboard_action = (
            dashboard_message_action
            or dashboard_database_action
            or dashboard_session_action
        )
        if parsed_path.path not in {"/api/auth/start", "/api/auth/logout"} and not dashboard_action:
            self.send_error(404)
            return
        if not self._same_origin_request():
            self._send_json(403, {"error": "origin_rejected"})
            return
        if dashboard_action:
            dashboard_session = _authorized_dashboard_session(
                self._cookie_value(WEB_AUTH_SESSION_COOKIE)
            )
            if not dashboard_session:
                self._send_json(401, {"error": "authentication_required"})
                return
            length = self._content_length()
            if length <= 0 or length > 12_000:
                self._send_json(400, {"error": "invalid_body_size"})
                return
            try:
                payload = json.loads(self.rfile.read(length))
            except (json.JSONDecodeError, UnicodeDecodeError):
                self._send_json(400, {"error": "invalid_json"})
                return
            if not isinstance(payload, dict):
                self._send_json(400, {"error": "invalid_payload"})
                return
            payload["_admin_id"] = str(dashboard_session["telegram_user_id"])
            action = payload.get("action")
            if dashboard_message_action:
                user_id = payload.get("user_id")
                if (
                    action not in {"send", "delete"}
                    or not isinstance(user_id, str)
                    or not user_id.isdigit()
                    or len(user_id) > 20
                ):
                    self._send_json(400, {"error": "invalid_action"})
                    return
                if action == "send" and (
                    not isinstance(payload.get("text"), str)
                    or len(payload["text"]) > 4096
                ):
                    self._send_json(400, {"error": "invalid_text"})
                    return
                if action == "delete" and (
                    not str(payload.get("message_id", "")).isdigit()
                    or len(str(payload.get("message_id", ""))) > 20
                ):
                    self._send_json(400, {"error": "invalid_message_id"})
                    return
                coroutine = _dashboard_message_action(payload)
            elif dashboard_database_action:
                user_id = payload.get("user_id")
                if (
                    action not in {"change_level", "set_prefixes"}
                    or not isinstance(user_id, str)
                    or not user_id.isdigit()
                    or len(user_id) > 20
                ):
                    self._send_json(400, {"error": "invalid_action"})
                    return
                if action == "change_level" and (
                    type(payload.get("level")) is not int
                    or payload["level"] < 0
                    or payload["level"] > 5
                    or not isinstance(payload.get("send_briefing"), bool)
                    or (
                        payload["level"] == 0
                        and payload.get("demotion_mode") not in {"silent", "notify_ban"}
                    )
                ):
                    self._send_json(400, {"error": "invalid_admin_level"})
                    return
                if action == "set_prefixes" and not isinstance(payload.get("prefixes"), list):
                    self._send_json(400, {"error": "invalid_prefixes"})
                    return
                coroutine = _dashboard_database_action(payload)
            else:
                session_id = payload.get("session_id")
                if (
                    action not in {"send", "delete"}
                    or not isinstance(session_id, str)
                    or not session_id.isalnum()
                    or len(session_id) > 64
                ):
                    self._send_json(400, {"error": "invalid_action"})
                    return
                if action == "send" and (
                    not isinstance(payload.get("text"), str)
                    or len(payload["text"]) > 4000
                ):
                    self._send_json(400, {"error": "invalid_text"})
                    return
                if action == "delete" and (
                    not str(payload.get("message_id", "")).isdigit()
                    or len(str(payload.get("message_id", ""))) > 20
                ):
                    self._send_json(400, {"error": "invalid_message_id"})
                    return
                coroutine = _dashboard_session_action(payload)
            try:
                result = _run_dashboard_coroutine(coroutine)
            except LookupError:
                error = "session_not_found" if dashboard_session_action else "chat_or_message_not_found"
                self._send_json(404, {"error": error})
                return
            except PermissionError as error:
                self._send_json(403, {"error": str(error)})
                return
            except ValueError as error:
                self._send_json(400, {"error": str(error)})
                return
            except TimeoutError:
                self._send_json(504, {"error": "telegram_timeout"})
                return
            except Exception:
                logging.exception("Dashboard Telegram action failed")
                self._send_json(502, {"error": "telegram_action_failed"})
                return
            self._send_json(200, {"ok": True, **result})
            return
        if self._content_length() != 0:
            self._send_json(400, {"error": "unexpected_body"})
            return

        if parsed_path.path == "/api/auth/logout":
            WEB_AUTH_STORE.revoke_session(
                self._cookie_value(WEB_AUTH_SESSION_COOKIE)
            )
            self._send_body(
                204,
                b"",
                "text/plain; charset=utf-8",
                headers={
                    "Set-Cookie": self._cookie_header(
                        WEB_AUTH_SESSION_COOKIE,
                        "",
                        max_age=0,
                    )
                },
            )
            return

        username = WEB_AUTH_STORE.bot_username
        if not username:
            self._send_json(503, {"error": "telegram_login_unavailable"})
            return

        browser_token = self._cookie_value(WEB_AUTH_BROWSER_COOKIE)
        result, browser_token, status = WEB_AUTH_STORE.create_request(
            self.client_address[0],
            browser_token,
        )
        if status == "rate_limited":
            self._send_json(429, {"error": "too_many_requests"})
            return
        if not result or not browser_token:
            self._send_json(500, {"error": "request_creation_failed"})
            return

        bot_url = f"https://t.me/{username}?start=web_{result['request_id']}"
        self._send_json(
            200,
            {
                **result,
                "bot_url": bot_url,
                "expires_in": 300,
            },
            headers={
                "Set-Cookie": self._cookie_header(
                    WEB_AUTH_BROWSER_COOKIE,
                    browser_token,
                    max_age=300,
                )
            },
        )

    def _content_length(self) -> int:
        value = self.headers.get("Content-Length", "0")
        if not value.isdigit():
            return -1
        return int(value)

    def _cookie_value(self, name: str) -> str | None:
        cookies = http.cookies.SimpleCookie()
        try:
            cookies.load(self.headers.get("Cookie", ""))
        except http.cookies.CookieError:
            return None
        morsel = cookies.get(name)
        return morsel.value if morsel else None

    def _same_origin_request(self) -> bool:
        origin = self.headers.get("Origin")
        host = self.headers.get("Host")
        if not origin or not host:
            return False
        parsed_origin = urlsplit(origin)
        return (
            parsed_origin.scheme in {"http", "https"}
            and parsed_origin.netloc.casefold() == host.casefold()
        )

    def _cookie_header(self, name: str, value: str, *, max_age: int) -> str:
        cookie = f"{name}={value}; Path=/; HttpOnly; SameSite=Lax; Max-Age={max_age}"
        forwarded_proto = self.headers.get("X-Forwarded-Proto", "").split(",", 1)[0].strip()
        if forwarded_proto == "https":
            cookie += "; Secure"
        return cookie

    def _send_json(
        self,
        status: int,
        payload: dict,
        *,
        headers: dict[str, str] | None = None,
    ) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send_body(
            status,
            body,
            "application/json; charset=utf-8",
            headers=headers,
        )

    def _send_body(
        self,
        status: int,
        body: bytes,
        content_type: str,
        *,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; base-uri 'none'; frame-ancestors 'none'; "
            "form-action 'self'; script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; connect-src 'self'",
        )
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        if body:
            self.wfile.write(body)

    def log_message(self, format: str, *args) -> None:  # noqa: A002 - stdlib signature
        pass


def _start_health_check_server() -> None:
    """Start the public health endpoint on the deployment port."""
    port = int(os.environ.get("PORT", "10000"))
    server = HTTPServer(("0.0.0.0", port), _HealthCheckHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
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
            "chat_member",
        ],
        close_loop=False,
    )
