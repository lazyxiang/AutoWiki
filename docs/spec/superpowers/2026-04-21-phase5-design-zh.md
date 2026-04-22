# AutoWiki 第 5 阶段设计 —— 多平台支持 + 首页项目搜索

**日期：** 2026-04-21  
**状态：** 草案  
**涵盖阶段：** 5

---

## 1. 范围

第 5 阶段重新定义了之前规划的路线图条目（“GitLab/Bitbucket + 混合搜索 + MCP 服务器”），将其聚焦于一组可交付的内容：

| 条目 | 决定 |
|---|---|
| GitLab + Bitbucket 支持（公共 + 私有仓库） | ✅ 在范围内 |
| 首页项目搜索（实时模糊过滤） | ✅ 在范围内 |
| MCP 服务器 | ❌ 永久移除 |
| GitHub Webhook 触发器 | ❌ 永久移除 |
| 混合搜索（关键词 + 语义） | ⏭ 推迟到第 6 阶段 |

---

## 2. 架构概览

### 新文件 / 模块

```text
worker/platform/
  __init__.py
  base.py          ← 平台抽象基类 (ABC) + RepoMetadata 数据类 + 自定义异常
  github.py        ← GitHubPlatform（重构自 ingestion.py）
  gitlab.py        ← GitLabPlatform
  bitbucket.py     ← BitbucketPlatform
  registry.py      ← detect_platform(url) → Platform

shared/models.py   ← + PlatformToken SQLAlchemy 模型
worker/platform/token_store.py  ← get_platform_token(platform_name, session) 辅助函数
api/routers/settings.py         ← 令牌 CRUD REST 端点
web/app/settings/page.tsx       ← 设置 UI（个人访问令牌 PAT 管理）
web/components/HomepageClient.tsx ← 拥有查询状态的客户端包装器
web/components/RepoGrid.tsx       ← 过滤并渲染的仓库卡片网格
```

### 修改的文件

```text
worker/pipeline/ingestion.py    ← 使用平台适配器；移除仅限 GitHub 的代码
shared/models.py                ← Repository 模型新增 is_private 字段
api/routers/repos.py            ← URL 验证扩展到 GitLab/Bitbucket
api/main.py                     ← 注册设置路由
web/components/IndexForm.tsx    ← + onQueryChange 回调；多平台 URL 解析
web/components/HeroSection.tsx  ← + 转发到 IndexForm 的 onQueryChange 属性；设置导航链接
web/app/page.tsx                ← 将仓库传递给 HomepageClient
web/lib/api.ts                  ← Repository 接口新增 is_private 字段
CLAUDE.md                       ← 更新第 5/6 阶段路线图
README.md                       ← 更新第 5/6 阶段路线图
```

### 端到端数据流

**私有仓库索引：**
```text
用户提交 gitlab.com/group/sub/repo
  → detect_platform() → GitLabPlatform
  → 从 platform_tokens 表中查找令牌
  → fetch_metadata(owner, name, token) → GitLab API → RepoMetadata(is_private=True)
  → authenticated_clone_url(owner, name, token) → 嵌入令牌的 git clone
  → 流水线其余部分保持不变（AST、规划器、生成器）
  → is_private 存储在 Repository 行中
```

**首页搜索：**
```text
服务器获取仓库 → 作为 props 传递给 HomepageClient
用户输入 "fast" → IndexForm 触发 onQueryChange("fast")
  → HomepageClient 更新查询状态
  → RepoGrid 重新渲染：过滤 owner/name/description 包含 "fast" 的仓库
```

---

## 3. 平台适配层

### 3.1 基础契约 (`worker/platform/base.py`)

```python
@dataclass
class RepoMetadata:
    owner: str
    name: str
    description: str
    stars: int
    language: str          # 主要语言字符串，未知则为空字符串
    default_branch: str
    is_private: bool

class PrivateRepoError(Exception):
    """当仓库不可访问且未存储令牌时抛出。"""

class AuthenticationError(Exception):
    """当平台拒绝存储的令牌时抛出。"""

class UnsupportedPlatformError(Exception):
    """当 URL 主机名不是公认的平台时抛出。"""

class Platform(ABC):
    name: str              # "github" | "gitlab" | "bitbucket"

    @abstractmethod
    def parse_url(self, url: str) -> tuple[str, str]:
        """返回 (owner, name)。对于 GitLab 子组，Owner 可能包含斜杠。"""

    @abstractmethod
    async def fetch_metadata(
        self, owner: str, name: str, token: str | None
    ) -> RepoMetadata:
        """
        从平台 API 获取仓库元数据。
        如果不可访问且 token 为 None，则抛出 PrivateRepoError。
        如果存在 token 但被拒绝，则抛出 AuthenticationError。
        """

    @abstractmethod
    def authenticated_clone_url(
        self, owner: str, name: str, token: str | None
    ) -> str:
        """返回 HTTPS 克隆 URL。如果提供令牌，则嵌入令牌。"""
```

