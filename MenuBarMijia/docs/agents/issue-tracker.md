# Issue tracker: GitLab

Issues, specs and merge requests live on GitLab at `https://gitlab.com/jryang1997/MenuBarMijia`. The GitHub account is suspended, so never use `gh`. The NAS Forgejo (`git remote nas`, `http://10.10.1.4:3000/jryang/MenuBarMijia`, LAN only) is a backup copy and nothing should be filed there.

Every operation goes through `python3 scripts/forge.py`. It reads the project from `git remote get-url origin` and authenticates with `$GITLAB_TOKEN`, or with the credential git resolves for gitlab.com. This clone's git helper reads that from the macOS keychain item `menubarmijia-gitlab`. To store a token (scopes `api`, `write_repository`): `security add-generic-password -U -a gitlab -s menubarmijia-gitlab -w`.

GitLab numbers issues (`#n`) and merge requests (`!n`) separately; `forge.py pr …` means a merge request.

## Conventions

- **Create an issue**: `python3 scripts/forge.py issue create --title "..." --body-file - --label ready-for-agent`, with the body on stdin (heredoc).
- **Read an issue**: `python3 scripts/forge.py issue view <n>` prints the description, labels, assignees and comments.
- **List issues**: `python3 scripts/forge.py issue list [--label L] [--state open|closed|all]`. Add `--json` to get the raw API objects.
- **Comment**: `python3 scripts/forge.py issue comment <n> --body-file -`
- **Apply / remove labels**: `python3 scripts/forge.py issue edit <n> --add-label "..." --remove-label "..."`
- **Close**: `python3 scripts/forge.py issue close <n> --comment-file -`
- **Anything else**: `python3 scripts/forge.py api <METHOD> '{repo}/<path>' [--data JSON] [--paginate]` calls the GitLab REST API v4.

## Migrated issue numbers

The open issues came from GitHub by way of Forgejo, and they were renumbered on the way. GitLab `#n` equals Forgejo `#n`. Their titles start with `[GH#<old>]`, and their bodies still cite **old GitHub numbers** (`#304`, `GH#304`).

Translate an old number through the title prefix: `#5` is `[GH#304]`. An old number with no `[GH#…]` issue was closed before the move. Its record is in the NAS backup at `/volume1/docker/forgejo/backups/issues-backup-20261106.md`.

New issues, branches and MRs use GitLab numbers. Existing `issue-<n>` branches may carry either number. The issue creation date shows the import day; the real date is in the footer of each description.

Some migrated bodies hold a broken `## Blocked by` section with `#$(cat n9)` placeholders. Use the `> 原 GitHub Issue … 前置：Blocked by GH#…` banner on the first line instead; it is the reliable blocker list.

The GitHub PR history was not migrated, so `pending-work.sh` shows `no PR` for every branch that predates the move.

## Merge requests

- **Open**: push the branch to `origin`, then run `python3 scripts/forge.py pr create --title "..." --body-file -`. The source branch defaults to the current branch and the target to `main`. The MR squashes and deletes its branch on merge.
- **Read**: `python3 scripts/forge.py pr view <n>`. For the diff, use `git diff origin/main...origin/<branch>`.
- **Checks**: `python3 scripts/forge.py pr checks <n>` prints the MR's latest pipeline and its jobs. `.gitlab-ci.yml` runs `bash scripts/check.sh` (snapshots, Windows TypeScript tests and `vite build` included) on the maintainer's Mac through a project runner (shell executor, tag `macos`). Rust is covered separately by the `windows` job on a GitLab.com hosted Windows runner (tag `saas-windows-medium-amd64`): `npm run build` plus `cargo test` for `windows/src-tauri`, triggered only by `windows/src-tauri/` or `.gitlab-ci.yml` changes. GitLab does not run the `.github/workflows` files. Failed job logs: `bash scripts/ci_errors.sh <pipeline-id>`.
- **Merge**: `python3 scripts/forge.py pr merge <n> --method squash --delete-branch`.

**PRs as a request surface: no.** _(Set to `yes` if this repo treats external MRs as feature requests; `/triage` reads this flag.)_

## When a skill says "publish to the issue tracker"

Create a GitLab issue with `python3 scripts/forge.py issue create`.

## When a skill says "fetch the relevant ticket"

Run `python3 scripts/forge.py issue view <n>`.

## Wayfinding operations

`/wayfinder` uses these operations. The **map** is a single issue, and its **child** issues are the tickets.

- **Map**: a single issue labelled `wayfinder:map`, holding the Notes / Decisions-so-far / Fog body.
- **Child ticket**: add the child to a task list in the map body and put `Part of #<map>` at the top of the child body. GitLab child items and blocking links are tier-dependent, so the body is the record. Labels: `wayfinder:<type>` (`research`/`prototype`/`grilling`/`task`). Once claimed, the ticket is assigned to the driving dev.
- **Blocking**: write a `Blocked by: #<n>, #<n>` line (or a `## Blocked by` section) in the child body. A ticket is unblocked when every blocker is closed.
- **Frontier query**: `bash scripts/issue-frontier.sh` checks every open `ready-for-agent` issue in one call. It skips an issue that has open blockers in its body (old numbers are translated for migrated issues), an assignee, a `claimed:*` label, an open MR that mentions it, or an `issue-<n>` branch.
- **Claim**: `python3 scripts/forge.py issue edit <n> --assign-me`, the session's first write.
- **Resolve**: comment with the answer, close the issue, then append a context pointer (gist + link) to the map's Decisions-so-far.
