# 刷机记录（2026-11-07）

刷机步骤本身见 [system/README.md](../system/README.md)（原作者写的，给人和 AI 一步步照做）。这台设备的情况：

- 原厂系统，走路线 A，镜像 20261101。整盘备份在 `local/flash/stock-full-emmc.bin`（7818182656 字节，SHA256 在同目录 `.sha256` 文件里），git 忽略。**请另存一份到移动硬盘，不要上传任何地方。**
- 从原厂 Android 进 EDL 可以用 `adb reboot edl`；刷完后只能按键进（拔电源 → 接 USB → 按住音量+和音量− → 插电源）。
- 这台 Mac 的直连 USB-C 口识别不到设备，EDL 要接在 CalDigit 扩展坞上。
- bootstrap（postmarketOS）里 `/sys/class/udc` 为空，USB 网络不可用，A6 改分区表是在设备触摸屏上登录 `pmos` 执行的。
- 刷机工具 edl 在 `local/edl/`（源码）和 `local/edl/.venv/`，git 忽略。重装：

  ```bash
  git clone https://github.com/bkerler/edl.git local/edl && (cd local/edl && git submodule update --init --recursive)
  python3.12 -m venv local/edl/.venv && local/edl/.venv/bin/pip install ./local/edl
  local/edl/.venv/bin/edl printgpt
  ```

- 原作者 README 里「看网关后台标题是不是『小智 Hub 管理后台』」那一步：本仓库的网关后台已改名为「小麦网关」。
