# Wiki Page Quality Redesign

**Date:** 2026-04-10
**Status:** Design
**Scope:** Stage 5 (Wiki Planner) and Stage 6 (Page Generator), plus a cross-cutting extension to the LLM provider abstraction.

## 1. Goals

Raise the accuracy and quality of generated wiki pages by changing *how* the pipeline produces each page, without materially increasing cost.

Three concrete requirements motivate the redesign:

1. **Source code is the canonical ground truth.** Non-README design docs (`ARCHITECTURE.md`, `DESIGN.md`, etc.) may be stale as the project iterates. Page generation must prefer source code over these docs and treat any doc content as *hints*, not facts.
2. **No raw code snippets in wiki output.** Rather than embedding fenced source blocks, pages should *synthesize* what the code does using prose, bullet lists, tables, and diagrams.
3. **Richer diagram variety.** Expand beyond flowcharts and class diagrams to cover the full Mermaid palette — sequence, state, ER, journey, gantt, mindmap — and require every diagram to carry a short header and a source reference.

A fourth, implicit goal surfaced during brainstorming: **improve grounding against hallucination.** Even with perfect inputs, LLMs invent method names, misread control flow, and conflate similar functions. A verification pass catches this class of error.

## 2. Non-Goals

- No changes to the ingestion, AST analysis, dependency graph, or RAG indexer stages (Stages 1–4), except for how retrieval *scores* documentation files at query time.
- No changes to the frontend rendering of wiki pages.
- No new LLM providers. The design extends existing providers (Anthropic, OpenAI, Gemini, Ollama, OpenAI-compatible).
- No backwards-compatible "single-pass mode" — the multi-pass flow replaces the current single-pass generator entirely.

## 3. High-Level Shape

### 3.1 Generation flow per page (replaces single-pass)

```
Outline (fast model)
  → Draft (main model)
    → Fact-check (fast model)
      → Revision (main model, conditional) — targeted fixes only
```

All four passes share a cached per-page evidence context to keep cost flat.

### 3.2 Supporting changes

- **Retrieval layer**: documentation files are downweighted and capped; the page generator prompt is instructed that docs may be stale and code is canonical.
- **LLM provider interface**: extended with a `PromptSegment` abstraction supporting cache markers. Every provider translates these to its native caching primitive (or ignores them).
- **Fast-model split**: a new `llm.fast_model` config option, surfaced as a second `LLMProvider` instance (`fast_llm`). Defaults to the main model if unset.
- **Planner**: Phase 2 (file assignment) moves to the fast model. Phase 1 (outline) stays on the main model.
- **Parent page flow**: adapted to use child Markdown + dependency graph as primary evidence, not source chunks.

## 4. Retrieval-Layer Changes

### 4.1 Documentation downweighting

The RAG indexer currently treats `.md`, `.rst`, `.txt`, and `.adoc` files the same as source code at retrieval time. This lets stale design docs dominate the retrieved context for pages whose keywords happen to match the doc's headings.

**Change:** `FAISSStore.search()` and `FAISSStore.multi_search()` gain an optional parameter that partitions results into "code" and "doc" buckets and applies separate caps.

```python
store.multi_search(
    query_vecs,
    k=12,
    code_k=11,       # up to 11 code chunks
    doc_k=1,         # up to 1 documentation chunk
)
```

- `doc_k` defaults to 1 for the page generator.
- `code_k` defaults to `k - doc_k`.
- README excerpts are *not* retrieved via the RAG path — the planner already consumes the README directly, and the page generator does not need it.
- Classification uses file extension: `{".md", ".rst", ".txt", ".adoc"}` → doc; everything else → code. (Consistent with `rag_indexer.is_code_file()`.)

### 4.2 Prompt-level instruction

The page generator `_SYSTEM` prompt gains an explicit precedence rule:

> **Source of truth**: the source code provided below is canonical. Any documentation excerpt (files ending in `.md`, `.rst`, etc.) may be out of date and must be treated as a *hint*, not a fact. When documentation and code disagree, trust the code. Never cite a documentation file as the source of a technical claim — cite the code file that actually implements the behavior.

This is the essential backstop: even if a stale doc chunk leaks into retrieval, the model treats it as untrusted.

## 5. Multi-Pass Page Generation

