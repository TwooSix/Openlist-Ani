# PIP 安装指南

通过 PIP 安装是最简单的方式，适合大多数用户。

## 环境要求

- **Python** ≥ 3.11
- **pip**（Python 包管理器）

确认 Python 版本：

```bash
python3 --version
```

## 第一步：安装 Openlist-Ani

```bash
pip install openlist-ani
```

安装完成后会自动注册以下命令：

| 命令 | 用途 |
|------|------|
| `openlist-ani` | 主程序（RSS 监控 + 自动下载 + 重命名） |
| `openlist-ani-assistant` | AI 智能助理（Telegram Bot 或本地 CLI） |
| `migrate-db` | 数据库迁移工具 |

## 第二步：创建配置文件

在你想要运行的目录下新建 `config.toml` 文件。以下是**完整配置模板**，请根据需要修改：

```toml
# ============================================================
# Openlist-Ani 完整配置文件
# ============================================================

# ---------- 后端 API ----------
[backend]
host = "127.0.0.1"   # 绑定地址，127.0.0.1 表示仅本机访问
port = 26666          # 监听端口

# ---------- RSS 订阅 ----------
[rss]
urls = [
    # 在此添加你的 RSS 链接，支持多个
    # "https://mikanani.me/RSS/MyBangumi?token=xxx"
]
interval_time = 300   # 抓取间隔，单位秒（默认 5 分钟）

# ---------- 下载优先级（可选） ----------
# 同一番剧同集存在多个资源时，按优先级自动过滤
# [rss.priority]
# field_order = ["fansub", "quality", "languages"]  # 比较顺序
# fansub = []                # 字幕组优先级（靠前优先）
# quality = ["2160p", "1080p", "720p", "480p"]  # 清晰度优先级
# languages = []             # 语言优先级，可选: "简", "繁", "日", "英"

# ---------- 代理（可选） ----------
[proxy]
http = ""     # HTTP 代理，如 "http://127.0.0.1:7890"
https = ""    # HTTPS 代理

# ---------- Openlist 网盘 ----------
[openlist]
url = "http://localhost:5244"          # Openlist 访问地址
token = ""                              # 令牌，见「设置 → 其他 → 令牌」
download_path = "/PikPak/Anime"         # 下载保存路径
offline_download_tool = "QBITTORRENT"   # 离线下载工具（大小写不敏感）
rename_format = "{anime_name} S{season:02d}E{episode:02d} {fansub} {quality} {languages}"

# ---------- LLM（AI 重命名） ----------
[llm]
openai_api_key = ""                       # API Key
openai_base_url = "https://api.deepseek.com/v1"   # API 地址（支持 OpenAI 兼容接口）
openai_model = "deepseek-chat"            # 模型名称

# TMDB（可选，用于获取番剧元数据提高重命名准确性）
tmdb_api_key = ""           # TMDB API Key，从 https://www.themoviedb.org/settings/api 获取
tmdb_language = "zh-CN"     # 元数据语言

# ---------- 通知（可选） ----------
[notification]
enabled = false        # 设为 true 启用通知
batch_interval = 300.0 # 批量通知间隔（秒），设为 0 立即发送

# Telegram 通知（取消注释以启用）
# [[notification.bots]]
# type = "telegram"
# enabled = true
# config = { bot_token = "你的Bot Token", user_id = "你的用户ID" }

# PushPlus 微信通知（取消注释以启用）
# [[notification.bots]]
# type = "pushplus"
# enabled = true
# config = { user_token = "你的PushPlus Token", channel = "wechat" }
# channel 可选: wechat（微信）, webhook, cp（企业微信）, mail（邮件）

# ---------- AI 智能助理（可选） ----------
[assistant]
enabled = false   # 设为 true 启用助理

[assistant.telegram]
bot_token = ""        # Telegram Bot Token，从 @BotFather 获取
allowed_users = []    # 允许的用户 ID 列表（留空则允许所有人，建议设置具体 ID）

# ---------- Bangumi（可选） ----------
[bangumi]
access_token = ""   # Bangumi API Token，也支持环境变量 BANGUMI_TOKEN

# ---------- Mikan（可选） ----------
[mikan]
username = ""   # Mikan 账号
password = ""   # Mikan 密码

# ---------- 日志 ----------
[log]
level = "INFO"           # 日志级别: DEBUG, INFO, WARNING, ERROR, CRITICAL
rotation = "00:00"       # 日志轮转时间
retention = "1 week"     # 旧日志保留时长
```

