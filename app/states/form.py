"""FSM-style stage constants for the admin-candidate onboarding "form".

The bot doesn't use a formal FSM library; each candidate's progress is tracked
as a plain dict stored in ``bot_data["admin_candidate_state"]`` with a
``"stage"`` key. These constants avoid scattering magic strings across
`app.handlers.candidates`, `app.handlers.admin` and `app.services.profiles`.
"""

STAGE_AWAIT_TAG = "await_tag"
STAGE_AWAIT_BIO = "await_bio"
STAGE_AWAIT_TIP_ADMIN = "await_tip_admin"
STAGE_AWAIT_ADMIN_GENDER = "await_admin_gender"
STAGE_CANDIDATE_PROFILE = "candidate_profile"
STAGE_AWAIT_CANDIDATE_IMAGES = "await_candidate_images"
STAGE_AWAIT_CHARACTER = "await_character"
STAGE_PENDING_REVIEW = "pending_review"
STAGE_AWAIT_REJECT_RESTART = "await_reject_restart"

# Stages during which the candidate must finish the form before using the bot.
ACTIVE_CANDIDATE_STAGES = frozenset(
    {
        STAGE_AWAIT_TAG,
        STAGE_AWAIT_BIO,
        STAGE_AWAIT_TIP_ADMIN,
        STAGE_AWAIT_ADMIN_GENDER,
        STAGE_CANDIDATE_PROFILE,
        STAGE_AWAIT_CANDIDATE_IMAGES,
        STAGE_AWAIT_CHARACTER,
        STAGE_PENDING_REVIEW,
        STAGE_AWAIT_REJECT_RESTART,
    }
)

# Stages handled by the private-chat message flow in app.handlers.candidates.
TEXT_INPUT_STAGES = frozenset(
    {
        STAGE_AWAIT_TAG,
        STAGE_AWAIT_BIO,
        STAGE_AWAIT_TIP_ADMIN,
        STAGE_AWAIT_ADMIN_GENDER,
        STAGE_AWAIT_REJECT_RESTART,
        STAGE_AWAIT_CHARACTER,
    }
)
