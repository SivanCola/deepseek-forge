<div align="center">

# deepseek-forge

**让 Codex 把 DeepSeek 当作安全的 patch 生成器使用。**

Codex 负责计划和验证。DeepSeek 只返回 unified diff。最终控制权仍在本地。

<a href="./README.md">English</a> · <strong>简体中文</strong>

![plugin](https://img.shields.io/badge/codex-plugin-24292f)
![version](https://img.shields.io/badge/version-v0.1.0-blue)
![python](https://img.shields.io/badge/python-%3E%3D3.11-3776ab)
![tests](https://img.shields.io/badge/tests-240%20passing-2ea44f)
![license](https://img.shields.io/badge/license-MIT-6f42c1)

</div>

## 快速开始

1. 安装本地插件：

```bash
git clone <repo-url>
cd deepseek-forge
codex plugin install ./deepseek-forge
```

如果你使用 Codex App 的插件管理界面，请导入本地 `deepseek-forge/` 目录。

2. 设置 DeepSeek API Key：

```bash
export DEEPSEEK_API_KEY="your-deepseek-api-key"
```

3. 在目标代码仓库里打开 Codex，然后输入：

```text
Use the deepseek-forge skill to implement:
<描述功能或 bug 修复需求>
```

Codex 会自动收集上下文、让 DeepSeek 生成 patch、校验并审查 patch、应用改动、运行检查；如果检查失败，会把脱敏后的日志发给 DeepSeek 生成修复 patch。

## 触发用法

为了稳定触发，请明确提到 `deepseek-forge` 或 `DeepSeek`：

```text
用 deepseek-forge 处理这个任务：
<任务描述>
```

也可以使用这些说法：

- `use deepseek`
- `delegate this to DeepSeek`
- `让 DeepSeek 生成 patch`
- `用 DeepSeek 修复这些测试失败`
- `让 DeepSeek review 这个 patch`
- `DeepSeek 只生成 patch，Codex 负责审查、应用和测试`

## 配置

只有 `DEEPSEEK_API_KEY` 必填。

| 变量 | 必填 | 默认值 | 用途 |
|---|---:|---|---|
| `DEEPSEEK_API_KEY` | 是 | 无 | DeepSeek API Key。 |
| `DEEPSEEK_MODEL` | 否 | `deepseek-v4-pro` | 用于生成 patch 的模型。 |
| `DEEPSEEK_REASONING_EFFORT` | 否 | `max` | `high` 或 `max`。兼容值：`low` / `medium` -> `high`，`xhigh` -> `max`。 |
| `DEEPSEEK_ENABLE_1M_CONTEXT` | 否 | `true` | 启用更大的上下文收集。设为 `false` 可降低成本和延迟。 |

启用 1M 上下文时，默认收集上限为 200 个文件、500,000 字节。关闭后默认上限为 80 个文件、120,000 字节。

## 执行流程

```text
1. Codex 生成计划。
2. Codex 收集仓库上下文。
3. DeepSeek 返回 unified diff。
4. Codex 校验并审查 patch。
5. Codex 应用 patch。
6. Codex 运行检查。
7. 如果检查失败，Codex 把脱敏日志发给 DeepSeek 生成修复 patch。
```

DeepSeek 不执行命令、不编辑文件、不应用 patch、不提交代码。它只返回文本 diff。

## 生成文件

运行时文件会写到目标仓库的 `.deepseek-forge/`：

| 文件 | 用途 |
|---|---|
| `plan.md` | Codex 生成的实现计划。 |
| `repo_context.md` | 发送给 DeepSeek 的上下文。 |
| `patch.diff` | 主 patch。 |
| `fix.patch.diff` | 检查失败后生成的修复 patch。 |
| `check.log` | 测试、lint、typecheck 输出。 |

## 可选：手动调试

大多数情况下应让 Codex 自动运行 Skill。插件开发或排障时，可以手动运行同样的步骤：

```bash
mkdir -p .deepseek-forge

python3 scripts/collect_context.py \
  --task task.md \
  --output .deepseek-forge/repo_context.md

python3 scripts/deepseek_worker.py \
  --model deepseek-v4-pro \
  --task task.md \
  --context .deepseek-forge/repo_context.md \
  --output .deepseek-forge/patch.diff

python3 scripts/apply_patch_safe.py --patch .deepseek-forge/patch.diff --check
python3 scripts/apply_patch_safe.py --patch .deepseek-forge/patch.diff --apply
bash scripts/run_checks.sh
```

高级调试环境变量：

| 变量 | 用途 |
|---|---|
| `DEEPSEEK_TEMPLATE_PATH` | 覆盖 prompt 模板自动检测路径。 |
| `CHECK_COMMANDS` | 覆盖 `run_checks.sh` 的执行命令。 |

## MCP 工具

插件包含 `deepseek-forge-mcp` server，提供以下工具：

| 工具 | 用途 |
|---|---|
| `deepseek.plan` | 生成实现计划。 |
| `deepseek.implement` | 生成 patch。 |
| `deepseek.fix_tests` | 根据失败日志生成修复 patch。 |
| `deepseek.review_patch` | 审查 patch。 |
| `deepseek.explain_patch` | 解释 patch。 |

## 验证本仓库

```bash
python3 -m unittest discover -s tests -v
```

当前本地结果：`240 tests, 0 failures`。

## License

MIT
