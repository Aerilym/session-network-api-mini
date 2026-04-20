import logging
import signal

import coloredlogs

from config import config
from price import PriceFetcher

coloredlogs.install(level=config.log_level, milliseconds=True, isatty=True)

log = logging.getLogger("mule_price_fetcher")

if not config.enable_price_fetcher:
    log.info("Price fetcher disabled — mule exiting")
else:
    if config.session_webhook_url:
        from webhook import WebhookLogHandler, install_excepthooks
        logging.getLogger().addHandler(WebhookLogHandler())
        install_excepthooks()
        log.info("Session webhook error reporting enabled")

    fetcher = PriceFetcher(config)
    fetcher.start()

    def _handle_sigterm(signum, frame):
        fetcher.stop()

    signal.signal(signal.SIGTERM, _handle_sigterm)
    signal.signal(signal.SIGINT, _handle_sigterm)

    fetcher.join()  # block until stop() is called or the thread exits
