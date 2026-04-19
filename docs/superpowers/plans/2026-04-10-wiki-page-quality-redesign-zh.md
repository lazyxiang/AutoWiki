# 维基页面质量重构 — 实施计划

> **[已完成]** 已实施并合并（PR #15 和 #17）。有关规范层面的实施说明，请参见 `docs/superpowers/specs/2026-04-10-wiki-page-quality-redesign.md`，包括未连接的 `cache_ttl: long` 存根。
>
> **对于智能体工作者：** 要求的子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 来逐项任务实施此计划。步骤使用复选框 (`- [ ]`) 语法进行跟踪。

**目标：** 将单次通过的维基页面生成器替换为多阶段流水线（大纲 → 草稿 → 事实核查 → 修订），以生成更具基础性、抗幻觉的维基页面和更丰富的 Mermaid 图表，同时通过快速/主模型分离和提示词缓存保持成本不变。

**架构：** `LLMProvider` 基类获得了用于感知缓存提示词的 `PromptSegment` 抽象。一个新的 `fast_model` 配置选项创建了第二个提供商实例。页面生成器被重构为四个离散阶段，并带有独立的提示词构建器。RAG 存储获得了文档/代码分区检索。维基规划器的第 2 阶段迁移到了快速模型。

**技术栈：** Python 3.12, pydantic-settings v2, FAISS, anthropic SDK, openai SDK, google-genai SDK, pytest (asyncio_mode=auto)

---

## 文件结构

### 新文件
- `worker/llm/prompt_segment.py` — `PromptSegment` 数据类和 `normalize_prompt()` 助手
- `worker/pipeline/page_outline.py` — 阶段 1：页面大纲生成和验证
- `worker/pipeline/page_draft.py` — 阶段 2：使用新提示词模板生成草稿
- `worker/pipeline/fact_check.py` — 阶段 3：针对源码的事实核查 + 阶段 4：有针对性的修订
- `worker/pipeline/diagram_post_processor.py` — 确保图表标题/来源的后处理器
- `tests/worker/test_prompt_segment.py` — 每个提供商的 PromptSegment 转换单元测试
- `tests/worker/test_page_outline.py` — 大纲验证单元测试
- `tests/worker/test_page_draft.py` — 草案提示词构建单元测试
- `tests/worker/test_fact_check.py` — 事实核查解析、修订拼接、回退单元测试
- `tests/worker/test_diagram_post_processor.py` — 图表标题/来源强制执行单元测试

### 修改的文件
- `shared/config.py` — 向 `LLMConfig` 添加 `fast_model` 和 `cache_ttl`
- `worker/llm/base.py` — 更新 `LLMProvider` 签名以接受 `str | list[PromptSegment]`
- `worker/llm/anthropic_provider.py` — 将 `PromptSegment` 转换为 Anthropic 缓存控制块
- `worker/llm/openai_provider.py` — 按顺序连接段（自动前缀缓存）
- `worker/llm/gemini_provider.py` — 按顺序连接段（隐式缓存）
- `worker/llm/ollama_provider.py` — 连接段，忽略缓存标记
- `worker/llm/__init__.py` — 添加 `make_fast_llm_provider()` 工厂
- `worker/pipeline/rag_indexer.py` — 向 `search()` 和 `multi_search()` 添加 `code_k`/`doc_k` 参数
- `worker/pipeline/wiki_planner.py` — 为第 2 阶段接入 `fast_llm`，使用 `PromptSegment` 进行缓存
- `worker/pipeline/page_generator.py` — 用调用 4 个新阶段的编排器替换单次通过生成器
- `worker/jobs.py` — 启动时构建 `fast_llm`，在流水线中传递两个提供商
- `tests/conftest.py` — 添加 `mock_fast_llm` 固件
- `tests/worker/test_page_generator.py` — 更新以测试多阶段编排器
- `tests/worker/test_rag_indexer.py` — 为文档/代码分区检索添加测试
- `tests/worker/test_wiki_planner.py` — 在 fast_llm 上为第 2 阶段添加测试

---

## 任务 1：PromptSegment 数据类与提供商规范化

**文件：**
- 创建：`worker/llm/prompt_segment.py`
- 测试：`tests/worker/test_prompt_segment.py`

- [ ] **步骤 1：为 PromptSegment 创建和 normalize_prompt 编写失败测试**

