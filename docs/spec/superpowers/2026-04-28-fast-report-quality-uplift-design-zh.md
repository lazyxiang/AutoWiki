# Fast Report 质量提升

## 概要

本文档规范 fast report 流水线的下一轮升级，目标是把报告质量从"对索引元数据的总结"提升到"基于真实实现切片的解释"，同时延续 2026-04-23 fast report 重设计中确立的"确定性、不依赖向量检索"的检索哲学。

本次重设计闭合前一份 spec 与当前实现之间的四个具体差距：

1. evidence 装载的是拼接出的元数据文本（`File: …`、`signature: …`、`doc: …`、`imports: …`），而不是真源码。前一份 spec 中的四层模型已经承诺过"实现切片"，但代码从未接入。
2. 检索预算硬编码为 `seed=2 / depth=2 / result=4`，对跨模块流程类问题过浅，对所有 question_type 又过于一致。
3. fast report 路径只检索四层模型中的三层，Interpretive Context Layer 完全缺失。
4. 确定性索引（`fast_report_index.json`）只存文件级 token / imports / imported_by 信号加每个实体的头部信息。没有调用点、没有异常触点、没有配置触点 —— 它能"定位"但无法"解释"。

本设计修复全部四个差距，且不改动用户面 report 流程、URL 模型、7 天 TTL、commit-SHA 绑定语义、以及前端 evidence rail 组件。

## 目标

- 用从已索引 clone 中读出的真实源码切片替换元数据形状的 evidence。
- 把 `fast_report_index.json` 从文件级 token 图升级为面向高 ROI question_type 的符号/触点级图。
- 让检索预算和扩展图由 `question_type` 驱动，而不是由唯一硬编码常量驱动。
- 重新接入 Interpretive Context Layer，作为确定性的、绑定到源码的解释层 —— 它为 generation 提供燃料但**绝不**单独支撑 claim。
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

- `worker/pipeline/fast_report_index.py` —— 索引 schema 升至 `index_version: 2`；新增字段抽取器。
- `worker/fast_report_search.py` —— 自适应检索算法；用按 `question_type` 的 profile 表替换固定的 `_SEED_LIMIT / _EXPANSION_DEPTH / _RESULT_LIMIT` 常量；新增按图扩展模式。
- `worker/fast_report.py` —— 在 `retrieve_fast_report_layers` 中加入 Interpretive Context Layer；更新生成 prompt 以在显式无引用规则下嵌入该层；emit 更细粒度的 `analysis_update` 事件。
- `worker/jobs.py` —— fast report 入口在检索前校验 `index_version`；索引过期时返回可执行的失败信息。

### 新增模块

- `worker/fast_report_slices.py` —— 纯函数式源码切片抽取器。从已索引 clone 按报告的 commit SHA 读取文件，返回 `{snippet_start, snippet_end, full_start, full_end, code, truncated_lines}` 结构。
- `worker/fast_report_interpretive.py` —— Interpretive Context Layer 装配器。从 `fast_report_index.json` 拉取 module docstring、entity docstring、leading comment、README section bodies，做确定性打分，返回 render-ready 的解释 bundle。

### 触动的数据形状

- `fast_report_index.json` —— 新增顶层 `index_version` 字段；新增顶层 `readme_sections` 数组；新增每文件字段 `call_sites`、`exception_touchpoints`、`config_touchpoints`、`module_docstring`；新增每实体 `leading_comment` 字段。
- `FastReportEvidenceBlock.code` —— 载荷变成真源码文本。dataclass 形状本身不变。`snippet_start` / `snippet_end` / `full_start` / `full_end` 的语义保持"切片行号区间和扩展边界"。
- `FastReportSectionResult` —— 新增内部 `interpretive_sources` 字段以便记入 analysis trace，但**不通过现有公共 DTO 暴露**，也**不在 evidence rail 渲染**。

### 故意保持不动

- `FastReportCitation` schema、`FastReportDiagram` schema、related wiki 链接规则、Mermaid sanitization、语言检测规则、canonical heading 集、arbitration 规则（仅 `code_evidence` ∪ `repository_structure`）。

## 索引 Schema v2

`fast_report_index.json` 升至 `index_version: 2`。新字段为追加式，旧字段保留。

### 新增顶层字段

```jsonc
{
  "index_version": 2,
  "top_level_entries": [...],
  "readme_headings": [...],
  "readme_sections": [
    {
      "heading": "Architecture",
      "body": "AutoWiki uses a 6-stage pipeline ..."  // 单段截断 ~400 字符
    }
  ],
  "files": { ... }
}
```

