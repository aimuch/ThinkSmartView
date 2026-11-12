"""设备屏幕用的：一次拿齐工作台和蓝牙灯要的数据（GET /kiosk/integrations?device=…）。

一个接入要「必填的设置填了」并且「这台设备开着这个插件」才给（plugins 里是每个接入在这台的开关状态，设计画布第 40 块），客厅那台关掉插件就看不到工作台。
"""

import asyncio

from core.integrations import common, deskhub, rack, repos, smarthub


async def _safe(coro):
    try:
        return await coro
    except common.IntegrationError as e:
        return {"error": str(e)}


async def overview(merged: dict, device: str, fresh: bool = False) -> dict:
    available = {n: common.ready(merged, n) and common.enabled_for(merged, device, n) for n in common.PLUGINS}
    jobs = {}
    if available["smart_hub"]:
        async def lights():
            return {"devices": await smarthub.devices(common.settings(merged, "smart_hub"), fresh)}
        jobs["smart_hub"] = _safe(lights())
    if available["rack_status"]:
        jobs["rack_status"] = _safe(rack.summary(common.settings(merged, "rack_status"), fresh))
    if available["repo_status"]:
        jobs["repo_status"] = _safe(repos.summary(common.settings(merged, "repo_status"), fresh))
    results = dict(zip(jobs, await asyncio.gather(*jobs.values()))) if jobs else {}
    out = {"available": available, "plugins": common.plugin_states(merged, device), **results}
    if available["desk_hub"]:
        out["desk_hub"] = deskhub.hub.view()
    return out
