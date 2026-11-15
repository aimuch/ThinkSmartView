"""仓库同步：读 repo-manager 的概览（GET /api/overview）和同步任务（GET /api/state 的 watches、jobs），
在设备上开始、暂停、继续、停止同步任务，开关定时（设计画布第 75 块）。

repo-manager 对本机（127.0.0.1）来的请求不要密码；网关不在同一台机器上时填它的局域网密码（HTTP Basic）。
"""

import aiohttp

from core.integrations.common import Cache, IntegrationError, request

_cache = Cache(30)
KIND = {"local_changes": "本地修改冲突", "auth": "认证失败", "gone": "上游不存在", "network": "网络错误", "timeout": "超时",
        "diverged": "无法快进", "not_found": "目录不存在", "guard": "疑似删库", "ssh": "服务器连接失败", "owner": "目录所有者不同"}


async def summary(s: dict, fresh: bool = False) -> dict:
    async def fetch():
        auth = aiohttp.BasicAuth("repo-manager", s["password"]) if s.get("password") else None
        data = await request("GET", f"{s['url']}/api/overview", auth=auth, timeout=10)
        if not isinstance(data, dict) or "repos" not in data:
            raise IntegrationError("repo-manager 回的不是概览")
        repos, watches, hosts = data.get("repos") or {}, data.get("watches") or {}, data.get("hosts") or {}
        views = repos.get("views") or {}
        guard = data.get("guard7") or {}
        attention = []
        for a in (data.get("attention") or [])[:5]:
            if not isinstance(a, dict):
                continue
            why = KIND.get(a.get("kind") or "", "") or {"failed": "失败", "protected": "被拦下", "gone": "上游不存在"}.get(a.get("state"), "")
            attention.append({"name": str(a.get("name") or a.get("path") or ""), "why": why or str(a.get("state") or ""),
                              "host": str(a.get("host_name") or ""), "fails": a.get("fails")})
        events = [e for e in data.get("events") or [] if isinstance(e, dict)]
        try:
            state = await request("GET", f"{s['url']}/api/state", auth=auth, timeout=10)
            tasks, host_names = _tasks(state), [str(h.get("name") or "") for h in state.get("hosts") or [] if isinstance(h, dict)]
        except IntegrationError:
            tasks, host_names = None, []
        try:   # 失败的仓库按原因数一数（设计画布第 77 块）
            failed = await request("GET", f"{s['url']}/api/repos?view=failed&limit=2000", auth=auth, timeout=10)
            reasons = _reasons((failed or {}).get("repos") or [], views)
        except IntegrationError:
            reasons = None
        return {"total": repos.get("total") or 0, "synced": views.get("synced") or 0, "behind": views.get("behind") or 0,
                "dirty": views.get("dirty") or 0, "failed": views.get("failed") or 0, "protected": views.get("protected") or 0,
                "gone": views.get("gone") or 0, "suspect7": int(guard.get("before") or 0) + int(guard.get("skip") or 0),
                "watches": {k: watches.get(k) or 0 for k in ("total", "running", "ok", "failed", "error")},
                "hosts": {k: hosts.get(k) or 0 for k in ("total", "online", "down")},
                "attention": attention, "attention_total": data.get("attention_total") or len(attention),
                "last_event": ({"at": events[0].get("at"), "msg": str(events[0].get("msg") or "")[:120]} if events else None),
                "tasks": tasks, "reasons": reasons,
                "views": {k: int(views.get(k) or 0) for k in ("synced", "behind", "dirty", "failed", "protected", "gone", "skipped")},
                "host_list": [{"name": n, "tasks": sum(1 for x in tasks or [] if x["host"] == n)} for n in host_names if n]}
    if fresh:
        _cache.drop(s["url"])
    return await _cache.get(s["url"], fetch)


ACTIVE = {"running", "queued", "starting", "paused"}
_samples = {}   # 在跑的那次 -> [(时间, 完成数)]：按最近 10 分钟的速度估剩余时间


def _reasons(rows: list, views: dict) -> list:
    """「要处理的」按原因：失败的按 kind 分，再加被拦下（疑似删库）、上游没了；每类带几个仓库名（点开看）。"""
    groups = {}
    for r in rows:
        if isinstance(r, dict):
            g = groups.setdefault(r.get("kind") or "other", [])
            g.append(str(r.get("name") or r.get("path") or ""))
    out = [{"kind": k, "label": KIND.get(k, {"other": "其他错误", "host": "服务器连不上"}.get(k, k)), "count": len(v), "names": v[:8]}
           for k, v in sorted(groups.items(), key=lambda kv: -len(kv[1]))]
    for k, label in (("protected", "被拦下（疑似删库）"), ("gone", "上游没了")):
        if views.get(k):
            out.append({"kind": k, "label": label, "count": int(views[k]), "names": []})
    return out


