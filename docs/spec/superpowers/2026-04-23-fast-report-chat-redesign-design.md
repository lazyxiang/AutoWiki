# Fast Report Chat Redesign

## Summary

This document specifies the redesign of the current repository chat experience into a fast, evidence-driven report workspace. The new experience replaces conversational chat bubbles with a report stack on the left and a synchronized evidence rail on the right, while preserving `FloatingAssistant` as the entry point for follow-up questions.

The design goal is to produce answers that are:

- fast to scan
- grounded in code and repository structure
- easy to navigate back to source evidence
- reusable through short-term persistence
- extensible through links to generated wiki content

This design also removes the existing streaming chat UI once the new fast report flow is complete.

## Goals

- Replace the current chat page with a non-conversational fast report experience.
- Preserve `FloatingAssistant` as the shared entry point for initial and follow-up questions.
- Render each answer as a structured markdown report section rather than a chat bubble.
- Show all supporting code evidence blocks in a right-side evidence rail, ordered by appearance in the answer.
- Allow markdown citations to deep-link into the evidence rail and highlight the corresponding block.
- Persist generated fast reports for 7 days behind a stable URL.
- Reopen persisted reports without requiring additional LLM calls.
- Use deterministic repository analysis, keyword retrieval, and bounded code graph expansion as the primary evidence path, while still requiring code evidence or repository structure evidence for final claims.
- Automatically attach related generated wiki pages as further reading.
- Support Mermaid diagrams as first-class report content when they improve understanding.

## Non-Goals

- Retain the existing bubble-based chat UI.
- Preserve chat history as a conversation transcript.
- Treat generated wiki pages as primary evidence.
- Introduce permanent long-term report storage beyond the 7-day TTL in this phase.
- Build a general-purpose report sharing system with auth, permissions, or multi-user collaboration.

## Product Direction

The new experience is not a chat window. It is a report workspace with iterative questioning.

Users ask a question through `FloatingAssistant`, then land on a fast report page that presents:

- a report stack on the left
- a synchronized evidence rail on the right
- an always-available `FloatingAssistant` overlay for follow-up questions

Each follow-up question generates a new report section in the stack rather than a new chat message. The page preserves a reading workflow instead of a messaging workflow.

## User Experience

### Entry Flow

- `FloatingAssistant` remains visible and usable as the main question entry surface.
- Submitting a question routes the user into the fast report page.
- If the page already contains persisted report sections for the current report URL, they are restored immediately.
- A new question appends a new report section instead of replacing the whole page.

### Layout

The fast report page uses a two-column layout:

- Left column: `report stack`
- Right column: `evidence rail`

The left column is the primary reading surface. The right column is the primary verification surface.

### Report Stack

Each answer is rendered as a structured markdown section, not a bubble.

Each section can include:

- title
- summary
- rich markdown body
- Mermaid diagrams
- related wiki links
- section status

The report stack is chronological. Users can scroll back to older sections and reactivate them. The active section controls the evidence focus on the right.

### Evidence Rail

The evidence rail shows all evidence blocks referenced by the active report section.

Evidence blocks are displayed:

- in citation appearance order
- without a fixed cap
- with a stable anchor per citation

Each evidence block:

- shows the cited code range
- includes default context of 3 lines above and 3 lines below
- supports expanding by 15 lines per interaction
- supports collapsing back to the default context
- supports visual focus when navigated to from the markdown

### Citation Navigation

Markdown citations in the report body are rendered as structured links rather than raw text.

When a user clicks a citation:

- the evidence rail scrolls to the corresponding evidence block
- the evidence block is highlighted
- the target block becomes the active focus region

### FloatingAssistant Overlay Behavior

`FloatingAssistant` remains available on the fast report page for follow-up questions.

To prevent it from covering the bottom of page content:

- the scrollable page container must reserve bottom safe space equal to the rendered assistant height
- the final report section must stop above the assistant overlay
- the final evidence block must also stop above the assistant overlay
- the reserved space must react to assistant height changes on responsive layouts

The old in-page bottom composer must be removed.

## Report Structure

The report markdown should be more structured than the current chat output. The renderer should support a richer default section template and omit sections that are not relevant to the current query.

Preferred report headings:

