"""Tests for KeywordIndex (BM25 retrieval).

Includes:
  - Unit tests for the core KeywordIndex API (6 tests).
  - A recall gate that verifies BM25 top-3 recall ≥ 0.70 on a hand-crafted
    20-question fixture (spec §6.4).

Spec §6.4 framed the gate as recall parity vs FAISS, but mock_embedding returns
zero vectors which makes a FAISS comparison vacuous in CI (all cosine similarities
are identical, so ranking is arbitrary).  The always-on assertion is therefore
BM25 top-N recall against a hand-crafted ground truth with known-answerable
queries.  An optional FAISS-comparison path is included below, gated by the
AUTOWIKI_TEST_REAL_EMBEDDING environment variable, for a human reviewer to run
before merge.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from worker.pipeline.rag_indexer import Chunk  # still present in B1
from worker.pipeline.retrieval.chunk import Chunk as CanonicalChunk
from worker.pipeline.retrieval.keyword_index import KeywordIndex

# ---------------------------------------------------------------------------
# Corpus fixtures and helpers
# ---------------------------------------------------------------------------

# Synthesized source files used by the recall gate.  Each file has clearly
# distinct vocabulary targeting one topic so that hand-crafted queries have a
# deterministic expected answer.
_SYNTHETIC_FILES: dict[str, str] = {
    "auth.py": """\
\"\"\"Authentication module: JWT token generation and verification.\"\"\"

import hashlib
import hmac

SECRET_KEY = "super-secret"


def hash_password(password: str) -> str:
    \"\"\"Hash a plaintext password using PBKDF2.\"\"\"
    salt = os.urandom(16)
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 100_000).hex()


def verify_credentials(username: str, password: str) -> bool:
    \"\"\"Verify username/password credentials against the stored hash.\"\"\"
    stored = _load_hash(username)
    return hmac.compare_digest(stored, hash_password(password))


def generate_jwt(user_id: int) -> str:
    \"\"\"Generate a signed JWT token for the authenticated user session.\"\"\"
    payload = {"sub": user_id, "iat": time.time()}
    return _encode_jwt(payload, SECRET_KEY)


def decode_jwt(token: str) -> dict:
    \"\"\"Decode and validate a JWT token; raise ValueError on invalid signature.\"\"\"
    return _decode_jwt(token, SECRET_KEY)


def login(username: str, password: str) -> str:
    \"\"\"Authenticate a user and return a JWT session token on success.\"\"\"
    if not verify_credentials(username, password):
        raise PermissionError("Invalid credentials")
    user_id = _get_user_id(username)
    return generate_jwt(user_id)
""",
    "database.py": """\
\"\"\"Database access layer: SQL connection pool and query execution.\"\"\"

import sqlite3
from contextlib import contextmanager
from typing import Any


_POOL: list[sqlite3.Connection] = []
_DSN = "autowiki.db"


def _get_connection() -> sqlite3.Connection:
    \"\"\"Acquire a connection from the pool or create a new one.\"\"\"
    if _POOL:
        return _POOL.pop()
    conn = sqlite3.connect(_DSN)
    conn.row_factory = sqlite3.Row
    return conn


@contextmanager
def cursor():
    \"\"\"Yield a database cursor; commit on success, rollback on error.\"\"\"
    conn = _get_connection()
    cur = conn.cursor()
    try:
        yield cur
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        _POOL.append(conn)


def execute(query: str, params: tuple = ()) -> list[dict[str, Any]]:
    \"\"\"Execute a parameterised SQL query and return all rows as dicts.\"\"\"
    with cursor() as cur:
        cur.execute(query, params)
        return [dict(row) for row in cur.fetchall()]


def transaction(statements: list[tuple[str, tuple]]) -> None:
    \"\"\"Execute multiple SQL statements in a single atomic transaction.\"\"\"
    with cursor() as cur:
        for sql, params in statements:
            cur.execute(sql, params)
""",
    "http_client.py": """\
\"\"\"HTTP client: send GET/POST/PUT/DELETE requests and parse responses.\"\"\"

import urllib.request
import urllib.parse
import json
from typing import Any


DEFAULT_TIMEOUT = 30


def _make_request(method: str, url: str, headers: dict, body: bytes | None) -> dict:
    \"\"\"Build and send an HTTP request; return response body and status code.\"\"\"
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=DEFAULT_TIMEOUT) as resp:
        return {
            "status_code": resp.status,
            "headers": dict(resp.headers),
            "body": json.loads(resp.read()),
        }


