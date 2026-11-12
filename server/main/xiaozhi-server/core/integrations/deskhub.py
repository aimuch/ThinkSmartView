"""桌面：Mac 上的 DeskHub App 主动连网关（设计画布第 39 块）。

- POST /deskhub/push：App 把发给 DeskHub 触摸屏的同一套消息交上来（输入源、当前输入、AI 用量、待办），
  {"name": "Mac mini", "messages": ["{\"op\":\"sources_begin\"…}", …], "allow_todo": true}。网关只留最近一次。
- GET /deskhub/wait：App 的长轮询，有指令马上返回 {"commands": [...]}，最多等 25 秒。
- POST /deskhub/result：指令的结果，就是 App 发给触摸屏的 result / todo_result 消息。

指令也照触摸屏的格式：{"op":"switch","req":n,"id":输入源 id}、{"op":"todo_act","req":n,"id":待办 id,"a":"done"}，
App 用它原来的解析函数（LinkProtocol.parseRequest、TodoBoard.parseAct）认，白名单和读回确认都照旧。
鉴权：Authorization: Bearer <连接口令>，口令在后台「桌面（DeskHub）› 设置」里填。
"""

import asyncio
import hmac
import json
import time

ONLINE_SECONDS = 20          # App 每 5 秒交一次；20 秒没交算不在线
COMMAND_SECONDS = 12         # 指令等结果最多这么久（和触摸屏一样）
WAIT_SECONDS = 25
FAILS, FAIL_WINDOW = 5, 300  # 口令错 5 次锁 5 分钟（按来源地址）
PORTS = {"dp1": "DP 1", "dp2": "DP 2", "hdmi1": "HDMI 1", "hdmi2": "HDMI 2", "usbc": "USB-C", "other": "其他"}


def _parse(messages) -> dict:
    """触摸屏消息 -> {sources, current, usage, todos, todos_total, todos_off}；不认识的忽略。"""
    out = {"sources": [], "current": "", "usage": None, "todos": [], "todos_total": 0, "todos_off": None}
    for raw in messages or []:
        try:
            m = json.loads(raw) if isinstance(raw, str) else raw
        except ValueError:
            continue
        if not isinstance(m, dict):
            continue
        op = m.get("op")
        if op == "sources_begin":
            out["sources"] = []
        elif op == "source" and m.get("id"):
            out["sources"].append({"id": str(m["id"])[:48], "name": str(m.get("name") or "")[:32],
                                   "port": PORTS.get(m.get("port"), "")})
        elif op == "current":
            out["current"] = str(m.get("id") or "")
        elif op == "usage":
            out["usage"] = m if m.get("ok") else {"ok": False}
        elif op == "todos_begin":
            out["todos"], out["todos_total"], out["todos_off"] = [], int(m.get("total") or 0), None
        elif op == "todo" and m.get("id"):
            out["todos"].append({"id": str(m["id"]), "title": str(m.get("t") or ""), "list": str(m.get("l") or ""),
                                 "kind": m.get("k") or "n", "y": m.get("y") or 0, "m": m.get("m") or 0, "d": m.get("d") or 0,
                                 "hm": m.get("hm") if isinstance(m.get("hm"), int) else -1})
        elif op == "todos_off":
            out["todos"], out["todos_off"] = [], str(m.get("why") or "")
    for t in out["todos"]:
        t["when"] = when_text(t)
    return out


def when_text(t: dict) -> str:
    kind, hm = t.get("kind"), t.get("hm", -1)
    clock = f" {hm // 60:02d}:{hm % 60:02d}" if isinstance(hm, int) and hm >= 0 else ""
    if kind == "n" or not t.get("m"):
        return ""
    if kind == "t":
        return "今天" + clock
    if kind == "m":
        return "明天" + clock
    if kind == "o":
        return f"{t['m']}月{t['d']}日{clock}（过期）"
    return f"{t['m']}月{t['d']}日{clock}"


