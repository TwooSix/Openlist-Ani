# Docker 部署指南

通过 Docker 部署无需安装 Python 环境，适合服务器或 NAS 用户。

## 环境要求

- **Docker** ≥ 20.10
- **Docker Compose**（可选，推荐）

## 第一步：准备配置文件

在你的工作目录下创建 `config.toml`，以下是**完整配置模板**：

```toml
# ============================================================
# Openlist-Ani 完整配置文件
# ============================================================

# ---------- 后端 API ----------
[backend]
host = "127.0.0.1"
port = 26666

# ---------- RSS 订阅 ----------
[rss]
urls = [
    # "https://mikanani.me/RSS/MyBangumi?token=xxx"
]
interval_time = 300

# ---------- 下载优先级（可选） ----------
# 同一番剧同集存在多个资源时，按优先级自动过滤
# [rss.priority]
# field_order = ["fansub", "quality", "languages"]  # 比较顺序
# fansub = []                # 字幕组优先级（靠前优先）
# quality = ["2160p", "1080p", "720p", "480p", "360p"]  # 清晰度优先级
# languages = []             # 语言优先级，可选: "简", "繁", "日", "英"

# ---------- 代理（可选） ----------
[proxy]
http = ""
https = ""

# ---------- Openlist 网盘 ----------
[openlist]
url = "http://localhost:5244"
token = ""
download_path = "/PikPak/Anime"
offline_download_tool = "QBITTORRENT"
rename_format = "{anime_name} S{season:02d}E{episode:02d} {fansub} {quality} {languages}"

# ---------- 元数据解析与校验 ----------
[metadata_parser]
provider = "llm"      # 推荐 llm + tmdb；未配置 LLM 时默认 regex + tmdb

[metadata_validator]
provider = "tmdb"

# ---------- LLM（AI 重命名） ----------
[llm]
openai_api_key = ""   # 启用 AI 助理时也必填
openai_base_url = "https://api.deepseek.com/v1"
openai_model = "deepseek-chat"
tmdb_api_key = ""
tmdb_language = "zh-CN"

# ---------- 通知（可选） ----------
[notification]
enabled = false
batch_interval = 300.0

# Telegram 通知
# [[notification.bots]]
# type = "telegram"
# enabled = true
# config = { bot_token = "你的Bot Token", user_id = "你的用户ID" }

# PushPlus 微信通知
# [[notification.bots]]
# type = "pushplus"
# enabled = true
# config = { user_token = "你的Token", channel = "wechat" }

# ---------- AI 智能助理（可选） ----------
[assistant]
enabled = false

[assistant.telegram]
bot_token = ""
allowed_users = []    # 允许的用户 ID 列表（留空则允许所有人，建议设置具体 ID）

# ---------- Bangumi（可选） ----------
[bangumi]
access_token = ""

# ---------- Mikan（可选） ----------
[mikan]
username = ""
password = ""

# ---------- 日志 ----------
[log]
level = "INFO"
rotation = "00:00"
retention = "1 week"
```

> 使用 `--network host` 模式，配置文件和本机运行完全一致，`localhost` 直接指向宿主机。

## 第二步：准备数据目录

```bash
mkdir -p data
```

## 第三步：配置必填项与推荐项

### 1. RSS 订阅链接

```toml
[rss]
urls = ["https://mikanani.me/RSS/MyBangumi?token=你的token"]
```

### 2. Openlist 配置

```toml
[openlist]
url = "http://localhost:5244"
token = "你的令牌"
download_path = "/PikPak/Anime"
offline_download_tool = "QBITTORRENT"
```

> **令牌获取**：登录 Openlist 后台 → 设置 → 其他 → 令牌

### 3. 推荐：LLM + TMDB

推荐配置 LLM 做标题解析，并继续使用 TMDB 做校验；如果不配置 LLM API Key，主程序会使用默认的 `regex` + `tmdb`。

```toml
[metadata_parser]
provider = "llm"

[metadata_validator]
provider = "tmdb"

[llm]
openai_api_key = "sk-xxx"
openai_base_url = "https://api.deepseek.com/v1"
openai_model = "deepseek-chat"
```

## 第四步：启动容器

### 方式 A：docker run

**仅主程序**：

```bash
docker run -d \
  --name openlist-ani \
  --network host \
  -v $(pwd)/config.toml:/config.toml \
  -v $(pwd)/data:/data \
  twosix26/openlist-ani:latest
```

**主程序 + AI 助理**：

```bash
docker run -d \
  --name openlist-ani \
  --network host \
  -e ENABLE_ASSISTANT=true \
  -v $(pwd)/config.toml:/config.toml \
  -v $(pwd)/data:/data \
  -v $(pwd)/skills:/skills \
  twosix26/openlist-ani:latest
```

### 方式 B：docker compose（推荐）

创建 `docker-compose.yml`：

```yaml
services:
  openlist-ani:
    image: twosix26/openlist-ani:latest
    container_name: openlist-ani
    network_mode: host
    restart: unless-stopped
    environment:
      - ENABLE_ASSISTANT=false    # 改为 true 启用 AI 助理
    volumes:
      - ./config.toml:/config.toml
      - ./data:/data
      - ./skills:/skills
```

启动：

```bash
docker compose up -d
```

## Docker 参数说明