### 5.1 Pass 1 — Outline (fast model)

**Purpose**: produce a structured plan for the page before any prose is written. This plan both *guides the draft* and *targets the fact-check pass*.

**Input** (~2–3k tokens, cacheable entity summaries):
- Page title, purpose, parent (if any)
- Entity summaries for assigned files (types, names, signatures, one-line doc excerpts)
- Dependency info: `depends_on`, `depended_by`, `external_deps`
- File list with one-line summaries
- (For parent pages) titles and purposes of child pages
- Output schema

**Output** (JSON):

```json
{
  "sections": [
    {
      "heading": "Overview",
      "kind": "prose",
      "focus": "What the RAG Indexer does and why it exists",
      "diagram": null
    },
    {
      "heading": "Chunking Strategy",
      "kind": "prose+table",
      "focus": "Token limits, overlap, language-specific splitters",
      "diagram": {
        "type": "flowchart",
        "purpose": "Show the chunking pipeline from raw file to FAISS vector",
        "source_files": ["worker/pipeline/rag_indexer.py"]
      }
    },
    {
      "heading": "Query Flow",
      "kind": "prose",
      "focus": "Multi-query RAG retrieval sequence",
      "diagram": {
        "type": "sequenceDiagram",
        "purpose": "Outline → query embedding → FAISS search → dedup → return",
        "source_files": ["worker/pipeline/page_generator.py"]
      }
    }
  ],
  "key_claims": [
    "FAISSStore uses IndexFlatIP for cosine similarity",
    "multi_search() deduplicates chunks across queries by (file, start_line)",
    "Chunk size defaults to 1000 characters with 200-character overlap"
  ]
}
```

**Schema rules** enforced by validation:
- `kind` is one of `prose`, `prose+table`, `prose+list`, `prose+diagram`, `prose+table+diagram`.
- `diagram.type` is one of the expanded Mermaid palette (§5.5).
- `diagram.source_files` must be a subset of the files assigned to the page.
- Every page must produce at least 1 diagram; pages with ≥ 3 sections must produce at least 2 diagrams.
- `key_claims` must contain 3–8 items. Each claim must reference something that can be verified against source code (no vague statements like "the design is elegant").

Validation failures self-retry up to 2 times within the outline pass, mirroring the planner's existing retry pattern.

### 5.2 Pass 2 — Draft (main model)

**Purpose**: generate the full Markdown page from the outline.

**Input** (~8k tokens of retrieved source chunks cached; variable tail ~1k tokens):
- **Cached prefix**: system prompt, repo-level context, per-page source chunks (`code_k=11`, `doc_k=1`), entity details, dependency info
- **Variable tail**: the outline JSON, draft instructions, prose/diagram requirements

**Prose rules in the prompt**:
- Never embed fenced code blocks (neither ```python nor ```js). The only fenced blocks allowed are ```mermaid.
- Short inline identifiers like `` `ClassName.method()` `` or `` `MAX_RETRIES = 3` `` are permitted — these describe the API surface, they are not code excerpts.
- Every major claim is followed by a source citation in the existing italic format: `*Source: worker/pipeline/rag_indexer.py:120-145*`
- Tables and bulleted lists are preferred for enumerating options, fields, parameters, or comparisons. Prose is preferred for narrative and design rationale.
- Every diagram emitted must match the corresponding entry from the outline. It must be preceded by a bolded one-line header and followed by a source reference line.

Diagram header/source format:

```
**Diagram: Query flow through multi-query RAG**

```mermaid
sequenceDiagram
    ...
```