### 3.2 注册表 (`worker/platform/registry.py`)

```python
_PLATFORMS: dict[str, Platform] = {
    "github.com":    GitHubPlatform(),
    "gitlab.com":    GitLabPlatform(),
    "bitbucket.org": BitbucketPlatform(),
}

def detect_platform(url: str) -> Platform:
    host = url.replace("https://", "").replace("http://", "").split("/")[0].lower()
    platform = _PLATFORMS.get(host)
    if platform is None:
        raise UnsupportedPlatformError(f"不支持的主机: {host!r}")
    return platform
```

### 3.3 各平台实现

#### GitHubPlatform (`worker/platform/github.py`)

重构自 `ingestion.py` 中现有的 GitHub 特定代码。

- **API 基地址:** `https://api.github.com/repos/{owner}/{name}`
- **认证头:** `Authorization: Bearer {token}`
- **隐私字段:** `repo["private"]` → `is_private`
- **语言:** `repo["language"]` (字符串或 `null`)
- **克隆 URL:** `https://{token}@github.com/{owner}/{name}.git` (存在令牌) 或普通 HTTPS (无令牌)
- **错误映射:** 无令牌时的 404 → `PrivateRepoError`；有令牌时的 401/403 → `AuthenticationError`

#### GitLabPlatform (`worker/platform/gitlab.py`)

- **API 基地址:** `https://gitlab.com/api/v4/projects/{encoded_path}`，其中 `encoded_path = urllib.parse.quote(f"{owner}/{name}", safe="")`
- **认证头:** `PRIVATE-TOKEN: {token}`
- **隐私字段:** `repo["visibility"] == "private"` → `is_private` (此外 `"internal"` 也计为非公开)
- **语言:** `GET /api/v4/projects/{encoded_path}/languages` → 按百分比排序的字典键；取第一个键
- **子组支持:** `parse_url` 去除 scheme 并根据最后一个 `/` 分割；最后一个片段之前的所有内容为 `owner`，最后一个片段为 `name`
  - `gitlab.com/group/sub/repo` → `owner="group/sub"`, `name="repo"`
- **克隆 URL:** `https://oauth2:{token}@gitlab.com/{owner}/{name}.git`
- **错误映射:** 无令牌时的 404 → `PrivateRepoError`；有令牌时的 401 → `AuthenticationError`

#### BitbucketPlatform (`worker/platform/bitbucket.py`)

- **API 基地址:** `https://api.bitbucket.org/2.0/repositories/{owner}/{name}`
- **认证头:** `Authorization: Bearer {token}`
- **隐私字段:** `repo["is_private"]`
- **语言:** `repo["language"]`
- **星标:** Bitbucket 没有星标计数；始终使用 `0`
- **克隆 URL:** `https://x-token-auth:{token}@bitbucket.org/{owner}/{name}.git`
- **错误映射:** 无令牌时的 401 → `PrivateRepoError`；有令牌时的 403 → `AuthenticationError`

---

## 4. 令牌存储与设置

### 4.1 SQLAlchemy 模型 (`shared/models.py`)

```python
class PlatformToken(Base):
    __tablename__ = "platform_tokens"
    platform:    Mapped[str]      = mapped_column(String, primary_key=True)
    token:       Mapped[str]      = mapped_column(String, nullable=False)
    created_at:  Mapped[datetime] = mapped_column(default=lambda: datetime.now(timezone.utc))
    updated_at:  Mapped[datetime] = mapped_column(onupdate=lambda: datetime.now(timezone.utc))
```

`platform` 是主键，取值为 `"github"`, `"gitlab"`, `"bitbucket"`。每个平台在 AutoWiki 实例中最多存储一个令牌（自托管，单用户模型）。

令牌当前以明文存储。由于 AutoWiki 是自托管的，SQLite 文件位于 `~/.autowiki/`（用户所有），因此本阶段未应用数据库级加密；后续版本可评估系统密钥环或本地 KMS。最低安全约束如下：

- `~/.autowiki/` 目录权限应为 `0700`，SQLite 数据库文件权限应为 `0600`
- GET 端点绝不返回原始 PAT，只返回掩码值
- 日志和错误消息不得输出原始 PAT；克隆后持久化的 `origin.url` 必须去除令牌
- 备份应加密，或明确排除包含 PAT 的 SQLite 数据库文件

