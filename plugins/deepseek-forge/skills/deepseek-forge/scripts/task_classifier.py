#!/usr/bin/env python3
"""Task classification layer for deepseek-forge.

Classifies task descriptions into one of five categories using keyword-based
heuristic matching.  Supports bilingual (English / Chinese) task descriptions.

Categories
----------
- ``forward_development_task`` — Task requires a full forward development loop
                                (acceptance criteria, plan, todos, implement,
                                review, fix, verify).
- ``patch_task``            — Task requires generating/implementing code changes.
- ``patch_review_task``     — Task requires reviewing an existing patch.
- ``pr_branch_topology_task`` — Task involves PR branch governance (force push,
                                branch splitting, commit graph analysis, etc.).
- ``unsupported_task``      — Task does not fit any recognised category.

Uses only stdlib — no external dependencies.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Keyword definitions — (keyword, weight)
# ---------------------------------------------------------------------------
# Higher weight = stronger signal for that category.
# Multi-word keywords are matched as substrings of the lowercased task text.

_CATEGORY_KEYWORDS: dict[str, list[tuple[str, int]]] = {
    "forward_development_task": [
        # English — strong signals for the full forward-dev loop
        ("forward development", 10),
        ("development loop", 10),
        ("acceptance criteria", 10),
        ("build from scratch", 9),
        ("implement from scratch", 9),
        ("full development cycle", 9),
        ("expand plan", 8),
        ("dev loop", 8),
        ("forward dev", 8),
        ("implement todo", 7),
        ("implement_todo", 7),
        ("fix open bugs", 7),
        ("fix_open_bugs", 7),
        ("final acceptance review", 7),
        ("write tests for todo", 7),
        ("acceptance.md", 7),
        ("todo.md", 6),
        ("bugs.md", 6),
        ("codex-regulated", 7),
        ("codex regulated", 7),
        # Chinese
        ("正向开发", 10),
        ("开发循环", 10),
        ("验收标准", 10),
        ("完整开发", 9),
        ("从头构建", 9),
        ("验收条件", 8),
        ("开发回路", 8),
        ("待办实现", 7),
        ("修复缺陷", 7),
        ("最终验收审查", 7),
    ],
    "pr_branch_topology_task": [
        # English
        ("pr head", 5),
        ("force push", 5),
        ("force-with-lease", 5),
        ("branch split", 5),
        ("split branch", 5),
        ("multiple pr", 5),
        ("prs share", 5),
        ("rebase", 5),
        ("cherry-pick", 5),
        ("cherry pick", 5),
        ("commit graph", 5),
        ("push --force", 5),
        ("push -f", 5),
        ("branch topology", 5),
        ("pr verification", 5),
        ("fork branch", 5),
        ("head sha", 5),
        ("merge conflict", 4),
        ("force-with-lease", 5),
        ("pr governance", 5),
        ("branch surgery", 5),
        ("pr branch", 5),
        # Chinese
        ("pr head", 5),
        ("强制推送", 5),
        ("强制推", 5),
        ("分支拆分", 5),
        ("分支拓扑", 5),
        ("合并冲突", 4),
        ("提交图", 5),
        ("分支治理", 5),
    ],
    "patch_review_task": [
        # English
        ("review", 4),
        ("check this patch", 4),
        ("audit", 3),
        ("inspect changes", 4),
        ("code review", 5),
        ("patch review", 5),
        ("review patch", 5),
        ("review the changes", 4),
        # Chinese
        ("审查", 3),
        ("审核代码", 4),
        ("检查补丁", 4),
        ("代码审查", 5),
        ("复查", 3),
    ],
    "patch_task": [
        # English
        ("implement", 2),
        ("fix", 2),
        ("add feature", 3),
        ("change", 1),
        ("refactor", 3),
        ("optimize", 2),
        ("optimise", 2),
        ("write code", 3),
        ("create function", 3),
        ("add function", 3),
        ("update", 1),
        ("bug", 2),
        ("feature request", 3),
        ("develop", 2),
        ("build", 1),
        ("enhance", 2),
        ("patch", 1),
        ("modify", 2),
        ("rewrite", 3),
        ("new endpoint", 3),
        ("add endpoint", 3),
        ("migrate", 2),
        ("upgrade", 2),
        ("add test", 3),
        ("write test", 3),
        # Chinese
        ("实现", 2),
        ("修复", 2),
        ("添加功能", 3),
        ("重构", 3),
        ("优化", 2),
        ("写代码", 3),
        ("创建函数", 3),
        ("更新", 1),
        ("错误", 2),
        ("功能需求", 3),
        ("开发", 2),
        ("构建", 1),
        ("修改", 2),
    ],
}

# Priority when scores tie (highest first).
_CATEGORY_PRIORITY: list[str] = [
    "forward_development_task",
    "pr_branch_topology_task",
    "patch_review_task",
    "patch_task",
    "unsupported_task",
]

# Human-readable descriptions for each task type.
_TASK_TYPE_DESCRIPTIONS: dict[str, str] = {
    "forward_development_task": (
        "Forward development — task requires the full Codex-regulated "
        "development loop: acceptance criteria, plan, todo items, "
        "implementation, review, bug fixing, and verification."
    ),
    "patch_task": (
        "Code patch generation — task requires implementing or modifying source "
        "code and producing a unified diff patch."
    ),
    "patch_review_task": (
        "Patch review — task requires inspecting, auditing, or reviewing an "
        "existing code patch or set of changes."
    ),
    "pr_branch_topology_task": (
        "PR branch governance — task involves branch topology operations such "
        "as force-with-lease pushes, branch splitting, rebase/cherry-pick, "
        "commit graph analysis, or multi-PR head conflict resolution."
    ),
    "unsupported_task": (
        "Unsupported — this task description does not match any known category "
        "that deepseek-forge can handle."
    ),
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def classify_task(task_text: str) -> str:
    """Classify *task_text* and return one of the four task-type strings.

    Returns one of:
        ``"patch_task"``
        ``"patch_review_task"``
        ``"pr_branch_topology_task"``
        ``"unsupported_task"``

    Classification is case-insensitive and uses weighted keyword matching.
    The category with the highest aggregate keyword weight wins.  On a tie,
    the predefined category priority order breaks the tie.
    """
    if not task_text or not task_text.strip():
        return "unsupported_task"

    text_lower = task_text.lower()
    scores: dict[str, int] = {cat: 0 for cat in _CATEGORY_PRIORITY}

    for category, keywords in _CATEGORY_KEYWORDS.items():
        for keyword, weight in keywords:
            if keyword in text_lower:
                scores[category] += weight

    # Determine the best category.
    best_category = "unsupported_task"
    best_score = 0

    for category in _CATEGORY_PRIORITY:
        if category == "unsupported_task":
            continue
        if scores[category] > best_score:
            best_score = scores[category]
            best_category = category

    return best_category


def task_type_description(task_type: str) -> str:
    """Return a human-readable description of *task_type*.

    *task_type* must be one of the four recognised categories, otherwise
    a ``ValueError`` is raised.
    """
    if task_type not in _TASK_TYPE_DESCRIPTIONS:
        raise ValueError(
            f"Unknown task type: {task_type!r}. "
            f"Expected one of: {', '.join(sorted(_TASK_TYPE_DESCRIPTIONS))}"
        )
    return _TASK_TYPE_DESCRIPTIONS[task_type]


# ---------------------------------------------------------------------------
# Convenience helpers (for programmatic use)
# ---------------------------------------------------------------------------

def is_forward_development_task(task_text: str) -> bool:
    """Return True if *task_text* is classified as ``forward_development_task``."""
    return classify_task(task_text) == "forward_development_task"


def is_patch_task(task_text: str) -> bool:
    """Return True if *task_text* is classified as ``patch_task``."""
    return classify_task(task_text) == "patch_task"


def is_patch_review_task(task_text: str) -> bool:
    """Return True if *task_text* is classified as ``patch_review_task``."""
    return classify_task(task_text) == "patch_review_task"


def is_pr_branch_topology_task(task_text: str) -> bool:
    """Return True if *task_text* is classified as ``pr_branch_topology_task``."""
    return classify_task(task_text) == "pr_branch_topology_task"


def is_supported_task(task_text: str) -> bool:
    """Return True if *task_text* is classified as a supported task type
    (i.e. not ``unsupported_task``)."""
    return classify_task(task_text) != "unsupported_task"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Classify a task description for deepseek-forge routing.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python3 task_classifier.py \"implement login feature\"\n"
            "  python3 task_classifier.py --file task.md\n"
            "  echo 'fix the auth bug' | python3 task_classifier.py\n"
            "  python3 task_classifier.py --list-types"
        ),
    )
    parser.add_argument(
        "task",
        nargs="*",
        help="Task description text (all positional arguments joined).",
    )
    parser.add_argument(
        "--file", "-f",
        default=None,
        help="Read task description from a file instead of command-line arguments.",
    )
    parser.add_argument(
        "--list-types",
        action="store_true",
        help="List all known task types and exit.",
    )
    return parser


# ---------------------------------------------------------------------------
# Direct script invocation
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    parser = _build_parser()
    args = parser.parse_args()

    if args.list_types:
        print("Known task types:")
        for tt in ["forward_development_task", "patch_task", "patch_review_task", "pr_branch_topology_task", "unsupported_task"]:
            print(f"  {tt:<30}  {task_type_description(tt)}")
        sys.exit(0)

    if args.file:
        try:
            task = Path(args.file).read_text(encoding="utf-8", errors="replace")
        except (OSError, FileNotFoundError) as exc:
            print(f"Error: cannot read file '{args.file}': {exc}", file=sys.stderr)
            sys.exit(1)
    elif args.task:
        task = " ".join(args.task)
    elif not sys.stdin.isatty():
        task = sys.stdin.read()
    else:
        parser.print_help(file=sys.stderr)
        sys.exit(1)

    if not task.strip():
        print("Error: empty task description.", file=sys.stderr)
        sys.exit(1)

    result = classify_task(task)
    desc = task_type_description(result)
    print(f"Category   : {result}")
    print(f"Description: {desc}")
