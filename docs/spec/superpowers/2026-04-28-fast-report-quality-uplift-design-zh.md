# Fast Report 质量提升

## 概要

本文档规范 fast report 流水线的下一轮升级，目标是把报告质量从"对索引元数据的总结"提升到"基于真实实现切片的解释"，同时延续 2026-04-23 fast report 重设计中确立的"确定性、不依赖向量检索"的检索哲学。

本次重设计闭合前一份 spec 与当前实现之间的五个具体差距：

1. evidence 装载的是拼接出的元数据文本（`File: …`、`signature: …`、`doc: …`、`imports: …`），而不是真源码。前一份 spec 中的四层模型已经承诺过"实现切片"，但代码从未接入。
2. 检索预算硬编码为 `seed=2 / depth=2 / result=4`，对跨模块流程类问题过浅，对所有 question_type 又过于一致。
3. fast report 路径只检索四层模型中的三层，Interpretive Context Layer 完全缺失。
4. 确定性索引（`fast_report_index.json`）只存文件级 token / imports / imported_by 信号加每个实体的头部信息。没有调用点、没有异常触点、没有配置触点，也没有任何仓库形态信号（完整目录树、hub 模块） —— 它能"定位"但既不能锚定 LLM 的规划，也不能支撑解释。
5. 单文件打分只保留得分最高的单一实体。多个实体都与问题相关的文件，在打分阶段（其余实体得分被丢弃）和切片锚定阶段（只有一个实体被切片）都损失信息。

本设计修复全部五个差距，且不改动用户面 report 流程、URL 模型、7 天 TTL、commit-SHA 绑定语义、以及前端 evidence rail 组件。

## 目标

- 用从已索引 clone 中读出的真实源码切片替换元数据形状的 evidence。
- 把 `fast_report_index.json` 从文件级 token 图升级为面向高 ROI question_type 的符号/触点级图，并新增仓库形态信号（完整目录树、hub 模块）以同时锚定 plan 和 generation。
- 让检索预算、扩展图、切片行数上限、单文件切片数全部由 `question_type` 驱动，而不是由唯一硬编码常量驱动。
- 允许单文件在多个实体都与问题强相关时贡献多条切片。
- 重新接入 Interpretive Context Layer，作为确定性的、绑定到源码的解释层 —— 它为 generation 提供燃料但**绝不**单独支撑 claim。
- 用仓库形态上下文加固 LLM plan 步骤，并对 `question_type` 做 enum 约束，让 plan 输出在新增的下游复杂度下保持可靠。
- 所有改动留在确定性检索路径内 —— 不引入 embeddings、不依赖 FAISS、不做向量相似度。
- 保持现有 `FastReportEvidenceBlock` schema、WebSocket 协议、报告 URL 模型、前端 rail 组件不动。

## 非目标

- 重新引入向量检索或 embedding-based retrieval 到 fast report 路径。
- 提供符号级 name resolution（reference sites / test assertion 明确推迟）。
- 维持对 `index_version: 1` 索引的向后兼容。新路径走严格模式，旧索引以 actionable error 拒绝。
- 改变报告 URL 模型、7 天 TTL 行为、四层检索框架、或 canonical heading 集合。
- 把源码切片持久化进 `fast_report_index.json`。切片在 generation 时抽取并仅持久化在报告记录里。
- 增加面向小上下文模型的可调参数（如 token budget 环境变量）。默认模型是 Sonnet 4.6（200k context），小模型本轮不在范围内。

## 架构 Delta

改动收敛在 worker pipeline 内部。前端、API DTO、`FastReportEvidenceBlock` 持久化 schema、以及 WebSocket 事件类型不变。

### 改动模块

- `worker/pipeline/fast_report_index.py` —— 索引 schema 升至 `index_version: 2`；新增字段抽取器；删除 `top_level_entries`。
- `worker/fast_report_search.py` —— 自适应检索算法；用按 `question_type` 的 profile 表替换固定的 `_SEED_LIMIT / _EXPANSION_DEPTH / _RESULT_LIMIT` 常量；多切片打分与 citation id；新增按图扩展模式。
- `worker/fast_report.py` —— 在 `retrieve_fast_report_layers` 中加入 Interpretive Context Layer；更新生成 prompt 以在显式无引用规则下嵌入该层；structure 层 signals 注入 `directory_tree`；emit 更细粒度的 `analysis_update` 事件。
- `worker/jobs.py` —— fast report 入口在检索前校验 `index_version`；索引过期时返回可执行的失败信息；删除遗留的 `top_level_entries` fallback 路径。

### 新增模块

- `worker/fast_report_slices.py` —— 纯函数式源码切片抽取器。从已索引 clone 按报告的 commit SHA 读取文件，返回 `{snippet_start, snippet_end, full_start, full_end, code, truncated_lines}` 结构。
- `worker/fast_report_interpretive.py` —— Interpretive Context Layer 装配器。从 `fast_report_index.json` 拉取 module docstring、entity docstring、leading comment、README section bodies，做确定性打分，返回 render-ready 的解释 bundle。
- `worker/fast_report_planning.py` —— planner 输入装配：从索引派生 `directory_tree`、`hub_modules`、`readme_headings` 视图供 plan prompt 注入；定义 `question_type` enum。

### 触动的数据形状

- `fast_report_index.json` —— 升至 `index_version: 2`。删除 `top_level_entries`。新增 `directory_tree`、`hub_modules`、`readme_sections`，每文件 `call_sites`、`exception_touchpoints`、`config_touchpoints`、`module_docstring`，每实体 `leading_comment`。
- `FastReportEvidenceBlock.code` —— 载荷变成真源码文本。dataclass 形状本身不变。`snippet_start` / `snippet_end` / `full_start` / `full_end` 的语义保持"切片行号区间和扩展边界"。
- `FastReportSectionResult` —— 新增内部 `interpretive_sources` 字段以便记入 analysis trace，但**不通过现有公共 DTO 暴露**，也**不在 evidence rail 渲染**。
- `FastReportCitation.id` —— code-evidence 的 citation id 由 `code-{N}` 改为 `code-{file_idx}-{entity_idx}`，以支持单文件多切片。

### 故意保持不动

- `FastReportCitation` 字段 schema（仅 `id` 格式约定改变）、`FastReportDiagram` schema、related wiki 链接规则、Mermaid sanitization、语言检测规则、canonical heading 集、arbitration 规则（仅 `code_evidence` ∪ `repository_structure`）。

## 索引 Schema v2

`fast_report_index.json` 升至 `index_version: 2`。新字段为追加式。删除一个遗留字段（`top_level_entries`），因为它是新 `directory_tree` 的严格子集。

