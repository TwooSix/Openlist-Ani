---
name: 'oAni Code Style Guide'
description: 'oAni 项目代码风格指南，适用于所有 Python 文件'
applyTo: '**/*.py'
---

# oAni Code Style Guide — 编码规范

所有 Python 代码必须遵循 [Google Python Style Guide](https://google.github.io/styleguide/pyguide.html)。

## 命名规范

- 模块名：`lower_with_under`
- 类名：`CapWords`（PascalCase）
- 函数 / 方法 / 变量：`lower_with_under`（snake_case）
- 常量：`CAPS_WITH_UNDER`（全大写 + 下划线），内部常量加 `_` 前缀
- 枚举成员：`CAPS_WITH_UNDER`（与常量相同）
- 受保护成员：`_leading_underscore`
- 私有成员：`__double_leading_underscore`（仅在必要时使用）

### 函数 / 方法命名细则

- **使用动词开头**：函数名应以动词或动词短语开头，清晰表达其行为。
  - 好：`fetch_episodes()`、`parse_title()`、`send_notification()`
  - 差：`episodes()`、`title_parse()`、`notification()`
- **使用描述性名称**：名称应准确反映函数的职责，避免过于笼统或含糊的命名。
  - 好：`download_torrent_file()`、`resolve_anime_metadata()`
  - 差：`do_stuff()`、`process()`、`handle()`
- **返回 `bool` 的函数**：使用 **谓语 + 正面形容词/状态** 的模式命名，始终从正面语义描述，不要使用否定或负面形容词。
  - 好：`is_valid()`、`is_available()`、`has_permission()`、`can_retry()`、`should_notify()`
  - 差：`is_invalid()`、`is_not_ready()`、`is_missing()`、`is_broken()`


## Docstring（Google 风格）

```python
def fetch_issues(project_key: str, *, severity: str | None = None) -> list[dict]:
    """Fetch all issues for the given project from SonarCloud.

    Args:
        project_key: SonarCloud project identifier.
        severity: Optional severity filter, e.g. "CRITICAL", "MAJOR".

    Returns:
        A list of dicts containing issue details.

    Raises:
        ConnectionError: When unable to connect to SonarCloud server.
    """
```

## Import 顺序

1. 标准库
2. 第三方库
3. 本地 / 项目内模块（使用相对导入 `.`）

每组之间空一行，组内按字母排序。

## 类型注解

- 全面使用类型注解（函数参数、返回值、关键变量）
- 优先使用 Python 3.11+ 内置泛型语法：`list[str]`、`dict[str, Any]`、`str | None`
- 避免从 `typing` 导入 `List`、`Dict`、`Optional` 等已被内置替代的类型

## 其他要点

- 行宽上限 88 字符（black 默认）
- 优先使用 `async/await` 而非同步阻塞调用
- 异常处理要具体，禁止裸 `except:`
- 使用 `loguru` 的 `logger` 进行日志记录，不要使用 `print`
- 本项目要求 Python ≥3.11，可使用 `match/case`、`tomllib`、`ExceptionGroup` 等新特性
