#!/usr/bin/env bash
# Merge the original authors' new commits into this repository.
#   scripts/sync-upstream.sh            all directories listed in UPSTREAM
#   scripts/sync-upstream.sh <dir>...   only these
# For each directory: fetch its upstream, take the diff from the commit recorded in UPSTREAM to
# upstream's latest, and apply it to the directory with a 3-way merge (our own changes stay).
# Conflicts are left in the files (<<<<<<< markers) and the script stops: fix them, `git add`, and
# commit. On success UPSTREAM moves to the new commit; review with `git diff --cached` and commit.
# Needs a clean working tree (commit or stash first).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CACHE="$ROOT/.upstream-cache"
cd "$ROOT"

[[ -z "$(git status --porcelain)" ]] || { echo "working tree not clean: commit or stash first" >&2; exit 1; }
mkdir -p "$CACHE"

only=("$@")
changed=0
while read -r dir url base; do
  [[ -z "$dir" || "$dir" == \#* ]] && continue
  if (( ${#only[@]} )) && [[ ! " ${only[*]} " == *" $dir "* ]]; then continue; fi
  repo="$CACHE/$dir"
  if [[ -d "$repo/.git" ]]; then git -C "$repo" fetch -q origin; else git clone -q --filter=blob:none "$url" "$repo"; fi
  head="$(git -C "$repo" rev-parse origin/HEAD)"
  if [[ "$head" == "$base" ]]; then echo "$dir: up to date (${base:0:7})"; continue; fi
  echo "$dir: ${base:0:7} -> ${head:0:7}"
  git -C "$repo" log --oneline "$base..$head" | sed 's/^/    /'
  patch="$CACHE/$dir.patch"
  git -C "$repo" diff --binary "$base" "$head" > "$patch"
  # -3 needs upstream's old blobs: hand them to this repository's object store
  git -C "$repo" diff --name-only "$base" "$head" | while read -r f; do
    if git -C "$repo" cat-file -e "$base:$f" 2>/dev/null; then git -C "$repo" show "$base:$f" | git hash-object -w --stdin >/dev/null; fi
  done
  if ! git apply -3 --index --directory="$dir" "$patch"; then
    sed -i.bak "s|^$dir $url $base\$|$dir $url $head|" UPSTREAM && rm -f UPSTREAM.bak
    git add UPSTREAM
    echo "$dir: conflicts — fix the files marked above, git add them, then commit" >&2
    exit 2
  fi
  sed -i.bak "s|^$dir $url $base\$|$dir $url $head|" UPSTREAM && rm -f UPSTREAM.bak
  git add UPSTREAM
  changed=1
done < UPSTREAM

if (( changed )); then echo "merged; review with: git diff --cached --stat, then commit"; else echo "nothing new upstream"; fi