### 顶层字段

```jsonc
{
  "index_version": 2,

  "directory_tree": "api/\n  main.py\n  jobs.py\n  routes/\n    repos.py\nworker/\n  fast_report.py\n  ...\n",

  "hub_modules": [
    {
      "path": "shared/fast_report_types.py",
      "in_degree": 14,
      "purpose": "Shared dataclasses for fast report citations, evidence blocks, ..."
    }
  ],

  "readme_headings": [
    "AutoWiki",
    "Architecture",
    "Generation Pipeline",
    "Deployment"
  ],

  "readme_sections": [
    {
      "heading": "Architecture",
      "body": "AutoWiki uses a 6-stage pipeline ..."   // 单段截断 ~800 字符
    }
  ],

  "files": { ... }
}
```

### 删除：`top_level_entries`

`top_level_entries` 就是 `directory_tree` 的第一层，留两份字段会引发漂移。从 schema 完全移除。原本读它的代码路径（worker/jobs.py 的 structure 层、worker/pipeline/fast_report_index.py）改为派生自 `directory_tree`，或者直接删除。

### `directory_tree`

单字符串，承载仓库过滤后文件树的紧凑嵌套表示。

- **来源**：`_collect_rel_paths` 收集到的全部路径，应用 gitignore + 标准排除清单后保留下来的部分。
- **格式**：嵌套缩进格式，每层 2 空格。目录以 `/` 结尾，文件作为纯叶子。每个目录下按字母序排序。
- **排除**：`.git`、`node_modules`、`dist`、`build`、`target`、`__pycache__`、`.next`、`.turbo`、`.venv`、`venv`、`.cache`、`.pytest_cache`、`coverage`、`.mypy_cache`、`.ruff_cache`、`*.pyc`、`*.lock`、`*.min.js`，以及 `.gitignore` 命中的任何路径。
- **soft target**：≤ 15k tokens（约 60k 字符）。绝大多数仓库远低于该值。
- **hard cap 与降级**：格式化后超 25k tokens 时回退到 depth-3-only 模式（深度 ≤ 3 的目录列出；深度 > 3 的层级仅保留 `hub_modules` 中出现的文件叶子）。仍超 25k 时按索引内实体数升序丢弃稀疏子目录直至合规。
- **索引时计算一次**；plan prompt 与 generation prompt 的 structure 层都复用同一份（零额外开销）。

### `hub_modules`

按 `in_degree`（`imported_by` 长度）排序的少量中枢模块清单 —— 仓库中被最多文件依赖的载荷。

- **计算**：对索引中所有文件按 `len(imported_by)` 降序排序，取 `in_degree >= 2` 的前 20 个。
- **每条字段**：`path`（相对路径）、`in_degree`（整数）、`purpose`（`module_docstring` 首句，截 120 字符；docstring 不存在时为 null）。
- **为什么是 hub 而不是 entry point**：真正的 entry point 是高 out-degree、低 in-degree 文件（如 `api/main.py`、`worker/jobs.py`）。在 9 种语言里可靠识别 entry point 需要每种语言的启发式（`if __name__ == "__main__"`、`[project.scripts]`、`package.json` `bin`、Go 的 `func main()`），精度边际效益低。Hub 模块用单一统一指标跨语言计算，对锚定 LLM 规划信息量等价。

### `readme_sections`

```jsonc
{
  "readme_sections": [
    {
      "heading": "Architecture",
      "body": "AutoWiki uses a 6-stage pipeline ..."
    }
  ]
}
```

`body` 是每个 heading 之后到下一个 heading 或文末之间的自然语言正文。索引时单段截断到 **800 字符**。`readme_sections` 累计载荷上限 **10k tokens**；超出时按 heading 出现顺序丢弃尾部 section。

`readme_headings` 作为独立字段保留 —— 它是 README 全部 heading 字符串的有序全集（不带 body）。它在 plan prompt 中被使用（README 完整结构概览），与 `readme_sections`（按问题排名后取 top-N + 正文）互补。

### 每文件字段

每个 `files[<rel_path>]` 条目：

```jsonc
{
  "path": "...",
  "tokens": [...],
  "imports": [...],
  "imported_by": [...],
  "external_deps": [...],
  "entities": [ /* 现在带 leading_comment */ ],
  "is_test": false,
  "is_config": false,

  "module_docstring": "Service for ...",        // 或 null

  "call_sites": [
    {
      "caller_symbol_path": "worker.jobs.run_fast_report",
      "callee_name": "plan_fast_report_search",
      "line": 412
    }
  ],

  "exception_touchpoints": [
    {
      "kind": "raise" | "throw" | "try" | "except" | "catch",
      "symbol_path": "worker.jobs.run_fast_report",
      "line": 437,
      "message": "Repository index is outdated"   // 或 null
    }
  ],

  "config_touchpoints": [
    {
      "kind": "read" | "write",
      "config_key": "AUTOWIKI_LLM_PROVIDER",
      "line": 88,
      "scope": "module" | "function"
    }
  ]
}
```

`entities[i]` 新增：

```jsonc
{
  "name": "...",
  "type": "...",
  "start_line": ...,
  "end_line": ...,
  "symbol_path": "...",
  "signature": "...",
  "docstring": "...",
  "leading_comment": "Single-pass AST analyzer ..."   // 或 null
}
```

### 字段语义

- **`call_sites`** 是 AST 级 call 表达式，按本文件的局部符号表解析。callee 记的是名字（不是完全解析的 symbol path），因为 AutoWiki 不做跨文件 name resolution。该名字足以支持扇出：检索时用 `callee_name` 在索引内匹配各文件 `entity.name` 即可定位被调目标文件。
- **`exception_touchpoints`** 记录 `try` / `raise` / `throw` / `except` / `catch` AST 节点。`message` 在为字面量参数（如 `raise ValueError("...")`）时捕获该字面量；非字面量为 null。测试文件中的 exception 站点照常 emit，但通过文件的 `is_test` 标识 —— 检索层据此把"被测试驱动"的扩展挡在生产流程类问题之外。
- **`config_touchpoints`** 通过每语言的小白名单识别：`os.environ.get` / `os.getenv`（Python），`process.env.X`（JS/TS），`viper.GetString`（Go），`System.getenv`（Java），以及现有 `is_config` 启发式识别出的任何模块的读取。`config_key` 在为字面量字符串时捕获。
- **`leading_comment`** 是紧贴实体 start_line 上方、与之间无空行的连续注释块。仅块状注释和语言原生 docstring 计入；函数体内注释不计。
- **`module_docstring`** 是文件级 docstring（Python 顶部 `"""..."""`、JS/TS 文件首部 `/** ... */` JSDoc、Go package comment）。语言/文件不适用时为 null。
- **`symbol_path`** 是 `<rel_path 去扩展名后用点替换斜杠>.<entity_name>`。`worker/fast_report.py` + `plan_fast_report_search` → `worker.fast_report.plan_fast_report_search`。这**不是**各语言原生限定符；它是一个跨 9 种语言均适用的统一合成标识符，便于 substring 匹配。plan prompt 显式告诉 LLM 在写 `retrieval_focus` 时使用该约定。

