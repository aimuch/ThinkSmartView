#!/usr/bin/env python3
"""Minimal GitLab client for this repository; replaces the `gh` CLI after the move off GitHub.

The project is read from `git remote get-url origin` (https://gitlab.com/jryang1997/MenuBarMijia.git).
Auth: $GITLAB_TOKEN if set, otherwise the credential git resolves for the host (`git credential fill`;
this clone's helper reads the macOS keychain item `menubarmijia-gitlab`). HTTP(S)_PROXY is honoured.
GitLab numbers issues (#n) and merge requests (!n) separately; `pr` here means a merge request.

  python3 scripts/forge.py issue list [--label L]... [--state open|closed|all] [--json]
  python3 scripts/forge.py issue view <n>                 # description, labels and comments
  python3 scripts/forge.py issue create --title T [--body-file F|-] [--label L]...
  python3 scripts/forge.py issue comment <n> --body-file F|-
  python3 scripts/forge.py issue edit <n> [--add-label L]... [--remove-label L]... [--assign-me]
  python3 scripts/forge.py issue close <n> [--comment-file F|-]
  python3 scripts/forge.py pr list [--state open|closed|merged|all] [--json]
  python3 scripts/forge.py pr create --title T [--head BRANCH] [--base main] [--body-file F|-]
  python3 scripts/forge.py pr view <n>
  python3 scripts/forge.py pr checks <n>                  # latest pipeline of the MR and its jobs
  python3 scripts/forge.py pr merge <n> [--method squash|merge] [--delete-branch]
  python3 scripts/forge.py api <METHOD> <path> [--data JSON|@file] [--paginate]
      <path> may contain {repo}, expanded to projects/<url-encoded owner/name>.
  python3 scripts/forge.py web                            # print the project web URL
  python3 scripts/forge.py fetch <url|path>               # raw authenticated GET (e.g. a job trace)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

ORIGIN_RE = re.compile(r"^(?:(?P<scheme>https?)://(?:[^@/]+@)?(?P<host>[^/]+)/|git@(?P<sshhost>[^:]+):)(?P<path>.+?)(?:\.git)?/?$")
STATES = {"open": "opened", "closed": "closed", "merged": "merged", "all": "all"}


class Forge:
    def __init__(self) -> None:
        url = subprocess.run(["git", "remote", "get-url", "origin"], capture_output=True, text=True, check=True).stdout.strip()
        match = ORIGIN_RE.match(url)
        if not match:
            sys.exit(f"❌ 看不懂 origin 地址：{url}")
        self.host = match["host"] or match["sshhost"]
        self.path = match["path"]
        self.base = f"https://{self.host}"
        self.repo = "projects/" + urllib.parse.quote(self.path, safe="")
        self.web = f"{self.base}/{self.path}"
        self._token: str | None = None

    def token(self) -> str:
        if self._token is None:
            token = os.environ.get("GITLAB_TOKEN")
            if not token:
                out = subprocess.run(["git", "credential", "fill"], input=f"protocol=https\nhost={self.host}\n\n",
                                     capture_output=True, text=True, env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
                fields = dict(line.split("=", 1) for line in out.stdout.splitlines() if "=" in line)
                token = fields.get("password")
            if not token:
                sys.exit("❌ 没有 GitLab 令牌：设置 GITLAB_TOKEN，或存进钥匙串："
                         "security add-generic-password -U -a gitlab -s menubarmijia-gitlab -w")
            self._token = token
        return self._token

    def request(self, method: str, path: str, data=None, *, raw: bool = False):
        path = path.replace("{repo}", self.repo).lstrip("/")
        url = path if path.startswith("http") else f"{self.base}/api/v4/{path}"
        body = None if data is None else json.dumps(data).encode()
        req = urllib.request.Request(url, data=body, method=method.upper())
        req.add_header("PRIVATE-TOKEN", self.token())
        if body is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                payload = resp.read()
        except urllib.error.HTTPError as error:
            detail = error.read().decode(errors="replace")[:500]
            sys.exit(f"❌ {method.upper()} {url} → HTTP {error.code}: {detail}")
        except urllib.error.URLError as error:
            sys.exit(f"❌ 连不上 {self.base}：{error.reason}")
        if raw:
            return payload
        return json.loads(payload) if payload.strip() else None

    def paginate(self, path: str) -> list:
        items, page = [], 1
        sep = "&" if "?" in path else "?"
        while True:
            chunk = self.request("GET", f"{path}{sep}per_page=100&page={page}")
            items.extend(chunk or [])
            if not chunk or len(chunk) < 100:
                return items
            page += 1

    def notes(self, kind: str, iid: int) -> list:
        notes = self.paginate(f"{{repo}}/{kind}/{iid}/notes?sort=asc&order_by=created_at")
        return [note for note in notes if not note.get("system")]

    def me(self) -> dict:
        return self.request("GET", "user")


def read_text(spec: str | None) -> str:
    if spec is None:
        return ""
    if spec == "-":
        return sys.stdin.read()
    with open(spec, encoding="utf-8") as handle:
        return handle.read()


def print_notes(notes: list) -> None:
    for note in notes:
        print(f"\n--- {note['author']['username']} @ {note['created_at']}\n{note['body']}")


def cmd_issue(forge: Forge, args) -> None:
    if args.action == "list":
        query = f"{{repo}}/issues?state={STATES[args.state]}&order_by=created_at&sort=asc"
        if args.label:
            query += "&labels=" + urllib.parse.quote(",".join(args.label))
        issues = forge.paginate(query)
        if args.json:
            json.dump(issues, sys.stdout, ensure_ascii=False, indent=2)
            print()
            return
        for issue in issues:
            print(f"#{issue['iid']}\t{issue['state']}\t{','.join(issue['labels'])}\t{issue['title']}")
    elif args.action == "view":
        issue = forge.request("GET", f"{{repo}}/issues/{args.number}")
        assignees = ", ".join(user["username"] for user in issue.get("assignees") or [])
        print(f"#{issue['iid']} [{issue['state']}] {issue['title']}")
        print(f"labels: {', '.join(issue['labels']) or '-'}  assignees: {assignees or '-'}  url: {issue['web_url']}\n")
        print(issue.get("description") or "")
        print_notes(forge.notes("issues", args.number))
    elif args.action == "create":
        data = {"title": args.title, "description": read_text(args.body_file)}
        if args.label:
            data["labels"] = ",".join(args.label)
        print(forge.request("POST", "{repo}/issues", data)["web_url"])
    elif args.action == "comment":
        forge.request("POST", f"{{repo}}/issues/{args.number}/notes", {"body": read_text(args.body_file)})
        print(f"{forge.web}/-/issues/{args.number}")
    elif args.action == "edit":
        data = {}
        if args.add_label:
            data["add_labels"] = ",".join(args.add_label)
        if args.remove_label:
            data["remove_labels"] = ",".join(args.remove_label)
        if args.assign_me:
            issue = forge.request("GET", f"{{repo}}/issues/{args.number}")
            data["assignee_ids"] = sorted({user["id"] for user in issue.get("assignees") or []} | {forge.me()["id"]})
        if data:
            forge.request("PUT", f"{{repo}}/issues/{args.number}", data)
        print(f"{forge.web}/-/issues/{args.number}")
    elif args.action == "close":
        if args.comment_file:
            forge.request("POST", f"{{repo}}/issues/{args.number}/notes", {"body": read_text(args.comment_file)})
        forge.request("PUT", f"{{repo}}/issues/{args.number}", {"state_event": "close"})
        print(f"closed #{args.number}")


def cmd_pr(forge: Forge, args) -> None:
    if args.action == "list":
        mrs = forge.paginate(f"{{repo}}/merge_requests?state={STATES[args.state]}")
        if args.json:
            json.dump(mrs, sys.stdout, ensure_ascii=False, indent=2)
            print()
            return
        for mr in mrs:
            print(f"!{mr['iid']}\t{mr['state'].upper()}\t{mr['source_branch']}\t{mr['title']}")
    elif args.action == "create":
        head = args.head or subprocess.run(["git", "branch", "--show-current"], capture_output=True, text=True, check=True).stdout.strip()
        data = {"title": args.title, "source_branch": head, "target_branch": args.base,
                "description": read_text(args.body_file), "remove_source_branch": True, "squash": True}
        print(forge.request("POST", "{repo}/merge_requests", data)["web_url"])
    elif args.action == "view":
        mr = forge.request("GET", f"{{repo}}/merge_requests/{args.number}")
        print(f"!{mr['iid']} [{mr['state']}] {mr['title']}")
        print(f"{mr['source_branch']} → {mr['target_branch']}  merge status: {mr.get('detailed_merge_status')}  url: {mr['web_url']}\n")
        print(mr.get("description") or "")
        print_notes(forge.notes("merge_requests", args.number))
    elif args.action == "checks":
        mr = forge.request("GET", f"{{repo}}/merge_requests/{args.number}")
        pipelines = forge.request("GET", f"{{repo}}/merge_requests/{args.number}/pipelines") or []
        print(f"head {mr['sha'][:7]}")
        if not pipelines:
            print("（没有流水线：仓库还没有 .gitlab-ci.yml 或没有 runner；用 bash scripts/check.sh --skip-snapshots 本地把关）")
            return
        pipeline = pipelines[0]
        print(f"pipeline {pipeline['id']}  {pipeline['status']}  {pipeline['web_url']}")
        for job in forge.paginate(f"{{repo}}/pipelines/{pipeline['id']}/jobs"):
            print(f"{job['status']:9} {job['stage']}/{job['name']}  {job['web_url']}")
        if pipeline["status"] in ("failed", "canceled"):
            sys.exit(1)
    elif args.action == "merge":
        data = {"squash": args.method == "squash", "should_remove_source_branch": args.delete_branch}
        forge.request("PUT", f"{{repo}}/merge_requests/{args.number}/merge", data)
        print(f"merged !{args.number} ({args.method})")


def cmd_api(forge: Forge, args) -> None:
    data = None
    if args.data is not None:
        data = json.loads(read_text(args.data[1:]) if args.data.startswith("@") else args.data)
    result = forge.paginate(args.path) if args.paginate else forge.request(args.method, args.path, data)
    json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
    print()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="group", required=True)

    issue = sub.add_parser("issue").add_subparsers(dest="action", required=True)
    p = issue.add_parser("list"); p.add_argument("--label", action="append"); p.add_argument("--state", default="open", choices=["open", "closed", "all"]); p.add_argument("--json", action="store_true")
    p = issue.add_parser("view"); p.add_argument("number", type=int)
    p = issue.add_parser("create"); p.add_argument("--title", required=True); p.add_argument("--body-file"); p.add_argument("--label", action="append")
    p = issue.add_parser("comment"); p.add_argument("number", type=int); p.add_argument("--body-file", required=True)
    p = issue.add_parser("edit"); p.add_argument("number", type=int); p.add_argument("--add-label", action="append"); p.add_argument("--remove-label", action="append"); p.add_argument("--assign-me", action="store_true")
    p = issue.add_parser("close"); p.add_argument("number", type=int); p.add_argument("--comment-file")

    pr = sub.add_parser("pr").add_subparsers(dest="action", required=True)
    p = pr.add_parser("list"); p.add_argument("--state", default="open", choices=list(STATES)); p.add_argument("--json", action="store_true")
    p = pr.add_parser("create"); p.add_argument("--title", required=True); p.add_argument("--head"); p.add_argument("--base", default="main"); p.add_argument("--body-file")
    p = pr.add_parser("view"); p.add_argument("number", type=int)
    p = pr.add_parser("checks"); p.add_argument("number", type=int)
    p = pr.add_parser("merge"); p.add_argument("number", type=int); p.add_argument("--method", default="squash", choices=["squash", "merge"]); p.add_argument("--delete-branch", action="store_true")

    p = sub.add_parser("api"); p.add_argument("method"); p.add_argument("path"); p.add_argument("--data"); p.add_argument("--paginate", action="store_true")
    sub.add_parser("web")
    p = sub.add_parser("fetch"); p.add_argument("target")

    args = parser.parse_args()
    forge = Forge()
    if args.group == "issue":
        cmd_issue(forge, args)
    elif args.group == "pr":
        cmd_pr(forge, args)
    elif args.group == "api":
        cmd_api(forge, args)
    elif args.group == "fetch":
        sys.stdout.buffer.write(forge.request("GET", args.target, raw=True))
    else:
        print(forge.web)


if __name__ == "__main__":
    main()
