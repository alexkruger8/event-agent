"""
Kafka / Redpanda consumer entry point.

Usage:
    python -m app.consumer

Reads KAFKA_BOOTSTRAP_SERVERS from settings, or per-tenant broker settings
from the database.
Runs until SIGTERM or SIGINT.
"""

import logging
import signal
import threading

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)

logger = logging.getLogger(__name__)


def main() -> None:
    from app.database.migrations import ensure_runtime_schema
    from app.database.session import _get_session_local
    from app.security.encryption import ensure_encryption_key
    from app.workers.kafka_consumer import run_consumer

    ensure_runtime_schema()
    db = _get_session_local()()
    try:
        ensure_encryption_key(db)
    finally:
        db.close()
    logger.info("Starting Kafka consumer")

    stop_event = threading.Event()

    def _handle_signal(signum: int, frame: object) -> None:
        logger.info("Received signal %d — shutting down consumer", signum)
        stop_event.set()

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    run_consumer(stop_event)
    logger.info("Consumer exited cleanly")


if __name__ == "__main__":
    main()