*Source: worker/pipeline/page_generator.py:403-517*
```

### 5.3 Pass 3 — Fact-check (fast model)

**Purpose**: verify the `key_claims` from the outline against source code, and check that each diagram's asserted relationships exist in the code / dep graph.

**Input** (reuses fast-model cache of entity summaries; variable tail carries the draft and targeted chunks):
- **Cached prefix (fast model)**: entity summaries + dep info (~2k tokens, same cache as the outline pass)
- **Variable tail**: the draft Markdown, the `key_claims` list, targeted source chunk snippets for each claim, and for each diagram the list of source files it references plus the dep graph edges for parent-page diagrams

**Output** (JSON):

```json
{
  "verdict": "fail",
  "issues": [
    {
      "kind": "claim",
      "claim": "FAISSStore uses IndexFlatL2 for Euclidean distance",
      "section": "## Chunking Strategy",
      "reason": "Source shows IndexFlatIP (inner product), not L2. See rag_indexer.py:78",
      "suggested_fix": "Replace IndexFlatL2 with IndexFlatIP and update the 'Euclidean distance' phrasing"
    },
    {
      "kind": "diagram",
      "diagram_index": 1,
      "section": "## Query Flow",
      "reason": "Sequence diagram shows arrow from FAISSStore → EmbeddingProvider; no such call exists. Embedding happens before the FAISSStore.search() call.",
      "suggested_fix": "Reverse the arrow: move the embedding step before the FAISSStore interaction"
    }
  ]
}
```

**Verdict values**: `pass`, `fail`. On `pass`, the page is published immediately.

**What fact-check does NOT do**:
- It does not re-verify everything in the draft — only the `key_claims` list and the declared diagrams. This is what lets fact-check run on the fast model cheaply.
- It does not rewrite the draft. Its only job is to produce a structured issue list.

### 5.4 Pass 4 — Targeted revision (main model, conditional)

Only runs when `verdict == "fail"`. At most one revision attempt per page.

**Behavior**:

- **Prose claim issues**: a new main-model LLM call receives `(draft + issue list + relevant source chunks)` with an instruction to revise only the sections containing the flagged claims and leave every other section **verbatim**. Cached prefix is reused from the draft pass.
- **Diagram issues**: the specific ```mermaid block is located by (`section` + `diagram_index`) and sent to the main model with `(section heading + planned diagram type + fact-check issue + relevant source chunks OR dep-graph edges for parent pages)`. The model returns only the corrected mermaid block. We splice it back into the draft and re-run `sanitize_mermaid_blocks()`.
- **Mixed issues**: prose revision runs first, then per-diagram regeneration.

**On continued failure** (fact-check still reports issues after revision):
- Log a warning with the remaining issues attached to the job record.
- **For claims**: locate the flagged sentence(s) by matching the `claim` string against the draft text (substring match, case-insensitive). If matched, replace the containing sentence with `<!-- removed: reason -->`. If not matched (e.g., the revision rephrased it), log a warning and ship the draft as-is — a vague claim is better than silently deleting the wrong text. Ship the page.
- **For diagrams**: drop the mermaid block entirely and replace with `<!-- diagram removed: reason -->`. Ship the page.

This prevents unbounded retry loops. It also guarantees we never publish a known-wrong diagram or claim — we prefer a gap over a lie.

### 5.5 Expanded Mermaid palette

The page generator prompt lists the full supported set and gives explicit guidance on when to pick each:

| Diagram type | Use for |
|---|---|
| `flowchart TD` / `flowchart LR` | Pipeline stages, data flow, decision trees |
| `sequenceDiagram` | Request/response flows, protocol exchanges, inter-component message ordering |
| `classDiagram` | Class hierarchies, inheritance, composition relationships |
| `stateDiagram-v2` | Finite state machines, job lifecycle, connection states |
| `erDiagram` | Database schemas, record relationships (e.g., `repositories` ↔ `jobs` ↔ `wiki_pages`) |
| `journey` | User-facing workflow walkthroughs (e.g., "Index a repository" end-to-end) |
| `gantt` | Temporal phases or rollout plans — rarely appropriate; only for pages describing scheduled/phased work |
| `mindmap` | Conceptual overviews, subsystem taxonomies — useful for parent pages |
| `graph LR` | Dependency relationships, import graphs |

The prompt instructs the model to choose diagrams that *add information the prose cannot convey compactly*, not to add diagrams for their own sake. The outline validation rule (§5.1) sets a minimum count to prevent diagram scarcity; this rule sets a quality bar to prevent noise.

### 5.6 Header and source reference format (enforced)

Every diagram block in generated pages must follow this exact structure, checked by a lightweight post-processor:

```
**Diagram: <one-line header>**

```mermaid
<body>
```

*Source: <file_path>:<start_line>-<end_line>*
```

If the post-processor finds a mermaid block without a preceding header line or following source line, it inserts a placeholder header ("Diagram") and a best-effort source reference derived from the outline's `source_files`. (This is a safety net — the prompt is the primary mechanism.)

