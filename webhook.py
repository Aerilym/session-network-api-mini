import logging
import sys
import threading
import traceback

import requests

log = logging.getLogger("webhook")


def send_error_webhook(message: str) -> None:
    """Fire-and-forget POST of an error message to the configured Session webhook.

    Returns immediately; the actual request is made in a daemon thread so it
    never blocks the caller. All exceptions inside the thread are silently
    swallowed to avoid cascading failures.
    """
    from config import config

    if not config.session_webhook_url:
        return

    url = config.session_webhook_url
    display_name = config.session_webhook_display_name

    def _send() -> None:
        try:
            requests.post(
                url,
                json={"text": message, "display_name": display_name},
                timeout=10,
            )
        except Exception:
            pass  # never let webhook delivery failure cause further errors

    t = threading.Thread(target=_send, daemon=True, name="webhook-send")
    t.start()


class WebhookLogHandler(logging.Handler):
    """Logging handler that forwards ERROR and above records to the Session webhook."""

    def __init__(self) -> None:
        super().__init__(level=logging.ERROR)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            send_error_webhook(self.format(record))
        except Exception:
            pass  # must not raise inside a log handler


def install_excepthooks() -> None:
    """Install excepthooks that forward unhandled exceptions to the Session webhook.

    Covers:
    - Main thread unhandled exceptions via sys.excepthook
    - Background thread unhandled exceptions via threading.excepthook
    """

    def _format(exc_type, exc_value, exc_tb) -> str:
        lines = traceback.format_exception(exc_type, exc_value, exc_tb)
        return "Unhandled exception:\n" + "".join(lines)

    def _main_excepthook(exc_type, exc_value, exc_tb) -> None:
        # Let KeyboardInterrupt through without sending a webhook.
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        log.critical(_format(exc_type, exc_value, exc_tb))

    def _thread_excepthook(args: threading.ExceptHookArgs) -> None:
        # SystemExit raised inside a thread should not trigger a webhook.
        if args.exc_type is SystemExit:
            return
        log.critical(
            _format(args.exc_type, args.exc_value, args.exc_traceback)
        )

    sys.excepthook = _main_excepthook
    threading.excepthook = _thread_excepthook