- `Overview`
- `Core Implementation Components`
- `Key Implementation Details`
- `Execution Flow / Steps`
- `Configuration`
- `Use Cases`
- `Notes`
- `Further Explore`

The generation layer may omit empty headings, but the prompt and schema should encourage this shape.

The implementation should keep stable internal canonical heading keys while rendering the visible heading text in the detected report language. This allows the generator to accept either English or Chinese heading labels while still normalizing the result into one stable internal shape.

## Language Strategy

Fast report output should follow the detected language of the user's question.

- default to English
- switch to Simplified Chinese when the question is detected to be Chinese
- keep code identifiers, class names, function names, file paths, config keys, and other repository-native tokens in their original English form
- keep internal JSON keys and canonical section identifiers in English regardless of output language

The initial implementation should not introduce a persisted `report_language` field. Language should be inferred per question at generation time.

## Retrieval and Evidence Model

### Retrieval Layers

The system should retrieve from four non-overlapping context layers.

#### 1. Repository Structure Layer

This layer covers repository-wide structural signals:

- directory tree
- package and module boundaries
- entry points
- file clusters
- dependency graph information
- outline anchors

`outline anchors` are part of this layer. They should not be treated as a separate retrieval category.

#### 2. Code Evidence Layer

This layer contains directly inspectable implementation evidence:

- symbol definitions
- implementation slices
- call sites
- reference sites
- types and interfaces
- configuration read/write sites
- test assertion points

This layer is not limited to symbol names. It must include implementation detail and relationship detail.

#### 3. Interpretive Context Layer

This layer contains explanatory context that helps interpret the code:

- docstrings
- inline comments
- README passages
- heading-level documentation fragments
- other natural-language explanation fragments selected from deterministic candidates

This layer helps explain meaning, but does not independently justify key claims.

#### 4. Curated Knowledge Layer

This layer contains previously synthesized project knowledge:

- generated wiki pages
- page purposes
- prior structured summaries

This layer is for organization and further reading, not final claim arbitration.

### Retrieval Policy

All four layers should be retrieved in parallel after a lightweight search-planning pass.

The arbitration rule is:

- final claims must be supported by the `Code Evidence Layer` or the `Repository Structure Layer`
- `Interpretive Context Layer` may clarify or connect evidence
- `Curated Knowledge Layer` may enrich organization and further reading

This preserves coverage without allowing unsupported narrative drift.

The initial implementation should avoid embedding- and vector-search-driven retrieval in the fast report path. Instead, retrieval should be driven by deterministic repository signals, keyword search, symbol lookup, and bounded dependency / reference expansion.

## Report Generation Pipeline

### 1. Question Language Detection

Detect the language of the incoming question before any LLM generation step.

Rules:

- default to English
- detect Chinese from the question text locally
- use the detected language to control title, summary, notes, and markdown generation

### 2. Search Plan Generation

Use a lightweight first LLM call to convert the raw question into a structured search plan.

The search plan should capture:

- question type
- target subsystem or symbol
- expected answer form
- likely evidence shape
- English keyword hints
- Chinese keyword hints when useful
- symbol hints
- path hints
- retrieval modes

Example question types:

- architecture
- execution flow
- implementation location
- configuration
- error handling
- testing
- dependency relationship

### 3. Deterministic Retrieval and Expansion

Retrieve candidates from the four layers described above without relying on vector similarity.

The retrieval stage should prefer:

- implementation slices over whole-file dumps
- boundary and entry-point files for flow questions
- configuration touchpoints for behavior questions
- dependency and structure signals for architecture questions
- symbol definitions and reference sites over broad file-level excerpts

Code evidence retrieval should combine:

- path and filename keyword matching
- symbol and signature matching
- docstring and README heading matching
- bounded dependency expansion from matched seed files
- bounded symbol / reference expansion where available

For execution-flow and dependency questions, the generator should capture a small call-chain-style evidence set with explicit roles such as `entrypoint`, `callee`, `config_touchpoint`, `interface`, or `test_assertion`.

### 4. Evidence Arbitration

Before final markdown assembly, the system should prune or downgrade unsupported claims.

Allowed outcomes:

- `supported`: backed by code evidence or repository structure evidence
- `qualified`: plausible but incomplete, explicitly marked as uncertain
- `dropped`: excluded from the final report