## 6. Parent Page Flow

Parent pages (those with children, processed bottom-up) follow the same 4-pass structure with three differences.

### 6.1 Evidence mix

Parent pages do **not** use RAG retrieval as their primary evidence source. Their context is:

- The Markdown of their **already fact-checked** child pages (from the current run)
- The dependency graph edges **between** the files of their children
- A small set of source chunks for high-level entry points (top-level `main` functions, public service initializers) — retrieved via a targeted query

Child Markdown is trusted content: each child has already passed its own fact-check pass. The parent is a synthesis task, not an extraction task.

### 6.2 Outline and fact-check targets

- The parent **outline** is shaped by the child page titles — sections typically describe how the children fit together rather than what each child does individually.
- The parent **fact-check** distinguishes three claim types:
  - **Child-derived claims** (the parent restates something a child already said): trusted, skipped by fact-check.
  - **Architectural claims** (e.g., "The Planner invokes the RAG Indexer"): verified against the **dependency graph**, not source chunks. Deterministic graph lookup, no LLM call needed for these.
  - **Novel claims** (anything else the parent asserts): verified against source chunks the same way a leaf page is.

This makes parent fact-check meaningfully cheaper than leaf fact-check.

### 6.3 Parent diagrams

Parent diagrams tend to span multiple components — sequence diagrams of cross-component flows, mindmaps of subsystem structure, flowcharts of high-level data flow. When one fails fact-check, the regeneration prompt is fed the dep graph edges as ground truth (not source chunks), because the truth about "does component A call component B" lives in the graph.

## 7. Planner Changes

### 7.1 Phase 2 moves to the fast model

The existing two-phase planner splits cleanly on cognitive load:

- **Phase 1 — Outline** (titles, purposes, parent hierarchy) is creative synthesis. Stays on the main model.
- **Phase 2 — File assignment** (each file → one page) is classification. Moves to the fast model.

Validation already catches over-stuffed and empty pages, so the fast model's lower creativity is not a risk. If the fast model struggles with retry-with-feedback, we escalate to the main model only on the second retry (configurable fallback).

### 7.2 Cache reuse between Phase 1 and Phase 2

Phase 1 and Phase 2 share the same large inputs: `file_summary`, `dep_info`, `clusters`. Today these are rebuilt into two separate prompts. Under the new `PromptSegment` interface, both phases mark these shared inputs as a cacheable prefix. The variable tail swaps between Phase 1's outline schema and Phase 2's assignment schema.

**Caveat**: Phase 1 runs on the main model and Phase 2 runs on the fast model, so the cached prefix is written to each model's cache independently (§8.5). The saving comes from *intra-phase* retry reuse (e.g., when Phase 1 self-retries, retries hit the cached prefix) and from *Phase 2 using the fast model* (which is cheaper regardless of caching).

## 8. LLM Provider Interface Extension

### 8.1 `PromptSegment` abstraction

```python
from dataclasses import dataclass

@dataclass
class PromptSegment:
    text: str
    cacheable: bool = False
```

Every `LLMProvider` method gains an overload that accepts `list[PromptSegment]` in place of `str`:

```python
async def generate(
    self,
    prompt: str | list[PromptSegment],
    system: str | list[PromptSegment] = "",
) -> str: ...
```

Passing a plain string is equivalent to passing `[PromptSegment(text=..., cacheable=False)]`. This keeps call sites that don't care about caching unchanged.

### 8.2 Per-provider translation