def get(url: str, headers: dict | None = None) -> dict[str, Any]:
    \"\"\"Send an HTTP GET request to the given URL and return the response.\"\"\"
    return _make_request("GET", url, headers or {}, None)


def post(url: str, payload: dict, headers: dict | None = None) -> dict[str, Any]:
    \"\"\"Send an HTTP POST request with a JSON payload; return response dict.\"\"\"
    body = json.dumps(payload).encode()
    hdrs = {"Content-Type": "application/json", **(headers or {})}
    return _make_request("POST", url, hdrs, body)


def put(url: str, payload: dict, headers: dict | None = None) -> dict[str, Any]:
    \"\"\"Send an HTTP PUT request.\"\"\"
    body = json.dumps(payload).encode()
    hdrs = {"Content-Type": "application/json", **(headers or {})}
    return _make_request("PUT", url, hdrs, body)


def delete(url: str, headers: dict | None = None) -> dict[str, Any]:
    \"\"\"Send an HTTP DELETE request to the given endpoint URL.\"\"\"
    return _make_request("DELETE", url, headers or {}, None)
""",
    "markdown_parser.py": """\
\"\"\"Markdown parser: convert Markdown text into a syntax tree of nodes.\"\"\"

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Literal


NodeKind = Literal["heading", "paragraph", "bold", "italic", "link", "code", "list"]


@dataclass
class Node:
    kind: NodeKind
    text: str
    children: list["Node"] = field(default_factory=list)
    level: int = 0  # heading level (1-6)
    href: str = ""  # for link nodes


def parse(source: str) -> list[Node]:
    \"\"\"Parse a Markdown string and return a flat list of AST nodes.\"\"\"
    nodes: list[Node] = []
    for line in source.splitlines():
        if line.startswith("#"):
            level = len(line) - len(line.lstrip("#"))
            nodes.append(Node("heading", line.lstrip("# ").strip(), level=level))
        elif line.startswith("- ") or line.startswith("* "):
            nodes.append(Node("list", line[2:].strip()))
        elif line.strip():
            nodes.append(_parse_inline(line))
    return nodes


def _parse_inline(text: str) -> Node:
    \"\"\"Parse inline Markdown syntax (bold, italic, links) in a single line.\"\"\"
    if text.startswith("**") and text.endswith("**"):
        return Node("bold", text[2:-2])
    if text.startswith("*") and text.endswith("*"):
        return Node("italic", text[1:-1])
    if text.startswith("[") and "](" in text:
        label = text[1 : text.index("](")]
        href = text[text.index("](") + 2 : -1]
        return Node("link", label, href=href)
    return Node("paragraph", text)


def render_html(nodes: list[Node]) -> str:
    \"\"\"Convert a list of AST nodes back to an HTML string.\"\"\"
    parts = []
    for node in nodes:
        if node.kind == "heading":
            parts.append(f"<h{node.level}>{node.text}</h{node.level}>")
        elif node.kind == "bold":
            parts.append(f"<strong>{node.text}</strong>")
        elif node.kind == "italic":
            parts.append(f"<em>{node.text}</em>")
        elif node.kind == "link":
            parts.append(f'<a href="{node.href}">{node.text}</a>')
        else:
            parts.append(f"<p>{node.text}</p>")
    return "\n".join(parts)
""",
    "mermaid_sanitizer.py": """\
\"\"\"Mermaid diagram sanitizer: fix labels and strip invalid syntax.\"\"\"

from __future__ import annotations
import re

_LABEL_RE = re.compile(r'(\\w+)\\[([^\\]]+)\\]')
_SPECIAL_CHARS = set("(){}|<>/")
_SUBGRAPH_RE = re.compile(r'^\\s*subgraph\\s', re.MULTILINE)
_END_RE = re.compile(r'^\\s*end\\s*$', re.MULTILINE)


def sanitize_mermaid(diagram: str) -> str:
    \"\"\"Sanitize a Mermaid diagram string for safe rendering in the browser.

    Quotes node and edge labels that contain special characters like
    parentheses, curly braces, pipe symbols, angle brackets, and slashes.
    Removes orphaned ``end`` keywords that have no matching ``subgraph``.
    Strips code-fence markers if the input is wrapped in triple backticks.
    \"\"\"
    diagram = _strip_fences(diagram)
    diagram = _quote_special_labels(diagram)
    diagram = _remove_orphaned_end(diagram)
    return diagram.strip()


