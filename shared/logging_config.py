from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler

from shared.config import Config


def setup_logging(config: Config, debug_override: bool = False) -> None:
    """Setup global logging with file handlers and optional debug mode."""
    # Ensure data directory exists for log files
    config.data_dir.mkdir(parents=True, exist_ok=True)

    debug_enabled = config.debug or debug_override
    log_level = logging.DEBUG if debug_enabled else logging.INFO

    # Root logger configuration
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    # Clear existing handlers to avoid duplicates
    root_logger.handlers.clear()

    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )

    # 1. Error Log: /data/error.log (ERROR and above)
    error_handler = RotatingFileHandler(
        config.error_log_path, maxBytes=10 * 1024 * 1024, backupCount=5
    )
    error_handler.setLevel(logging.ERROR)
    error_handler.setFormatter(formatter)
    root_logger.addHandler(error_handler)

    # 2. Task Log: /data/task.log (INFO and above)
    # We use a filter to exclude LLM debug logs from task.log if possible,
    # but normally INFO and above is fine.
    task_handler = RotatingFileHandler(
        config.task_log_path, maxBytes=10 * 1024 * 1024, backupCount=5
    )
    task_handler.setLevel(logging.INFO)
    task_handler.setFormatter(formatter)
    root_logger.addHandler(task_handler)

    # 3. LLM Log: /data/llm.log (DEBUG specifically for LLM interactions)
    if debug_enabled:
        llm_handler = RotatingFileHandler(
            config.llm_log_path, maxBytes=20 * 1024 * 1024, backupCount=5
        )
        llm_handler.setLevel(logging.DEBUG)
        llm_handler.setFormatter(formatter)

        # We can attach this handler specifically to the 'worker.llm' logger
        # or just leave it on root but it might be noisy.
        # The prompt says "all input and output during interactions with
        # the LLM are logged into /data/llm.log"
        # and "print into stdio at the debug level".

        llm_logger = logging.getLogger("worker.llm")
        llm_logger.setLevel(logging.DEBUG)
        llm_logger.addHandler(llm_handler)
        llm_logger.propagate = True  # Allow it to reach root handlers (like stdio)

        # 4. Stdout: Print debug logs to stdio if debug mode enabled
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(logging.DEBUG)
        console_handler.setFormatter(formatter)
        root_logger.addHandler(console_handler)
    else:
        # Standard INFO console handler if not in debug mode
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(logging.INFO)
        console_handler.setFormatter(formatter)
        root_logger.addHandler(console_handler)

    logging.info(f"Logging initialized. Mode: {'DEBUG' if debug_enabled else 'INFO'}")
    logging.info(f"Task log: {config.task_log_path}")
    logging.info(f"Error log: {config.error_log_path}")
    if debug_enabled:
        logging.info(f"LLM log: {config.llm_log_path}")
