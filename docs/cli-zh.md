# 命令行界面 (CLI)

```bash
# 为仓库建立索引
autowiki index github.com/owner/repo

# 重新建立索引，但不重建 FAISS 向量索引（速度更快，跳过嵌入步骤）
autowiki index github.com/owner/repo --reuse-index

# 列出所有已建立索引的仓库
autowiki list

# 启动完整技术栈（API + worker + web UI）
autowiki serve [--port 3000] [--api-port 3001]

# 对已建立索引的仓库运行深度研究查询（已禁用，见 issue #43）
autowiki research github.com/owner/repo "身份验证系统是如何工作的？"

# 在不运行流水线的情况下检查存储的维基计划 (wiki plan)
autowiki validate-plan owner-repo

# 显示或更新配置
autowiki config show
autowiki config set <key> <value>  # 点分隔的键名，例如 llm.provider, embedding.model
```
