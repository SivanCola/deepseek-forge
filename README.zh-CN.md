<div align="center">

# deepseek-forge

**让 Codex 把 DeepSeek 当作安全的 patch 生成器使用。**

Codex 负责计划和验证。DeepSeek 返回 unified diff。最终控制权仍在本地。

<a href="./README.md">English</a> · <strong>简体中文</strong>

![plugin](https://img.shields.io/badge/codex-plugin-24292f)
![version](https://img.shields.io/badge/version-v0.1.0-blue)
![python](https://img.shields.io/badge/python-%3E%3D3.11-3776ab)
![tests](https://img.shields.io/badge/tests-391%20passing-2ea44f)
![license](https://img.shields.io/badge/license-MIT-6f42c1)

</div>

## 快速开始

1. 安装本地插件：

```bash
git clone git@github.com:SivanCola/deepseek-forge.git
cd deepseek-forge
codex plugin marketplace add .
codex plugin add deepseek-forge@deepseek-forge
```

如果你使用 Codex App 的插件管理界面，请导入 `plugins/deepseek-forge/`。

2. 设置 DeepSeek API Key：

写入到 `~/.zshrc`：

```bash
echo 'export DEEPSEEK_API_KEY="your-deepseek-api-key"' >> ~/.zshrc
echo 'export DEEPSEEK_MODEL="deepseek-v4-pro"' >> ~/.zshrc
echo 'export DEEPSEEK_REASONING_EFFORT="max"' >> ~/.zshrc
source ~/.zshrc
```

或者写入 `~/.profile`：

```bash
echo 'export DEEPSEEK_API_KEY="your-deepseek-api-key"' >> ~/.profile
echo 'export DEEPSEEK_MODEL="deepseek-v4-pro"' >> ~/.profile
echo 'export DEEPSEEK_REASONING_EFFORT="max"' >> ~/.profile
source ~/.profile
```

3. 在目标仓库里打开 Codex，然后输入：

```text
Use the deepseek-forge skill to implement:
<描述功能或 bug 修复需求>
```

Codex 会收集上下文、让 DeepSeek 生成 patch、校验并审查 patch、应用改动、运行检查；如果检查失败，会请求 DeepSeek 生成修复 patch。

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
| `DEEPSEEK_FORGE_HOME` | 否 | 已安装插件内的 `skills/deepseek-forge` | deepseek-forge 的 skill 根目录，也就是包含 `SKILL.md` 和 `references/prompt_templates.md` 的目录。 |
| `DEEPSEEK_FORGE_ARTIFACT_DIR` | 否 | `/tmp/deepseek-forge-{pid}/` | 运行时产物目录，包括 patch、日志和上下文文件。设置为 `.deepseek-forge` 可把产物保留在目标仓库中。 |

启用 1M 上下文时，默认收集上限为 200 个文件、500,000 字节。关闭后默认上限为 80 个文件、120,000 字节。

## 执行流程

```text
1. Codex 生成计划。
2. Codex 对任务分类：patch、patch review 或 PR branch topology。
3. Codex 收集仓库上下文：按模式收集完整源码或轻量元数据。
4. patch 任务中，DeepSeek 返回 unified diff；topology 任务中，本地脚本生成 dry-run 分支拆分计划。
5. Codex 校验并审查输出。
6. Codex 安全地应用结果：patch 或分支命令。
7. Codex 运行检查。
8. 如果检查失败，Codex 把脱敏日志发给 DeepSeek 生成修复 patch。
```

DeepSeek 不执行命令、不编辑文件、不应用 patch、不提交代码。它只返回文本 diff。

## Patch Review

使用 `deepseek.review_patch` MCP 工具，或通过 `patch_review_task` 分类，让 DeepSeek 审查已有 patch。该流程和标准 patch 生成流程一致，但会使用 `review_patch` prompt 模板。DeepSeek 会返回结构化 review，包括正确性问题、风格建议和安全标记。

## PR 分支拓扑模式

当多个 PR 共享同一个 head SHA，例如堆叠分支或串联分支需要拆成独立 review 时，deepseek-forge 可以检测拓扑并生成安全的分支拆分计划。

### 适用场景

- 多个 PR 指向同一个 commit SHA 和同一个 base ref
- 需要把一个累计分支拆成多个可独立 review 的 PR 分支
- 需要通过 branch surgery 隔离每个 PR 的文件改动

### 工作流

1. **任务分类。** `task_classifier.py` 检测 `pr_branch_topology_task` 并路由到 branch surgery 模式。
2. **轻量上下文。** `collect_context.py --mode pr-branch-topology` 只收集 Git/PR 元数据，不包含源码，从而保持 payload 较小。
3. **拆分计划生成。** `branch_surgery.py` 分析共享 head、计算每个 PR 的 commit 范围和文件列表，并生成安全 push 命令。
4. **人工审查。** 生成的计划仅为 dry-run。执行前需要人工审查每条拆分命令。
5. **推送后验证。** checklist 会逐个 PR 确认 commits、files、head SHA 和 base ref。

### 安全保证

- **只生成 dry-run。** `branch_surgery.py` 不会自动执行 Git 变更。不会 commit，也不会 push。
- **Force-with-lease。** 每条 push 命令都使用 `--force-with-lease=<remote-ref>:<expected-sha>`，确保远端 ref 和预期一致后才允许覆盖。
- **Manual-review fallback。** 如果多个 PR 同时共享 base 和 head，commit 隔离不可能自动完成；cherry-pick 和 push 命令会被注释，直到人工确认拆分。
- **Shell-safe 命令渲染。** 生成 shell 命令前会校验 PR refs、commit SHAs、PR numbers 和 remotes；分支名使用保守 ref 白名单。
- **Fork-aware push 和 rollback。** fork PR 会使用解析出的 fork remote 或 SSH URL 生成 fetch、push 和 rollback 指令。
- **Human-in-the-loop。** 所有命令都需要审查后手动执行。

### 手动用法

```bash
# 检测共享 head 并生成拆分计划（通过 gh CLI 自动获取 PR）
python3 ${DEEPSEEK_FORGE_HOME}/scripts/branch_surgery.py \
  --output .deepseek-forge/branch_surgery.md

# 也可以预先收集 PR 数据并显式传入
python3 ${DEEPSEEK_FORGE_HOME}/scripts/collect_context.py \
  --mode pr-branch-topology \
  --task task.md \
  --output .deepseek-forge/repo_context.md

python3 ${DEEPSEEK_FORGE_HOME}/scripts/branch_surgery.py \
  --output .deepseek-forge/branch_surgery.md \
  --pr-list "$(gh pr list --json number,title,headRefName,baseRefName,headRefOid,baseRefOid,state,url,headRepository,headRepositoryOwner --state open --limit 50)"
```

## 生成文件

运行时文件写入 `DEEPSEEK_FORGE_ARTIFACT_DIR`，默认 `/tmp/deepseek-forge-{pid}/`。设置 `DEEPSEEK_FORGE_ARTIFACT_DIR=.deepseek-forge` 可将产物保留在目标仓库中，也可以设置成任意自定义路径。

如果使用 `.deepseek-forge/` 作为产物目录，建议把它加入 `.git/info/exclude`，避免进入版本控制：

```bash
echo '.deepseek-forge/' >> .git/info/exclude
```

| 文件 | 用途 |
|---|---|
| `plan.md` | Codex 生成的实现计划。 |
| `repo_context.md` | 发送给 DeepSeek 的上下文。 |
| `patch.diff` | 主 patch。 |
| `fix.patch.diff` | 检查失败后生成的修复 patch。 |
| `check.log` | 测试、lint、typecheck 输出。 |

## 可选：手动调试

大多数情况下应让 Codex 自动运行 Skill。插件开发或排障时，可以手动运行同样的步骤。

当 deepseek-forge 作为插件安装时，使用 ``${DEEPSEEK_FORGE_HOME}`` 定位脚本：

```bash
mkdir -p .deepseek-forge

python3 ${DEEPSEEK_FORGE_HOME}/scripts/collect_context.py \
  --task task.md \
  --output .deepseek-forge/repo_context.md

python3 ${DEEPSEEK_FORGE_HOME}/scripts/deepseek_worker.py \
  --model deepseek-v4-pro \
  --task task.md \
  --context .deepseek-forge/repo_context.md \
  --output .deepseek-forge/patch.diff

python3 ${DEEPSEEK_FORGE_HOME}/scripts/apply_patch_safe.py --patch .deepseek-forge/patch.diff --check
python3 ${DEEPSEEK_FORGE_HOME}/scripts/apply_patch_safe.py --patch .deepseek-forge/patch.diff --apply
bash ${DEEPSEEK_FORGE_HOME}/scripts/run_checks.sh
```

如果是在 deepseek-forge 仓库内部开发或调试，请使用 marketplace plugin 路径：

```bash
mkdir -p .deepseek-forge

python3 plugins/deepseek-forge/skills/deepseek-forge/scripts/collect_context.py \
  --task task.md \
  --output .deepseek-forge/repo_context.md

python3 plugins/deepseek-forge/skills/deepseek-forge/scripts/deepseek_worker.py \
  --model deepseek-v4-pro \
  --task task.md \
  --context .deepseek-forge/repo_context.md \
  --output .deepseek-forge/patch.diff

python3 plugins/deepseek-forge/skills/deepseek-forge/scripts/apply_patch_safe.py --patch .deepseek-forge/patch.diff --check
python3 plugins/deepseek-forge/skills/deepseek-forge/scripts/apply_patch_safe.py --patch .deepseek-forge/patch.diff --apply
bash plugins/deepseek-forge/skills/deepseek-forge/scripts/run_checks.sh
```

本地插件安装会被复制到 Codex 的 plugin cache。修改本仓库后，需要刷新已安装插件：

```bash
codex plugin remove deepseek-forge@deepseek-forge
codex plugin add deepseek-forge@deepseek-forge
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

当前本地结果：`391 tests, 0 failures`。

## License

本项目基于 MIT License 发布。完整协议文本见 [LICENSE](./LICENSE)。