```python
# tests/worker/test_prompt_segment.py
from worker.llm.prompt_segment import PromptSegment, normalize_prompt


def test_prompt_segment_defaults():
    seg = PromptSegment(text="hello")
    assert seg.text == "hello"
    assert seg.cacheable is False


def test_prompt_segment_cacheable():
    seg = PromptSegment(text="context", cacheable=True)
    assert seg.cacheable is True


def test_normalize_prompt_from_string():
    result = normalize_prompt("plain text")
    assert result == [PromptSegment(text="plain text", cacheable=False)]


def test_normalize_prompt_from_list():
    segments = [
        PromptSegment(text="cached", cacheable=True),
        PromptSegment(text="variable"),
    ]
    result = normalize_prompt(segments)
    assert result is segments


def test_normalize_prompt_empty_string():
    result = normalize_prompt("")
    assert result == [PromptSegment(text="", cacheable=False)]
```

- [ ] **步骤 2：运行测试以验证其失败**

运行：`pytest tests/worker/test_prompt_segment.py -v`
预期：失败，提示 `ModuleNotFoundError`

- [ ] **步骤 3：实现 PromptSegment 和 normalize_prompt**

```python
# worker/llm/prompt_segment.py
"""用于感知缓存的 LLM 调用的提示词段抽象。

提供商将 PromptSegment 列表转换为其原生的缓存原语。
传递普通字符串等同于传递单个不可缓存的段。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class PromptSegment:
    """带有可选缓存提示的提示词段。

    Attributes:
        text: 此提示词段的文本内容。
        cacheable: 为 True 时，支持提示词缓存的提供商将标记此段进行缓存
            （例如 Anthropic 的 cache_control）。不支持缓存的提供商将忽略此标志。
    """

    text: str
    cacheable: bool = False


PromptInput = str | list[PromptSegment]
"""接受普通字符串或 PromptSegment 对象列表的提示词参数的类型别名。"""


def normalize_prompt(prompt: PromptInput) -> list[PromptSegment]:
    """将 PromptInput 转换为 PromptSegment 对象列表。

    如果 *prompt* 已经是列表，则原样返回。
    如果是普通字符串，则将其包装在单个不可缓存的段中。
    """
    if isinstance(prompt, list):
        return prompt
    return [PromptSegment(text=prompt, cacheable=False)]


def segments_to_text(segments: list[PromptSegment]) -> str:
    """将各段文本连接成单个字符串。

    用于不支持缓存标记的提供商（OpenAI, Ollama）— 它们只是按顺序合并所有段文本。
    """
    return "".join(seg.text for seg in segments)
```

- [ ] **步骤 4：为 segments_to_text 添加测试**

- [ ] **步骤 5：运行所有测试以验证其通过**

- [ ] **步骤 6：提交**

---

## 任务 2：更新 LLMProvider 基类

**文件：**
- 修改：`worker/llm/base.py`
- 测试：`tests/worker/test_prompt_segment.py`（扩展）

- [ ] **步骤 1：为 LLMProvider 接受 PromptSegment 列表编写失败测试**

- [ ] **步骤 2：运行测试以验证其失败**

- [ ] **步骤 3：更新 LLMProvider 和 LoggingLLMProvider 签名**

修改 `worker/llm/base.py`：

将 `generate`、`generate_structured` 和 `generate_stream` 的类型注解更改为接受 `str | list[PromptSegment]`。`generate_batch` 方法的 `prompts` 参数变为 `list[str | list[PromptSegment]]`。

在文件顶部导入：
```python
from worker.llm.prompt_segment import PromptInput, normalize_prompt, segments_to_text
```

更新 `LLMProvider`：
```python
class LLMProvider(ABC):
    @abstractmethod
    async def generate(self, prompt: PromptInput, system: PromptInput = "") -> str:
        """从提示词生成文本。返回完整的响应字符串。"""

    @abstractmethod
    async def generate_structured(
        self, prompt: PromptInput, schema: dict[str, Any], system: PromptInput = ""
    ) -> dict[str, Any]:
        """生成并解析匹配给定模式的 JSON 响应。"""

    @abstractmethod
    async def generate_stream(
        self, prompt: PromptInput, system: PromptInput = ""
    ) -> AsyncIterator[str]:
        """异步生成器，随文本块到达产生它们。"""

    async def generate_batch(
        self,
        prompts: list[PromptInput],
        system: PromptInput = "",
        max_concurrency: int = 5,
    ) -> list[str]:
        # ... 除了类型注解外，主体保持不变
```

- [ ] **步骤 4：运行测试以验证其通过**

- [ ] **步骤 5：运行完整测试套件以验证没有回归**

- [ ] **步骤 6：提交**

---

## 任务 3：为缓存控制更新 Anthropic 提供商

