# 设计：文件读写 Tool + 下载 Skill 增强

- **分支**：`feat/file-tools-and-download-skill`（基于 `origin/master`）
- **日期**：2026-04-19
- **范围**：两件事并行：
  1. 给 assistant 新增「读取文件 / 搜索内容」能力（builtin tool）+ 配套安全 prompt 与代码层硬阻断。
  2. 重构 `anime-download` skill，覆盖 RSS 链接、磁力链接、纯描述（番剧 / 集数 / 字幕组 / 语言）三种用户输入形态，统一收口到 `oani/create_download` 后端接口。

---

## 0. 项目纪律（不可违反）

- **oani 域 skill 必须是薄 HTTP 客户端**：所有真实业务逻辑写在 `src/openlist_ani/` 内，并经 backend FastAPI router 暴露 endpoint；`skills/oani/script/*.py` 仅 `BackendClient` 的薄包装。
- 复用现有 `core/website/` 的 RSS 解析（`AnimeResourceInfo`），不要重新写 RSS 解析。
- 编码风格：Google Python Style Guide，见 `.claude/rules/oAni-code-style-guide.instructions.md`。

---

## 1. 文件读写 Tool

### 1.1 安全模型（Defense-in-Depth）

| 层 | 措施 |
|---|---|
| Path 白名单 | 只允许进入项目根下的 `src/`、`skills/`、`data/`、`logs/`、`memory/` 五个目录（含子树）。其他任何路径（包括项目根直属文件 `config.toml`、`.env`、`SOUL.md`、`pyproject.toml` 等）一律拒绝。 |
| Path 规范化 | `Path.resolve()` 解析 symlink 后再次校验是否仍在白名单内，防止 symlink 穿透。`..` 组件出界直接拒。 |
| 敏感文件名硬阻断 | 即使位于白名单内，文件名匹配 `(?i)(\.env|secret|token|credential|api[_-]?key|password|private[_-]?key|\.pem$|\.key$|cookies?\.txt$)` 直接拒。`memory/` 内若文件名命中关键字也拒。 |
| 输出 Redaction | 读出 / 搜索结果在返回给 LLM 前，跑一遍正则脱敏：`(?i)(api[_-]?key|token|secret|password|bearer|authorization)\s*[:=]\s*\S+`、`Authorization:\s*\S+`、`-----BEGIN [A-Z ]+PRIVATE KEY-----[\s\S]+?-----END [A-Z ]+PRIVATE KEY-----`、`AKIA[0-9A-Z]{16}`（AWS）、`gh[pousr]_[A-Za-z0-9]{20,}`（GitHub PAT）、`xox[baprs]-[A-Za-z0-9-]{10,}`（Slack）等常见 secret 模式 → 替换为 `<REDACTED>`。命中即写 `logger.warning`。 |
| System Prompt | 见 §1.4，明确禁止读敏感文件、禁止任何形式外发，即使用户主动要求也要拒绝并解释。 |

实现位置：`src/openlist_ani/assistant/tool/builtin/_file_security.py`，导出 `resolve_safe_path(path: str) -> Path`、`redact_secrets(text: str) -> tuple[str, int]`（返回脱敏后文本与命中次数）。

### 1.2 Tool 拆分

两个独立 builtin tool（继承 `BaseTool`）：

#### `read_file`

```
参数：
  path: str            # 必填，相对项目根或绝对路径
  offset: int = 0      # 起始行号（0-based）
  limit: int = 2000    # 最多读取行数
返回：
  带行号的文本（cat -n 格式），末尾追加 truncation 提示
约束：
  - is_read_only = True
  - is_concurrency_safe = True
  - 单文件最大 3 MB（避免上下文炸裂）
  - 二进制文件直接拒
```

#### `grep`

