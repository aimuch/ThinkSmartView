#!/usr/bin/env bash
# 一条命令列出所有 ready-for-agent 票，以及每张现在能不能做（#338）。
#
# 用法：bash scripts/issue-frontier.sh            # 表格：可做 → 可拆 → 跳过，组内按找票顺序
#       bash scripts/issue-frontier.sh --doable   # 只输出可做的编号，一行一个，第一行就是下一张该做的
#
# 可做：ticket 格式（## What to build + ## Acceptance criteria），没有未关闭的 blocker、
#       没有 assignee、没有 claimed:* 标签、没有关联的 open PR、远端没有 issue-<号> 分支。
# 可拆：spec 格式（## Problem Statement 或 ## User Stories），还没有 sub-issue，其余条件同上。
# 所有会话共用一个 GitLab 账号，所以 assignee 和 claimed:* 标签都算已被认领。
# 从 GitHub 迁来的票标题带 [GH#旧号]，正文里的编号是旧号：按标题前缀翻译成新号，翻不到的旧号已归档关闭；
# 分支名和 PR 里提到新号或旧号都算关联。
# 找票顺序：priority:high → medium → 没标优先级 → low，同级编号小的在前。
#
# 只取三次数据：全部 open issue、open PR（都走 scripts/forge.py 调 GitLab API，转成下面判定逻辑用的字段）、git ls-remote。
# 设了 ISSUE_FRONTIER_FIXTURES=<目录> 时改读其中的 issues.json / prs.json / branches.txt（自测用）。
set -euo pipefail
cd "$(dirname "$0")/.."

mode=table
for argument in "$@"; do
    case "$argument" in
        --doable) mode=doable ;;
        *)
            echo "❌ 未知参数：${argument}（可用：--doable）" >&2
            exit 2
            ;;
    esac
done

command -v python3 >/dev/null 2>&1 || {
    echo "❌ issue-frontier.sh 需要 python3" >&2
    exit 1
}

if [ -n "${ISSUE_FRONTIER_FIXTURES:-}" ]; then
    data_dir="$ISSUE_FRONTIER_FIXTURES"
else
    data_dir="$(mktemp -d "${TMPDIR:-/tmp}/issue-frontier.XXXXXX")"
    trap 'rm -rf "$data_dir"' EXIT
    # 取全部 open issue 而不只是 ready-for-agent：正文 Blocked by 里的票要靠它判断是否还开着。
    python3 scripts/forge.py api GET '{repo}/issues?state=opened' --paginate | python3 -c '
import json, sys
issues = json.load(sys.stdin)
json.dump([[{"number": i["iid"], "title": i["title"], "body": i.get("description") or "",
             "labels": [{"name": name} for name in i.get("labels") or []],
             "assignees": [{"login": user["username"]} for user in i.get("assignees") or []]} for i in issues]], sys.stdout)' \
        > "$data_dir/issues.json"
    # 不读 GitLab 的 closes 关联：关联只靠 MR 标题、描述和源分支名里的编号。
    python3 scripts/forge.py api GET '{repo}/merge_requests?state=opened' --paginate | python3 -c '
import json, sys
mrs = json.load(sys.stdin)
json.dump([{"number": m["iid"], "title": m["title"], "body": m.get("description") or "", "headRefName": m["source_branch"],
            "closingIssuesReferences": []} for m in mrs], sys.stdout)' > "$data_dir/prs.json"
    git ls-remote --heads origin > "$data_dir/branches.txt"
fi

PYTHONIOENCODING=utf-8 python3 - "$mode" "$data_dir" <<'PY'
import json
import re
import sys
import unicodedata
from pathlib import Path

sys.stdout.reconfigure(newline="\n")

mode, data_dir = sys.argv[1], Path(sys.argv[2])


def load(name):
    return (data_dir / name).read_text(encoding="utf-8")


# --paginate --slurp 给的是「页」的数组。
issues = [issue for page in json.loads(load("issues.json")) for issue in page]
issues = [issue for issue in issues if not issue.get("pull_request")]
open_numbers = {issue["number"] for issue in issues}
LEGACY_TITLE = re.compile(r"^\s*\[GH#(\d+)\]")
legacy_of = {}
for issue in issues:
    match = LEGACY_TITLE.match(issue.get("title") or "")
    if match:
        legacy_of[issue["number"]] = int(match.group(1))
new_of_legacy = {legacy: number for number, legacy in legacy_of.items()}
prs = json.loads(load("prs.json"))
branches = [line.split("refs/heads/", 1)[1] for line in load("branches.txt").splitlines() if "refs/heads/" in line]

BRANCH_ISSUE = re.compile(r"(?:^|[/_-])issue[-_/]?(\d+)(?!\d)", re.IGNORECASE)
PRIORITIES = {"priority:high": (0, "high"), "priority:medium": (1, "medium"), "priority:low": (3, "low")}
GROUP = {"可做": 0, "可拆": 1, "跳过": 2}


def branch_issue_numbers(name):
    return {int(match) for match in BRANCH_ISSUE.findall(name)}


def has_heading(body, *names):
    return any(re.search(rf"(?mi)^##\s+{re.escape(name)}\b", body) for name in names)