| Provider | Behavior |
|---|---|
| **Anthropic** | Segments become content blocks. `cache_control: {"type": "ephemeral"}` is attached to the last cacheable segment in each contiguous run (up to Anthropic's 4-breakpoint limit). System segments are similarly marked via the structured `system` parameter. |
| **OpenAI** | Ignore markers. OpenAI applies automatic prefix caching (50% discount) based on the request prefix; ordering stable content first is sufficient. The provider's job is to concatenate segments in order and not shuffle. |
| **OpenAI-compatible** | Same as OpenAI. Real savings depend on whether the backend implements prefix caching; treat as best-effort. |
| **Gemini** | Implicit-caching-only in the initial implementation: concatenate segments in order and rely on Gemini 2.5's automatic prefix detection (requires ≥ 1k tokens for Flash, ≥ 4k for Pro). Explicit `cachedContents` is a follow-up, gated on measured need, because of Gemini's per-hour storage cost and lifecycle management. |
| **Ollama / local** | Ignore markers. No caching, no cost. |

### 8.3 Cache TTL configuration

A new config knob `llm.cache_ttl`:

```yaml
llm:
  cache_ttl: short  # "short" (default, 5-minute TTL) or "long" (1-hour TTL)
```

- `short` uses Anthropic's default ephemeral cache (5 minutes, 1.25× write cost).
- `long` uses Anthropic's 1-hour cache (2× write cost). Intended for very large repos where page generation batches span multiple 5-minute windows.
- Ignored by all non-Anthropic providers.

### 8.4 Fast-model configuration

```yaml
llm:
  provider: anthropic
  model: claude-sonnet-4-6        # existing — main / quality model
  fast_model: claude-haiku-4-5    # new — classification / verification model
```

Environment variable: `AUTOWIKI_LLM_FAST_MODEL`. Defaults to the value of `llm.model` when unset (no-op for users who don't want the split).

At worker startup, the factory constructs **two `LLMProvider` instances**: `llm` (main) and `fast_llm` (fast). Both are threaded through the pipeline alongside the existing `embedding` dependency. When `fast_model == model`, the factory returns the *same* instance twice — no duplication.

### 8.5 Cross-model cache behavior (explicit note)

Caches are keyed on `(model_id + prefix_content + params)`. A cache written by the main model cannot be read by the fast model, and vice versa. The multi-pass flow therefore writes to *two* caches per page:

- **Main-model cache** — full source chunks (~8k tokens), written once at draft, read at revision
- **Fast-model cache** — entity summaries + dep info (~2k tokens), written once at outline, read at fact-check

This is a deliberate design choice: fast passes don't need the full chunks, so we write a smaller prefix to the fast-model cache rather than duplicating the 8k chunk prefix. See §9 for the cost implications.

## 9. Cost and Latency Analysis

### 9.1 Per-page cost (Anthropic Sonnet + Haiku)

Assumptions: ~8k tokens of source chunks per page, ~2k tokens of entity summaries, revision fires ~30% of the time. Pricing: Sonnet $3/M input, $15/M output; Haiku $1/M input, $5/M output. Cached input $0.30/M (Sonnet) / $0.10/M (Haiku); cache write 1.25× normal.

| Pass | Model | Input (cached) | Input (variable) | Output | Est. cost |
|---|---|---|---|---|---|
| Outline | Haiku | 2k (write, first pass) | 1k | 0.8k | ~$0.004 |
| Draft | Sonnet | 8k (write, first pass) | 1k | 2k | ~$0.038 |
| Fact-check | Haiku | 2k (read, cached) | 3k draft + chunks | 0.5k | ~$0.008 |
| Revision (~30%) | Sonnet | 8k (read, cached) | 2k draft + issues | 1.5k | ~$0.010 avg |
| **Total** | | | | | **~$0.060/page** |

Current single-pass cost: ~$0.060/page. **Net change: ~flat.** The fast-model split and prompt caching offset the extra passes.

### 9.2 Per-repo cost

| Repo size | Current | New | Delta |
|---|---|---|---|
| Small (10 pages) | ~$0.60 | ~$0.60 | ~$0 |
| Medium (25 pages) | ~$1.50 | ~$1.50 | ~$0 |
| Large (60 pages) | ~$3.60 | ~$3.60 | ~$0 |

Planner cost on large repos drops ~25% from Phase-2-on-fast plus cache reuse.

### 9.3 Latency

Per page: ~3 sequential LLM round-trips on the critical path (outline → draft → fact-check), with revision adding a 4th for ~30% of pages. Wall-clock per page is roughly **2–2.5× the current single-pass**, but pages at the same depth still batch in parallel, so overall job latency scales more gently than per-page latency.

### 9.4 Providers without rich caching

- **OpenAI / OpenAI-compatible**: automatic prefix caching at 50% discount. Cost approximately 1.3× current.
- **Gemini 2.5 (implicit caching)**: ~75% discount on qualifying prefixes. Cost approximately 1.1–1.2× current.
- **Gemini 1.5**: no implicit caching. Cost approximately 2.5× current — users should be encouraged to upgrade or switch.
- **Ollama / local**: no external cost. Latency impact (~3× round-trips) is the only concern; local inference is slower per call.

## 10. Migration

No backwards compatibility. The single-pass `generate_page` and `generate_page_batch` in `worker/pipeline/page_generator.py` are replaced by the four-pass pipeline. The single-phase caller in `worker/jobs.py` is updated to pass both `llm` and `fast_llm`.

Interface surface that breaks:

- `LLMProvider.generate()` signature accepts `str | list[PromptSegment]`. Internal callers updated; no public API change (LLMProvider is not exposed outside the worker).
- `worker/jobs.py` factory constructs two provider instances at worker startup. Config loading gains `llm.fast_model` and `llm.cache_ttl`.
- Stage 6 orchestration logic (`run_full_index` and `run_incremental_refresh` in `worker/jobs.py`) changes to call the four-pass generator.

Incremental refresh flow is unaffected except that refreshed pages go through the same four-pass generator as full-index pages.

## 11. Testing Strategy

### 11.1 Unit tests

- `PromptSegment` translation per provider (Anthropic, OpenAI, Gemini, Ollama) — verify correct ordering and correct cache marker placement (Anthropic only).
- Outline schema validation — duplicate sections, missing diagrams for multi-section pages, invalid diagram types.
- Fact-check issue parsing and diagram-block splicing — given a draft and a structured issue list, the splicer produces the expected revised draft.
- Documentation downweighting in `FAISSStore.multi_search` — mixed code/doc corpus, verify `code_k` / `doc_k` caps are respected.
- Header/source reference post-processor — verify it inserts placeholders only when missing and leaves compliant blocks untouched.

### 11.2 Integration tests

- Run the four-pass pipeline end-to-end against the fixture repo at `tests/fixtures/simple-repo/` using the existing `mock_llm` and `mock_embedding` fixtures.
- Mock the fact-check pass to return both `pass` and `fail` verdicts; verify revision fires only on `fail`.
- Mock the fact-check to fail after revision; verify the deterministic strip-and-ship fallback.
- Parent page flow: fixture with a deliberate parent/child split; verify parent uses child Markdown rather than source chunks and that dep-graph-verified architectural claims bypass the LLM fact-check.

### 11.3 Coverage target

Maintain the existing 80% target on `worker/`. New modules (multi-pass orchestrator, fact-check, revision splicer) should land at ≥ 85% since they are new code with no legacy baggage.

## 12. Open Questions / Risks

1. **Fast-model quality on fact-check.** If Haiku systematically misses subtle hallucinations, fact-check becomes a false-sense-of-security layer. Mitigation: provide a config escape hatch (`llm.fact_check_model: fast | main`) so users can force fact-check onto the main model if they observe issues. Default stays `fast`.
2. **Gemini 1.5 users pay 2.5×.** Documented but worth an explicit note in the release notes and README.
3. **Cache TTL and very large repos.** A repo with 100+ pages may span multiple 5-minute windows per depth level, causing cache misses mid-batch. The `llm.cache_ttl: long` knob is the escape hatch, but the default is `short`. If measurement shows the default is wrong for typical AutoWiki users, we flip the default.
4. **Diagram minimum might produce forced diagrams on small pages.** The outline rule "pages with ≥ 3 sections produce ≥ 2 diagrams" may cause low-quality diagrams on small, prose-heavy pages. We should measure and relax if needed — the rule is tunable.
5. **Parent page architectural-claim verification is deterministic but brittle.** If the dep graph is wrong (rare but possible), false negatives leak through. Mitigation: log warnings when the dep graph lookup contradicts a parent's asserted relationship, so users can surface graph bugs.
6. **No explicit Gemini context caching in v1.** Users with very large repos on Gemini may want explicit `cachedContents`. Follow-up work if demand surfaces.

## 13. Summary

This design replaces the single-pass page generator with a four-pass flow (outline → draft → fact-check → conditional revision), adds a fast-model split to keep cost flat, extends the LLM provider interface with prompt caching support, downweights stale documentation at the retrieval layer, expands the supported Mermaid diagram palette, and enforces a consistent header/source format on every diagram. The result is materially better grounding and diagram variety at approximately the same cost and manageable latency increase.