def _eta(job_id: str, done: int, total: int):
    import time

    now = time.time()
    seq = [x for x in _samples.get(job_id, []) if now - x[0] <= 900] + [(now, done)]
    _samples[job_id] = seq
    for k in [k for k in _samples if k != job_id and now - _samples[k][-1][0] > 3600]:
        _samples.pop(k, None)
    t0, d0 = seq[0]
    if now - t0 < 120 or done <= d0 or not total:
        return None
    return int((total - done) / ((done - d0) / (now - t0)))


def _schedule(w: dict) -> str:
    if not w.get("enabled"):
        return "定时关"
    sc = w.get("schedule") or {}
    if sc.get("mode") == "daily":
        return f"每天 {sc.get('at') or ''}".strip()
    if sc.get("mode") == "weekly":
        return f"每周 {sc.get('at') or ''}".strip()
    if sc.get("every"):
        return f"每 {sc['every']} 分钟"
    return "定时开"


def _tasks(state) -> list:
    """repo-manager 的同步任务 -> 屏幕上一行一个：名字、在哪台机器、定时、在跑的进度、上次怎么样。"""
    if not isinstance(state, dict):
        return []
    jobs = {j.get("id"): j for j in state.get("jobs") or [] if isinstance(j, dict)}
    out = []
    for w in state.get("watches") or []:
        if not isinstance(w, dict) or not w.get("id"):
            continue
        last = w.get("last") or {}
        job = jobs.get(w.get("job_id")) if w.get("job_id") in jobs else None
        if job is None:
            job = next((j for j in jobs.values() if j.get("watch_id") == w["id"] and j.get("status") in ACTIVE), None)
        active = bool(job and job.get("status") in ACTIVE)
        path = str(w.get("path") or last.get("root") or "")
        name = path.rstrip("/").rsplit("/", 1)[-1] or str((job or last).get("title") or w["id"])
        counts = last.get("counts") or {}
        x = {"id": w["id"], "name": name, "host": str(w.get("host_name") or last.get("host_name") or ""),
             "schedule": _schedule(w), "enabled": bool(w.get("enabled")),
             "state": ("paused" if job.get("paused") or job.get("status") == "paused" else "running") if active else "idle",
             "job": job.get("id") if active else None,
             "done": int((job or {}).get("done") or 0) if active else None, "total": int((job or {}).get("total") or 0) if active else None,
             "last": None if active else (last.get("status") or None), "failed": int(counts.get("failed") or 0) if not active else 0,
             "ended": None if active else (last.get("ended") or None)}
        if active:
            jc = job.get("counts") or {}
            x.update(started=job.get("started"), trigger=job.get("trigger"),
                     counts={k: int(jc.get(k) or 0) for k in ("cloned", "updated", "failed")},
                     eta_s=_eta(job["id"], x["done"], x["total"]))
        out.append(x)
    out.sort(key=lambda x: (x["state"] == "idle", not x["enabled"], x["name"]))
    return out


async def act(s: dict, op: str, task_id: str, on=None) -> None:
    """op：run（立即同步）、pause、resume、stop（对在跑的那次）、schedule（定时开关，on）。"""
    auth = aiohttp.BasicAuth("repo-manager", s["password"]) if s.get("password") else None
    state = await request("GET", f"{s['url']}/api/state", auth=auth, timeout=10)
    task = next((x for x in _tasks(state) if x["id"] == task_id), None)
    if task is None:
        raise IntegrationError("repo-manager 里没有这个同步任务")
    if op == "run":
        if task["state"] != "idle":
            raise IntegrationError("这个同步任务正在跑")
        path, body = "/api/watch/run", {"id": task_id}
    elif op in ("pause", "resume", "stop"):
        if not task["job"]:
            raise IntegrationError("这个同步任务现在没在跑")
        path, body = f"/api/job/{op}", {"id": task["job"]}
    elif op == "schedule":
        path, body = "/api/watch/enable", {"id": task_id, "enabled": bool(on)}
    else:
        raise IntegrationError(f"不支持的操作：{op}")
    data = await request("POST", f"{s['url']}{path}", auth=auth, json=body, headers={"X-Repo-Manager": "1"}, timeout=15)
    if isinstance(data, dict) and data.get("error"):
        raise IntegrationError(str(data["error"]))
    _cache.drop(s["url"])


def spoken(d: dict) -> str:
    text = (f"一共 {d['total']} 个仓库，已同步 {d['synced']} 个，失败 {d['failed']} 个，有本地修改 {d['dirty']} 个，"
            f"上游不存在 {d['gone']} 个。")
    text += f"最近 7 天疑似删库 {d['suspect7']} 次。" if d["suspect7"] else "最近 7 天没有疑似删库。"
    w = d["watches"]
    if w["running"]:
        text += f"现在有 {w['running']} 个同步任务在跑。"
    if w["failed"] or w["error"]:
        text += f"同步任务里 {w['failed'] + w['error']} 个上次没成功。"
    if d["hosts"]["down"]:
        text += f"有 {d['hosts']['down']} 台服务器连不上。"
    if d["attention"]:
        text += "需要处理的比如：" + "；".join(f"{a['name']}（{a['why']}）" for a in d["attention"][:3]) + "。"
    return text
