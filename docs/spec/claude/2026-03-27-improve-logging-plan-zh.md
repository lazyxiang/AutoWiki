# 方案：AutoWiki 增强型日志

> **[已完成]** 已实施并合并。`LoggingLLMProvider`、`--debug` 标志以及结构化日志文件均已就绪。

本方案概述了 AutoWiki 增强型日志功能的实现过程，包括为错误、任务状态（含关键 I/O）和 LLM 交互分别建立日志文件，并为 Worker 和 API 添加调试模式。

## 目标
- **错误日志 (Error Logging)**：将捕获的异常记录到 `{data_dir}/error.log` 中。
- **任务日志 (Task Logging)**：将任务执行状态及流水线各阶段的关键输入/输出记录到 `{data_dir}/task.log` 中。
- **LLM 日志 (LLM Logging)**：启用调试模式时，将所有 LLM 输入和输出记录到 `{data_dir}/llm.log` 中。
- **调试模式 (Debug Mode)**：为 Worker 和 CLI 添加 `--debug` 标志，以切换详细日志记录和 LLM 交互捕获。
- **标准输出 (Stdio)**：启用调试模式时，在 DEBUG 级别将 LLM 交互日志打印到 stdout。
- **性能 (Performance)**：确保日志记录是非阻塞的，以防止 Worker 和 API 的事件循环发生延迟。

## 关键文件与上下文
- `shared/config.py`：日志路径和调试标志的配置模型。
- `shared/logging_config.py`（新增）：使用 `QueueHandler` 和 `QueueListener` 实现的集中式、非阻塞日志设置。
- `worker/main.py`：Worker 入口点，处理 `--debug` 标志并将其传播到配置。
- `api/main.py`：API 入口点，在 `lifespan` 上下文中初始化日志。
- `cli/commands/serve.py`：CLI 命令，将 `--debug` 传递给 Worker 并设置 `AUTOWIKI_DEBUG`。
- `worker/jobs.py`：主流水线，记录任务状态和 I/O（包括 `run_full_index` 和 `run_refresh_index`）。
- `worker/llm/base.py`：基础 LLM 提供商，实现带有日志截断功能的 `LoggingLLMProvider`。
- `worker/llm/__init__.py`：工厂函数，根据配置应用日志包装器。

## 实施详情

### 1. 配置与日志设置
- **`shared/config.py`**：
    - 在 `Config` 类中添加 `debug: bool = Field(default=False)`。
    - 添加 `error_log_path`、`task_log_path` 和 `llm_log_path` 属性，返回 `data_dir` 中的对应文件。
- **`shared/logging_config.py`**：
    - 实现 `setup_logging(config: Config)`：
        - 使用挂载到根记录器的 `logging.handlers.QueueHandler`。
        - 在后台线程中启动 `logging.handlers.QueueListener` 来处理实际的文件和流写入。
        - 为 `error.log` (ERROR+)、`task.log` (INFO+) 和 `llm.log` (DEBUG，针对 `worker.llm` 过滤) 配置 `RotatingFileHandler` 接收器。
        - 确保相对于主执行流，所有日志写入都是非阻塞的。

### 2. Worker 与 CLI 增强
- **`worker/main.py`**：
    - 解析 `--debug` 标志并显式更新 `cfg.debug`。
    - 启动时调用 `setup_logging(cfg)`。
- **`api/main.py`**：
    - 在 FastAPI 的 `lifespan` 内初始化 `setup_logging(cfg)`，确保 API 也能从结构化日志中受益。
- **`cli/commands/serve.py`**：
    - 将 `--debug` 传递给 Worker 进程，并设置 `AUTOWIKI_DEBUG` 环境变量。

### 3. 流水线 I/O 日志
- **`worker/jobs.py`**：
    - 使用 `logging.getLogger("worker.task")` 进行任务日志记录。
    - 在 `run_full_index` 和 `run_refresh_index` 中为所有 6 个流水线阶段添加详细的 `INFO` 日志。
    - **异步安全 I/O**：使用 `run_in_executor` 替换了阻塞的文件读取（如 `read_text()`），以保持事件循环的响应性。
    - 在 `try...except` 块中使用 `logger.exception()`，确保完整的堆栈跟踪被捕获到 `error.log` 中。

### 4. LLM 交互日志
- **`worker/llm/base.py`**：
    - 将 `LoggingLLMProvider` 实现为 `LLMProvider` 的包装器。
    - **日志截断**：包含一个 `_truncate` 辅助函数（默认 2000 字符），防止日志文件因 RAG 上下文或过长的响应而过度膨胀。
    - 在 `DEBUG` 级别记录 `system` 消息、`prompt` 和 `response`（包括完整的流式重组结果）。

## 验证与测试
- **手动验证**：
    - 使用 `--debug` 启动 Worker 并运行全量索引。
    - 验证 `{data_dir}/error.log`、`{data_dir}/task.log` 和 `{data_dir}/llm.log` 是否已正确创建并填充内容。
    - 检查 `task.log` 中的各阶段 I/O 详情。
    - 检查 `llm.log` 中截断后的交互记录。
- **单元测试**：
    - 更新 `tests/worker/test_llm.py`，使用 `patch.dict("os.environ", {"AUTOWIKI_DEBUG": "false"})` 来确保环境隔离和稳定的提供商断言。
    - 验证在新的日志基础设施下，所有 127 个测试均已通过。