**文件：**
- 修改：`worker/llm/anthropic_provider.py`
- 测试：`tests/worker/test_prompt_segment.py`（扩展）

- [ ] **步骤 1：为 Anthropic 缓存控制转换编写失败测试**

- [ ] **步骤 2：运行测试以验证其失败**

- [ ] **步骤 3：在 AnthropicProvider 中实现缓存控制转换**

修改 `worker/llm/anthropic_provider.py`：

```python
from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import anthropic

from worker.llm.base import LLMProvider, _parse_json_response
from worker.llm.prompt_segment import PromptInput, normalize_prompt, segments_to_text


def _segments_to_anthropic_content(
    segments: list,
) -> str | list[dict[str, Any]]:
    """将 PromptSegment 列表转换为 Anthropic 内容块。

    如果没有段是可缓存的，则返回普通字符串（行为保持不变）。
    否则，返回文本块列表，并在每组连续的可缓存段的最后一个段上放置 cache_control，
    最多达到 Anthropic 的 4 个断点限制。
    """
    from worker.llm.prompt_segment import PromptSegment

    has_cache = any(s.cacheable for s in segments)
    if not has_cache:
        return segments_to_text(segments)

    blocks: list[dict[str, Any]] = []
    cache_breakpoints = 0
    max_breakpoints = 4

    for i, seg in enumerate(segments):
        block: dict[str, Any] = {"type": "text", "text": seg.text}
        if seg.cacheable and cache_breakpoints < max_breakpoints:
            # 如果这是连续运行中的最后一个可缓存段，或者简单地在每个限制内的可缓存段上放置 cache_control。
            next_cacheable = (
                i + 1 < len(segments) and segments[i + 1].cacheable
            )
            if not next_cacheable:
                block["cache_control"] = {"type": "ephemeral"}
                cache_breakpoints += 1
        blocks.append(block)

    return blocks
```

- [ ] **步骤 4：运行测试以验证其通过**

- [ ] **步骤 5：运行完整测试套件以检查回归**

- [ ] **步骤 6：提交**

---

## 任务 4：更新 OpenAI、Gemini 和 Ollama 提供商

**文件：**
- 修改：`worker/llm/openai_provider.py`
- 修改：`worker/llm/gemini_provider.py`
- 修改：`worker/llm/ollama_provider.py`
- 测试：`tests/worker/test_prompt_segment.py`（扩展）

---

## 任务 5：快速模型配置与工厂

**文件：**
- 修改：`shared/config.py`
- 修改：`worker/llm/__init__.py`
- 测试：`tests/worker/test_prompt_segment.py`（扩展）

- [ ] **步骤 1：为配置和工厂编写失败测试**

- [ ] **步骤 2：运行测试以验证其失败**

- [ ] **步骤 3：向 LLMConfig 添加 fast_model 和 cache_ttl**

修改 `shared/config.py`：

```python
class LLMConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AUTOWIKI_LLM_")
    provider: Literal[
        "anthropic", "google", "openai", "openai-compatible", "ollama"
    ] = "anthropic"
    model: str = "claude-sonnet-4-6"
    fast_model: str = ""
    api_key: str = ""
    base_url: str = ""
    cache_ttl: Literal["short", "long"] = "short"
```

- [ ] **步骤 4：向工厂添加 make_fast_llm_provider**

---

## 任务 6：RAG 存储文档降权

**文件：**
- 修改：`worker/pipeline/rag_indexer.py`
- 测试：`tests/worker/test_rag_indexer.py`（扩展）

- [ ] **步骤 1：为 code_k/doc_k 分区检索编写失败测试**

- [ ] **步骤 2：运行测试以验证其失败**

- [ ] **步骤 3：在 search() 和 multi_search() 中实现文档/代码分区**

修改 `worker/pipeline/rag_indexer.py`。添加模块级常量并更新两个方法：

```python
_DOC_EXTENSIONS = frozenset({".md", ".rst", ".txt", ".adoc"})


def _is_doc_chunk(meta: dict[str, Any]) -> bool:
    """如果此分块来自文档文件，则返回 True。"""
    file_path = meta.get("file", "")
    return Path(file_path).suffix.lower() in _DOC_EXTENSIONS
```

---

## 任务 7：页面大纲阶段 (Pass 1)

**文件：**
- 创建：`worker/pipeline/page_outline.py`
- 测试：`tests/worker/test_page_outline.py`

- [ ] **步骤 1：为大纲模式、验证和生成编写失败测试**

