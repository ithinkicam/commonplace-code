"""Entry point: python -m commonplace_worker

Opens the database, runs migrations, then starts the polling loop.
"""

from __future__ import annotations

import logging
import os
import sys

from commonplace_db import connect, migrate
from commonplace_worker.worker import HANDLERS, run_forever

# ---------------------------------------------------------------------------
# Logging — structured single-line format, level from env
# ---------------------------------------------------------------------------

_LOG_LEVEL = os.environ.get("COMMONPLACE_LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    stream=sys.stdout,
    level=_LOG_LEVEL,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%SZ",
)

logger = logging.getLogger(__name__)


def main() -> None:
    from commonplace_db import DB_PATH

    db_path = os.environ.get("COMMONPLACE_DB_PATH", DB_PATH)
    logger.info(
        "commonplace-worker starting — db=%s handlers=%s",
        db_path,
        sorted(HANDLERS.keys()),
    )

    conn = connect(db_path)
    version = migrate(conn)
    logger.info("schema version %d", version)

    run_forever(conn, HANDLERS)


if __name__ == "__main__":
    main()