`readme_sections.body` 是每个 heading 之后到下一个 heading 或文末之间的自然语言正文。索引时单段截断到 ~400 字符以避免 README 失控膨胀。`readme_sections` 累计载荷上限 ~3k tokens；超出时按 heading 出现顺序丢弃尾部 section。

### 新增每文件字段

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

### 构建成本预期

新字段搭单次 AST 分析的车 —— 不做二次 parse。`tests/fixtures/simple-repo` 上预期索引耗时 +20% ~ +40%；`fast_report_index.json` 体积 +30% ~ +60%，主要来自 `readme_sections` 和 `call_sites`。

## 自适应检索

当前代码以 `seed=2 / depth=2 / result=4` 检索 4 个文件，且不分 question_type 全部走 `imports + imported_by`。新路径按 `question_type` 参数化。

### 预算 profiles

| `question_type`           | seed | depth | result_limit | code_evidence token 预算 |
|---------------------------|------|-------|--------------|--------------------------|
| `architecture`            | 4    | 3     | 12           | 35k                      |
| `execution_flow`          | 3    | 3     | 10           | 35k                      |
| `dependency`              | 3    | 2     | 10           | 30k                      |
| `error_handling`          | 2    | 2     | 8            | 25k                      |
| `configuration`           | 3    | 2     | 8            | 25k                      |
| `testing`                 | 2    | 1     | 6            | 30k                      |
| `implementation_location` | 2    | 1     | 4            | 20k                      |
| _(default / unknown)_     | 2    | 2     | 6            | 30k                      |

`result_limit` 是建议值。token 预算是最终守卫：所选切片 tokenized 累计载荷一旦超出预算，就按 `score` 升序丢弃直至载荷合规。被丢弃的切片**不降级为元数据** —— 直接移除。丢弃的数量记入 analysis trace。

token 估算用粗粒度的 `len(text) / 4`，不引入 tiktoken。

### 扩展图

种子文件打分算法（`worker/fast_report_search.py` 的 `_score_file`）不变。变化的是种子如何扩展为最终选择集。

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

### 切片行数上限

文件选择集敲定后，每个文件的主实体被切片。切片行数上限也按 `question_type` 驱动：

| `question_type`           | 单切片行数上限 |
|---------------------------|----------------|
| `implementation_location` | 120            |
| `testing`                 | 60             |
| `execution_flow`          | 50             |
| `error_handling`          | 40             |
| `configuration`           | 30             |
| `dependency`              | 30             |
| `architecture`            | 25             |
| _(default)_               | 50             |

切片始终从实体的 `start_line` 起。实体体超出上限时切片到 `start_line + cap`，并在切片文本尾部追加一行注释式标记：

```text
# … 47 more lines truncated
```

每侧 `±3` 行上下文（沿用前一份 spec）保留。`FastReportEvidenceBlock.full_start` 和 `full_end` 继续暴露前端用的 +15 行展开边界。

无实体文件（如纯 config 文件被 `config_touchpoint` 命中）切片以触点行为锚点居中，使用同一行数上限。

## 真实源码切片抽取

新增模块 `worker/fast_report_slices.py` 负责切片抽取。

### 公开 API

```python
@dataclass(frozen=True)
class SliceResult:
    snippet_start: int        # 1-based, 闭区间
    snippet_end: int          # 1-based, 闭区间
    full_start: int           # snippet_start - 3，下截至 1
    full_end: int             # snippet_end + 3，上截至文件长度
    code: str                 # 真源码，不是元数据
    truncated_lines: int      # 0 表示完整实体在切片上限内

def extract_source_slice(
    *,
    clone_root: Path,
    rel_path: str,
    anchor_start: int,
    anchor_end: int,
    line_cap: int,
    context_lines: int = 3,
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

1. 被 code evidence 层选中的每个 file/entity 的 **entity docstring**。
2. 每个被选中文件的 **module docstring**（`module_docstring`）。
3. 每个被选中实体的 **leading comment**（`leading_comment`）。
4. 按问题排名的 **README section bodies**。

`docs/` 目录扫描、函数体内注释、设计文档文件 v1 明确不在范围内。

### 选取规则

对 code evidence 层选中的每个实体，interpretive 层**自动绑定**：该实体的 `docstring`、`leading_comment`、所属文件的 `module_docstring`（任何非空字段）。自动绑定不打分，绑定关系是结构性的。

此外，README section bodies 单独排名：

- 排名 token 来自 `intent.search_terms ∪ intent.retrieval_focus ∪ tokens(question)`。
- 得分 = `|tokens ∩ tokens(heading + body)|`。
- 取 top 3。每段 body 硬截 400 字符。section 累计载荷硬截 3k tokens。

### Prompt 位置

generation prompt 在现有 `Code evidence layer:` 与 `Curated knowledge layer:` 之间新增一段：

```text
Interpretive context layer:
- Module docstring (worker/fast_report.py): Fast report domain service.
- Entity leading comment (FastReportPipeline): Bottom-up bottom-up child-synthesis ...
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