### 构建成本预期

新字段搭单次 AST 分析的车 —— 不做二次 parse。`directory_tree` 与 `hub_modules` 在 AST 分析后从已收集的路径与 import 边派生。`tests/fixtures/simple-repo` 上预期索引耗时 +20% ~ +40%；`fast_report_index.json` 体积 +30% ~ +60%，主要来自 `readme_sections`、`directory_tree` 与 `call_sites`。

## Plan 步骤输入与加固

LLM plan 步骤是**唯一**把自然语言问题转换为下游检索结构化 intent 的入口（影响 budget profile、扩展图、切片行数上限、slices_per_file）。它的输出质量决定了下游检索的上限。本节规范 plan prompt 的输入清单和让其输出可靠的加固规则。

### 当前 planner 输入审计

当前 `plan_fast_report_search` prompt（worker/fast_report.py:241-250）只给 LLM 看了四件东西：

| 输入                  | 来源                              |
|-----------------------|-----------------------------------|
| 仓库名               | `repo_name` 参数                   |
| 用户问题             | 用户原始字符串                       |
| 输出语言提示         | LLM 调用前在本地检测                  |
| JSON schema 字段     | 嵌入在 prompt 指令里                  |

LLM **完全看不到**仓库的文件树、模块、README、符号、任何索引信号。它从问题文本和模型自带的知识（如果有）中"猜"路径与符号。这是当前 `retrieval_focus` hint 不精确的根因。

### Plan 输出字段及下游消费表

`FastReportQuestionIntent` 含 7 个字段。每个字段都有至少一个下游消费；本步骤一旦遗漏或幻觉会传染到检索。

| 字段              | 例子                                                  | 下游消费                                                                                                                                                              |
|-------------------|-------------------------------------------------------|-----------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `language`        | `"en"` / `"zh"`                                       | `assemble_fast_report_markdown`（canonical heading 翻译）；`_build_generation_prompt`（输出语言提示）。                                                                |
| `question_type`   | `"execution_flow"`                                    | **驱动整套自适应检索 profile**：预算表、扩展图、切片行数上限、`slices_per_file`。同时进入 `query_tokens` 用于打分。                                                    |
| `target`          | `"fast report retrieval pipeline"`                    | 进入 `query_tokens`；在 generation prompt 里原样透出。                                                                                                                |
| `answer_shape`    | `"step-by-step explanation with code anchors"`        | 进入 `query_tokens`；在 generation prompt 里原样透出。                                                                                                                |
| `evidence_shape`  | `"function bodies showing call chain"`                | 进入 `query_tokens`；含 `"config"` 字样时触发 config 文件白名单。                                                                                                     |
| `search_terms`    | `["retrieve", "code evidence", "expansion"]`          | 每条被 tokenize 后并入 `query_tokens`。在 generation prompt 中透出。                                                                                                  |
| `retrieval_focus` | `["worker.fast_report.retrieve_fast_report_layers"]`  | **影响最强的字段**：触发 `_focus_hint_score`（+4 ~ +14 加成），通过 `_is_low_signal_entry` 覆盖让 test/config 文件被强制保留。在 prompt 中透出。                       |

`question_type` 与 `retrieval_focus` 是检索质量决定性最强的两个字段。

### 加固 v1

三个改动让 planner 输出可靠：

#### 1. `question_type` 改 enum

schema 把 `question_type` 约束到固定集合：

```python
"question_type": {
    "type": "string",
    "enum": [
        "architecture",
        "execution_flow",
        "dependency",
        "error_handling",
        "configuration",
        "testing",
        "implementation_location",
        "unknown",
    ],
},
```

provider 端 structured-output 校验（Anthropic / OpenAI / Gemini）会把这条 enum 当硬约束。planner 不能再返回 `"general"` / `"how_X_works"` 这种绕过 budget profile 表的自由字符串。

#### 2. 仓库形态上下文注入

plan prompt 新增四块派生内容，全部来自索引 —— 请求时零额外计算：

| Plan prompt 段              | 来源                              | 用途                                                                                              |
|------------------------------|-----------------------------------|----------------------------------------------------------------------------------------------------|
| `Directory tree:`            | `index.directory_tree`            | 真实路径级可见性，让 `retrieval_focus` 能写出实际存在的文件/模块。                                |
| `README headings:`           | `index.readme_headings`（top 12） | 仓库自描述的章节结构。                                                                            |
| `Hub modules:`               | `index.hub_modules`（top 20）     | 最被依赖的模块的名字与一句话用途；帮 plan 选对子系统。                                            |
| `Symbol path convention:`    | 静态指令                          | 显式告诉 LLM："`retrieval_focus` 用 `module.path.symbol_name` 形式（路径斜杠→点，去扩展名）"。 |

预估增量：~3k–18k tokens，按仓库规模而定。planner 用 `fast_llm`（Haiku 级），200k 上下文容得下；即便 50k 行的目录树也无压力。

#### 3. 单轮反馈重试

当 plan 解析结果退化 —— `question_type == "unknown"` **且** `search_terms` 与 `retrieval_focus` 都为空 —— 用反馈 prompt 再调用 planner 一次：

```text
Your previous plan returned no question_type and no retrieval hints. The
repository has the following structure:

{directory_tree summary}

{readme_headings}

Re-plan the search. Choose one of the enumerated question_type values and
return at least one retrieval_focus hint pointing at a real path or symbol.
```

只重试一次。第二次仍退化时按解析结果落地；下游 deterministic 检索通过 question 文本 token 重合仍能给出可用结果。

重试路径与初次调用共享同一个 prompt-cache 边界（directory tree 在可缓存的 system 段中）。

## 自适应检索

当前代码以 `seed=2 / depth=2 / result=4` 检索 4 个文件，且不分 question_type 全部走 `imports + imported_by`。新路径按 `question_type` 参数化。

### 预算 profiles

