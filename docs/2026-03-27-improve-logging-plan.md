# Plan: Enhanced Logging for AutoWiki

This plan outlines the implementation of enhanced logging capabilities for AutoWiki, including separate log files for errors, task status (with critical I/O), and LLM interactions, as well as a debug mode for the worker and API.

## Objectives
... [3,465 characters omitted] ...
- **Manual Verification**:
    - Start the worker with `--debug` and run a full index.
    - Verify that `{data_dir}/logs/error.log`, `{data_dir}/logs/task.log`, and `{data_dir}/logs/llm.log` are created and populated correctly.
    - Check `task.log` for stage-by-stage I/O details.
    - Check `llm.log` for truncated interaction transcripts.
- **Unit Tests**:
    - `tests/worker/test_llm.py` updated to use `patch.dict("os.environ", {"AUTOWIKI_DEBUG": "false"})` to ensure environment isolation and stable provider assertions.
    - Verified all tests pass with the new logging infrastructure.

---

## Status: Implemented (Wiki Optimization Phase)

This plan is fully implemented. The `logs` directory is now located within the `data_dir` (defaults to `~/.autowiki/logs/`). The centralized logging system uses `QueueHandler`/`QueueListener` to ensure non-blocking operation, and `LoggingLLMProvider` captures all LLM interactions when the `--debug` flag is enabled.
