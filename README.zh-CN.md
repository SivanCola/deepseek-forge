<div align="center">

# deepseek-forge

**Codex 负责规划 · DeepSeek 负责生成 patch · Codex 负责验证**

面向 Codex 的 DeepSeek 开发调度插件，打包形式为 Skill + MCP + Plugin。

<a href="./README.md">English</a> · <strong>简体中文</strong>

![plugin](https://img.shields.io/badge/codex-plugin-24292f)
![version](https://img.shields.io/badge/version-v0.1.0-blue)
![python](https://img.shields.io/badge/python-%3E%3D3.11-3776ab)
![tests](https://img.shields.io/badge/tests-213%20passing-2ea44f)
![license](https://img.shields.io/badge/license-MIT-6f42c1)

</div>

## 项目概览

`deepseek-forge` 让 Codex 把“生成代码 patch”的部分委托给 DeepSeek，同时保留 Codex 对本地仓库的执行权和审查权。

目标流程：

```text
Codex 生成计划
DeepSeek 生成 unified diff
Codex 校验并审查 diff
Codex 应用 patch
Codex 运行测试、lint、typecheck
如果检查失败，Codex 把脱敏后的日志回传给 DeepSeek 生成修复 patch
```

DeepSeek 不执行 shell、不访问文件系统、不提交 git。DeepSeek 只返回文本 patch。所有本地动作都由 Codex 执行。

## 插件包含什么

| 组件 | 用途 |
|---|---|
| `deepseek-forge` Skill | 指导 Codex 完成计划、上下文收集、patch 生成、校验、审查、应用、检查和失败修复循环。 |
| `deepseek-mcp` MCP server | 提供结构化工具，用于实现、修测试、审查 patch、解释 patch 和生成计划。 |
| 安全脚本 | 收集上下文、调用 DeepSeek、校验 patch、应用 patch、运行项目检查。 |
| Codex 插件包 | 可安装插件位于 `deepseek-forge/`。 |

## 环境要求

- Python 3.11+
- Git
- Bash
- DeepSeek API Key
- 支持本地插件的 Codex

核心脚本只使用 Python 标准库，不需要安装额外 Python 包。

## 安装插件

1. 克隆或打开本仓库：

```bash
git clone <repo-url>
cd deepseek-forge
```

2. 配置 DeepSeek API Key：

```bash
export DEEPSEEK_API_KEY="your-deepseek-api-key"
```

3. 在 Codex 中安装或导入本地插件目录：

```text
deepseek-forge/
```

插件包结构：

```text
deepseek-forge/
├── .codex-plugin/plugin.json
├── .mcp.json
├── skills/deepseek-forge/
└── mcp/deepseek-mcp/
```

如果你的 Codex CLI 支持本地插件安装，可以在仓库根目录执行：

```bash
codex plugin install ./deepseek-forge
```

如果你的 Codex 使用插件管理界面，请把 `deepseek-forge/` 作为本地插件导入。

## 在 Codex 中使用

在目标代码仓库里打开 Codex 会话，然后要求 Codex 使用该 Skill：

```text
Use the deepseek-forge skill to implement this task with DeepSeek:
<描述功能或 bug 修复需求>
```

示例：

```text
Use the deepseek-forge skill to add input validation to the user signup endpoint.
DeepSeek should generate the patch, and Codex should apply it only after review.
```

Codex 应执行以下流程：

1. 创建 `.deepseek-forge/plan.md`。
2. 收集仓库上下文到 `.deepseek-forge/repo_context.md`。
3. 请求 DeepSeek 生成 unified diff patch。
4. 使用 `apply_patch_safe.py --check` 校验 patch。
5. 应用前由 Codex 审查 patch。
6. 使用 `apply_patch_safe.py --apply` 应用 patch。
7. 运行 `run_checks.sh`。
8. 如果检查失败，把脱敏后的失败日志发给 DeepSeek 并请求修复 patch。

## 可用 MCP 工具

| 工具 | 用途 |
|---|---|
| `deepseek.plan` | 生成结构化实现计划。 |
| `deepseek.implement` | 根据任务、计划和上下文生成 unified diff patch。 |
| `deepseek.fix_tests` | 根据脱敏后的失败日志生成修复 patch。 |
| `deepseek.review_patch` | 审查 patch 的正确性、安全性和完整性。 |
| `deepseek.explain_patch` | 用自然语言解释 patch 的改动。 |

## 手动 CLI 流程

通常应让 Codex 通过 Skill 自动执行这些步骤。开发或排障时，也可以在本仓库根目录手动运行脚本。

1. 创建任务文件：

```bash
cat > task.md <<'EOF'
Add a hello_world() function to src/main.py that returns "Hello, World!".
EOF
```

2. 收集仓库上下文：

```bash
mkdir -p .deepseek-forge

python3 scripts/collect_context.py \
  --task task.md \
  --output .deepseek-forge/repo_context.md
```

3. 生成 patch：

```bash
python3 scripts/deepseek_worker.py \
  --model deepseek-v4-pro \
  --task task.md \
  --context .deepseek-forge/repo_context.md \
  --output .deepseek-forge/patch.diff
```

4. 校验并应用：

```bash
python3 scripts/apply_patch_safe.py \
  --patch .deepseek-forge/patch.diff \
  --check

python3 scripts/apply_patch_safe.py \
  --patch .deepseek-forge/patch.diff \
  --apply
```

5. 运行检查：

```bash
scripts/run_checks.sh
```

如果检查失败，生成修复 patch：

```bash
python3 scripts/deepseek_worker.py \
  --model deepseek-v4-pro \
  --task task.md \
  --context .deepseek-forge/repo_context.md \
  --output .deepseek-forge/fix.patch.diff \
  --template fix_tests \
  --failure-log .deepseek-forge/check.log
```

## 配置项

| 变量 | 必填 | 默认值 | 用途 |
|---|---:|---|---|
| `DEEPSEEK_API_KEY` | 是 | 无 | DeepSeek API Key。 |
| `DEEPSEEK_ENDPOINT` | 否 | `https://api.deepseek.com/chat/completions` | 覆盖 MCP 工具使用的 API endpoint。 |
| `DEEPSEEK_MODEL` | 否 | `deepseek-v4-pro` | MCP 工具默认模型。 |
| `DEEPSEEK_TEMPERATURE` | 否 | `0.2` | 采样温度。 |
| `DEEPSEEK_TIMEOUT` | 否 | `120` | API 超时时间，单位秒。 |
| `DEEPSEEK_TEMPLATE_PATH` | 否 | 自动检测 | 覆盖 MCP 工具的 prompt 模板路径。 |
| `CHECK_COMMANDS` | 否 | 自动检测 | 指定 `run_checks.sh` 要执行的检查命令。 |

运行时文件都写入 `.deepseek-forge/`：

| 文件 | 用途 |
|---|---|
| `plan.md` | Codex 生成的实现计划。 |
| `repo_context.md` | 发送给 DeepSeek 的仓库上下文。 |
| `patch.diff` | DeepSeek 生成的主 patch。 |
| `fix.patch.diff` | 检查失败后生成的修复 patch。 |
| `check.log` | 测试、lint、typecheck 输出。 |

## 安全规则

- DeepSeek 只能输出 unified diff。
- DeepSeek 不能执行 shell 命令。
- DeepSeek 不能提交 git。
- patch 默认会拒绝绝对路径、路径穿越、`.git/` 修改、空目标和文件删除。
- 失败日志回传给 DeepSeek 前会脱敏。
- Codex 负责最终审查，并决定是否提交。

## 验证仓库

```bash
python3 -m py_compile scripts/*.py
python3 -m py_compile deepseek-mcp/server.py deepseek-mcp/tools/*.py
bash -n scripts/run_checks.sh
python3 -m unittest discover
python3 <path-to-plugin-creator>/scripts/validate_plugin.py ./deepseek-forge
```

当前本地验证结果：

```text
213 tests, 0 failures, 0 errors
Plugin validation passed
```

## License

MIT