def usage_view(u: dict) -> dict:
    """App 的 usage 消息（MagpieUsage.message）-> 屏幕和语音用的。"""
    if not u or not u.get("ok"):
        return None
    windows = []
    for w in u.get("w") or []:
        minutes = int(w.get("m") or 0)
        name = "5 小时" if minutes == 300 else "每周" if minutes == 10080 else "每天" if minutes == 1440 else \
            (f"{minutes // 60} 小时" if minutes % 60 == 0 else f"{minutes} 分钟")
        windows.append({"name": name, "left": int(w.get("r") or 0), "reset_s": int(w.get("s") or 0)})
    return {"provider": u.get("p") or "", "plan": u.get("plan") or "", "windows": windows, "calls": u.get("c") or 0,
            "errors": u.get("e") or 0, "tokens": u.get("t") or 0, "last_model": u.get("lm") or ""}


def reset_text(seconds: int) -> str:
    if seconds <= 0:
        return ""
    if seconds < 3600:
        return f"{max(1, seconds // 60)} 分钟后"
    if seconds < 86400:
        return f"{seconds // 3600}:{seconds % 3600 // 60:02d} 后"
    return f"{seconds // 86400} 天后"


class DeskHub:
    def __init__(self):
        self.state = None            # _parse 的结果，加上 name、at、allow_todo
        self._queue = []
        self._wake = asyncio.Event()
        self._req = int(time.time()) % 100000 * 10
        self._pending = {}           # req -> Future
        self._fails = {}             # 地址 -> [时间]
        self.log = []                # 最近的指令和结果（给后台看）

    # ---- 鉴权 ----
    def locked(self, ip: str) -> bool:
        now = time.time()
        recent = [t for t in self._fails.get(ip, []) if now - t < FAIL_WINDOW]
        self._fails[ip] = recent
        return len(recent) >= FAILS

    def check(self, header: str, token: str, ip: str) -> bool:
        given = header[7:].strip() if (header or "").startswith("Bearer ") else ""
        ok = bool(token) and bool(given) and hmac.compare_digest(given.encode(), token.encode())
        if not ok:
            self._fails.setdefault(ip, []).append(time.time())
        return ok

    # ---- App 那边 ----
    def push(self, body: dict) -> None:
        parsed = _parse(body.get("messages"))
        parsed.update(name=str(body.get("name") or "Mac")[:40], at=time.time(), allow_todo=bool(body.get("allow_todo", True)))
        self.state = parsed

    async def wait(self, timeout: float = WAIT_SECONDS) -> list:
        if not self._queue:
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), timeout)
            except asyncio.TimeoutError:
                return []
        out, self._queue = self._queue, []
        return out

    def result(self, body: dict) -> None:
        try:
            m = json.loads(body["message"]) if isinstance(body.get("message"), str) else body
            req = int(m.get("req"))
        except (KeyError, TypeError, ValueError):
            return
        fut = self._pending.pop(req, None)
        if fut and not fut.done():
            fut.set_result(m)

    # ---- 设备和语音那边 ----
    def online(self) -> bool:
        return bool(self.state) and time.time() - self.state["at"] < ONLINE_SECONDS

    def view(self) -> dict:
        s = self.state
        if not s:
            return {"online": False, "seen": None}
        return {"online": self.online(), "seen": s["at"], "name": s["name"], "sources": s["sources"], "current": s["current"],
                "usage": usage_view(s["usage"]), "usage_off": bool(s["usage"]) and not s["usage"].get("ok"),
                "todos": s["todos"][:30], "todos_total": s["todos_total"], "todos_off": s["todos_off"],
                "allow_todo": s["allow_todo"]}

    async def command(self, op: str, ident: str) -> dict:
        """-> {"ok", "text"}：text 是给人看/听的一句话。"""
        if not self.online():
            return {"ok": False, "text": "Mac 上的 DeskHub 没连上网关"}
        if op == "switch":
            src = next((x for x in self.state["sources"] if x["id"] == ident), None)
            if not src:
                return {"ok": False, "text": "Mac 上没有这个输入源"}
            label, msg = src["name"], {"op": "switch", "id": ident}
        elif op == "todo_done":
            todo = next((x for x in self.state["todos"] if x["id"] == ident), None)
            if not todo:
                return {"ok": False, "text": "这条待办已经变了"}
            if not self.state["allow_todo"]:
                return {"ok": False, "text": "Mac 上的 DeskHub 没打开「允许在屏上完成待办」"}
            label, msg = todo["title"], {"op": "todo_act", "id": ident, "a": "done"}
        else:
            return {"ok": False, "text": "不认识的指令"}
        self._req = self._req % 4_000_000_000 + 1
        msg["req"] = self._req
        fut = asyncio.get_running_loop().create_future()
        self._pending[self._req] = fut
        self._queue.append(json.dumps(msg, ensure_ascii=False, separators=(",", ":")))
        self._wake.set()
        try:
            answer = await asyncio.wait_for(fut, COMMAND_SECONDS)
        except asyncio.TimeoutError:
            self._pending.pop(msg["req"], None)
            out = {"ok": False, "text": "Mac 没回应"}
        else:
            out = self._answer(op, label, answer)
        self.log = ([{"at": time.time(), "op": op, "label": label, **out}] + self.log)[:20]
        return out

    @staticmethod
    def _answer(op: str, label: str, m: dict) -> dict:
        detail = str(m.get("detail") or "")
        if op == "switch":
            outcome = m.get("outcome")
            if outcome == "confirmed":
                return {"ok": True, "text": f"已切到{label}，显示器确认了"}
            if outcome == "sent_unconfirmed":
                return {"ok": True, "text": f"已发给显示器切到{label}，但没读回确认" + (f"（{detail}）" if detail else "")}
            return {"ok": False, "text": detail or "没有切"}
        if m.get("ok"):
            return {"ok": True, "text": f"「{label}」已完成"}
        return {"ok": False, "text": detail or "没做成"}


