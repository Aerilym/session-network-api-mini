import logging

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

    try:
        import uwsgi
        log.info("Running as uWSGI mule")
        while True:
            uwsgi.mule_msg_recv()  # blocking; loops to handle any incoming messages
    except ImportError:
        log.info("Running outside uWSGI — blocking on price fetcher thread")
        fetcher.join()  # plain blocking loop for local dev
