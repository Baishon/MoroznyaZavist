"""Temporary admin-mute helpers (mute is stored inline on the admin's profile)."""
import time

from telegram import ChatPermissions


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
