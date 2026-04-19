# API

```text
POST  /api/repos                                      Submit a repo for indexing → {repo_id, job_id, status}
GET   /api/repos                                      List all repos
GET   /api/repos/{repo_id}                            Repo status and metadata
POST  /api/repos/{repo_id}/refresh                    Trigger incremental refresh → {job_id}
GET   /api/repos/{repo_id}/wiki                       List wiki pages (ordered)
GET   /api/repos/{repo_id}/wiki/{slug}                Get a wiki page (Markdown + metadata)
POST  /api/repos/{repo_id}/chat                       Create a new chat session → {session_id}
GET   /api/repos/{repo_id}/chat/{session_id}          Get chat history
POST  /api/repos/{repo_id}/research                   Start a deep research query → {job_id, report_id, status}
GET   /api/repos/{repo_id}/research/{job_id}          Get research report (plan, findings, Markdown)
GET   /api/jobs/{job_id}                              Job status and progress (0–100)
WS    /ws/jobs/{job_id}                               Stream {progress, status} until done/failed
WS    /ws/repos/{repo_id}/chat/{session_id}           Stream chat responses in real time
WS    /ws/repos/{repo_id}/research/{job_id}           Stream research events (plan/step/finding/report)
```

Example:

```bash
# Submit a repo
curl -s -X POST http://localhost:3001/api/repos \
  -H "Content-Type: application/json" \
  -d '{"url": "https://github.com/psf/requests"}' | jq .
# → {"repo_id": "a3f8...", "job_id": "uuid...", "status": "queued"}

# Poll progress
curl -s http://localhost:3001/api/jobs/<job_id> | jq .progress

# Read a wiki page
curl -s http://localhost:3001/api/repos/<repo_id>/wiki/overview | jq .content
```
