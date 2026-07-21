"""Logging configuration, including a handler mirroring warnings/errors to Telegram."""
import logging
import time

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
            return _DummyResponse(resp.getcode(), resp.read().decode(errors="ignore"))

from app.config import LOG_CHAT_ID, TOKEN

logging.basicConfig(level=logging.INFO)


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