def _strip_fences(text: str) -> str:
    \"\"\"Remove surrounding ```mermaid ... ``` code fences if present.\"\"\"
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        inner = [l for l in lines[1:] if not l.strip().startswith("```")]
        return "\\n".join(inner)
    return text


def _quote_special_labels(text: str) -> str:
    \"\"\"Quote node labels containing special Mermaid characters.\"\"\"
    def _replacer(m: re.Match) -> str:
        node_id, label = m.group(1), m.group(2)
        if any(c in label for c in _SPECIAL_CHARS):
            return f'{node_id}["{label}"]'
        return m.group(0)
    return _LABEL_RE.sub(_replacer, text)


def _remove_orphaned_end(text: str) -> str:
    \"\"\"Remove ``end`` keywords with no matching ``subgraph`` opening.\"\"\"
    subgraph_count = len(_SUBGRAPH_RE.findall(text))
    end_count = len(_END_RE.findall(text))
    orphans = end_count - subgraph_count
    if orphans <= 0:
        return text
    lines = text.splitlines()
    result = []
    removed = 0
    for line in reversed(lines):
        if removed < orphans and _END_RE.match(line):
            removed += 1
            continue
        result.append(line)
    return "\\n".join(reversed(result))
""",
    "keyword_index.py": """\
\"\"\"BM25 keyword index for document retrieval.

Uses Okapi BM25 scoring via rank_bm25.  Tokenizes documents and queries
with a shared tokenizer.  Supports per-file quotas and file-scope filtering.
\"\"\"

from __future__ import annotations
from dataclasses import dataclass
from collections import defaultdict
from rank_bm25 import BM25Okapi


@dataclass(frozen=True)
class Chunk:
    file: str
    text: str
    line_start: int
    line_end: int


def tokenize(text: str) -> list[str]:
    \"\"\"Lowercase word tokenizer: split on non-word chars, filter short tokens.\"\"\"
    import re
    return [t.lower() for t in re.findall(r'[\\w]+', text) if len(t) >= 3]


class KeywordIndex:
    \"\"\"BM25 inverted index over a list of Chunk objects.

    Build with ``KeywordIndex.build(chunks)``.  Query with ``search(queries, k=5)``.
    IDF scores are available in ``token_idf`` for downstream scoring (spec §5.3).
    \"\"\"

    def __init__(self, chunks: list[Chunk], bm25: BM25Okapi | None):
        self.chunks = chunks
        self.bm25 = bm25
        self.file_to_chunks: dict[str, list[int]] = defaultdict(list)
        for i, c in enumerate(chunks):
            self.file_to_chunks[c.file].append(i)
        self.token_idf: dict[str, float] = (
            dict(bm25.idf) if bm25 and hasattr(bm25, "idf") else {}
        )

    @classmethod
    def build(cls, chunks: list[Chunk]) -> "KeywordIndex":
        \"\"\"Build a BM25 index from a list of Chunk objects.\"\"\"
        if not chunks:
            return cls([], None)
        tokenized = [tokenize(c.text) for c in chunks]
        if not any(tokenized):
            return cls(chunks, None)
        return cls(chunks, BM25Okapi(tokenized))

    def search(
        self, queries: list[str], *, k: int, files: list[str] | None = None
    ) -> list[Chunk]:
        \"\"\"Return the top-k chunks matching the union of all queries.\"\"\"
        if self.bm25 is None:
            return []
        file_set = frozenset(files) if files else None
        scores: dict[int, float] = defaultdict(float)
        for q in queries:
            tokens = tokenize(q)
            for i, s in enumerate(self.bm25.get_scores(tokens)):
                if file_set and self.chunks[i].file not in file_set:
                    continue
                scores[i] += s
        ranked = sorted(scores, key=lambda i: scores[i], reverse=True)
        return [self.chunks[i] for i in ranked[:k]]
""",
    "page_generator.py": """\
\"\"\"Wiki page generator: orchestrates multi-pass LLM page drafting.

Passes:
  1. Outline  — fast LLM produces a structured section outline.
  2. Draft    — main LLM writes the full wiki page Markdown.
  3. Fact-check — fast LLM verifies claims against source files.
  4. Revision  — main LLM rewrites flagged sections (only if fact-check fails).
