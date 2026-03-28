from __future__ import annotations

import logging
import sys
from logging.handlers import QueueHandler, QueueListener, RotatingFileHandler
from queue import Queue

from shared.config import Config

_listener: QueueListener | None = None


def setup_logging(config: Config) -> None:
    """Setup global logging with non-blocking file handlers."""
    global _listener

    # Stop existing listener if any
    if _listener:
        _listener.stop()

    # Ensure data directory and logs subdirectory exist for log files
    config.error_log_path.parent.mkdir(parents=True, exist_ok=True)

    debug_enabled = config.debug
    log_level = logging.DEBUG if debug_enabled else logging.INFO

    # Root logger configuration
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    # Clear existing handlers to avoid duplicates
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )

    log_queue: Queue = Queue(-1)  # Unlimited size
    queue_handler = QueueHandler(log_queue)
    root_logger.addHandler(queue_handler)

    handlers: list[logging.Handler] = []

    # 1. Error Log: {data_dir}/error.log (ERROR and above)
    error_handler = RotatingFileHandler(
        config.error_log_path, maxBytes=10 * 1024 * 1024, backupCount=5
    )
    error_handler.setLevel(logging.ERROR)
    error_handler.setFormatter(formatter)
    handlers.append(error_handler)

    # 2. Task Log: {data_dir}/task.log (INFO and above)
    task_handler = RotatingFileHandler(
        config.task_log_path, maxBytes=10 * 1024 * 1024, backupCount=5
    )
    task_handler.setLevel(logging.INFO)
    task_handler.setFormatter(formatter)
    handlers.append(task_handler)

    # 3. Console Handler
    console_level = logging.DEBUG if debug_enabled else logging.INFO
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(console_level)
    console_handler.setFormatter(formatter)
    handlers.append(console_handler)

    # 4. LLM Log: {data_dir}/llm.log (DEBUG specifically for LLM interactions)
    if debug_enabled:
        llm_handler = RotatingFileHandler(
            config.llm_log_path, maxBytes=20 * 1024 * 1024, backupCount=5
        )
        llm_handler.setLevel(logging.DEBUG)
        llm_handler.setFormatter(formatter)

        llm_logger = logging.getLogger("worker.llm")
        llm_logger.setLevel(logging.DEBUG)
        # Clear existing handlers on llm_logger to avoid duplicates
        for h in llm_logger.handlers[:]:
            llm_logger.removeHandler(h)

        # We don't add llm_handler to llm_logger directly to keep it non-blocking.
        # Instead, we add it to the global listener sinks.
        # To make sure llm_handler only gets logs from 'worker.llm', we add a filter.
        class NameFilter(logging.Filter):
            def filter(self, record):
                return record.name == "worker.llm"

        llm_handler.addFilter(NameFilter())
        handlers.append(llm_handler)

    # Start the listener with all collected handlers
    _listener = QueueListener(log_queue, *handlers, respect_handler_level=True)
    _listener.start()

    logging.info(f"Logging initialized. Mode: {'DEBUG' if debug_enabled else 'INFO'}")
    logging.info(f"Task log: {config.task_log_path}")
    logging.info(f"Error log: {config.error_log_path}")
    if debug_enabled:
        logging.info(f"LLM log: {config.llm_log_path}")


def stop_logging() -> None:
    """Stop the logging listener."""
    global _listener
    if _listener:
        _listener.stop()
        _listener = None