hub = DeskHub()


def find(items: list, name: str, key: str):
    """名字 -> (条目, 候选)。"""
    want = str(name or "").strip().lower().replace(" ", "")
    if not want:
        return None, []
    norm = lambda x: str(x.get(key) or "").lower().replace(" ", "")
    exact = [x for x in items if norm(x) == want or x.get("id") == name]
    if len(exact) == 1:
        return exact[0], []
    hits = [x for x in items if want in norm(x) or (norm(x) and norm(x) in want)
            or want in str(x.get("port") or "").lower().replace(" ", "")]
    if len(hits) == 1:
        return hits[0], []
    return None, [x.get(key) for x in (hits or items)][:6]


def spoken(v: dict, what: str) -> str:
    if not v.get("seen"):
        return "Mac 上的 DeskHub 还没连过网关。"
    stale = "" if v["online"] else "（Mac 现在没连上，下面是上次的）"
    if what == "usage":
        u = v.get("usage")
        if not u:
            return "Mac 上读不到 Magpie 的用量" + stale + "。"
        parts = [f"{w['name']}额度还剩 {w['left']}%" + (f"，{reset_text(w['reset_s'])}重置" if w["reset_s"] else "") for w in u["windows"]]
        head = " ".join(x for x in (u["provider"], u["plan"]) if x)
        tokens = u["tokens"]
        amount = f"{tokens / 1e6:.1f} 百万" if tokens >= 1e6 else f"{tokens / 1e4:.1f} 万" if tokens >= 1e4 else str(tokens)
        return (f"{head}：" if head else "") + "；".join(parts or ["没有额度信息"]) + \
            f"。今天调用 {u['calls']} 次，用了 {amount} token" + stale + "。"
    if what == "todos":
        if v.get("todos_off"):
            return "Mac 上的 DeskHub 没打开提醒事项，或者没有权限。"
        todos = v.get("todos") or []
        if not todos:
            return "电脑上没有未完成的待办" + stale + "。"
        due = [t for t in todos if t["kind"] in ("o", "t")]
        pick = due or todos[:5]
        head = f"电脑上有 {v['todos_total']} 条未完成的待办，" + (f"今天要做或过期的 {len(due)} 条：" if due else "最近的几条：")
        return head + "；".join(t["title"] + (f"（{t['when']}）" if t["when"] else "") for t in pick[:8]) + stale + "。"
    srcs = v.get("sources") or []
    cur = next((x["name"] for x in srcs if x["id"] == v.get("current")), "")
    return ("显示器现在在" + cur + "。" if cur else "") + ("能切的输入源：" + "、".join(x["name"] for x in srcs) + "。" if srcs
                                                    else "Mac 上还没有试切过的输入源。") + stale