| `question_type`           | seed | depth | result_limit | code_evidence token 预算 | 单切片行数上限 | `slices_per_file` |
|---------------------------|------|-------|--------------|--------------------------|----------------|-------------------|
| `architecture`            | 4    | 3     | 12           | 50k                      | 40             | 3                 |
| `execution_flow`          | 3    | 3     | 10           | 50k                      | 50             | 2                 |
| `dependency`              | 3    | 2     | 10           | 40k                      | 30             | 1                 |
| `error_handling`          | 2    | 2     | 8            | 35k                      | 40             | 2                 |
| `configuration`           | 3    | 2     | 8            | 35k                      | 30             | 2                 |
| `testing`                 | 2    | 1     | 6            | 40k                      | 60             | 2                 |
| `implementation_location` | 2    | 1     | 4            | 25k                      | 200            | 1                 |
| _(default / unknown)_     | 2    | 2     | 6            | 40k                      | 50             | 1                 |

`result_limit` 控制进入切片抽取阶段的 distinct 文件数。`slices_per_file` 限制单文件可贡献的切片数（见下文「多切片打分」）。`code_evidence token 预算` 是最终守卫：所选切片 tokenized 累计载荷一旦超出预算，就按 `score` 升序丢弃直至载荷合规。被丢弃的切片直接移除（不降级为元数据），丢弃数计入 analysis trace。

token 估算用粗粒度的 `len(text) / 4`，不引入 tiktoken。

### 扩展图

种子文件打分算法（`worker/fast_report_search.py` 的 `_score_file`，加上下文「多切片打分」扩展）所有 question_type 共享。变化的是种子如何扩展为最终选择集。

| `question_type`           | 主扩展图                                | 次级 fallback                         |
|---------------------------|-----------------------------------------|---------------------------------------|
| `architecture`            | `imports + imported_by`                 | 同 package 兄弟文件                   |
| `execution_flow`          | `call_sites`（callee → caller，双向）   | `imports`                             |
| `error_handling`          | `exception_touchpoints` 共现文件        | `imports`                             |
| `configuration`           | `config_touchpoints`（按 key 匹配）     | 仓库内 `is_config` 文件               |
| `dependency`              | `imports + imported_by`                 | `external_deps` 重合                  |
| `testing`                 | 同目录兄弟文件 + token 重合             | `imports`                             |
| `implementation_location` | `imports`（仅一跳）                     | 无                                    |
| _(default / unknown)_     | `imports + imported_by`                 | 无                                    |

扩展是受 `depth` 限制的 BFS。每跳先从主图取邻居；主图为空则查次级 fallback。一旦累积到 `result_limit` 个候选，停止扩展。

### 各扩展图机制

- **`imports + imported_by`** —— 现有行为，保留。
- **`call_sites`** —— 对每个种子文件的 `call_sites`，在索引中找任意 `entity.name` 与 `callee_name` 匹配的文件；反向（找其 `call_sites.callee_name` 匹配种子实体的文件）覆盖 caller 扇出。
- **`exception_touchpoints`** —— 含 `raise` / `throw` / `try` 块且引用与种子相同 symbol path 或 message tokens 的文件。除 `testing` 类问题外，测试文件被排除。
- **`config_touchpoints`** —— 读/写与种子 `config_touchpoints` 相同 `config_key` 的文件，加上定义该 key 的 `is_config` 文件。
- **同目录兄弟文件 + token 重合** —— 与种子位于同一目录、token 与问题重合的文件。

### 多切片打分

当前 `_score_file` 通过 `if candidate_score > entity_score` 只保留每文件最佳实体。多个实体都相关的文件在打分（其余实体不贡献文件得分）和切片锚定（只有一个实体被切片）两个阶段都丢信息。

新算法：

1. 用现有公式计算文件中每个实体的得分：
   `entity_score = 2 * |query_tokens ∩ _entity_tokens(entity)| + _focus_hint_score(entity, ...)`。
2. 按 `entity_score` 降序排序实体。
3. 设 `K = profile.slices_per_file`。
4. 取前 K 个实体，**附加约束** `entity_score >= 0.5 * top_entity_score`（即便在前 K 内，弱命中实体也丢弃）。
5. **文件得分** = `file_level_score + sum(selected_entity_scores)`。文件排名因此反映组合相关性，而不仅是单一最佳命中。
6. **切片产出**：每个被选中实体产生一个独立 `SliceResult`，按其 `start_line` / `end_line` 锚定，使用 question_type 的单切片行数上限。每个切片获得形如 `code-{file_idx}-{entity_idx}` 的 citation id（file_idx 按选择顺序，entity_idx 按文件内得分顺序）。

无实体文件（如纯 config 文件被 `config_touchpoint` 命中）只产生一个切片，以触点行为锚点。

token-budget 守卫覆盖**所有切片**（不是按文件），所以一个文件贡献 3 个切片就会在预算账上记 3 行。

### 切片抽取参数

文件选择集敲定后，每个被选中实体被切片。切片始终从实体的 `start_line` 起。实体体超出 question_type 单切片行数上限时切片到 `start_line + cap`，并在切片文本尾部追加一行注释式标记：

```text
# … 47 more lines truncated
```

每侧 **±5 行上下文**（前一份 spec 是 ±3）。`FastReportEvidenceBlock.full_start` 和 `full_end` 继续暴露前端用的 +15 行展开边界。

## 真实源码切片抽取

新增模块 `worker/fast_report_slices.py` 负责切片抽取。

### 公开 API

```python
@dataclass(frozen=True)
class SliceResult:
    snippet_start: int        # 1-based, 闭区间
    snippet_end: int          # 1-based, 闭区间
    full_start: int           # snippet_start - 5，下截至 1
    full_end: int             # snippet_end + 5，上截至文件长度
    code: str                 # 真源码，不是元数据
    truncated_lines: int      # 0 表示完整实体在切片上限内

def extract_source_slice(
    *,
    clone_root: Path,
    rel_path: str,
    anchor_start: int,
    anchor_end: int,
    line_cap: int,
    context_lines: int = 5,
) -> SliceResult | None: ...
```

### 行为

- 以 UTF-8（`errors="replace"`）读取 `clone_root / rel_path`。
- 文件不存在或读取失败（二进制、权限）时返回 `None`。调用方直接丢弃该 citation；报告其余部分继续。这是**唯一**会让 citation 静默失败的失败模式。
- 切片有效区间 `[anchor_start, min(anchor_end, anchor_start + line_cap - 1)]`。
- 实体体超过上限时设 `truncated_lines = anchor_end - (anchor_start + line_cap - 1)`，并在 `code` 末尾追加一行语言适配的尾标记 `<comment_token> … {N} more lines truncated`。注释 token：`#` 用于 Python / Ruby / Bash；`//` 用于 JS / TS / Java / Go / Rust / C / C++ / C#。
- 源码 tab 保留。本层不做格式化、归一化或语法高亮 —— 前端已处理真源码的语法高亮。

