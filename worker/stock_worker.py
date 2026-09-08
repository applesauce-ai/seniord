"""Background stock worker.

Polls the outbox for `order.accepted` events and reduces stock. Runs as its own
process (its own Compose service) so it can be paused/resumed independently of
the API — which is exactly how the outage/catch-up scenario is demonstrated:

    docker compose stop worker    # orders keep being accepted; events pile up
    docker compose start worker   # worker drains the backlog and stock catches up
"""

from __future__ import annotations

import logging
import signal
import time
from types import FrameType

from app.config import settings
from app.db.pool import close_pool
from app.services import stock_service

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [stock-worker] %(message)s",
)
log = logging.getLogger("stock_worker")

_running = True


def _request_stop(signum: int, frame: FrameType | None) -> None:
    global _running
    _running = False
    log.info("stop signal received; finishing current cycle")


def main() -> None:
    signal.signal(signal.SIGINT, _request_stop)
    signal.signal(signal.SIGTERM, _request_stop)

    log.info(
        "started (poll=%.1fs, batch=%d)",
        settings.worker_poll_interval,
        settings.worker_batch_size,
    )

    while _running:
        try:
            processed = stock_service.process_available(settings.worker_batch_size)
            if processed:
                log.info("processed %d event(s)", processed)
        except Exception:  # keep the loop alive; the events stay pending and retry
            log.exception("error while processing events; will retry next cycle")

        time.sleep(settings.worker_poll_interval)

    close_pool()
    log.info("stopped")


if __name__ == "__main__":
    main()
