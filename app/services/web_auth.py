"""Short-lived Telegram approvals and browser sessions for the website."""
import hashlib
import hmac
import secrets
import threading
import time


AUTH_REQUEST_TTL_SECONDS = 5 * 60
WEB_SESSION_TTL_SECONDS = 12 * 60 * 60
_BROWSER_COOKIE_NAME = "web_auth_browser"
_SESSION_COOKIE_NAME = "web_auth_session"


class WebAuthStore:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._bot_username: str | None = None
        self._requests: dict[str, dict] = {}
        self._sessions: dict[str, dict] = {}
        self._start_attempts: dict[str, list[float]] = {}

    @property
    def bot_username(self) -> str | None:
        with self._lock:
            return self._bot_username

    def set_bot_username(self, username: str | None) -> None:
        with self._lock:
            self._bot_username = username.lstrip("@") if username else None

    @staticmethod
    def _digest(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    def create_request(
        self,
        client_key: str,
        browser_token: str | None,
    ) -> tuple[dict | None, str | None, str]:
        now = time.time()
        with self._lock:
            attempts = [stamp for stamp in self._start_attempts.get(client_key, []) if now - stamp < 60]
            if len(attempts) >= 10:
                self._start_attempts[client_key] = attempts
                return None, None, "rate_limited"
            attempts.append(now)
            self._start_attempts[client_key] = attempts

            if not browser_token or len(browser_token) > 128:
                browser_token = secrets.token_urlsafe(32)
            request_id = secrets.token_urlsafe(24)
            verification_code = secrets.token_hex(3).upper()
            request = {
                "status": "pending",
                "browser_digest": self._digest(browser_token),
                "verification_code": verification_code,
                "created_at": now,
                "expires_at": now + AUTH_REQUEST_TTL_SECONDS,
                "telegram_user_id": None,
                "username": None,
                "display_name": None,
                "session_token": None,
            }
            self._requests[request_id] = request
            self._prune(now)
            return {"request_id": request_id, "verification_code": verification_code}, browser_token, "ok"

    def _prune(self, now: float) -> None:
        expired_requests = [
            request_id
            for request_id, request in self._requests.items()
            if request["expires_at"] <= now
        ]
        for request_id in expired_requests:
            self._requests.pop(request_id, None)

        expired_sessions = [
            token for token, session in self._sessions.items()
            if session["expires_at"] <= now
        ]
        for token in expired_sessions:
            self._sessions.pop(token, None)

        for client_key, attempts in list(self._start_attempts.items()):
            recent = [stamp for stamp in attempts if now - stamp < 60]
            if recent:
                self._start_attempts[client_key] = recent
            else:
                self._start_attempts.pop(client_key, None)

    def claim_request(self, request_id: str, user_id: int) -> tuple[str, str | None]:
        now = time.time()
        with self._lock:
            self._prune(now)
            request = self._requests.get(request_id)
            if not request or request["status"] != "pending":
                return "expired", None
            claimed_by = request["telegram_user_id"]
            if claimed_by is not None and claimed_by != user_id:
                return "claimed", None
            request["telegram_user_id"] = user_id
            return "ok", request["verification_code"]

    def finish_request(
        self,
        request_id: str,
        user_id: int,
        *,
        approved: bool,
        username: str | None = None,
        display_name: str | None = None,
        avatar: bytes | None = None,
    ) -> bool:
        now = time.time()
        with self._lock:
            self._prune(now)
            request = self._requests.get(request_id)
            if (
                not request
                or request["status"] != "pending"
                or request["telegram_user_id"] != user_id
            ):
                return False
            request["status"] = "approved" if approved else "denied"
            if approved:
                request["username"] = username
                request["display_name"] = display_name
                request["avatar"] = avatar
            return True

    def poll_request(
        self,
        request_id: str,
        browser_token: str | None,
    ) -> tuple[str, str | None, dict | None]:
        now = time.time()
        with self._lock:
            self._prune(now)
            request = self._requests.get(request_id)
            if not request or not browser_token:
                return "expired", None, None
            if not hmac.compare_digest(request["browser_digest"], self._digest(browser_token)):
                return "invalid", None, None
            if request["expires_at"] <= now:
                return "expired", None, None

            session_token = None
            if request["status"] == "approved":
                session_token = request["session_token"]
                if not session_token:
                    session_token = secrets.token_urlsafe(32)
                    request["session_token"] = session_token
                    self._sessions[session_token] = {
                        "telegram_user_id": request["telegram_user_id"],
                        "username": request["username"],
                        "display_name": request["display_name"],
                        "avatar": request["avatar"],
                        "expires_at": now + WEB_SESSION_TTL_SECONDS,
                    }
            session = (
                {
                    "telegram_user_id": request["telegram_user_id"],
                    "username": request["username"],
                    "display_name": request["display_name"],
                    "avatar_available": bool(request["avatar"]),
                }
                if request["status"] == "approved"
                else None
            )
            return request["status"], session_token, session

    def get_session(self, token: str | None) -> dict | None:
        if not token:
            return None
        now = time.time()
        with self._lock:
            self._prune(now)
            session = self._sessions.get(token)
            if not session:
                return None
            return {
                "telegram_user_id": session["telegram_user_id"],
                "username": session["username"],
                "display_name": session["display_name"],
                "avatar_available": bool(session["avatar"]),
            }

    def revoke_session(self, token: str | None) -> None:
        if not token:
            return
        with self._lock:
            self._sessions.pop(token, None)

    def get_session_avatar(self, token: str | None) -> bytes | None:
        if not token:
            return None
        now = time.time()
        with self._lock:
            self._prune(now)
            session = self._sessions.get(token)
            return session["avatar"] if session else None


WEB_AUTH_STORE = WebAuthStore()
WEB_AUTH_BROWSER_COOKIE = _BROWSER_COOKIE_NAME
WEB_AUTH_SESSION_COOKIE = _SESSION_COOKIE_NAME