### Commit-SHA 绑定

切片抽取从位于 `~/.autowiki/repos/{repo_hash}/clone/` 的**单一工作树**读取。clone 始终在最新已索引 commit 上。每个报告并不维护独立的 SHA 工作树。

generation 时，clone 总是与报告的 `commit_sha` 一致 —— 因为 generation 紧跟 indexing 执行。所以装入报告记录的切片与该次 indexed commit 一致。仓库被在更新 commit 上重索引后，clone 前进，但持久化的切片仍然冻结在原 SHA 上。下文 invalidation 规则决定 reopen 时如何处置。

## Interpretive Context Layer

新增模块 `worker/fast_report_interpretive.py` 负责该层。

### 数据来源

该层只从 `fast_report_index.json` 拉取 —— 不再读取原始源码。v1 仅允许以下来源：

1. 多切片打分阶段选中的每个实体的 **entity docstring**（不再是每文件一个）。
2. 每个被选中文件的 **module docstring**（`module_docstring`）。
3. 每个被选中实体的 **leading comment**（`leading_comment`）。
4. 按问题排名的 **README section bodies**。

`docs/` 目录扫描、函数体内注释、设计文档文件 v1 明确不在范围内。

### 选取规则

对 code evidence 层选中的每个实体（多切片规则下可能每文件多个），interpretive 层**自动绑定**：该实体的 `docstring`、`leading_comment`、所属文件的 `module_docstring`（任何非空字段）。自动绑定不打分，绑定关系是结构性的。

自动绑定的 docstring + leading comment 累计载荷上限为 **8k tokens**。超出时按 `entity_score` 升序丢弃直至合规。这道闸防止单个 200 行的 module docstring 把其它内容挤出 prompt。

此外，README section bodies 单独排名：

- 排名 token 来自 `intent.search_terms ∪ intent.retrieval_focus ∪ tokens(question)`。
- 得分 = `|tokens ∩ tokens(heading + body)|`。
- 取 **top 5**（前一份 spec 是 3）。每段 body 硬截 **800 字符**（前一份是 400）。section 累计载荷硬截 **10k tokens**（前一份是 3k）。

### Prompt 位置

generation prompt 在现有 `Code evidence layer:` 与 `Curated knowledge layer:` 之间新增一段：

```text
Interpretive context layer:
- Module docstring (worker/fast_report.py): Fast report domain service.
- Entity leading comment (FastReportPipeline): Bottom-up child-synthesis ...
- Entity docstring (plan_fast_report_search): Plan fast-report retrieval ...
- README section "Architecture": AutoWiki uses a 6-stage pipeline ...

Use this layer only to explain or connect code evidence. Never cite this layer
as primary support for a claim. Final claims must still cite repository_structure
or code_evidence.
```

### Citation 政策

- interpretive 层**不产出 `FastReportCitation` 记录**。
- 传给 `arbitrate_report_claims` 的 `available_citation_ids` **不包含**任何 interpretive 标识。
- 因此任何只引用 interpretive id 的 claim 都会在 arbitration 阶段被丢弃。这维持前一份 spec 的 arbitration 规则（仅 `code_evidence` ∪ `repository_structure`）。
- evidence rail 不渲染 interpretive 内容。前端零新增组件。

### 为什么不渲染

把 interpretive sources 显示在 rail 里会引导用户把它当成与代码同等的证据，违反 arbitration 规则。Interpretive 是 generation 的燃料，不是用户面证据。

## 持久化与失效

唯一影响用户的行为变化。

### 既有规则（不变）

- 报告持久化 7 天。
- 报告记录 `commit_sha`。
- TTL 内 reopen 不触发 LLM 调用。

### 新规则：SHA 不一致即失效

报告被打开时（`GET /api/repos/{repo_id}/fast/{report_id}`）：

- 比对 `report.commit_sha` 与 `repository.last_indexed_commit_sha`。
- 一致：从持久化状态正常渲染。
- 不一致：返回与 7 天 TTL 同样的 expired 响应（HTTP 410，前端渲染过期态并提供 regenerate）。**持久化记录不删除** —— API 只是拒绝服务。
- 该规则与 `expires_at` 是否到达**无关**，独立判定。

### 为什么这是有意保守

持久化的切片本身稳定（generation 时已捕获）。理论上用户可以继续读一份"陈旧但自洽"的报告。但仓库被重索引到新 commit 后，叙述可能引用 HEAD 上已经不存在的行为。用户明确要求在这种情形下硬失效，让 URL 自带"这份报告是新鲜的"这层保证。

### 衍生影响

- 一次 reindex（无论全量还是增量）会让该仓库**所有**未到 TTL 的 fast report 失效。
- 重索引时报告**不**做垃圾回收 —— 仅是被隐藏在过期态后，等 7 天 TTL sweeper 清理。持久化层保持简单。
- 前端对两种过期原因（TTL 到期、SHA 不一致）渲染同一个过期态。过期态提供 "Regenerate" CTA。两种原因在文案上的区分 v1 不做。

## 迁移：index_version v2

### 硬切换

不存在对 `index_version: 1` 的并行路径支持。

### 检测

`worker/jobs.py` 的 `run_fast_report`（或对应入口）在检索前加载 `fast_report_index.json` 并检查 `index_version`：

- 缺失或 `< 2`：短路。返回结构化错误：

  ```json
  {
    "error": "fast_report_index_outdated",
    "message": "Repository index is outdated for fast reports. Run `autowiki index <repo>` to upgrade.",
    "actionable_command": "autowiki index <repo>"
  }
  ```

  WebSocket 推送一条 `error` 事件然后关闭。REST POST 返回 HTTP 409 Conflict。

### 既有 pipeline 不受影响

`fast_report_index.json` 仅由 fast report 路径消费。Wiki 生成、deep research、chat、refresh、validate-plan 都不读它。所以索引过期不会阻断其他产品面 —— 仅 fast report 入口被拦。

### Reindex 流程

用户运行 `autowiki index github.com/owner/repo` 升级索引。`--reuse-index` 仅跳过 FAISS 重建、不跳过 AST 分析，因此一次普通 reindex 会自动重建 `fast_report_index.json`。无需新 flag。

## Generation Prompt 改动

`worker/fast_report.py` 的 `_build_generation_prompt` 获得增强的 structure 层、interpretive 段，以及一处用语调整以引导模型读真源码。

### Structure 层增强

structure 层 signals 从原本的三行（top_level_entries / readme_headings[:6] / README 首 160 字符）扩展为：