\"\"\"

from __future__ import annotations
from dataclasses import dataclass
from typing import Any


@dataclass
class WikiPage:
    title: str
    slug: str
    purpose: str
    markdown: str = ""
    parent: str | None = None


async def generate_page(
    page: WikiPage,
    source_files: list[str],
    llm: Any,
    fast_llm: Any,
) -> WikiPage:
    \"\"\"Run all four generation passes and return the completed WikiPage.\"\"\"
    outline = await _pass1_outline(page, source_files, fast_llm)
    draft = await _pass2_draft(page, outline, source_files, llm)
    verdict = await _pass3_fact_check(draft, source_files, fast_llm)
    if verdict == "fail":
        draft = await _pass4_revision(draft, source_files, llm)
    page.markdown = draft
    return page


async def _pass1_outline(page: WikiPage, files: list[str], llm: Any) -> dict:
    \"\"\"Pass 1: produce a structured section outline with key claims.\"\"\"
    prompt = f"Outline a wiki page titled '{page.title}'. Purpose: {page.purpose}."
    return await llm.generate_structured(prompt, schema={"type": "object"})


async def _pass2_draft(
    page: WikiPage, outline: dict, files: list[str], llm: Any
) -> str:
    \"\"\"Pass 2: write the full wiki page draft using the outline and context.\"\"\"
    ctx = "\\n".join(f"// {f}" for f in files)
    prompt = f"Write a wiki page for '{page.title}' using:\\n{ctx}"
    return await llm.generate(prompt)


async def _pass3_fact_check(draft: str, files: list[str], llm: Any) -> str:
    \"\"\"Pass 3: fact-check the draft against the source files.\"\"\"
    result = await llm.generate_structured(
        f"Fact-check:\\n{draft}", schema={"properties": {"verdict": {}}}
    )
    return result.get("verdict", "pass")


async def _pass4_revision(draft: str, files: list[str], llm: Any) -> str:
    \"\"\"Pass 4: revise the draft to fix fact-check failures.\"\"\"
    return await llm.generate(f"Revise to fix errors:\\n{draft}")
""",
    "dependency_graph.py": """\
\"\"\"Dependency graph: build a file-level import graph and detect cycles.\"\"\"

from __future__ import annotations
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Iterable


@dataclass
class Graph:
    \"\"\"Directed graph of file-level import dependencies.\"\"\"
    nodes: set[str] = field(default_factory=set)
    edges: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))

    def add_edge(self, src: str, dst: str) -> None:
        \"\"\"Add a directed edge from source file to imported module.\"\"\"
        self.nodes.add(src)
        self.nodes.add(dst)
        self.edges[src].append(dst)

    def in_degree(self) -> dict[str, int]:
        \"\"\"Return the in-degree (number of importers) for every graph node.\"\"\"
        counts: dict[str, int] = {n: 0 for n in self.nodes}
        for neighbors in self.edges.values():
            for dst in neighbors:
                counts[dst] = counts.get(dst, 0) + 1
        return counts

    def bfs(self, start: str) -> list[str]:
        \"\"\"BFS traversal from *start*; return nodes in breadth-first order.\"\"\"
        visited: set[str] = set()
        queue: deque[str] = deque([start])
        result: list[str] = []
        while queue:
            node = queue.popleft()
            if node in visited:
                continue
            visited.add(node)
            result.append(node)
            queue.extend(self.edges.get(node, []))
        return result

    def detect_cycles(self) -> list[list[str]]:
        \"\"\"Find all strongly connected components (circular import cycles).\"\"\"
        cycles: list[list[str]] = []
        visited: set[str] = set()
        stack: list[str] = []
        on_stack: set[str] = set()

        def dfs(node: str) -> None:
            visited.add(node)
            stack.append(node)
            on_stack.add(node)
            for neighbor in self.edges.get(node, []):
                if neighbor not in visited:
                    dfs(neighbor)
                elif neighbor in on_stack:
                    idx = stack.index(neighbor)
                    cycles.append(list(stack[idx:]))
            stack.pop()
            on_stack.discard(node)

        for node in self.nodes:
            if node not in visited:
                dfs(node)
        return cycles