```python
# tests/worker/test_page_outline.py
import pytest
from worker.pipeline.page_outline import (
    VALID_DIAGRAM_TYPES,
    VALID_SECTION_KINDS,
    PageOutline,
    validate_outline,
)


def test_valid_outline_passes_validation():
    raw = {
        "sections": [
            {"heading": "Overview", "kind": "prose", "focus": "What it does", "diagram": None},
            {
                "heading": "Architecture",
                "kind": "prose+diagram",
                "focus": "How it works",
                "diagram": {
                    "type": "flowchart",
                    "purpose": "Show data flow",
                    "source_files": ["src/main.py"],
                },
            },
        ],
        "key_claims": [
            "FAISSStore uses IndexFlatIP",
            "multi_search deduplicates by (file, start_line)",
            "Chunk size defaults to 1000",
        ],
    }
    outline = validate_outline(raw, page_files=["src/main.py", "src/models.py"])
    assert isinstance(outline, PageOutline)
```

- [ ] **步骤 2：运行测试以验证其失败**

- [ ] **步骤 3：实现 page_outline.py**

```python
# worker/pipeline/page_outline.py
"""多阶段页面生成器的阶段 1 — 结构化页面大纲。

生成 JSON 大纲（章节、计划的图表、关键主张），用于指导草案阶段
并作为事实核查阶段的目标。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from worker.llm.base import LLMProvider
from worker.llm.prompt_segment import PromptInput, PromptSegment
from worker.utils.retry import TRANSIENT_EXCEPTIONS, OnRetryCallback, async_retry

if TYPE_CHECKING:
    from worker.pipeline.wiki_planner import WikiPageSpec

VALID_SECTION_KINDS = frozenset({
    "prose",
    "prose+table",
    "prose+list",
    "prose+diagram",
    "prose+table+diagram",
})
```

---

## 任务 8：页面草稿阶段 (Pass 2)

**文件：**
- 创建：`worker/pipeline/page_draft.py`
- 测试：`tests/worker/test_page_draft.py`

- [ ] **步骤 1：为草案提示词构建和系统提示词编写失败测试**

- [ ] **步骤 2：运行测试以验证其失败**

- [ ] **步骤 3：实现 page_draft.py**

```python
# worker/pipeline/page_draft.py
"""多阶段页面生成器的阶段 2 — 草案生成。

获取经过验证的 PageOutline 以及检索到的源码分块，
并通过主 LLM 模型生成完整的 Markdown 页面。
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from worker.llm.base import LLMProvider
from worker.llm.prompt_segment import PromptSegment
from worker.pipeline.page_generator import PageResult, _format_context_chunks, _format_entity_details
from worker.pipeline.page_outline import PageOutline
from worker.utils.mermaid import sanitize_mermaid_blocks
from worker.utils.retry import TRANSIENT_EXCEPTIONS, OnRetryCallback, async_retry
```

---

## 任务 9：事实核查与修订 (Pass 3 + Pass 4)

**文件：**
- 创建：`worker/pipeline/fact_check.py`
- 测试：`tests/worker/test_fact_check.py`

- [ ] **步骤 1：为事实核查输出解析和回退编写失败测试**

- [ ] **步骤 2：运行测试以验证其失败**

- [ ] **步骤 3：实现 fact_check.py**

```python
# worker/pipeline/fact_check.py
"""多阶段页面生成器的阶段 3（事实核查）和阶段 4（有针对性的修订）。

事实核查阶段根据源代码验证大纲中的 key_claims，并检查图表关系。
修订阶段在发现问题时对草案应用有针对性的修复。
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from worker.llm.base import LLMProvider
from worker.llm.prompt_segment import PromptSegment
from worker.pipeline.page_outline import PageOutline
from worker.utils.mermaid import sanitize_mermaid_blocks
from worker.utils.retry import TRANSIENT_EXCEPTIONS, OnRetryCallback, async_retry
```

---

## 任务 10：图表后处理器

**文件：**
- 创建：`worker/pipeline/diagram_post_processor.py`
- 测试：`tests/worker/test_diagram_post_processor.py`

- [ ] **步骤 1：为图表标题/来源强制执行编写失败测试**

- [ ] **步骤 2：运行测试以验证其失败**

- [ ] **步骤 3：实现 diagram_post_processor.py**

