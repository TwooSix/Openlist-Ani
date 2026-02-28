---
name: sonarcloud-fetch
description: 从 SonarCloud 获取项目的所有未解决问题报告，或获取某个 Pull Request 的 Quality Gate 报告和新增问题。当用户需要查看、分析或修复 SonarCloud 上的代码质量问题时使用此技能。
argument-hint: '[organization] [project-key] [--pr <number>]'
---

# SonarCloud 问题拉取

从 SonarCloud（`https://sonarcloud.io`）获取项目的未解决问题。支持两种模式：

- **项目全量模式**（默认）：获取项目所有未解决问题
- **Pull Request 模式**：获取指定 PR 的 Quality Gate 状态和该 PR 引入的新问题

## 前置条件

### 加载环境变量

项目根目录下的 `.env` 文件通常被 `.gitignore` 忽略，**不可使用编辑器搜索或 readFile 工具读取**。必须通过 #tool:execute/runInTerminal 在终端中执行命令加载：

```bash
# 检查并加载 .env 文件（source 会将变量导出到当前 shell 会话）
if [ -f .env ]; then
    set -a && source .env && set +a
    echo "✅ 已加载 .env 环境变量"
else
    echo "⚠️ 未找到 .env 文件，需要手动提供凭据"
fi
```

### 验证连接信息

加载 `.env` 后，检查以下环境变量是否已设置：

```bash
# 仅验证变量是否存在，不要输出完整值
for var in SONAR_TOKEN SONAR_ORG PROJECT_KEY; do
    if [ -n "${!var}" ]; then
        echo "✅ $var 已设置"
    else
        echo "❌ $var 未设置"
    fi
done
```

如果缺少以下任一变量，需通过 #tool:vscode/askQuestions 向用户询问：

- **SONAR_ORG**：SonarCloud 组织标识（通常与 GitHub 用户名/组织名一致，可在 SonarCloud → My Account → Organizations 中查看）
- **PROJECT_KEY**：SonarCloud 项目标识（在项目主页 → Information 中查看，格式通常为 `orgKey_repoName`）
- **SONAR_TOKEN**：在 SonarCloud → My Account → Security → Generate Tokens 中生成

将缺少的连接信息手动设置为环境变量：

```bash
export SONAR_TOKEN="<token>"
export SONAR_ORG="<organization>"
export PROJECT_KEY="<project-key>"
```

> **安全**：绝不在聊天输出中明文展示 API Token。

## 模式 A：项目全量问题拉取

### A1. 分页拉取所有未解决问题

使用 [SonarCloud Web API](https://sonarcloud.io/web_api/api/issues/search) 获取问题列表：

```bash
curl -s -u "$SONAR_TOKEN:" \
  "https://sonarcloud.io/api/issues/search?organization=$SONAR_ORG&componentKeys=$PROJECT_KEY&resolved=false&ps=500&p=1"
```

如果响应中的 `total` 大于 `ps * p`（当前页 500 条），递增 `p` 参数继续拉取，直到所有问题覆盖完毕。

### A2. 解析问题数据

从 API 响应的 `issues` 数组中提取每个 issue 的关键字段：

| 字段 | 说明 |
|------|------|
| `key` | 问题唯一标识 |
| `rule` | 触发的规则 ID（如 `python:S1481`） |
| `severity` | 严重级别：BLOCKER / CRITICAL / MAJOR / MINOR / INFO |
| `message` | 问题描述 |
| `component` | 文件路径（格式 `projectKey:path/to/file.py`，需去掉 `projectKey:` 前缀） |
| `line` | 问题所在行号 |
| `type` | 问题类型：BUG / VULNERABILITY / CODE_SMELL |
| `effort` | 预估修复工作量 |

---

## 模式 B：Pull Request 问题拉取

当用户提供了 PR 编号时使用此模式。需要额外获取：

- **Pull Request 编号**：GitHub PR 的编号（如 `42`）

### B1. 获取 PR 的 Quality Gate 状态

```bash
curl -s -u "$SONAR_TOKEN:" \
  "https://sonarcloud.io/api/qualitygates/project_status?projectKey=$PROJECT_KEY&pullRequest=$PR_NUMBER"
```

解析响应中的 `projectStatus` 对象：

| 字段 | 说明 |
|------|------|
| `status` | Quality Gate 状态：`OK`（通过）/ `ERROR`（未通过） |
| `conditions` | 各指标的条件判定数组 |
| `conditions[].metricKey` | 指标名（如 `new_reliability_rating`、`new_security_rating`、`new_coverage`、`new_duplicated_lines_density`） |
| `conditions[].status` | 该指标状态：`OK` / `ERROR` |
| `conditions[].actualValue` | 实际值 |
| `conditions[].errorThreshold` | 阈值 |

向用户展示 Quality Gate 结果：

```
Quality Gate: ❌ 未通过 (ERROR)

| 指标 | 状态 | 实际值 | 阈值 |
|------|------|--------|------|
| 新代码可靠性 | ❌ ERROR | 3 | 1 |
| 新代码安全性 | ✅ OK | 1 | 1 |
| 新代码覆盖率 | ✅ OK | 82% | 80% |
```

### B2. 拉取 PR 引入的新问题

```bash
curl -s -u "$SONAR_TOKEN:" \
  "https://sonarcloud.io/api/issues/search?organization=$SONAR_ORG&componentKeys=$PROJECT_KEY&pullRequest=$PR_NUMBER&resolved=false&ps=500&p=1"
```

> **关键**：`pullRequest` 参数会将结果限定为该 PR 分支上的问题，而非主分支的存量问题。

同样需要分页处理。解析字段与模式 A 相同。

### B3. 获取 PR 的度量数据（可选）

如果需要查看覆盖率、重复率等度量信息：

```bash
curl -s -u "$SONAR_TOKEN:" \
  "https://sonarcloud.io/api/measures/component?component=$PROJECT_KEY&pullRequest=$PR_NUMBER&metricKeys=new_coverage,new_duplicated_lines_density,new_bugs,new_vulnerabilities,new_code_smells"
```

---

## 通用步骤（两种模式共用）

### 排序与分组

按 severity 排序（BLOCKER > CRITICAL > MAJOR > MINOR > INFO），同一级别内按文件路径分组。

### 展示汇总报告

以表格形式向用户展示：

```
| # | 严重级别 | 类型 | 文件 | 行号 | 规则 | 描述 |
|---|----------|------|------|------|------|------|
| 1 | CRITICAL | BUG  | src/openlist_ani/worker.py | 42 | python:S1481 | ... |
```

同时展示统计摘要：按严重级别/按类型/按文件的数量统计。

PR 模式下额外展示 Quality Gate 状态和各指标详情。

### 查询规则详情（按需）

如果对某条规则不熟悉，可查询规则说明：

```bash
curl -s -u "$SONAR_TOKEN:" \
  "https://sonarcloud.io/api/rules/show?key=python:S1481"
```

关注响应中的 `htmlDesc` 字段获取修复建议。