| 参数 | 说明 |
|------|------|
| `--network host` | 使用宿主机网络，容器直接共享宿主机的网络栈 |
| `-e ENABLE_ASSISTANT=true` | 启用 AI 智能助理（同时运行主程序和助理） |
| `-v ./config.toml:/config.toml` | 挂载配置文件 |
| `-v ./data:/data` | 挂载数据目录（持久化数据库、日志等） |
| `-v ./skills:/skills` | 挂载助理 skills 目录；首次启动助理时自动生成内置 skills，之后可在宿主机修改或新增 |

> `--network host` 的好处：容器与宿主机共享网络，`localhost` 直接指向宿主机，无需额外的网络配置。配置文件和本机运行时完全一致。

## 启用通知

### Telegram 通知

1. 向 [@BotFather](https://t.me/BotFather) 发送 `/newbot` 创建 Bot，获取 `bot_token`
2. 向 [@userinfobot](https://t.me/userinfobot) 发送消息获取你的 `user_id`
3. 配置：

```toml
[notification]
enabled = true

[[notification.bots]]
type = "telegram"
enabled = true
config = { bot_token = "123456:ABC-DEF...", user_id = "你的UserID" }
```

### PushPlus 微信通知

1. 前往 [PushPlus](https://pushplus.plus/) 注册获取 Token
2. 配置：

```toml
[notification]
enabled = true

[[notification.bots]]
type = "pushplus"
enabled = true
config = { user_token = "你的Token", channel = "wechat" }
```

### 微信 iLink 通知

1. 先执行一次 setup 命令，完成扫码和会话 ID 获取：

```bash
uv run openlist-ani-wechat-login
```

如果只使用 Docker 镜像，可以用等价方式运行镜像里的命令，例如：

```bash
docker run --rm -it --entrypoint openlist-ani-wechat-login twosix26/openlist-ani:latest
```

命令会在终端打印二维码；微信扫码确认后，继续按提示给机器人发送任意文本消息。命令最后会打印完整 TOML 配置块。

2. 把输出里的内容填入配置：

```toml
[notification]
enabled = true

[[notification.bots]]
type = "wechat"
enabled = true
config = { account_id = "bot@im.bot", token = "your_bot_token", base_url = "https://ilinkai.weixin.qq.com", home_channel = "user@im.wechat" }
```

`home_channel` 就是 setup 命令捕获到的微信 iLink `chat_id`。如果启用了微信通知但缺少这些字段，容器启动时会失败并提示先运行 `openlist-ani-wechat-login`。

微信通知固定发送到配置里的 `home_channel`。如果要改通知目标，重新运行 `openlist-ani-wechat-login`，或把新的 `chat_id` 手动填到 `home_channel`。

### 飞书通知

1. 在飞书开放平台创建自建应用，启用机器人能力，在「凭证与基础信息」中复制 `App ID` 和 `App Secret`。
2. 同时启用飞书助理和飞书通知，并把同一组 `App ID` / `App Secret` 填入配置。
3. 启动容器时设置 `ENABLE_ASSISTANT=true`。
4. 在要接收通知的飞书私聊中发送 `/set-notify-home`；如果是群聊，请先 @机器人 再发送该命令。

```toml
[notification]
enabled = true

[[notification.bots]]
type = "feishu"
enabled = true
config = { app_id = "cli_xxx", app_secret = "你的 App Secret" }
```

修改配置后重启容器：

```bash
docker restart openlist-ani
# 或
docker compose restart
```

## 启用 AI 智能助理

1. 在 `config.toml` 中配置助理：

```toml
[assistant]
enabled = true

[assistant.telegram]
enabled = true
bot_token = "你的Bot Token"
allowed_users = [123456789]
```

微信助理：

```toml
[assistant]
enabled = true

[assistant.wechat]
enabled = true
account_id = "bot@im.bot"
token = "your_bot_token"
base_url = "https://ilinkai.weixin.qq.com"
home_channel = "user@im.wechat"
```

微信助理启动前也先执行 `openlist-ani-wechat-login`，并把命令打印的 `account_id/token/base_url/home_channel` 填入配置。微信助理只接受 `home_channel` 这个会话的消息；发送 `/id` 可查看当前 `chat_id`。

飞书助理：

```toml
[assistant]
enabled = true

[assistant.feishu]
enabled = true
app_id = "cli_xxx"
app_secret = "你的 App Secret"
domain = "feishu"
```

把机器人加入目标私聊或群聊后启动助理。私聊中可直接发送消息；群聊中请 @机器人发送消息。需要接收通知时，在目标会话发送 `/set-notify-home` 完成绑定。

2. 启动时设置 `ENABLE_ASSISTANT=true`（见第四步的启动命令）

> 容器中设置 `ENABLE_ASSISTANT=true` 后，会自动同时启动主程序和助理进程，无需额外操作。

## 启用 Bangumi 和 Mikan

### Bangumi 收藏同步

```toml
[bangumi]
access_token = "你的Bangumi Token"
```

> 也支持环境变量 `BANGUMI_TOKEN`。

### Mikan 账号集成

```toml
[mikan]
username = "你的Mikan用户名"
password = "你的Mikan密码"
```

## 常用运维命令

```bash
# 查看日志
docker logs -f openlist-ani

# 重启（修改配置后需要重启）
docker restart openlist-ani

# 停止
docker stop openlist-ani

# 更新到最新版
docker pull twosix26/openlist-ani:latest
docker stop openlist-ani && docker rm openlist-ani
# 然后重新运行 docker run 命令

# docker compose 更新
docker compose pull
docker compose up -d
```