```python
# worker/pipeline/diagram_post_processor.py
"""确保每个 Mermaid 图表块都有标题和来源引用的后处理器。

检查每个 ```mermaid 块是否具有前置的 **Diagram: ...** 标题
和后置的 *Source: ...* 注解。如果缺失，则插入占位符值。
"""

from __future__ import annotations

import re


_MERMAID_BLOCK = re.compile(r'```mermaid\n.*?```', re.DOTALL)
_HEADER_PATTERN = re.compile(r'\*\*Diagram:[^\n]*\*\*')
_SOURCE_PATTERN = re.compile(r'\*Source:[^\n]*\*')
```

---

## 任务 11：多阶段编排器（替换单次通过生成器）

**文件：**
- 修改：`worker/pipeline/page_generator.py`
- 修改：`tests/worker/test_page_generator.py`

此任务将单次通过的 `generate_page` 和 `generate_page_batch` 替换为调用 大纲 → 草稿 → 事实核查 → 修订 的多阶段编排器。

- [ ] **步骤 1：为新的多阶段 generate_page 编写失败测试**

- [ ] **步骤 2：运行测试以验证其失败**

- [ ] **步骤 3：重写 page_generator.py 编排器**

修改 `worker/pipeline/page_generator.py`。保留 `compute_generation_order`, `PageResult`, `_format_entity_details`, `_format_context_chunks`（它们被新阶段使用）。将 `_build_page_prompt`, `generate_page`, 和 `generate_page_batch` 替换为多阶段编排器。

---

## 任务 12：在 jobs.py 和规划器中接入 fast_llm

**文件：**
- 修改：`worker/jobs.py`
- 修改：`worker/pipeline/wiki_planner.py`
- 修改：`tests/conftest.py`
- 测试：`tests/worker/test_wiki_planner.py`（扩展）

- [ ] **步骤 1：向 conftest.py 添加 mock_fast_llm 固件**

- [ ] **步骤 2：为规划器第 2 阶段使用 fast_llm 编写失败测试**

- [ ] **步骤 3：运行测试以验证其失败**

- [ ] **步骤 4：更新 wiki_planner.py 以接受用于第 2 阶段的 fast_llm**

- [ ] **步骤 5：更新 jobs.py 以构建并传递 fast_llm**

修改 `worker/jobs.py`：

在 `run_full_index` 和 `run_refresh_index` 的顶部，在 `llm = make_llm_provider(cfg)` 之后添加：

```python
from worker.llm import make_fast_llm_provider
fast_llm = make_fast_llm_provider(cfg, llm)
```

- [ ] **步骤 6：运行维基规划器测试**

- [ ] **步骤 7：运行完整测试套件**

- [ ] **步骤 8：提交**

---

## 任务 12b（后续）：将规划器提示词转换为 PromptSegment

**注意：** 规范 §7.2 要求规划器的第 1 阶段和第 2 阶段提示词使用 `PromptSegment` 以便在阶段内重试时复用缓存。这是一种成本优化（而非功能变更），可以在核心流水线运行后作为后续工作完成。`wiki_planner.py` 中的 `_build_outline_prompt` 和 `_build_assignment_prompt` 函数将返回 `list[PromptSegment]` 而不是 `str`，并将文件摘要和依赖项信息部分标记为可缓存。这对于具有相同大背景且自重试 2-3 次的大型仓库大有裨益。

---

## 任务 13：为新签名更新现有测试

**文件：**
- 修改：`tests/worker/test_page_generator.py`
- 修改：`tests/worker/test_jobs.py`

- [ ] **步骤 1：运行完整测试套件以识别失败点**

- [ ] **步骤 2：修复任何 test_page_generator.py 失败**

- [ ] **步骤 3：修复任何 test_jobs.py 失败**

- [ ] **步骤 4：运行完整测试套件**

- [ ] **步骤 5：提交**

---

## 任务 14：Lint 与格式化

- [ ] **步骤 1：运行 ruff check 并修复**

- [ ] **步骤 2：运行 ruff format**

- [ ] **步骤 3：运行 npm lint**

- [ ] **步骤 4：最后一次运行完整测试套件**

- [ ] **步骤 5：提交 lint 修复（如果有）**

---

## 任务 15：集成冒烟测试

- [ ] **步骤 1：针对固件仓库运行端到端测试**

运行：`pytest tests/worker/test_jobs.py -v -k "full_index"`
预期：通过 — 完整的流水线在使用模拟 LLM 的情况下可以工作

- [ ] **步骤 2：验证覆盖率目标**

运行：`pytest tests/ --ignore=tests/e2e --cov=worker --cov-report=term-missing | tail -30`
预期：`worker/` 覆盖率 ≥ 80%，新模块覆盖率 ≥ 85%

- [ ] **步骤 3：如果需要，进行最终提交**
