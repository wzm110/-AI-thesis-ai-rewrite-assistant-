"""路径、日志（供 PyInstaller / 源码共用）。"""
from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

if getattr(sys, "frozen", False):
    RESOURCE_DIR = Path(getattr(sys, "_MEIPASS"))
    APP_DIR = Path.cwd()
else:
    _PKG = Path(__file__).resolve().parent
    RESOURCE_DIR = _PKG.parent
    APP_DIR = RESOURCE_DIR

PROMPT_PATH = APP_DIR / "prompt.txt"
THESIS_PATH = APP_DIR / "论文.txt"
CONFIG_PATH = APP_DIR / "default.yaml"
PROMPTS_DIR = APP_DIR / "prompts"
EXPORT_DIR = APP_DIR / "outputs"
EXPORT_DIR.mkdir(exist_ok=True)
LOG_DIR = APP_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)


def setup_logger(name: str, file_path: Path) -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    handler = RotatingFileHandler(
        filename=str(file_path),
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    logger.addHandler(handler)
    logger.propagate = False
    return logger


server_logger = setup_logger("rewrite_server", LOG_DIR / "server.log")
frontend_logger = setup_logger("rewrite_frontend", LOG_DIR / "frontend.log")