def build_graph(file_imports: dict[str, list[str]]) -> Graph:
    \"\"\"Build a Graph from a mapping of file -> list of imported modules.\"\"\"
    g = Graph()
    for src, imports in file_imports.items():
        for imp in imports:
            g.add_edge(src, imp)
        if src not in g.nodes:
            g.nodes.add(src)
    return g
""",
    "cache.py": """\
\"\"\"Caching layer: in-memory and Redis-backed key-value stores with TTL expiry.\"\"\"

from __future__ import annotations
import time
from typing import Any


class MemoryCache:
    \"\"\"Simple in-memory cache with TTL expiry (seconds).\"\"\"

    def __init__(self) -> None:
        self._store: dict[str, tuple[Any, float]] = {}

    def set(self, key: str, value: Any, ttl: int = 300) -> None:
        \"\"\"Store *value* under *key* with a TTL expiry in seconds.\"\"\"
        self._store[key] = (value, time.monotonic() + ttl)

    def get(self, key: str) -> Any | None:
        \"\"\"Return the cached value for *key*, or ``None`` if expired or absent.\"\"\"
        entry = self._store.get(key)
        if entry is None:
            return None
        value, expires_at = entry
        if time.monotonic() > expires_at:
            del self._store[key]
            return None
        return value

    def invalidate(self, key: str) -> None:
        \"\"\"Delete *key* from the cache immediately.\"\"\"
        self._store.pop(key, None)

    def clear(self) -> None:
        \"\"\"Remove all entries from the cache store.\"\"\"
        self._store.clear()


class RedisCache:
    \"\"\"Redis-backed key-value cache using SET/GET/EXPIRE commands.\"\"\"

    def __init__(self, host: str = "localhost", port: int = 6379, db: int = 0) -> None:
        import redis
        self._client = redis.Redis(host=host, port=port, db=db, decode_responses=True)

    def set(self, key: str, value: str, ttl: int = 300) -> None:
        \"\"\"Write *value* to Redis with an EXPIRE TTL in seconds.\"\"\"
        self._client.set(key, value, ex=ttl)

    def get(self, key: str) -> str | None:
        \"\"\"Return the Redis value for *key*, or None if absent/expired.\"\"\"
        return self._client.get(key)

    def expire(self, key: str, ttl: int) -> None:
        \"\"\"Reset the TTL expiry for an existing Redis key.\"\"\"
        self._client.expire(key, ttl)

    def delete(self, key: str) -> None:
        \"\"\"Delete a key-value pair from Redis.\"\"\"
        self._client.delete(key)
""",
    "tokenizer.py": """\
\"\"\"Text tokenizer: Unicode-aware identifier splitting for BM25 and IDF scoring.\"\"\"

from __future__ import annotations
import re
import unicodedata

