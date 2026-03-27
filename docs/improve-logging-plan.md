# Plan: Enhanced Logging for AutoWiki

This plan outlines the implementation of enhanced logging capabilities for AutoWiki, including separate log files for errors, task status (with critical I/O), and LLM interactions, as well as a debug mode for the worker.

## Objectives
- **Error Logging**: Record caught exceptions into `{data_dir}/error.log`.
- **Task Logging**: Record task execution status and critical inputs/outputs of pipeline stages into `{data_dir}/task.log`.
- **LLM Logging**: Record all LLM inputs and outputs into `{data_dir}/llm.log` when debug mode is enabled.
- **Debug Mode**: Add a `--debug` flag to the worker and CLI to toggle detailed logging and LLM interaction capture.
- **Stdio**: Print LLM interaction logs to stdout at the DEBUG level when debug mode is enabled.

## Key Files & Context
- `shared/config.py`: Configuration model for logging paths and debug flag.
- `shared/logging_config.py` (New): Centralized logging setup.
- `worker/main.py`: Worker entry point to handle `--debug` flag.
- `cli/commands/serve.py`: CLI command to pass `--debug` to the worker.
- `worker/jobs.py`: Main pipeline where task status and I/O are recorded.
- `worker/llm/base.py`: Base LLM provider to implement logging wrapper.
- `worker/llm/__init__.py`: Factory to apply logging wrapper.

## Implementation Steps

### 1. Configuration & Logging Setup
- **`shared/config.py`**:
    - Add `debug: bool = Field(default=False)` to the `Config` class.
    - Add `error_log_path`, `task_log_path`, and `llm_log_path` properties that default to files in `data_dir`.
- **`shared/logging_config.py`** (Create):
    - Implement `setup_logging(config: Config, debug_override: bool = False)`:
        - Set global log level to `INFO`.
        - Add `RotatingFileHandler` for `error.log` with level `ERROR`.
        - Add `RotatingFileHandler` for `task.log` with level `INFO`.
        - If `debug` is enabled:
            - Set global log level to `DEBUG`.
            - Add `RotatingFileHandler` for `llm.log` with level `DEBUG` (specifically for `worker.llm` logger).
            - Add a `StreamHandler` to `stdout` with level `DEBUG`.

### 2. Worker & CLI Enhancements
- **`worker/main.py`**:
    - Use `argparse` to parse a `--debug` flag.
    - Call `setup_logging` with the flag value.
    - Pass debug status to `WorkerSettings` if necessary (or just rely on the logger level).
- **`cli/commands/serve.py`**:
    - Add a `--debug` option to `serve_cmd`.
    - Pass `--debug` when spawning the worker process.

### 3. Pipeline I/O Logging
- **`worker/jobs.py`**:
    - Use `logging.getLogger("worker.task")` for task logging.
    - Log critical data at each stage:
        - **Ingestion**: Repository name, HEAD SHA, number of files found.
        - **AST Analysis**: Number of modules in the tree, enhanced tree summary.
        - **Dependency Graph**: Number of nodes/edges, summary of dependencies.
        - **RAG Indexing**: Index path, number of chunks indexed.
        - **Wiki Planner**: Repository name in plan, number of pages planned.
        - **Page Generator**: Slug and title of each page, content length.
        - **Diagram Synthesis**: Mermaid diagram snippet.
    - Wrap the main loop in `try...except` and use `logger.exception()` to log errors to `error.log`.

### 4. LLM Interaction Logging
- **`worker/llm/base.py`**:
    - Implement `LoggingLLMProvider` as a decorator/wrapper for `LLMProvider`.
    - It should log the `system` message, `prompt`, and the final `response` (or stream chunks) to `worker.llm` at `DEBUG` level.
- **`worker/llm/__init__.py`**:
    - Wrap the created provider with `LoggingLLMProvider` if `cfg.debug` is enabled.

## Verification & Testing
- **Manual Verification**:
    - Start the worker with `--debug` and run a full index.
    - Verify that `{data_dir}/error.log`, `{data_dir}/task.log`, and `{data_dir}/llm.log` are created in the data directory.
    - Check `task.log` for pipeline I/O details.
    - Check `llm.log` for LLM interaction history.
    - Check `stdout` for debug logs.
- **Unit Tests**:
    - Add tests to verify that `setup_logging` correctly configures handlers.
    - Add tests for `LoggingLLMProvider` to ensure it logs what is expected.
