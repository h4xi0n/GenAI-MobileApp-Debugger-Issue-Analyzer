"""Standardized logging setup for GenAI pipelines."""

import logging
import sys


def setup_logger(name: str = "genai", level: int = logging.INFO) -> logging.Logger:
    """Configures and returns a logger instance with formatted console output."""
    logger = logging.getLogger(name)
    if not logger.handlers:
        logger.setLevel(level)
        handler = logging.StreamHandler(sys.stdout)
        handler.setLevel(level)
        formatter = logging.Formatter(
            fmt="%(asctime)s | %(levelname)-7s | %(name)s:%(lineno)d - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    return logger