_WORD_RE = re.compile(r\"[^\\W_]+\", re.UNICODE)
_CAMEL_RE = re.compile(r\"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])\")
_SEP_RE = re.compile(r\"[/_\\-.]\")
_CJK_RE = re.compile(
    r\"[\\u3400-\\u4dbf\\u4e00-\\u9fff\\uf900-\\ufaff\\u3040-\\u30ff\\uac00-\\ud7af]+\"
)


def tokenize(text: str, *, min_ascii_len: int = 3) -> set[str]:
    \"\"\"Tokenize *text* into a set of lowercase ASCII tokens and CJK n-grams.

    Performs camel-case splitting, path/separator splitting, and Unicode
    normalization (NFKC).  ASCII tokens shorter than *min_ascii_len* are
    discarded.  CJK runs are expanded into unigrams and bigrams for
    fine-grained search.
    \"\"\"
    if not text:
        return set()
    text = unicodedata.normalize(\"NFKC\", text)
    normalized = _CAMEL_RE.sub(\" \", text)
    normalized = _SEP_RE.sub(\" \", normalized)
    word_text = _CJK_RE.sub(\" \", normalized)
    tokens: set[str] = set()
    for token in _WORD_RE.findall(word_text):
        t = token.lower()
        if token.isascii() and len(t) < min_ascii_len:
            continue
        tokens.add(t)
    for run in _CJK_RE.findall(text):
        tokens.add(run)
        for size in range(2, min(4, len(run) + 1)):
            for i in range(len(run) - size + 1):
                tokens.add(run[i : i + size])
    return tokens
""",
}

# Path to the hand-crafted question fixture.
_FIXTURE_PATH = (
    Path(__file__).parent.parent
    / "fixtures"
    / "keyword_index_recall"
    / "questions.json"
)


def _build_chunks(repo_dir: Path) -> list[CanonicalChunk]:
    """Build one Chunk per synthetic file using the file text as a single chunk."""
    chunks: list[CanonicalChunk] = []
    for filename, content in _SYNTHETIC_FILES.items():
        # Write the file to repo_dir so it exists on disk if needed later.
        file_path = repo_dir / filename
        file_path.write_text(content, encoding="utf-8")
        lines = content.splitlines()
        chunks.append(
            CanonicalChunk(
                file=filename,
                text=content,
                line_start=1,
                line_end=len(lines),
            )
        )
    return chunks


# ---------------------------------------------------------------------------
# Original 6 unit tests (unchanged)
# ---------------------------------------------------------------------------


def test_search_returns_top_k_for_single_query():
    chunks = [
        Chunk(
            file="a.py",
            text="dependency graph build_graph",
            line_start=1,
            line_end=2,
        ),
        Chunk(file="b.py", text="wiki planner phase 2", line_start=1, line_end=2),
        Chunk(file="c.py", text="fact check verdict", line_start=1, line_end=2),
    ]
    idx = KeywordIndex.build(chunks, repo_index={"files": []})
    out = idx.search(["dependency graph"], k=1)
    assert out[0].file == "a.py"


def test_search_applies_per_file_quota():
    chunks = [
        Chunk(file="a.py", text="x" * 20, line_start=i, line_end=i) for i in range(10)
    ]
    chunks += [
        Chunk(file="b.py", text="x" * 20, line_start=i, line_end=i) for i in range(10)
    ]
    idx = KeywordIndex.build(chunks, repo_index={"files": []})
    out = idx.search(["x"], k=10, files=["a.py", "b.py"], per_file_quota=2)
    counts = {"a.py": 0, "b.py": 0}
    for c in out:
        counts[c.file] += 1
    assert counts["a.py"] == 2
    assert counts["b.py"] == 2


def test_search_files_filter_restricts_scope():
    chunks = [
        Chunk(file="a.py", text="alpha", line_start=1, line_end=1),
        Chunk(file="b.py", text="alpha", line_start=1, line_end=1),
    ]
    idx = KeywordIndex.build(chunks, repo_index={"files": []})
    out = idx.search(["alpha"], k=5, files=["a.py"])
    assert all(c.file == "a.py" for c in out)


def test_build_empty_chunks_returns_empty_index():
    idx = KeywordIndex.build([], repo_index={"files": []})
    assert idx.search(["anything"], k=5) == []


def test_build_only_short_text_returns_empty_index():
    """All-short text means every chunk tokenizes to empty (min_ascii_len=3)."""
    chunks = [CanonicalChunk(file="a.py", text="ab", line_start=1, line_end=1)]
    idx = KeywordIndex.build(chunks, repo_index={"files": []})
    assert idx.search(["anything"], k=5) == []


def test_search_hard_cap_returns_fewer_than_k():
    chunks = [
        CanonicalChunk(file="a.py", text="hello", line_start=i, line_end=i)
        for i in range(5)
    ]
    idx = KeywordIndex.build(chunks, repo_index={"files": []})
    out = idx.search(["hello"], k=10, files=["a.py"], per_file_quota=2)
    assert len(out) == 2  # quota caps before k


# ---------------------------------------------------------------------------
# Recall gate (spec §6.4)
# ---------------------------------------------------------------------------


def test_keyword_index_recall_gate(tmp_path: Path) -> None:
    """BM25 top-3 recall ≥ 0.70 on a 20-question hand-crafted fixture.

    Spec §6.4 framed this as recall parity vs FAISS.  The FAISS comparison
    is omitted from the always-on path because ``mock_embedding`` returns
    zero vectors, making every cosine similarity equal — FAISS ranking is
    therefore random and the parity assertion would be vacuous (it would pass
    trivially, not because BM25 is good enough).

    Instead, the gate is: BM25 top-3 recall against ground-truth expected_files
    must be ≥ 0.70.  This catches regressions in tokenization or scoring that
    make BM25 obviously worse than a random baseline.

    An optional FAISS comparison path (for human verification before merge) is
    included below, gated by AUTOWIKI_TEST_REAL_EMBEDDING.
    """
    questions = json.loads(_FIXTURE_PATH.read_text())

    # Build chunks from the synthesized corpus.
    chunks = _build_chunks(tmp_path)
    idx = KeywordIndex.build(chunks, repo_index={"files": []})

    TOP_N = 3
    hits = 0
    misses: list[str] = []

    for q in questions:
        query: str = q["query"]
        expected: list[str] = q["expected_files"]
        results = idx.search([query], k=TOP_N)
        result_files = {c.file for c in results}
        if result_files & set(expected):
            hits += 1
        else:
            misses.append(
                f"MISS: {query!r} → got {sorted(result_files)}, expected {expected}"
            )

    total = len(questions)
    recall = hits / total
    threshold = 0.70

    # Print misses for debugging if the assertion fails.
    if misses:
        print(f"\nBM25 recall@{TOP_N}: {hits}/{total} = {recall:.2f}")
        for m in misses:
            print(" ", m)

    assert recall >= threshold, (
        f"BM25 recall@{TOP_N} = {recall:.2f} ({hits}/{total}) is below "
        f"the required threshold of {threshold:.2f}.\n" + "\n".join(misses)
    )


@pytest.mark.skipif(
    not os.getenv("AUTOWIKI_TEST_REAL_EMBEDDING"),
    reason=(
        "FAISS parity comparison requires a real embedding provider. "
        "Set AUTOWIKI_TEST_REAL_EMBEDDING=1 and configure ANTHROPIC_API_KEY "
        "(or another provider key) to run this test."
    ),
)
def test_keyword_index_recall_parity_vs_faiss(tmp_path: Path) -> None:
    """BM25 top-3 recall is within ±10% of FAISS top-3 on the fixture.

    This test is gated by AUTOWIKI_TEST_REAL_EMBEDDING because a meaningful
    FAISS comparison requires real embeddings.  Run it manually before merge:

        AUTOWIKI_TEST_REAL_EMBEDDING=1 pytest tests/worker/test_keyword_index.py \
            -k test_keyword_index_recall_parity_vs_faiss -v

    The test uses the same 20-question fixture and synthesized corpus as the
    always-on recall gate above.
    """
    import asyncio

    import numpy as np

    from worker.pipeline.retrieval.rag_indexer import FAISSStore

    questions = json.loads(_FIXTURE_PATH.read_text())
    chunks = _build_chunks(tmp_path)

    # Build the BM25 index.
    bm25_idx = KeywordIndex.build(chunks, repo_index={"files": []})

    # Build the FAISS store using a real embedding provider.
    # Import lazily so the test module loads cleanly without API keys.
    from worker.llm.factory import make_embedding_provider  # type: ignore[import]

    embedding_provider = make_embedding_provider()
    faiss_store = FAISSStore(
        dimension=1536,
        index_path=tmp_path / "faiss.index",
        meta_path=tmp_path / "faiss.meta.pkl",
    )

    async def _build_faiss() -> None:
        texts = [c.text for c in chunks]
        vectors = await embedding_provider.embed_batch(texts, is_code=True)
        faiss_store.add(vectors, [{"file": c.file} for c in chunks])

    asyncio.run(_build_faiss())

    TOP_N = 3
    bm25_hits = 0
    faiss_hits = 0

    async def _embed_query(q: str) -> np.ndarray:
        vecs = await embedding_provider.embed_batch([q], is_code=False)
        return vecs[0]

    for item in questions:
        query: str = item["query"]
        expected: list[str] = item["expected_files"]

        # BM25
        bm25_results = {c.file for c in bm25_idx.search([query], k=TOP_N)}
        if bm25_results & set(expected):
            bm25_hits += 1

        # FAISS
        qvec = asyncio.run(_embed_query(query))
        faiss_results = {m["file"] for m in faiss_store.search(qvec, k=TOP_N)}
        if faiss_results & set(expected):
            faiss_hits += 1

    total = len(questions)
    bm25_recall = bm25_hits / total
    faiss_recall = faiss_hits / total

    print(f"\nRecall@{TOP_N}: BM25={bm25_recall:.2f} FAISS={faiss_recall:.2f}")

    assert bm25_recall >= faiss_recall - 0.10, (
        f"BM25 recall@{TOP_N} ({bm25_recall:.2f}) is more than 10 pp below "
        f"FAISS recall@{TOP_N} ({faiss_recall:.2f}). "
        "BM25 quality is not sufficient to replace FAISS."
    )
