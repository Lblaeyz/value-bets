import logging
import os
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
LOG_DIR = Path("logs")
LOG_DIR.mkdir(exist_ok=True)

_formatter = logging.Formatter(
    fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S%z",
)

_console_handler = logging.StreamHandler()
_console_handler.setFormatter(_formatter)

_file_handler = TimedRotatingFileHandler(
    filename=LOG_DIR / "app.log",
    when="midnight",
    interval=1,
    backupCount=30,
    encoding="utf-8",
    utc=True,
)
_file_handler.setFormatter(_formatter)
_file_handler.suffix = "%Y-%m-%d"

logger = logging.getLogger("football_value_betting")
logger.setLevel(LOG_LEVEL)
logger.addHandler(_console_handler)
logger.addHandler(_file_handler)
logger.propagate = False