### 4.2 令牌辅助函数 (`worker/platform/token_store.py`)

```python
async def get_platform_token(platform_name: str, session: AsyncSession) -> str | None:
    result = await session.get(PlatformToken, platform_name)
    return result.token if result else None
```

由 `ingestion.py` 调用，在执行任何 API 或克隆操作前检索令牌。

### 4.3 设置 REST API (`api/routers/settings.py`)

```http
GET    /api/settings/tokens
       → [{ platform, has_token, masked_token }]
       masked_token: 最后 4 位可见，例如 "••••••••1234" (如果没有令牌则为 null)

PUT    /api/settings/tokens/{platform}
       body: { "token": "ghp_..." }
       → 204 No Content
       插入或更新令牌；更新 updated_at。

DELETE /api/settings/tokens/{platform}
       → 204 No Content
```

`platform` 路径参数根据集合 `{"github", "gitlab", "bitbucket"}` 进行验证 —— 否则返回 422。

### 4.4 设置 UI (`web/app/settings/page.tsx`)

`/settings` 处的一个新页面，包含三个可折叠部分（GitHub、GitLab、Bitbucket），每个部分包含：

- 平台名称 + Logo 图标
- 令牌状态：“未存储令牌”或掩码后的令牌字符串
- 密码类型的 `<input>`，用于输入/更换令牌
- **保存** 按钮 —— 调用 `PUT /api/settings/tokens/{platform}`
- **清除** 按钮（仅在令牌存在时显示） —— 调用 `DELETE /api/settings/tokens/{platform}`

导航至该页面：在 `HeroSection` 右上角增加一个齿轮图标按钮（与现有的调试开关并列）。

设置页面是一个用于初始加载的服务端组件（获取掩码令牌列表），每个平台部分使用 `"use client"` 子组件进行客户端表单处理。

---

## 5. 摄取流水线变更

### 5.1 `worker/pipeline/ingestion.py`

GitHub 特有的元数据获取和克隆 URL 构建被替换为适配器调用。`ingest_repo()`（或等效入口点）内部的核心变更：

```python
platform = detect_platform(url)
owner, name = platform.parse_url(url)

async with get_session(cfg.database_path) as s:
    token = await get_platform_token(platform.name, s)

# 失败时抛出 PrivateRepoError 或 AuthenticationError
metadata = await platform.fetch_metadata(owner, name, token)

clone_url = platform.authenticated_clone_url(owner, name, token)
await clone_or_fetch(clone_url, clone_path)   # 已由 run_in_executor 包装
```

`PrivateRepoError` 和 `AuthenticationError` 传播到 `run_full_index`，后者捕获它们并将消息存储在 `job.error` 中 —— 这与现有流水线错误呈现给前端的方式一致。

### 5.2 Repository 模型 (`shared/models.py`)

在 `Repository` 模型中添加 `is_private: Mapped[bool] = mapped_column(Boolean, default=False)`。包含在 `GET /api/repos` 和 `GET /api/repos/{id}` 的响应负载中。

### 5.3 URL 验证 (`api/routers/repos.py`)

现有的 GitHub URL 正则表达式被多平台检查取代：

```python
SUPPORTED_HOSTS = {"github.com", "gitlab.com", "bitbucket.org"}

def validate_repo_url(url: str) -> None:
    host = url.replace("https://", "").replace("http://", "").split("/")[0].lower()
    if host not in SUPPORTED_HOSTS:
        raise HTTPException(status_code=422, detail=f"不支持的主机: {host!r}")
```

仍然验证最小路径深度（owner + name，即主机后至少 2 个片段）。

### 5.4 `web/components/IndexForm.tsx`

提交成功后的 owner/repo 重定向目前硬编码了 GitHub URL 模式。现更新为处理所有三个平台：

```ts
function parseRepoUrl(url: string): { owner: string; name: string } | null {
  const cleaned = url.replace(/^https?:\/\//, "").replace(/\.git$/, "");
  const parts = cleaned.split("/");
  // parts[0] = host, parts[1..n-1] = owner (对于 GitLab 可能是多段), parts[n] = name
  if (parts.length < 3) return null;
  const name = parts[parts.length - 1];
  const owner = parts.slice(1, parts.length - 1).join("/");
  return { owner, name };
}
```

---

## 6. 首页项目搜索

### 6.1 组件架构