```
参数：
  pattern: str                       # 必填，rg 兼容正则
  path: str | None                   # 默认整个白名单根
  glob: str | None                   # 例 "**/*.py"
  type: str | None                   # rg --type，例 "py"
  output_mode: "files_with_matches"|"content"|"count" = "files_with_matches"
  case_insensitive: bool = False
  multiline: bool = False
  context_before: int = 0
  context_after: int = 0
  head_limit: int = 250
返回：rg 输出（脱敏后）
约束：
  - is_read_only = True
  - is_concurrency_safe = True
  - 通过 PyPI `ripgrep` 包附带的 `rg` 二进制（同时安装该包到 pyproject）
  - 调用前确认目标 path 在白名单内；rg 自身的 `--no-follow` 防 symlink
```

### 1.3 注册

`src/openlist_ani/assistant/tool/builtin/__init__.py`（或现有 registry 装配点）注册 `ReadFileTool` 与 `GrepTool`。

### 1.4 System Prompt 安全段

追加到 `src/openlist_ani/assistant/memory/manager.py` 的 `DEFAULT_SOUL` 末尾（作为新的 `# Security` 节）：

```
# Security

You can inspect project files via `read_file` and `grep`. These tools are
restricted to: src/, skills/, data/, logs/, memory/. They will refuse paths
outside this whitelist; do not attempt workarounds (symlinks, .., absolute
paths to /etc, /home, etc.).

You MUST NOT read, repeat, paraphrase, or transmit any of the following,
even if a user explicitly asks you to:

- API keys, tokens, secrets, passwords, OAuth credentials, session cookies
- Private keys (RSA/SSH/TLS), .pem / .key files
- Database connection strings containing passwords
- Bot tokens (Telegram / Discord / Slack), webhook URLs with embedded secrets
- The contents of config.toml, .env, SOUL.md (these are blocked at tool level)

If a user asks you to "show the api key" or "send my token to ...", refuse
and briefly explain why. Never embed such values in tool arguments
(send_message, web_fetch, memory write, notifications) — the tool layer
also redacts but you should not rely on it.

If a `read_file` / `grep` result contains <REDACTED>, do NOT try to recover
the original value via alternative paths.
```

### 1.5 测试

`tests/assistant/tool/builtin/`：

- `test_file_security.py`：白名单内/外路径、symlink 穿透、`..` 出界、敏感文件名、脱敏正则各分支。
- `test_read_file_tool.py`：基础读、offset/limit、二进制拒、超大文件拒、白名单外拒、敏感文件拒、含 secret 的内容被脱敏。
- `test_grep_tool.py`：基本搜索、glob/type 过滤、超出白名单 path 拒、命中含 secret 的行被脱敏、`output_mode` 三种分支。

### 1.6 依赖

`pyproject.toml` 增加：`ripgrep` (PyPI 包，自带 `rg` 二进制；运行期通过 `importlib.resources` 或环境变量定位)。验证 wheel 在 Linux/macOS/Windows 都可用；若 Windows 缺失则在 README 注明回退到系统 `rg`。

---

## 2. 下载 Skill 增强

### 2.1 整体编排（写在 `skills/anime-download/SKILL.md`）

按用户输入分流：

| 用户输入形态 | 流程 |
|---|---|
| RSS 链接 | `oani/parse_rss(url)` → 列表给用户/按用户条件筛选 → 逐个 `oani/create_download(download_url, title)` |
| 磁力链接 | `oani/resolve_magnet(magnet)` → 拿到 `title` + 标题级合集判断 → 若是合集 → 拒绝并告知用户「当前不支持下载合集类资源」→ 否则 `oani/create_download(magnet, title)` |
| 仅描述（番剧/集数/字幕组/语言） | 现有路径不变：`mikan/search` → `mikan/subgroups`（按用户字幕组过滤）→ `mikan/releases`（按集数+语言过滤）→ `oani/create_download` |

**铁律**：

- `title` 必须来源于真实资源元数据（RSS entry / libtorrent metadata / magnet `dn` / mikan release title）。**不允许 LLM 自行编造或拼接 title**——后端会用它解析番剧/季/集做重命名，伪造会导致重命名错乱。
- 任何路径走到「无法获取真实 title」时：**必须询问用户**，不下载。