### 5. Report Assembly

Generate structured markdown with:

- section headings
- narrative synthesis
- inline evidence citations
- optional Mermaid diagrams
- related wiki references

### 6. Evidence Packing

For every inline citation in the markdown:

- create or reuse a stable citation id
- attach a matching evidence block payload
- preserve citation order

### 7. Analysis Trace

Before the final markdown is ready, the system should stream a lightweight analysis trace that explains which files are currently being inspected and why.

The trace should be good enough to support a temporary pre-report UI state, not a full debug transcript.

## Data Model

### Fast Report

Each generated report should have a unique persisted record.

Suggested fields:

- `id`
- `repo_id`
- `commit_sha`
- `created_at`
- `expires_at`
- `status`
- `active_section_id`

### Report Section

Each submitted question produces one report section.

Suggested fields:

- `id`
- `report_id`
- `query`
- `title`
- `summary`
- `markdown`
- `analysis_trace`
- `citations`
- `related_wiki_pages`
- `related_diagrams`
- `created_at`
- `status`

### Citation

Each citation must have a stable identity that can be shared between the markdown renderer and the evidence rail.

Suggested fields:

- `id`
- `section_id`
- `file_path`
- `start_line`
- `end_line`
- `label`
- `kind`
- `reason`
- `score`

### Evidence Block

Evidence blocks are render-ready citation payloads for the right rail.

Suggested fields:

- `citation_id`
- `snippet_start`
- `snippet_end`
- `full_start`
- `full_end`
- `default_context`
- `expanded_context`
- `is_collapsed`
- `code`
- `symbol_path`

### Analysis Trace

Suggested fields:

- `phase`
- `language`
- `search_plan`
- `files`

Each file entry should include:

- `path`
- `role`
- `reason`
- `status`

### Related Wiki Link

Suggested fields:

- `slug`
- `title`
- `reason`

### Related Diagram

Mermaid diagrams should be stored as first-class report content.

Suggested fields:

- `id`
- `section_id`
- `title`
- `type`
- `source`
- `caption`
- `reason`
- `citations`
- `placement`

Supported diagram types should include all Mermaid diagram types currently supported by the product's rendering pipeline, not only dependency diagrams.

## Persistence and TTL

Fast reports should be persisted for 7 days.

### URL Model

Each report must have a stable URL of the form:

- `/{owner}/{repo}/fast/{report_id}`

### Reopen Behavior

Reopening a report within the TTL must:

- load persisted report metadata
- load persisted report sections
- load persisted markdown and citation mappings
- load persisted diagram payloads
- re-render the page without calling the LLM

### Expiration

After 7 days:

- the report is treated as expired
- the user may be shown an expired state and a regeneration affordance
- regeneration creates fresh output rather than silently mutating the expired result

### Commit Binding

Reports must be bound to the commit SHA used at generation time.

This is required so that:

- old reports remain evidence-consistent
- citations do not drift after repository updates
- evidence rendering can be treated as a replay of a known analysis snapshot

## Wiki Linking Strategy

Related wiki pages should be attached as extension paths, not as the main proof source.

Recommended attachment order:

1. explicit file-to-page overlap
2. title or term similarity
3. structural proximity within the same subsystem

Each report section should attach a small set of high-signal wiki links rather than a large link list.

## Mermaid Strategy

Mermaid diagrams should be generated only when the diagram adds explanatory value.

Good triggers:

- cross-module execution flow
- async or stateful control flow
- layered dependency relationships
- entity relationships
- configuration-to-runtime path mapping

Diagrams are evidence organization tools, not decorative content.

## API and Backend Changes

### New Fast Report API Surface

The current chat-oriented surface should be replaced by a report-oriented surface.

Suggested logical operations:

- create fast report
- append section to report
- fetch persisted report by id
- fetch report section payload

The precise route naming can follow existing API conventions, but the payload model should not reuse chat message DTOs.

### Generation Service

Create a distinct fast report generation path rather than reusing the old chat response contract.

The generator may reuse:

- AST outputs
- dependency graph outputs
- generated wiki data
- Mermaid sanitization and rendering path

