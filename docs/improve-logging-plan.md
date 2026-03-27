# Plan: Enhanced Logging for AutoWiki

This plan outlines the implementation of enhanced logging capabilities for AutoWiki, including separate log files for errors, task status (with critical I/O), and LLM interactions, as well as a debug mode for the worker and API.

## Objectives
- **Error Logging**: Record caught exceptions into `{data_dir}/error.log`.
- **Task Logging**: Record task execution status and critical inputs/outputs of pipeline stages into `{data_dir}/task.log`.
- **LLM Logging**: Record all LLM inputs and outputs into `{data_dir}/llm.log` when debug mode is enabled.
- **Debug Mode**: Add a `--debug` flag to the worker and CLI to toggle detailed logging and LLM interaction capture.
- **Stdio**: Print LLM interaction logs to stdout at the DEBUG level when debug mode is enabled.
- **Performance**: Ensure logging is non-blocking to prevent event loop delays in the worker and API.

## Key Files & Context
- `shared/config.py`: Configuration model for logging paths and debug flag.
- `shared/logging_config.py` (New): Centralized, non-blocking logging setup using `QueueHandler` and `QueueListener`.
- `worker/main.py`: Worker entry point to handle `--debug` flag and propagate to config.
- `api/main.py`: API entry point to initialize logging in the `lifespan` context.
- `cli/commands/serve.py`: CLI command to pass `--debug` to the worker and set `AUTOWIKI_DEBUG`.
- `worker/jobs.py`: Main pipeline where task status and I/O are recorded (both `run_full_index` and `run_refresh_index`).
- `worker/llm/base.py`: Base LLM provider implementing `LoggingLLMProvider` with log truncation.
- `worker/llm/__init__.py`: Factory to apply logging wrapper based on config.

## Implementation Details

### 1. Configuration & Logging Setup
- **`shared/config.py`**:
    - Add `debug: bool = Field(default=False)` to the `Config` class.
    - Add `error_log_path`, `task_log_path`, and `llm_log_path` properties that return files in `data_dir`.
- **`shared/logging_config.py`**:
    - Implement `setup_logging(config: Config)`:
        - Uses a `logging.handlers.QueueHandler` attached to the root logger.
        - Starts a `logging.handlers.QueueListener` in a background thread to handle actual file and stream writes.
        - Configures `RotatingFileHandler` sinks for `error.log` (ERROR+), `task.log` (INFO+), and `llm.log` (DEBUG, filtered for `worker.llm`).
        - Ensures all log writes are non-blocking relative to the main execution flow.

### 2. Worker & CLI Enhancements
- **`worker/main.py`**:
    - Parses `--debug` flag and explicitly updates `cfg.debug`.
    - Calls `setup_logging(cfg)` during startup.
- **`api/main.py`**:
    - Initializes `setup_logging(cfg)` within the FastAPI `lifespan` to ensure the API also benefits from structured logging.
- **`cli/commands/serve.py`**:
    - Passes `--debug` to the worker process and sets the `AUTOWIKI_DEBUG` environment variable.

### 3. Pipeline I/O Logging
- **`worker/jobs.py`**:
    - Uses `logging.getLogger("worker.task")` for task logging.
    - Added detailed `INFO` logs for all 6 pipeline stages in both `run_full_index` and `run_refresh_index`.
    - **Async-Safe I/O**: Replaced blocking file reads (e.g., `read_text()`) with `run_in_executor` to maintain event loop responsiveness.
    - Uses `logger.exception()` in `try...except` blocks to ensure full stack traces are captured in `error.log`.

### 4. LLM Interaction Logging
- **`worker/llm/base.py`**:
    - Implements `LoggingLLMProvider` as a wrapper for `LLMProvider`.
    - **Log Truncation**: Includes a `_truncate` helper (default 2000 chars) to prevent log files from growing excessively large with RAG context or long responses.
    - Logs `system` message, `prompt`, and `response` (including full stream re-assembly) at `DEBUG` level.

## Verification & Testing
- **Manual Verification**:
    - Start the worker with `--debug` and run a full index.
    - Verify that `{data_dir}/error.log`, `{data_dir}/task.log`, and `{data_dir}/llm.log` are created and populated correctly.
    - Check `task.log` for stage-by-stage I/O details.
    - Check `llm.log` for truncated interaction transcripts.
- **Unit Tests**:
    - `tests/worker/test_llm.py` updated to use `patch.dict("os.environ", {"AUTOWIKI_DEBUG": "false"})` to ensure environment isolation and stable provider assertions.
    - Verified all 127 tests pass with the new logging infrastructure.
