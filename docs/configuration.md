# Configuration

AutoWiki resolves config in this order (highest wins):

1. Environment variables
2. `autowiki.yml` in the current directory
3. `~/.autowiki/autowiki.yml`
4. Built-in defaults

## Key environment variables

| Variable | Default | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | — | Anthropic API key (used when `AUTOWIKI_LLM_PROVIDER=anthropic`) |
| `OPENAI_API_KEY` | — | OpenAI API key (LLM and/or embeddings) |
| `GOOGLE_API_KEY` | — | Google API key (Gemini LLM and/or embeddings) |
| `AUTOWIKI_LLM_PROVIDER` | `anthropic` | `anthropic` · `openai` · `openai-compatible` · `ollama` · `google` |
| `AUTOWIKI_LLM_MODEL` | `claude-sonnet-4-6` | Model name for the configured provider |
| `AUTOWIKI_LLM_API_KEY` | — | API key override. Required if provider-specific key (e.g. `ANTHROPIC_API_KEY`) is not set or if using a custom base URL. |
| `AUTOWIKI_LLM_BASE_URL` | — | Base URL for `openai-compatible` or `ollama` providers |
| `AUTOWIKI_EMBEDDING_PROVIDER` | `openai` | `openai` · `ollama` · `google` |
| `AUTOWIKI_EMBEDDING_MODEL` | `text-embedding-3-small` | Embedding model name |
| `AUTOWIKI_EMBEDDING_API_KEY` | — | API key override. Required if provider-specific key (e.g. `OPENAI_API_KEY`) is not set or if using a custom base URL. |
| `AUTOWIKI_LLM_FAST_MODEL` | *(same as LLM model)* | Faster/cheaper model for outline and fact-check passes (e.g. `claude-haiku-4-5`) |
| `REDIS_URL` | `redis://localhost:6379` | Redis connection string |
| `DATABASE_PATH` | `~/.autowiki/autowiki.db` | SQLite database path |
| `AUTOWIKI_DATA_DIR` | `~/.autowiki` | Root directory for clones, indexes, and wiki files |

## YAML config file

```yaml
# autowiki.yml (or ~/.autowiki/autowiki.yml)
llm:
  provider: anthropic          # anthropic | openai | openai-compatible | ollama | google
  model: claude-sonnet-4-6
  api_key: ${ANTHROPIC_API_KEY}
  # base_url: http://localhost:11434/v1   # openai-compatible / ollama only

embedding:
  provider: openai             # openai | ollama | google
  model: text-embedding-3-small
  api_key: ${OPENAI_API_KEY}
```

Manage via CLI:

```bash
autowiki config show
autowiki config set llm.provider ollama
autowiki config set llm.model llama3.2
autowiki config set embedding.provider ollama
autowiki config set embedding.model nomic-embed-text
```