## 可观测性

`analysis_update` WebSocket 事件获得结构化 phase。事件 schema 不变；只是值更细。

### 新 phase 标识

| `phase`                       | 一并发出                                                                |
|-------------------------------|-------------------------------------------------------------------------|
| `index_check`                 | `{ index_version }`                                                    |
| `search_plan`                 | `{ question_type, search_terms[], retrieval_focus[] }`（既有）          |
| `code_evidence_seed`          | `{ files: [{path, score}] }` 种子集                                     |
| `code_evidence_expansion`     | `{ files: [{path, role, score}], graph: "call_sites" \| ... }`         |
| `slice_extraction`            | `{ files: [{path, lines, truncated_lines}], dropped_due_to_budget }`   |
| `interpretive_layer`          | `{ entity_docs, module_docs, readme_sections }` 计数                    |
| `generation`                  | `{ prompt_token_estimate }`                                            |
| `arbitration`                 | `{ claims_kept, claims_dropped }`                                      |

`report_section.analysis_trace` 持久化完整有序事件列表，reopen 时呈现的 trace 与 generation 时一致。reopen 不会重新发射事件。

### 日志

每次重试、每次 fallback、每次切片丢弃都走 `worker/pipeline/pipeline_logging.py`（依 CLAUDE.md 的 "Pipeline observability" 规则）。禁止 `except: pass` 静默吞错。

## Generation Prompt 改动

`worker/fast_report.py` 的 `_build_generation_prompt` 新增 interpretive 段落，并轻微改文以引导模型读真源码：

```text
Code evidence layer:
{format_retrieved_chunks_for_prompt(layers.code_evidence.snippets)}

Interpretive context layer:
{interpretive bullet list}

Use this interpretive layer ONLY to explain or connect code evidence.
Never cite it as primary support. Final claims must cite repository_structure
or code_evidence ids.
```

`format_retrieved_chunks_for_prompt` 已经源码感知（接受 `{file, start_line, end_line, text}`），把真源码穿进去无需上游改动。

arbitration 步骤（`arbitrate_report_claims`）不变。

## 测试策略

### 单元测试

- `fast_report_slices.extract_source_slice`
  - happy path：返回区间内的真源码
  - 文件缺失：返回 `None`
  - 行号区间超过文件长度：截到文件尾，不抛异常
  - 实体超 cap：返回带尾标记 `… N more lines truncated` 的截断切片，每个支持语言验证
  - 上下文边界：`full_start = max(1, anchor_start - 3)`、`full_end = min(file_len, anchor_end + 3)`
- `fast_report_index` v2 构建
  - schema 含 `index_version: 2` 与全部新字段
  - call_sites 在一组 fixture 中跨文件调用时被采集
  - exception_touchpoints 捕获 `raise ValueError(...)` 与 `try/except`
  - config_touchpoints 捕获 `os.getenv("X")`
  - leading_comment 抓取实体上方紧邻注释块；忽略空行隔开的注释
  - readme_sections 遵守 400 字符/段与 3k tokens 累计上限
- `fast_report_search` 自适应
  - profile 表查询返回各 `question_type` 期望的 `(seed, depth, result, token_budget, line_cap)`
  - `execution_flow` 扩展使用 `call_sites`，忽略 `imported_by`
  - `error_handling` 扩展使用 `exception_touchpoints`，排除测试文件
  - `configuration` 扩展使用 `config_touchpoints`，按 `config_key` 匹配
  - 超预算驱逐丢弃低分切片，并记录丢弃数
- `fast_report_interpretive`
  - 自动绑定的 docstring/comment 仅来自 code evidence 层中的实体
  - README section 排名按 token 重合；返回 top-3；超载时丢弃
  - interpretive 载荷产生零 `FastReportCitation`
- `worker/jobs` index_version 守卫
  - version 缺失 → 409，含 `actionable_command`
  - `index_version: 1` → 409
  - `index_version: 2` → 通过
- 持久化
  - `commit_sha` 一致时 reopen 不调 LLM 即返回持久化 markdown
  - `commit_sha` 不一致时 reopen 返回过期态（与 TTL 过期共用路径）