```text
Repository structure layer:
- Directory tree:
  api/
    main.py
    ...
- README headings: AutoWiki, Architecture, Generation Pipeline, Deployment, ...
- README first paragraph: AutoWiki is a self-hosted, open-source AI-powered wiki ...   (截 400 字符)
- Hub modules:
  - shared/fast_report_types.py — Shared dataclasses for fast report ...
  - worker/llm/base.py — Abstract base for LLM provider implementations ...
  ...
```

目录树和 hub 模块**贡献上下文，不发 citation**。`RepositoryStructureLayer.citations` 继续仅 emit 一条锚 README.md 的 citation（struct-1）。LLM 即使在叙述里提到目录树中的某条路径，没有附带 code-evidence citation 的 claim 仍会在 arbitration 阶段被丢弃 —— 与今天行为一致。

### Curated 层用语调整

- Wiki 页摘要截断从 200 提至 400 字符（`_curated`）。

### 最终 prompt 结构

```text
Repository structure layer:
{directory_tree, readme_headings, README first paragraph, hub_modules}

Code evidence layer:
{format_retrieved_chunks_for_prompt(layers.code_evidence.snippets)}
   (现在含真源码切片，包括 slices_per_file > 1 时的同文件多切片)

Interpretive context layer:
{auto-attached docstrings/leading_comments + top-5 README sections}

Use this interpretive layer ONLY to explain or connect code evidence.
Never cite it as primary support. Final claims must cite repository_structure
or code_evidence ids.

Curated knowledge layer:
{up to 3 wiki pages, summaries up to 400 chars}
```

`format_retrieved_chunks_for_prompt` 已经源码感知（接受 `{file, start_line, end_line, text}`），把真源码穿进去无需上游改动。

arbitration 步骤（`arbitrate_report_claims`）不变。

## 可观测性

`analysis_update` WebSocket 事件获得结构化 phase。事件 schema 不变；只是值更细。

### 新 phase 标识

| `phase`                       | 一并发出                                                                                |
|-------------------------------|------------------------------------------------------------------------------------------|
| `index_check`                 | `{ index_version }`                                                                      |
| `search_plan`                 | `{ question_type, search_terms[], retrieval_focus[], plan_retried: bool }`               |
| `code_evidence_seed`          | `{ files: [{path, score}] }` 种子集                                                      |
| `code_evidence_expansion`     | `{ files: [{path, role, score}], graph: "call_sites" \| ... }`                          |
| `slice_extraction`            | `{ files: [{path, slices: [{entity, lines, truncated_lines}]}], dropped_due_to_budget }` |
| `interpretive_layer`          | `{ entity_docs, module_docs, readme_sections }` 计数与 dropped_due_to_cap                |
| `generation`                  | `{ prompt_token_estimate }`                                                              |
| `arbitration`                 | `{ claims_kept, claims_dropped }`                                                        |

`slice_extraction` 嵌套返回每文件多切片的详细信息，便于观测多切片产出。

`report_section.analysis_trace` 持久化完整有序事件列表，reopen 时呈现的 trace 与 generation 时一致。reopen 不会重新发射事件。

### 日志

每次重试、每次 fallback、每次切片丢弃都走 `worker/pipeline/pipeline_logging.py`（依 CLAUDE.md 的 "Pipeline observability" 规则）。禁止 `except: pass` 静默吞错。

## 测试策略

### 单元测试

- `fast_report_slices.extract_source_slice`
  - happy path：返回区间内的真源码
  - 文件缺失：返回 `None`
  - 行号区间超过文件长度：截到文件尾，不抛异常
  - 实体超 cap：返回带尾标记 `… N more lines truncated` 的截断切片，每个支持语言验证
  - 上下文边界：`full_start = max(1, anchor_start - 5)`、`full_end = min(file_len, anchor_end + 5)`
- `fast_report_index` v2 构建
  - schema 含 `index_version: 2`、全部新字段，**且不含** `top_level_entries`
  - `directory_tree` 是非空嵌套字符串，应用文档约定的排除清单
  - `directory_tree` 在超 25k tokens 硬上限时回退到 depth-3-only 模式
  - `hub_modules` 按 `len(imported_by)` 排序、含 `module_docstring` 首句作为 `purpose`
  - call_sites 在一组 fixture 中跨文件调用时被采集
  - exception_touchpoints 捕获 `raise ValueError(...)` 与 `try/except`
  - config_touchpoints 捕获 `os.getenv("X")`
  - leading_comment 抓取实体上方紧邻注释块；忽略空行隔开的注释
  - readme_sections 遵守 800 字符/段与 10k tokens 累计上限，emit top 5
- `fast_report_planning`
  - `question_type` enum 被强制 —— 非 enum 值在 provider 层被 reject（mock）
  - 退化的 plan 输出触发恰好一次反馈重试
  - directory_tree、hub_modules、readme_headings 出现在构造的 plan prompt 中
- `fast_report_search` 自适应
  - profile 表查询返回各 `question_type` 期望的 `(seed, depth, result, token_budget, line_cap, slices_per_file)`
  - `execution_flow` 扩展使用 `call_sites`，忽略 `imported_by`
  - `error_handling` 扩展使用 `exception_touchpoints`，排除测试文件
  - `configuration` 扩展使用 `config_touchpoints`，按 `config_key` 匹配
  - **多切片打分**：在 `architecture`（slices_per_file=3）下，含三个高分实体的文件 emit 三个切片，citation id 为 `code-{i}-0`、`code-{i}-1`、`code-{i}-2`
  - **多切片阈值**：得分 < 文件最佳实体得分 50% 的实体被丢弃，即便 slices_per_file 还有名额
  - 超预算驱逐丢弃低分切片，并记录丢弃数
- `fast_report_interpretive`
  - 自动绑定的 docstring/comment 仅来自 code evidence 层中的实体（每文件可能多个）
  - 自动绑定累计载荷上限 8k tokens，超出按 entity_score 升序丢弃
  - README section 排名按 token 重合；返回 top-5；超载时丢弃
  - interpretive 载荷产生零 `FastReportCitation`
- `worker/fast_report._build_generation_prompt`
  - structure 层 signals 含 `directory_tree`、`readme_headings`、README 首段（≤400 字符）、`hub_modules`
  - structure 层无论目录树多大，只 emit 一条 citation（struct-1，README）
- `worker/jobs` index_version 守卫
  - version 缺失 → 409，含 `actionable_command`
  - `index_version: 1` → 409
  - `index_version: 2` → 通过
- 持久化
  - `commit_sha` 一致时 reopen 不调 LLM 即返回持久化 markdown
  - `commit_sha` 不一致时 reopen 返回过期态（与 TTL 过期共用路径）

### 集成测试

