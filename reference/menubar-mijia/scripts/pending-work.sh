#!/usr/bin/env bash
# One line per local branch worth looking at: its own change size, its PR state, and its worktree.
#
# Usage: bash scripts/pending-work.sh [base]   # base defaults to origin/main
#
# A squash-merged branch looks "ahead" in `git rev-list`, so the authoritative landed signal is
# the merge request state from GitLab (scripts/forge.py). The OWN column is the branch's own diff against its merge base, which
# stays stable while the base branch moves.
set -euo pipefail

base="${1:-origin/main}"
git rev-parse --verify --quiet "$base^{commit}" >/dev/null || {
    echo "无法解析基线 ref：$base（先 git fetch）" >&2
    exit 1
}

prs="$(python3 "$(dirname "$0")/forge.py" pr list --state all 2>/dev/null \
    | awk -F'\t' '{ sub(/^!/, "", $1); print $3 "\t" $2 "\t" $1 }' || true)"

pr_state() {
    printf '%s\n' "$prs" | awk -F'\t' -v b="$1" '!found && $1 == b { print $2 " !" $3; found = 1 } END { if (!found) print "no PR" }'
}

worktrees="$(git worktree list --porcelain | awk '
    /^worktree / { sub(/^worktree /, ""); path=$0 }
    /^branch / { sub(/^branch refs\/heads\//, ""); print $0 "\t" path }
')"

printf '%-46s %7s %-38s %-12s %s\n' BRANCH AHEAD OWN PR WORKTREE
git for-each-ref --format='%(refname:short)|%(committerdate:short)' refs/heads |
while IFS='|' read -r branch last; do
    [ -n "$branch" ] || continue
    merge_base="$(git merge-base "$base" "$branch" 2>/dev/null || true)"
    own=""
    if [ -n "$merge_base" ]; then
        own="$(git diff --shortstat "$merge_base" "$branch" 2>/dev/null || true)"
    fi
    ahead="$(git rev-list --count "$base..$branch" 2>/dev/null || echo '?')"
    worktree="$(printf '%s\n' "$worktrees" | awk -v b="$branch" -F'\t' '$1 == b {print $2}')"
    # Branches that changed nothing and have no checkout left are noise.
    if [ -z "$own" ] && [ -z "$worktree" ]; then continue; fi
    state=""
    if [ -n "$worktree" ]; then
        if ! git -C "$worktree" status --porcelain >/dev/null 2>&1; then
            state=" (unknown)"
        elif [ -n "$(git -C "$worktree" status --porcelain)" ]; then
            state=" (dirty)"
        else
            state=" (clean)"
        fi
    fi
    printf '%-46s %7s %-38s %-12s %s%s\n' \
        "$branch" "$ahead" "${own:-no file delta}" "$(pr_state "$branch")" \
        "${worktree:-<no worktree>}" "$state"
done