```text
web/app/page.tsx (服务端组件)
  → 通过 getRepositories() 获取仓库
  → <HomepageClient repos={repos} />

web/components/HomepageClient.tsx ("use client")
  → 拥有 query: string 状态
  → <HeroSection onQueryChange={setQuery} />
  → <RepoGrid repos={repos} query={query} />

web/components/HeroSection.tsx ("use client", 已存在)
  → 接收 onQueryChange?: (q: string) => void
  → 传递给 <IndexForm onQueryChange={onQueryChange} />

web/components/IndexForm.tsx ("use client")
  → 接收 onQueryChange?: (q: string) => void
  → 在 onChange 处理函数中调用 onQueryChange(value)
```

### 6.2 模糊匹配 (`web/components/RepoGrid.tsx`)

基于 Token 的匹配 —— 无需新依赖：

```ts
function matchesQuery(query: string, repo: Repository): boolean {
  if (!query.trim()) return true;
  const tokens = query.toLowerCase().split(/\s+/);
  const haystack =
    `${repo.owner} ${repo.name} ${repo.description ?? ""}`.toLowerCase();
  return tokens.every((t) => haystack.includes(t));
}
```

查询中每个以空格分隔的单词必须出现在组合后的 `owner + name + description` 字符串的某个位置。顺序不限。这对于只有少量语料库的自托管工具来说已经“足够模糊”了，且不需要额外的库。

### 6.3 UI 状态

| 状态 | 标题 | 正文 |
|---|---|---|
| 无查询 | "最近索引" | 最多 20 个仓库卡片 |
| 有查询，有结果 | "'{query}' 的结果" | 匹配的卡片 |
| 有查询，无结果 | "'{query}' 的结果" | "没有匹配您搜索的仓库。" 空状态 |
| 完全没有仓库 | *(标题省略)* | "尚未索引任何仓库。成为第一个吧！" |

### 6.4 提交按钮行为（保持不变）

“立即开始”按钮仅用于 URL 提交。输入框为空时该按钮禁用（现有行为）。未添加客户端 URL 检测 —— 服务器验证 URL 并对非仓库输入返回 422，该错误会显示在表单下方的现有错误消息中。实时过滤和 URL 提交完全正交：前者在每次按键时触发，后者在明确点击提交时触发。

---

## 7. API 表面 —— 全量差异

### 新端点

```http
GET  /api/settings/tokens
PUT  /api/settings/tokens/{platform}    body: { token: str }
DEL  /api/settings/tokens/{platform}
```

### 修改的端点

```http
POST /api/repos               — 接受 github.com, gitlab.com, bitbucket.org URL
GET  /api/repos               — 每个仓库的响应包含 is_private 字段
GET  /api/repos/{repo_id}     — 响应包含 is_private 字段
```

### 未修改的端点

所有 wiki、聊天、研究、任务和 WebSocket 端点均不受影响。

---

## 8. 路线图清理

### CLAUDE.md + README.md

替换：
```diff
- **Phase 5** — GitLab/Bitbucket + hybrid search + MCP server
```

为：
```diff
- **Phase 5** — 支持 GitLab/Bitbucket（公共 + 私有仓库，全量 API 元数据） + 首页项目搜索
- **Phase 6** — 混合搜索（关键词 + 语义 BM25/FAISS 融合）
```

从这两个文件中移除所有与 MCP 服务器或 GitHub Webhook 触发器相关的残留提及。

---

## 9. 测试策略

| 测试文件 | 覆盖范围 |
|---|---|
| `tests/test_platform_adapters.py` | 所有三个平台的 `parse_url`, `authenticated_clone_url`, 和 `fetch_metadata` (模拟 `httpx`)；GitLab 的子组 URL；`PrivateRepoError` / `AuthenticationError` 路径 |
| `tests/test_settings_router.py` | 令牌插入/更新，掩码后的 GET 响应，删除，无效平台的 422 |
| `tests/test_ingestion_multiplatform.py` | 模拟适配器 + 令牌存储；验证传递给 `clone_or_fetch` 的克隆 URL；`PrivateRepoError` 在 `job.error` 中体现 |
| `web/__tests__/RepoGrid.test.tsx` | `matchesQuery` 处理单 token、多 token、无匹配、空查询、描述匹配的情况 |

覆盖率目标：新 `worker/platform/` 和 `api/routers/settings.py` 的覆盖率 ≥ 80%（与现有项目目标一致）。

---

## 10. 第 5 阶段超出范围的内容

- 私有部署的 GitLab / Bitbucket Server 实例（仅限云端托管版）
- 每用户令牌隔离（单用户自托管模型；全局每个平台一个令牌）
- 令牌过期检测或刷新流
- 仓库卡片中匹配文本的高亮显示
- 搜索结果的分页
- 私有仓库的重新索引 / 刷新不需要额外的 UI —— 将自动使用存储的令牌（这在范围内；列在这里仅为说明该路径无需 UI 变更）