- `tests/fixtures/simple-repo` 在 v2 上重建索引；为 `execution_flow` 类问题生成报告，evidence rail 载荷含真源码行（不再是 `File: …` 元数据）。
- 为 `architecture` 类问题生成第二份报告；验证扩展更广（slice extraction trace 中 ≥4 文件）且至少一个文件 emit 多切片（slices_per_file=3）。
- 为 `error_handling` 类问题生成第三份报告；验证 exception_touchpoints 扩展生效（trace 中扩展 graph 值为 `exception_touchpoints`）。
- 上述任一报告的 plan prompt 包含 `Directory tree:` 块。
- 模拟在不同 commit 重索引同一仓库（DB 中改 SHA），任何已有报告 reopen 都返回过期态。

### 覆盖率目标

`worker/` 与 `api/` 覆盖率维持 ≥80% 现状。新增模块（`fast_report_slices.py`、`fast_report_interpretive.py`、`fast_report_planning.py`）行覆盖 ≥85%，以补偿 `fast_report_index.py` 中略复杂的 AST 抽取代码。

## 风险

- **索引耗时回归**。触点抽取增加 AST 遍历成本。`directory_tree` 与 `hub_modules` 在 AST 分析后派生。缓解：每个新抽取器复用单次分析已经产出的 AST 树，不做二次 parse。验收阈值：`tests/fixtures/simple-repo` 上 wall-clock 回归 ≤ 50%。
- **索引体积膨胀**。`readme_sections`、`directory_tree`、`call_sites` 是主要贡献者。缓解：单段截断（800 字符）、累计上限（10k tokens）、`directory_tree` 超 25k 自适应降级。
- **Tree-Sitter 触点保真度按语言不一**。Python 抽取最干净；C/C++/C# 的 exception/config 触点精度较低。v1 提供尽力而为的抽取器。某语言抽取得到零触点时自动回退到次级扩展图 —— 不崩、只是粗一些。
- **切片抽取依赖在线 clone**。clone 缺失或部分损坏时，单条切片失败。pipeline 必须带剩余 citation 继续。完全无法读取 clone 视为硬错误向用户报出（与今天的行为一致）。
- **重索引会让所有报告失效**。这是用户明确要求的不变量。前端如何呈现归 frontend 团队负责；v1 复用现有 TTL 过期态。
- **Prompt token 成本明显上升**。真源码切片 + 多切片 emit + 增大的 interpretive 载荷 + structure 层增强叠加，使典型 generation prompt 升至 ~80k–100k tokens。各 question_type 的 token 预算控住增量。Sonnet 4.6 ($3/M input)，单份报告成本约 $0.30 —— 是有意为之的质量/成本取舍。
- **多切片 citation id 改格式**。既有持久化报告用 `code-{N}` 形式 id；新报告用 `code-{file_idx}-{entity_idx}` 形式。无迁移问题，因为报告记录是自包含的 —— 旧报告保留旧 id 在持久化 markdown 中，新报告用新 id；前端按字符串相等匹配 id，不解释格式。

## 验收标准

- 升级后 indexer 写入的 `fast_report_index.json` 携带 `index_version: 2`，并填充 `directory_tree`、`hub_modules`、`readme_sections`、`call_sites`、`exception_touchpoints`、`config_touchpoints`、`module_docstring`、每实体 `leading_comment`，**且不含** `top_level_entries`。
- 在 `tests/fixtures/simple-repo` 上生成的 fast report 其 evidence block `code` 字段含真源码行，可通过抽样核对：渲染文本与该行号区间的源码内容一致。
- `architecture` 或 `execution_flow` 类问题的报告选中超过 4 个文件（即原硬编码 `_RESULT_LIMIT = 4` 不再封顶）。
- 在含多实体文件的 fixture 上生成 `architecture` 类报告，至少一个文件 emit 三个切片，每个切片 citation id 形如 `code-{file_idx}-{entity_idx}`，互不相同。
- `error_handling`、`configuration`、`execution_flow` 类问题在其 `analysis_trace` 中分别使用对应扩展图（`exception_touchpoints`、`config_touchpoints`、`call_sites`）。
- plan prompt 含 `Directory tree:` 块、`README headings:` 块、`Hub modules:` 块；plan 输出的 `question_type` 是 8 个 enum 值之一。
- generation prompt 的 structure 层 signals 含 `Directory tree:` 块，且 structure 层只 emit 一条 citation（锚 README）。
- generation prompt 中出现 Interpretive Context Layer，且不产生 `FastReportCitation`；arbitration 丢弃任何只引用 interpretive 的 claim。
- `index_version` 不为 2 的索引导致 `POST /api/repos/{repo_id}/fast` 返回 HTTP 409 与 actionable error 载荷，WebSocket emit 单条 `error` 事件后关闭。
- 在 commit `X` 上生成的报告，在仓库被重索引到 SHA `Y ≠ X` 后 reopen 时返回过期态，与 7 天 TTL 是否到达无关。
- `tests/fixtures/simple-repo` 上索引 wall-clock 回归 ≤ 50%。
- 同一问题在同一 commit 上生成的报告：确定性各层（search plan、retrieval、slice extraction）字节稳定。LLM 叙述自然不确定，但 citation 集合、扩展路径、切片行号区间可复现。

## 待定问题

- 是否把扩展图选择（如 `analysis_trace.code_evidence_expansion.graph`）暴露到前端 evidence rail header。v1 不做；如收到用户反馈再启动。
- 是否把每个 call_site 的方向（caller-of / callee-of）记入 analysis trace 以便深调试。等到真有调试需求再加。
- 是否允许 LLM planner 通过单一 `wants_broader_context: bool` 标志覆盖预算 profile（brainstorming 中的选项 C）。推迟 —— 先严格、需要时再放宽。
- 是否让 `slices_per_file` 同时驱动 interpretive 自动绑定的预算（当前自动绑定只受 8k 累计上限约束）。等观察到 interpretive 载荷在多切片大文件上集中后再决定。

## 附录 A：Tokenizer 规则

tokenizer（`worker/fast_report_search.py` 的 `_tokenize`）被文件级 token 计算、实体级 token 计算、query token 装配、README section 排名、wiki 页排名共享。它的行为对整条检索路径都是关键的。本附录完整记录其规则，便于在测试或实现讨论中引用。

### 输入与输出

- 输入：任意字符串。
- 输出：长度 ≥ 2 的小写归一化 token 集合 `set[str]`。

### CJK 规则

- "CJK 连续段"是匹配 `[㐀-䶿一-鿿豈-﫿぀-ヿ가-힯]+` 的最长连续子串。
- 对每个 CJK 连续段：
  - 整段作为一个 token 加入。
  - 段内每个连续 bigram 加入。
  - 段内每个连续 trigram 加入。

