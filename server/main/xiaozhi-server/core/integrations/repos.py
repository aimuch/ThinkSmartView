"""仓库同步：读 repo-manager 的概览（GET /api/overview）。

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
        return {"total": repos.get("total") or 0, "synced": views.get("synced") or 0, "behind": views.get("behind") or 0,
                "dirty": views.get("dirty") or 0, "failed": views.get("failed") or 0, "protected": views.get("protected") or 0,
                "gone": views.get("gone") or 0, "suspect7": int(guard.get("before") or 0) + int(guard.get("skip") or 0),
                "watches": {k: watches.get(k) or 0 for k in ("total", "running", "ok", "failed", "error")},
                "hosts": {k: hosts.get(k) or 0 for k in ("total", "online", "down")},
                "attention": attention, "attention_total": data.get("attention_total") or len(attention),
                "last_event": ({"at": events[0].get("at"), "msg": str(events[0].get("msg") or "")[:120]} if events else None)}
    if fresh:
        _cache.drop(s["url"])
    return await _cache.get(s["url"], fetch)


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