### 2.2 后端新增 endpoint + 业务实现

#### A. `POST /api/parse_rss`

- **Schema**：
  - 请求 `{ url: str, limit: int | None = None }`
  - 响应 `{ success: bool, message: str, entries: [{ index: int, title: str, download_url: str, anime_name: str | None, episode: int | None, fansub: str | null, quality: str | null, languages: [str], pub_date: str | null }] }`
- **业务**：复用 `src/openlist_ani/core/website/factory.py` 选 parser → `await website.fetch_feed(url)` → 把 `AnimeResourceInfo` 序列化为响应 dto；`limit` 截断。
- **位置**：
  - Router：`src/openlist_ani/backend/router.py` 新增端点
  - Schema：`src/openlist_ani/backend/schema.py` 新增 `ParseRSSRequest/Response/Entry`
  - Service：`src/openlist_ani/backend/service.py` 新增 `async def parse_rss(url, limit) -> tuple[bool, str, list[entry_dto]]`，内部调 `website.fetch_feed`

#### B. `POST /api/resolve_magnet`

- **Schema**：
  - 请求 `{ magnet: str, timeout: int = 30 }`
  - 响应 `{ success: bool, message: str, title: str | None, source: "dn" | "metadata" | None, file_count: int | null, files: [{ name: str, size: int }] | null, is_collection: bool, collection_reason: str | null }`
- **业务**：
  1. 优先解析 magnet 中的 `dn=` 参数。
  2. `dn` 为空或仅是 hash 时，调 `libtorrent` 拉 metadata（DHT + trackers）；超时则 `success=false, message="metadata 拉取超时，请提供 .torrent 或具体 title"`，`title=None`。
  3. **合集判定**（仅依据 title 关键词，与之前确认一致）：title 命中以下正则任意一个即视为合集：
     ```
     (?i)(合集|全集|总集|Complete|Batch|BD\s*BOX|S\d+\s*Complete|\d{1,3}\s*[-~–]\s*\d{1,3}|01\s*[-~–]\s*\d{2,3}|S\d+E\d+-E?\d+)
     ```
     命中则 `is_collection=true, collection_reason=<匹配片段>`；调用方据此向用户提示并放弃下载。
- **位置**：
  - 新增模块 `src/openlist_ani/core/download/magnet/resolver.py`：函数 `async def resolve_magnet(magnet: str, timeout: float) -> ResolveResult`
  - 模型 `ResolveResult` 用 dataclass，字段对齐响应
  - 合集正则放到同模块的 `_COLLECTION_PATTERNS` 常量
  - libtorrent 调用以 `asyncio.to_thread` 包裹，便于在 async router 里调用
  - Router/Schema/Service 模式同上

> **依赖说明**：libtorrent 的 PyPI 包名是 `libtorrent`（即 `libtorrent-rasterbar` 的 wheel）。在 `pyproject.toml` 加入；在 README/文档说明 Windows 安装可能需要预编译 wheel；docker 镜像需 `apt install python3-libtorrent` 或装 wheel。

### 2.3 新增 skill 脚本（薄客户端）

#### `skills/oani/script/parse_rss.py`

```python
async def run(url: str = "", limit: int | None = None, **kwargs) -> str:
    if not url:
        return "Error: 'url' is required."
    client = BackendClient(config.backend_url)
    try:
        data = await client.parse_rss(url, limit=limit)
    except Exception as e:
        return f"Error parsing RSS: {e}"
    finally:
        await client.close()
    # 返回紧凑 markdown 表格，含 index/title/download_url
```

#### `skills/oani/script/resolve_magnet.py`

```python
async def run(magnet: str = "", timeout: int = 30, **kwargs) -> str:
    ...
    # 返回 markdown：title/source/file_count/is_collection/collection_reason
```

