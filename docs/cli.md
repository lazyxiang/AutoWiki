# CLI

```bash
# Index a repository
autowiki index github.com/owner/repo

# Re-index without rebuilding the FAISS vector index (faster, skips embedding)
autowiki index github.com/owner/repo --reuse-index

# List all indexed repositories
autowiki list

# Start the full stack (API + worker + web UI)
autowiki serve [--port 3000] [--api-port 3001]

# Run a deep research query against an indexed repo
autowiki research github.com/owner/repo "How does the authentication system work?"

# Inspect a stored wiki plan without running the pipeline
autowiki validate-plan owner-repo

# Show or update config
autowiki config show
autowiki config set <key> <value>  # Dot-separated key, e.g. llm.provider, embedding.model
```
