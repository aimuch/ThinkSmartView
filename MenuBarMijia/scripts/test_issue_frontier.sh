#!/usr/bin/env bash
# issue-frontier.sh 判定规则的自测（#338）：读 scripts/testdata/issue-frontier 里的固定数据，不连 GitLab。
# fixture 里每张票只踩一条跳过规则，所以哪条规则被放宽，哪张票的断言就会失败。
set -euo pipefail
cd "$(dirname "$0")/.."

export ISSUE_FRONTIER_FIXTURES=scripts/testdata/issue-frontier
failures=0
pass() { printf 'PASS %s\n' "$1"; }
fail() {
    printf 'FAIL %s\n' "$1" >&2
    failures=$((failures + 1))
}

doable="$(bash scripts/issue-frontier.sh --doable | tr '\n' ' ')" || fail "--doable 运行失败"
if [ "$doable" = "11 10 12 14 28 34 37 38 13 " ]; then
    pass "--doable 按 high → medium → 无优先级 → low、同级编号从小到大输出"
else
    fail "--doable 应为 '11 10 12 14 28 34 37 38 13 '，实际 '$doable'"
fi

table="$(bash scripts/issue-frontier.sh)" || fail "表格模式运行失败"

verdict_of() {
    printf '%s\n' "$table" | awk -v n="#$1" '$1 == n {
        for (i = 2; i <= NF; i++) if ($i == "可做" || $i == "可拆" || $i == "跳过") { print $i; exit }
    }'
}

expect_verdict() {
    local number="$1" expected="$2" reason="$3" actual
    actual="$(verdict_of "$number")"
    if [ "$actual" = "$expected" ]; then
        pass "#${number} ${expected}：${reason}"
    else
        fail "#${number} 应为「${expected}」（${reason}），实际「${actual:-不在表里}」"
    fi
}

expect_verdict 12 可做 "有原生依赖时只看原生依赖，它的 blocker 已关闭"
expect_verdict 14 可做 "没有原生依赖，正文 Blocked by 一节里的票已关闭，下一节的编号不算"
expect_verdict 28 可做 "#280、other/repo#28 和 issue-280 分支都不算关联"
expect_verdict 20 跳过 "原生依赖里有未关闭的 blocker"
expect_verdict 21 跳过 "没有原生依赖，正文 **Blocked by:** 行写了未关闭的票"
expect_verdict 29 跳过 "没有原生依赖，正文 Blocked by 一节用一句话写了未关闭的票"
expect_verdict 22 跳过 "已有 assignee"
expect_verdict 23 跳过 "有 claimed:* 标签"
expect_verdict 24 跳过 "open PR 的 closingIssuesReferences 指向它"
expect_verdict 25 跳过 "open PR 正文写了它的编号"
expect_verdict 16 跳过 "open PR 标题里中文紧挨着写了它的编号"
expect_verdict 26 跳过 "远端有 issue-<号> 分支"
expect_verdict 27 跳过 "正文不是 ticket 格式"
expect_verdict 30 可拆 "spec 还没有 sub-issue"
expect_verdict 31 跳过 "spec 已经拆过"
expect_verdict 32 跳过 "迁移票正文的 GH#410 翻译成新号 #33，它还开着"
expect_verdict 34 可做 "迁移票正文的 #12 是旧号，没有对应新票即已归档，不能误认成新票 #12"
expect_verdict 35 跳过 "远端有按旧号命名的 issue-312 分支"
expect_verdict 36 跳过 "迁移横幅写了还开着的 GH#410，正文 Blocked by 一节是坏掉的占位符"
expect_verdict 37 可做 "迁移横幅括号里「已合入」的编号不算 blocker，GH#411 已归档"
expect_verdict 38 可做 "pull_request 为 null 的是普通 issue，不是 PR"

for number in 40 99; do
    if [ -z "$(verdict_of "$number")" ]; then
        pass "#$number 不在表里（不是 ready-for-agent，或是 PR）"
    else
        fail "#$number 不该出现在表里"
    fi
done

for expected in "local-agent" "#501" "#502" "#503" "claude/issue-26-foo" "其他"; do
    if printf '%s\n' "$table" | grep -qF -- "$expected"; then
        pass "表里写出了跳过原因 $expected"
    else
        fail "表里缺少跳过原因 $expected"
    fi
done

status=0
bash scripts/issue-frontier.sh --bogus >/dev/null 2>&1 || status=$?
if [ "$status" = 2 ]; then
    pass "未知参数退出码为 2"
else
    fail "未知参数应退出 2，实际 $status"
fi

if [ "$failures" -ne 0 ]; then
    echo "❌ issue-frontier 自测失败 $failures 项" >&2
    exit 1
fi
echo "✅ issue-frontier 自测通过"