It should also introduce a fast-report-specific repository analysis index that is persisted during repository indexing and reused at report time.

The initial implementation should not require the FAISS index or embedding provider to serve fast reports.

The fast report path should emit a dedicated structured report schema plus an intermediate analysis trace.

### WebSocket Contract

The fast report WebSocket stream should support intermediate analysis events before the final section payload is available.

Suggested event types:

- `analysis_update`
- `section_complete`
- `report_complete`
- `error`

## Frontend Changes

### Replace ChatPanel

The current `ChatPanel` component should be removed and replaced by fast report workspace components.

Suggested UI split:

- `FastReportPage` shell
- `ReportStack`
- `ReportSection`
- `CitationLink`
- `EvidenceRail`
- `EvidenceBlock`

### FloatingAssistant Integration

`FloatingAssistant` should continue routing questions into the fast report flow.

The component may need a small mode update, but should remain the user's visible question entry point.

### Analysis-First Rendering

Before a section is fully generated, the workspace should be able to render a temporary analysis state that shows:

- the current analysis phase
- the list of files being inspected
- the role of each file in the current retrieval pass

Once the section is complete, this temporary state should be replaced by the final markdown section and evidence rail.

### Safe Bottom Spacing

The page shell should measure the rendered `FloatingAssistant` height and expose it to the scroll containers.

This can be implemented through:

- CSS variable driven padding
- a measured spacer element
- or a shared layout context

The implementation choice is flexible. The behavior is not.

## Migration and Deletion Plan

Once the fast report flow is working end to end:

- remove the existing bubble-based chat page
- remove the old bottom chat composer
- remove the chat-specific streaming UI contract
- remove dead frontend state and message rendering paths

Streaming generation may still exist internally, but it should surface as report section generation state rather than token-by-token chat output.

## Testing Strategy

### Frontend

- citation click scrolls to the correct evidence block
- evidence block highlight state updates correctly
- `Expand +15` and `Collapse` work deterministically
- bottom content remains visible above `FloatingAssistant`
- persisted report reload renders without network generation calls
- active section switches the evidence rail correctly

### Backend

- report generation persists the expected schema
- TTL expiration is enforced
- report reload does not call the LLM
- commit SHA binding is preserved
- unsupported claims are downgraded or removed during arbitration
- related wiki links are attached deterministically
- detected Chinese questions produce Chinese report output while preserving repository-native code tokens
- fast report generation does not depend on FAISS availability
- analysis trace events are persisted and streamed in a deterministic order

### End-to-End

- submit a question from `FloatingAssistant`
- land on a fast report page
- see an analysis state with the current file list
- see final section content and synchronized evidence
- reopen the report URL within TTL without regeneration
- ask a follow-up question and see a new section appended

## Risks

- Citation drift if evidence payloads are not strictly tied to commit SHA.
- Overproduction of Mermaid diagrams if generation rules are too permissive.
- Retrieval overlap if the four retrieval layers are not enforced clearly.
- Rendering complexity if evidence expansion is implemented with repeated file reads without caching.
- UI regressions if `FloatingAssistant` height changes are not measured robustly on mobile widths.
- Quality regressions if deterministic keyword and call-chain expansion are too shallow for loosely named codebases.
- Cold-start latency if fast-report-specific repository analysis artifacts are not generated during indexing.

## Acceptance Criteria

- The current chat page no longer renders a bubble-based conversation UI.
- `FloatingAssistant` remains the entry point for follow-up questions.
- Each question produces a structured report section, not a chat message.
- The report stack and evidence rail stay synchronized by citation id.
- All cited evidence blocks are shown in the evidence rail in citation order.
- Evidence blocks default to `±3` lines and can expand by `15` lines per interaction.
- Bottom page content remains visible above the floating overlay.
- Reports persist behind a unique URL for 7 days.
- Reopening a valid report URL does not trigger fresh LLM generation.
- Chinese questions yield Chinese report prose without translating repository-native identifiers.
- Fast reports do not require embedding or vector search to produce the initial implementation's results.
- Final claims are grounded in code evidence or repository structure evidence.
- Related wiki content appears as further reading, not as primary evidence.
- The workspace can display the active analysis file list before the final markdown section is rendered.
- Legacy chat UI code paths are safely removable.