### ASCII 规则

- 字符串先 lowercase，并把标点 `/` 与 `.` 替换为空格（`worker.fast_report.X` → `worker fast report X`）。
- 用 `[^A-Za-z0-9]+`（任意非字母数字段，含 `_`、`-`、` `、`,`、`(`、`)` 等）切分。
- 每个切片在 camelCase 边界 `(?<=[a-z0-9])(?=[A-Z])` 进一步切分。
- 每个切片片段 lowercase + strip；长度 < 2 的丢弃。

### 示例

| 输入                                                  | 切出的 token                                                                  |
|-------------------------------------------------------|--------------------------------------------------------------------------------|
| `retrieve_fast_report_layers`                         | `retrieve`、`fast`、`report`、`layers`                                         |
| `getUserConfig`                                       | `get`、`user`、`config`                                                        |
| `worker.fast_report.retrieve_fast_report_layers`      | `worker`、`fast`、`report`、`retrieve`、`layers`                               |
| `os.environ.get("API_KEY")`                           | `os`、`environ`、`get`、`api`、`key`                                           |
| `path/to/file.py`                                     | `path`、`to`、`file`、`py`                                                     |
| `配置加载`                                            | `配置加载`、`配置`、`置加`、`加载`、`配置加`、`置加载`                         |
| `RetryStrategy`                                       | `retry`、`strategy`                                                            |
| `HTTPClient`                                          | `httpclient`（连续大写之间不切）                                                |

### 打分中的 token 来源

- **文件级 token**（`_file_tokens` 索引时预算）：`rel_path` + 每个实体的 `name + symbol_path + signature` 切出的 token 并集。**不含 docstring**。
- **实体级 token**（`_entity_tokens` 检索时即时算）：`entity.name + symbol_path + signature + docstring` 切出的 token。**含 docstring**。
- **查询 token**（`_query_tokens`）：`question` ∪ `intent.question_type` ∪ `intent.target` ∪ `intent.answer_shape` ∪ `intent.evidence_shape` ∪ `intent.search_terms` 每条 ∪ `intent.retrieval_focus` 每条，全部 tokenize 后并集。

docstring 仅在实体级被纳入是有意为之的 —— 文件级 token 集合需要落盘，纳入 docstring 会把索引体积撑大；实体级 token 在内存中即时算，纳入 docstring 只增 CPU 而不增存储。

## 附录 B：Token 预算总表

fast report 路径上每一处数值上限的 single source of truth。调参时先改本表，再向实现代码传播。

### 索引时上限

| 上限                                              | 值              | 应用位置                                       |
|---------------------------------------------------|-----------------|------------------------------------------------|
| `readme_sections` 单段 body                       | 800 字符        | `fast_report_index._extract_readme_sections`   |
| `readme_sections` 累计                            | 10k tokens      | 同上                                           |
| `directory_tree` soft target                      | 15k tokens      | `fast_report_index._build_directory_tree`      |
| `directory_tree` hard cap（触发降级）            | 25k tokens      | 同上                                           |
| `hub_modules` 数量                                | top 20          | `fast_report_index._compute_hub_modules`       |
| `hub_modules` purpose 首句截断                    | 120 字符        | 同上                                           |

### Plan prompt 上限

| 上限                              | 值            | 应用位置                                  |
|-----------------------------------|---------------|-------------------------------------------|
| 注入的 `readme_headings`          | top 12        | `fast_report_planning.build_plan_prompt`  |
| 注入的 `directory_tree`           | 完整（≤25k） | 同上                                      |
| 注入的 `hub_modules`              | top 20        | 同上                                      |

### Generation prompt 上限（按 question_type）

参见**自适应检索 / 预算 profiles** 表为 canonical 值：

| `question_type`           | code_evidence token | 单切片行数上限 | slices_per_file |
|---------------------------|---------------------|----------------|-----------------|
| `architecture`            | 50k                 | 40             | 3               |
| `execution_flow`          | 50k                 | 50             | 2               |
| `dependency`              | 40k                 | 30             | 1               |
| `error_handling`          | 35k                 | 40             | 2               |
| `configuration`           | 35k                 | 30             | 2               |
| `testing`                 | 40k                 | 60             | 2               |
| `implementation_location` | 25k                 | 200            | 1               |
| _default_                 | 40k                 | 50             | 1               |

### 切片抽取上限

| 上限                              | 值        | 应用位置                                  |
|-----------------------------------|-----------|-------------------------------------------|
| 切片上下文行数（每侧）           | ±5        | `fast_report_slices.extract_source_slice` |
| 前端展开增量                     | +15 行    | （前端，前一份 spec 已定）                |

### Interpretive 层上限

| 上限                                                  | 值            | 应用位置                                       |
|-------------------------------------------------------|---------------|------------------------------------------------|
| Prompt 中保留的 README sections                        | top 5         | `fast_report_interpretive.select_sections`     |
| Prompt 中 README section body                          | 800 字符      | 同上                                           |
| Prompt 中 README sections 累计                         | 10k tokens    | 同上                                           |
| 自动绑定 docstring/leading_comment 累计上限            | 8k tokens     | `fast_report_interpretive.attach_to_entities`  |

### Curated 层上限

| 上限                       | 值        | 应用位置                  |
|----------------------------|-----------|---------------------------|
| Wiki 摘要截断             | 400 字符  | `worker/jobs.py:_curated` |
| 选取的 wiki 页数           | top 3     | 同上                      |

### Structure 层上限（generation prompt）

| 上限                                  | 值        | 应用位置                                |
|---------------------------------------|-----------|-----------------------------------------|
| 注入的 README 首段                    | 400 字符  | `worker/jobs.py:_repository_structure`  |
| 注入的 `readme_headings`              | top 12    | 同上                                    |

### 单份报告聚合估算

```
Plan prompt 总量:           ~18k tokens
Plan output:                ~500 tokens

Generation prompt 总量:
  Structure 层:              ~16k（主要是 directory_tree）
  Code evidence:             25k–50k（按 question_type）
  Interpretive:              ~18k（8k 自动绑定 + 10k README sections）
  Curated:                   ~2k
  Scaffolding:               ~3k
  Generation 输出预留:       ~10k
  ───────────────────────────────────────
  合计:                      ~74k–99k tokens

单份报告预算合计:           ~92k–117k tokens
```

200k 上下文模型保留 ~50% headroom；1M 上下文模型不受预算约束。Sonnet 4.6 单份报告成本约 $0.25–$0.40 —— 有意为之的质量/成本取舍。
