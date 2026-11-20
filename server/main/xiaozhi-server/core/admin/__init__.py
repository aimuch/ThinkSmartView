"""轻量管理后台：挂在 HTTP 服务（server.http_port）的 /admin 下，读写 data/.config.yaml。"""

import asyncio

from core.admin.admin_handler import AdminHandler


def register_admin_routes(app, config, ws_server=None):
    """在 aiohttp 应用上注册管理后台路由，并在服务起来时排上常驻的活（每天的备份、门口来人）。"""
    handler = AdminHandler(config, ws_server)
    handler.register(app)
    # [xiaozhi-hub] 设备上看机柜、智能中枢自己的网页（设计画布第 62 块）
    from core.integrations import webproxy
    webproxy.register(app)

    async def start_background(_app):
        # 网关备份（设计画布第 87 块）：每分钟看一次到没到点
        _app["hub_backup_task"] = asyncio.get_running_loop().create_task(handler.backups.loop())
        start_door = getattr(handler, "start_door_watch", None)
        if start_door:   # 门口来人（设计画布第 86 块）
            _app["hub_door_task"] = asyncio.get_running_loop().create_task(start_door())

    app.on_startup.append(start_background)
    return handler