def body_blockers(body, legacy):
    # docs/agents/issue-tracker.md 的回退写法：没有原生依赖时，blocker 写在正文的 Blocked by 一节或一行里，
    # 格式不固定（列表、勾选框、一句话）。宁可多跳过：这一节里出现的、仍开着的编号都算。
    refs = set()
    section = re.search(r"(?ims)^##\s+Blocked by\b(.*?)(?=^##\s|\Z)", body)
    if section:
        refs.update(int(n) for n in re.findall(r"#(\d+)", section.group(1)))
    for line in re.findall(r"(?im)^[*_ ]*Blocked by[*_ ]*:(.*)$", body):
        refs.update(int(n) for n in re.findall(r"#(\d+)", line))
    # 迁移横幅「> 原 GitHub Issue `#305` | 前置：Blocked by `GH#304`（`#303` 已合入）」：括号里是已合入的旁注。
    for line in re.findall(r"(?im)^>\s*原 GitHub Issue.*?Blocked by(.*)$", body):
        line = re.sub(r"（[^）]*）|\([^)]*\)", "", line)
        refs.update(int(n) for n in re.findall(r"#(\d+)", line))
    if legacy:
        # 迁移票正文里是 GitHub 旧号；没有对应新票的旧号都已合入归档。
        refs = {new_of_legacy[n] for n in refs if n in new_of_legacy}
    return refs & open_numbers


def linked_prs(numbers):
    # 前面不能紧挨英文字母、数字、_、/ 或 &，后面不能再接数字：#280、other/repo#28、&#28; 都不算提到 #28，
    # 「修复#28」算；GH#28 也算（迁移票的旧号）。
    alternatives = "|".join(str(n) for n in sorted(numbers))
    mention = re.compile(rf"(?:(?<=GH)|(?<![A-Za-z0-9_/&]))#(?:{alternatives})(?!\d)")
    found = []
    for pr in prs:
        closes = {ref.get("number") for ref in pr.get("closingIssuesReferences") or []}
        text = f"{pr.get('title') or ''}\n{pr.get('body') or ''}"
        if closes & numbers or mention.search(text) or branch_issue_numbers(pr.get("headRefName") or "") & numbers:
            found.append(pr["number"])
    return found


rows = []
for issue in issues:
    labels = [label["name"] for label in issue.get("labels") or []]
    if "ready-for-agent" not in labels:
        continue
    number = issue["number"]
    body = issue.get("body") or ""
    rank, priority = min((PRIORITIES[label] for label in labels if label in PRIORITIES), default=(2, "-"))
    # 原生依赖是权威记录（issue-tracker.md），有就只看它的未关闭数；一条都没有才回退到正文。
    dependencies = issue.get("issue_dependencies_summary") or {}
    if dependencies.get("total_blocked_by"):
        blockers = dependencies.get("blocked_by") or 0
    else:
        blockers = len(body_blockers(body, number in legacy_of))
    sub_issues = (issue.get("sub_issues_summary") or {}).get("total") or 0
    if has_heading(body, "What to build") and has_heading(body, "Acceptance criteria"):
        kind, kind_cell = "ticket", "ticket"
    elif has_heading(body, "Problem Statement", "User Stories"):
        kind, kind_cell = "spec", f"spec/{sub_issues}"
    else:
        kind, kind_cell = "other", "其他"
    assignees = [assignee["login"] for assignee in issue.get("assignees") or []]
    claims = [label.split(":", 1)[1] for label in labels if label.startswith("claimed:")]
    aliases = {number} | ({legacy_of[number]} if number in legacy_of else set())
    pr_numbers = linked_prs(aliases)
    own_branches = [name for name in branches if branch_issue_numbers(name) & aliases]
    free = not (blockers or assignees or claims or pr_numbers or own_branches)
    if free and kind == "ticket":
        verdict = "可做"
    elif free and kind == "spec" and not sub_issues:
        verdict = "可拆"
    else:
        verdict = "跳过"
    rows.append({
        "order": (GROUP[verdict], rank, number),
        "verdict": verdict,
        "number": number,
        "cells": [
            f"#{number}",
            priority,
            kind_cell,
            str(blockers) if blockers else "-",
            ",".join(assignees) or "-",
            ",".join(claims) or "-",
            ",".join(f"#{n}" for n in pr_numbers) or "-",
            ",".join(own_branches) or "-",
            verdict,
            issue.get("title") or "",
        ],
    })
rows.sort(key=lambda row: row["order"])

if mode == "doable":
    for row in rows:
        if row["verdict"] == "可做":
            print(row["number"])
    sys.exit(0)

if not rows:
    print("没有 open 的 ready-for-agent 票。")
    sys.exit(0)


def width(text):
    return sum(2 if unicodedata.east_asian_width(char) in "WF" else 1 for char in text)


header = ["#", "优先级", "格式", "blocker", "assignee", "认领", "open PR", "分支", "结论", "标题"]
table = [header] + [row["cells"] for row in rows]
widths = [max(width(line[i]) for line in table) for i in range(len(header) - 1)]
for line in table:
    padded = [cell + " " * (widths[i] - width(cell)) for i, cell in enumerate(line[:-1])]
    print("  ".join(padded + [line[-1]]))

counts = {verdict: sum(row["verdict"] == verdict for row in rows) for verdict in ("可做", "可拆", "跳过")}
print(f"\n可做 {counts['可做']} 张，可拆 {counts['可拆']} 个，跳过 {counts['跳过']} 张。")
PY