## 第三步：配置必填项

以下是**最小必填配置**，让程序能够跑起来：

### 1. RSS 订阅链接

从 [Mikan Project](https://mikanani.me) 获取你的 RSS 订阅链接，填入 `rss.urls`：

```toml
[rss]
urls = ["https://mikanani.me/RSS/MyBangumi?token=你的token"]
```

### 2. Openlist 配置

确保你的 Openlist 已部署并开启了离线下载功能：

```toml
[openlist]
url = "http://localhost:5244"          # Openlist 地址
token = "你的令牌"                      # 令牌
download_path = "/PikPak/Anime"         # 下载路径
offline_download_tool = "QBITTORRENT"   # 离线下载工具
```

> **令牌获取**：登录 Openlist 后台 → 设置 → 其他 → 令牌

### 3. LLM API Key

配置用于 AI 重命名的 LLM：

```toml
[llm]
openai_api_key = "sk-xxx"
openai_base_url = "https://api.deepseek.com/v1"
openai_model = "deepseek-chat"
```

> 支持所有 OpenAI 兼容 API

## 第四步：启动主程序

```bash
openlist-ani
```

程序启动后会：
1. 按照 `interval_time` 间隔定期抓取 RSS
2. 发现新资源后通过 Openlist 离线下载
3. 下载完成后通过 LLM 分析并重命名

## 第五步（可选）：启用通知

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

## 第六步（可选）：启用 AI 智能助理

AI 助理通过 Telegram Bot 提供自然语言交互，可以让 AI 帮你搜索并下载番剧。

1. 再创建一个 Telegram Bot（或复用通知 Bot）
2. 配置：

```toml
[assistant]
enabled = true

[assistant.telegram]
bot_token = "你的Bot Token"
allowed_users = [123456789]   # 你的 Telegram 用户 ID
```

3. 启动助理（需单独运行）：

```bash
openlist-ani-assistant
```

> 主程序和助理需要**同时运行**。可以使用 `tmux`、`screen` 或 systemd 来管理。

> 助理支持两种运行模式：
> - **Telegram 模式**（默认）：通过 Telegram Bot 交互
> - **CLI 模式**：本地终端交互界面，添加 `--cli` 参数启动
>
> ```bash
> openlist-ani-assistant --cli          # 本地 TUI 模式
> openlist-ani-assistant --cli --resume # 恢复上次会话
> ```

## 第七步（可选）：配置 Bangumi 和 Mikan，用于 Assistant 支持更多功能

### Bangumi 收藏同步

```toml
[bangumi]
access_token = "你的Bangumi Token"
```

> Token 获取方式：前往 [Bangumi 开发者页面](https://bgm.tv/dev/app) 创建应用并获取 Access Token。也可以通过环境变量 `BANGUMI_TOKEN` 设置。

### Mikan 账号集成

```toml
[mikan]
username = "你的Mikan用户名"
password = "你的Mikan密码"
```

## 后台运行

### 使用 systemd（推荐）

创建 `/etc/systemd/system/openlist-ani.service`：

```ini
[Unit]
Description=Openlist-Ani
After=network.target

[Service]
Type=simple
User=你的用户名
WorkingDirectory=/path/to/你的运行目录
ExecStart=/usr/local/bin/openlist-ani
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now openlist-ani
```

如需同时运行助理，创建类似的 service 文件将 `ExecStart` 改为 `openlist-ani-assistant`。

### 使用 tmux

```bash
tmux new-session -d -s oani 'openlist-ani'
tmux new-session -d -s oani-assistant 'openlist-ani-assistant'
```