### 集成测试

- `tests/fixtures/simple-repo` 在 v2 上重建索引；为 `execution_flow` 类问题生成报告，evidence rail 载荷含真源码行（不再是 `File: …` 元数据）。
- 为 `architecture` 类问题生成第二份报告；验证扩展更广（slice extraction trace 中 ≥4 文件）。
- 为 `error_handling` 类问题生成第三份报告；验证 exception_touchpoints 扩展生效（trace 中扩展 graph 值为 `exception_touchpoints`）。
- 模拟在不同 commit 重索引同一仓库（DB 中改 SHA），任何已有报告 reopen 都返回过期态。

### 覆盖率目标

`worker/` 与 `api/` 覆盖率维持 ≥80% 现状。新增模块（`fast_report_slices.py`、`fast_report_interpretive.py`）行覆盖 ≥85%，以补偿 `fast_report_index.py` 中略复杂的 AST 抽取代码。

## 风险

- **索引耗时回归**。触点抽取增加 AST 遍历成本。缓解：每个新抽取器复用单次分析已经产出的 AST 树，不做二次 parse。验收阈值：`tests/fixtures/simple-repo` 上 wall-clock 回归 ≤ 50%。
- **索引体积膨胀**。`readme_sections` 与 `call_sites` 是主要贡献者。缓解：单段截断（400 字符）、累计上限（3k tokens）、`call_sites` 只存名字不存正文。
- **Tree-Sitter 触点保真度按语言不一**。Python 抽取最干净；C/C++/C# 的 exception/config 触点精度较低。v1 提供尽力而为的抽取器。某语言抽取得到零触点时自动回退到次级扩展图 —— 不崩、只是粗一些。
- **切片抽取依赖在线 clone**。clone 缺失或部分损坏时，单条切片失败。pipeline 必须带剩余 citation 继续。完全无法读取 clone 视为硬错误向用户报出（与今天的行为一致）。
- **重索引会让所有报告失效**。这是用户明确要求的不变量。前端如何呈现归 frontend 团队负责；v1 复用现有 TTL 过期态。
- **Prompt token 成本上升**。真源码切片比元数据更重。按 `question_type` 的 token 预算控住增量；预算表已校到把中位报告控制在 ~40k 输入 token 以内。

## 验收标准

- 升级后 indexer 写入的 `fast_report_index.json` 携带 `index_version: 2`，并填充 `call_sites`、`exception_touchpoints`、`config_touchpoints`、`module_docstring`、每实体 `leading_comment`、`readme_sections`。
- 在 `tests/fixtures/simple-repo` 上生成的 fast report 其 evidence block `code` 字段含真源码行，可通过抽样核对：渲染文本与该行号区间的源码内容一致。
- `architecture` 或 `execution_flow` 类问题的报告选中超过 4 个文件（即原硬编码 `_RESULT_LIMIT = 4` 不再封顶）。
- `error_handling`、`configuration`、`execution_flow` 类问题在其 `analysis_trace` 中分别使用对应扩展图（`exception_touchpoints`、`config_touchpoints`、`call_sites`）。
- generation prompt 中出现 Interpretive Context Layer，且不产生 `FastReportCitation`；arbitration 丢弃任何只引用 interpretive 的 claim。
- `index_version` 不为 2 的索引导致 `POST /api/repos/{repo_id}/fast` 返回 HTTP 409 与 actionable error 载荷，WebSocket emit 单条 `error` 事件后关闭。
- 在 commit `X` 上生成的报告，在仓库被重索引到 SHA `Y ≠ X` 后 reopen 时返回过期态，与 7 天 TTL 是否到达无关。
- `tests/fixtures/simple-repo` 上索引 wall-clock 回归 ≤ 50%。
- 同一问题在同一 commit 上生成的报告：确定性各层（search plan、retrieval、slice extraction）字节稳定。LLM 叙述自然不确定，但 citation 集合、扩展路径、切片行号区间可复现。

## 待定问题

- 是否把扩展图选择（如 `analysis_trace.code_evidence_expansion.graph`）暴露到前端 evidence rail header。v1 不做；如收到用户反馈再启动。
- 是否把每个 call_site 的方向（caller-of / callee-of）记入 analysis trace 以便深调试。等到真有调试需求再加。
- 是否允许 LLM planner 通过单一 `wants_broader_context: bool` 标志覆盖预算 profile（brainstorming 中的选项 C）。推迟 —— 先严格、需要时再放宽。
