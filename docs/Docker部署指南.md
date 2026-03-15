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

# ---------- LLM（AI 重命名） ----------
[llm]
openai_api_key = ""
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
allowed_users = []

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

## 第三步：配置必填项

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

### 3. LLM API Key

```toml
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
bot_token = "你的Bot Token"
allowed_users = [123456789]
```

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
