# API

```text
POST  /api/repos                                      提交仓库以建立索引 → {repo_id, job_id, status}
GET   /api/repos                                      列出所有仓库
GET   /api/repos/{repo_id}                            仓库状态和元数据
POST  /api/repos/{repo_id}/refresh                    触发增量刷新 → {job_id}
GET   /api/repos/{repo_id}/wiki                       列出维基页面（已排序）
GET   /api/repos/{repo_id}/wiki/{slug}                获取维基页面（Markdown + 元数据）
POST  /api/repos/{repo_id}/chat                       创建新的聊天会话 → {session_id}
GET   /api/repos/{repo_id}/chat/{session_id}          获取聊天历史
POST  /api/repos/{repo_id}/research                   开始深度研究查询 → {job_id, report_id, status}
GET   /api/repos/{repo_id}/research/{job_id}          获取研究报告（计划、发现、Markdown）
GET   /api/jobs/{job_id}                              任务状态和进度 (0–100)
WS    /ws/jobs/{job_id}                               流式传输 {progress, status} 直到完成/失败
WS    /ws/repos/{repo_id}/chat/{session_id}           实时流式传输聊天响应
WS    /ws/repos/{repo_id}/research/{job_id}           流式传输研究事件（计划/步骤/发现/报告）
```

示例：

```bash
# 提交仓库
curl -s -X POST http://localhost:3001/api/repos \
  -H "Content-Type: application/json" \
  -d '{"url": "https://github.com/psf/requests"}' | jq .
# → {"repo_id": "a3f8...", "job_id": "uuid...", "status": "queued"}

# 轮询进度
curl -s http://localhost:3001/api/jobs/<job_id> | jq .progress

# 读取维基页面
curl -s http://localhost:3001/api/repos/<repo_id>/wiki/overview | jq .content
```
