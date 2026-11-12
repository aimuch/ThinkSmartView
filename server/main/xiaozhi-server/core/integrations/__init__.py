"""[xiaozhi-hub] 接入家里别的服务（设计画布第 38、39 块）：智能中枢（蓝牙灯）、机柜（Prometheus 和 rack-screen）、
仓库同步（repo-manager）、桌面（Mac 上的 DeskHub App）。

每个接入是后台「意图识别 › 插件」里的一个插件：设置（地址、令牌）存在 plugins.<键> 下，语音命令在
plugins_func/functions 里，设备屏幕上的工作台和蓝牙灯经 /kiosk/integrations 读。
"""
