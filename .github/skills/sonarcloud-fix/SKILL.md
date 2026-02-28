---
name: sonarcloud-fix
description: "修复 SonarCloud 报告的代码质量问题（支持项目全量问题和 PR 新增问题）。根据问题类型（BUG / CODE_SMELL / VULNERABILITY）定位代码并应用合适的修复模式。当用户需要修复 SonarCloud 问题时使用此技能。"
argument-hint: '[issue-key or file path] [--pr <number>]'
---

# SonarCloud 问题修复

根据 SonarCloud 报告的问题，定位代码并应用修复。遵循最小变更原则。

支持两种场景：
- **项目全量修复**：修复项目积累的所有未解决问题
- **PR 修复**：仅修复某个 Pull Request 引入的新问题，使 Quality Gate 通过

## 操作步骤

### 0. 确保环境变量可用

此技能依赖 `/sonarcloud-fetch` 已加载的环境变量。如果在新终端会话中执行，需先通过 #tool:execute/runInTerminal 重新加载（`.env` 文件被 `.gitignore` 忽略，不可用 readFile 读取）：

```bash
if [ -z "$SONAR_TOKEN" ] && [ -f .env ]; then
    set -a && source .env && set +a
    echo "✅ 已重新加载 .env 环境变量"
fi
```

### 1. 定位问题代码

从 SonarCloud issue 的 `component` 字段提取文件路径（去掉 `projectKey:` 前缀），然后读取问题行号前后各 15 行代码，充分理解上下文。

### 2. 分析根因

结合 SonarCloud 规则说明和代码上下文，判断：

- 问题的具体原因是什么
- 最佳修复方案是什么
- 修复是否会影响其他功能
- 是否需要同步修改测试用例

### 3. 制定修复计划

使用 todo list 列出所有待修复问题及计划，将同一文件中的多个问题合并处理。

### 4. 执行修复

保持 **最小变更** 原则——只修改与问题直接相关的代码，避免不必要的重构。

对不确定是否安全的修复（如涉及业务逻辑变更），先向用户说明方案并获得确认。

### 5. 验证修复

- 检查文件语法错误和类型错误
- 运行 lint 检查：`uv run ruff check <modified_file>`
- 如有对应测试，运行测试：`uv run pytest <test_file> -v`
- 全部修复后运行全量验证：`uv run ruff check src/` 和 `uv run pytest tests/ -v`

### 6. 汇总报告

为每个已修复问题生成简要说明：

| Issue Key | 规则 | 文件:行号 | 修复方式 |
|-----------|------|-----------|----------|
| AX... | python:S1481 | src/worker.py:42 | 删除未使用变量 |

## 常见修复模式

参考下方的修复模式文件选择合适的修复策略。

### BUG

- 空指针 / `None` 引用 → 添加 `None` 检查或使用类型守卫
- 资源未关闭 → 使用 `async with` 或 `contextlib.asynccontextmanager`
- 异常被吞没 → 确保 `except` 块中有 `logger.exception()` 或 `raise`

### CODE_SMELL

- 未使用的变量 → 删除或替换为 `_`
- 未使用的 import → 删除
- 过长函数 → 提取子函数
- 重复代码 → 提取公共函数
- 认知复杂度过高 → 简化条件分支、使用 early return、提取辅助函数
- 硬编码字符串 / 魔法数字 → 提取为常量

### VULNERABILITY

- SQL 注入 → 使用参数化查询
- 硬编码密钥 → 从配置或环境变量读取
- 不安全的反序列化 → 使用安全的解析方式

## PR 模式额外说明

在 PR 模式下：

- **只修复该 PR 引入的新问题**，不要修改 PR 变更范围之外的代码
- 修复后可重新检查 Quality Gate 状态确认是否通过：
  ```bash
  curl -s -u "$SONAR_TOKEN:" \
    "https://sonarcloud.io/api/qualitygates/project_status?projectKey=$PROJECT_KEY&pullRequest=$PR_NUMBER"
  ```
- 如果 Quality Gate 失败是因为覆盖率不足（`new_coverage`），需要为新代码补充测试用例
- 如果是因为重复率过高（`new_duplicated_lines_density`），需要提取公共函数消除重复

## 注意事项

- 同一问题不重复修复，修复前先确认问题代码仍然存在
- 充分理解文件的完整上下文和项目的架构模式（异步架构、回调模式、队列模式、单例模式等）
- 如果修复改变了函数签名或行为，同步更新对应的测试文件
