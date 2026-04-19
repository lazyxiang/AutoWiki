# 配置

AutoWiki 按以下顺序解析配置（优先级从高到低）：

1. 环境变量
2. 当前目录下的 `autowiki.yml`
3. `~/.autowiki/autowiki.yml`
4. 内置默认值

## 关键环境变量

| 变量 | 默认值 | 描述 |
|---|---|---|
| `ANTHROPIC_API_KEY` | — | Anthropic API 密钥（当 `AUTOWIKI_LLM_PROVIDER=anthropic` 时使用） |
| `OPENAI_API_KEY` | — | OpenAI API 密钥（用于 LLM 和/或嵌入） |
| `GOOGLE_API_KEY` | — | Google API 密钥（用于 Gemini LLM 和/或嵌入） |
| `AUTOWIKI_LLM_PROVIDER` | `anthropic` | `anthropic` · `openai` · `openai-compatible` · `ollama` · `google` |
| `AUTOWIKI_LLM_MODEL` | `claude-sonnet-4-6` | 所配置提供商的模型名称 |
| `AUTOWIKI_LLM_API_KEY` | — | API 密钥覆盖。如果未设置特定提供商的密钥（例如 `ANTHROPIC_API_KEY`）或使用自定义基础 URL，则此项必填。 |
| `AUTOWIKI_LLM_BASE_URL` | — | `openai-compatible` 或 `ollama` 提供商的基础 URL |
| `AUTOWIKI_EMBEDDING_PROVIDER` | `openai` | `openai` · `ollama` · `google` |
| `AUTOWIKI_EMBEDDING_MODEL` | `text-embedding-3-small` | 嵌入模型名称 |
| `AUTOWIKI_EMBEDDING_API_KEY` | — | API 密钥覆盖。如果未设置特定提供商的密钥（例如 `OPENAI_API_KEY`）或使用自定义基础 URL，则此项必填。 |
| `AUTOWIKI_LLM_FAST_MODEL` | *(与 LLM 模型相同)* | 用于大纲 (outline) 和事实核查 (fact-check) 阶段的更快/更便宜的模型（例如 `claude-haiku-4-5`） |
| `REDIS_URL` | `redis://localhost:6379` | Redis 连接字符串 |
| `DATABASE_PATH` | `~/.autowiki/autowiki.db` | SQLite 数据库路径 |
| `AUTOWIKI_DATA_DIR` | `~/.autowiki` | 存放克隆仓库、索引和维基文件的根目录 |

## YAML 配置文件

```yaml
# autowiki.yml (或 ~/.autowiki/autowiki.yml)
llm:
  provider: anthropic          # anthropic | openai | openai-compatible | ollama | google
  model: claude-sonnet-4-6
  api_key: ${ANTHROPIC_API_KEY}
  # base_url: http://localhost:11434/v1   # 仅限 openai-compatible / ollama

embedding:
  provider: openai             # openai | ollama | google
  model: text-embedding-3-small
  api_key: ${OPENAI_API_KEY}
```

通过 CLI 进行管理：

```bash
autowiki config show
autowiki config set llm.provider ollama
autowiki config set llm.model llama3.2
autowiki config set embedding.provider ollama
autowiki config set embedding.model nomic-embed-text
```
