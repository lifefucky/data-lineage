"""CLI logging helpers for live GP commands."""
from __future__ import annotations

import logging


def configure_live_logging(level: int = logging.INFO) -> None:
    """INFO progress for our packages; mute paramiko/sshtunnel noise."""
    root = logging.getLogger()
    if not root.handlers:
        logging.basicConfig(
            level=level,
            format="%(asctime)s %(levelname)s %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    else:
        root.setLevel(level)

    logging.getLogger("paramiko").setLevel(logging.WARNING)
    logging.getLogger("paramiko.transport").setLevel(logging.WARNING)
    logging.getLogger("sshtunnel").setLevel(logging.WARNING)
    logging.getLogger("entities_lineage").setLevel(level)
    logging.getLogger("counter").setLevel(level)
    logging.getLogger("builder").setLevel(level)