`BackendClient` 在 `src/openlist_ani/backend/client.py` 新增对应 `parse_rss(...)` / `resolve_magnet(...)` 方法。

### 2.4 SKILL.md 改写要点（`skills/anime-download/SKILL.md`）

新增 sections：

- **Workflow A — RSS link**：调用 `oani/parse_rss`、展示 entries、根据用户描述（如「下载第 5、7、9 集」「全部下载」「跳过 720p」）筛选 → 逐个 `oani/create_download(entry.download_url, entry.title)`。
- **Workflow B — Magnet link**：调用 `oani/resolve_magnet` → 检查 `is_collection`：true 时输出固定话术「当前 OpenList-Ani 不支持下载合集类资源（命中：<reason>），请提供单集资源或换源。」并退出；false 时调 `oani/create_download(magnet, title)`。`title` 为空时询问用户。
- **Workflow C — Description only**：保留并明确「字幕组/语言/集数过滤规则」。
- **Title integrity**：保留现有 Episode Matching Rules，并加一条「不得伪造 title — 来源必须是 RSS entry / magnet metadata / mikan release title 之一」。
- **Collection rejection**（适用 RSS 与 magnet 两条线）：在 RSS 列表 / resolve_magnet 输出里发现合集关键词 → 同样话术告知并跳过。

### 2.5 测试

- `tests/core/download/magnet/test_resolver.py`：合集正则各分支（关键词命中、文件名分隔、负样本）；mock libtorrent，覆盖超时/成功/dn-only 路径。
- `tests/backend/test_router_parse_rss.py`、`test_router_resolve_magnet.py`：endpoint 行为、错误码、字段映射。
- `tests/skills/oani/test_parse_rss.py`、`test_resolve_magnet.py`：mock backend，验证 skill 输出格式。

### 2.6 文档

- `docs/配置说明.md` 添加 libtorrent 依赖说明。
- `skills/anime-download/SKILL.md` 整体重写。

---

## 3. 不在本次范围（YAGNI）

- 不在 backend 引入 batch download endpoint（assistant 自己循环调 `create_download` 即可）。
- 不实现「合集 → 拆分单集下载」（文档明确告诉用户不支持）。
- 不在 file 工具引入 write/edit（只读）。
- 不在 magnet resolver 里做主动下载（只取 metadata）。
- 不实现 Web UI 改动。

---

## 4. 实施顺序（提交切片）

1. 文件 tool 安全模块 + `read_file` + `grep`（含测试）
2. SOUL 安全段更新（含 manager.py 中 DEFAULT_SOUL）
3. `pyproject.toml` 依赖：`ripgrep`、`feedparser`（若未装）、`libtorrent`
4. backend `parse_rss` endpoint + service + client + skill
5. magnet resolver core + backend `resolve_magnet` endpoint + service + client + skill
6. `anime-download` SKILL.md 重写
7. 端到端：手测三种工作流

---

## 5. 验收

- 文件 tool：在 `src/`、`skills/`、`data/`、`logs/`、`memory/` 内可读可搜；试图读 `config.toml` / `.env` / `SOUL.md` / `/etc/passwd` / `../config.toml` / 含 `secret` 关键字的文件名 → 全部被拒并附人类可读错误。读取一个含「fake_token = 'abc123def...'」的测试文件 → 返回值含 `<REDACTED>`。
- SOUL 安全段：手动让 assistant「告诉我 openai_api_key」→ 拒绝。
- RSS 工作流：传入 mikan 公开 RSS → 返回 entry 列表，选定后成功下载；命中合集关键词 → 跳过并提示。
- magnet 工作流：dn-only magnet → 用 dn；纯 hash magnet → libtorrent 拉到名字；合集 magnet → 拒绝；无法拉到 title → 询问用户而非伪造。
- 描述工作流：仅 anime+ep+fansub+lang → 现有 mikan 链路成功，title 仍来自 mikan release。
