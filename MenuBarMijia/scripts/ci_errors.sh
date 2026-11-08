#!/usr/bin/env bash
# Print only the failure lines of a GitLab pipeline, one screenful instead of the full log.
#
# Usage: bash scripts/ci_errors.sh <pipeline-id>   # the number in .../-/pipelines/<id>
#        bash scripts/ci_errors.sh <log-file>      # a job log saved from the web UI
set -euo pipefail
cd "$(dirname "$0")/.."

if [ "$#" -ne 1 ]; then
    echo "用法：bash scripts/ci_errors.sh <pipeline-id|log-file>" >&2
    exit 2
fi

log="$(mktemp "${TMPDIR:-/tmp}/ci-errors.XXXXXX")"
trap 'rm -f "$log"' EXIT
if [ -f "$1" ]; then
    sed 's/^/log\t/' "$1" >"$log"
else
    jobs="$(python3 scripts/forge.py api GET "{repo}/pipelines/$1/jobs?scope[]=failed" --paginate \
        | python3 -c 'import json, sys; [print(str(j["id"]) + "\t" + j["name"]) for j in json.load(sys.stdin)]')"
    while IFS=$'\t' read -r id name; do
        [ -n "$id" ] || continue
        python3 scripts/forge.py fetch "{repo}/jobs/$id/trace" | sed "s/^/$name\t/" >>"$log"
    done <<<"$jobs"
fi

if [ ! -s "$log" ]; then
    echo "没有失败日志：$1（流水线仍在运行、已成功、不存在，或在建任务前就失败了，例如账号未验证）"
    echo "   $(python3 scripts/forge.py web)/-/pipelines/$1"
    exit 1
fi

pattern='error:|✘ (Test|Suite)|Test run with .* failed|ERROR: Job failed'
echo "=== 失败的 job"
awk -F'\t' '!seen[$1]++ {print $1}' "$log"
echo "=== 错误与失败用例"
cut -f2- "$log" |
    sed -E 's/\x1b\[[0-9;]*[mK]//g; s/^section_(start|end):[0-9]+:[^ ]*//' |
    grep -E "$pattern" |
    awk '!seen[$0]++' |
    head -60
