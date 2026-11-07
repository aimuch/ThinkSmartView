# ThinkSmart View 语音终端

把联想 ThinkSmart View（CD-18781Y）刷成本地语音助手，网关部署在局域网服务器上。本仓库由三个上游项目合并而成，外加本地部署用的配置。

| 目录 | 来源 | 合并时的版本 |
| --- | --- | --- |
| `thinksmart-ubuntu-image/` | https://github.com/justhalfbit/thinksmart-ubuntu-image | `d2e3239` |
| `xiaozhi-thinksmart-client/` | https://github.com/justhalfbit/xiaozhi-thinksmart-client | `4a94d8e` |
| `xiaozhi-hub-server/` | https://github.com/justhalfbit/xiaozhi-hub-server | `38b5daa` |
| `remote-hub/` | 本仓库 | 设备改连远程网关的隧道配置 |

刷机步骤见 [thinksmart-ubuntu-image/README.md](thinksmart-ubuntu-image/README.md)。

## 本地部署

```
ThinkSmart View (10.10.10.192)                     服务器 Dev (10.10.10.224)
  thinksmart-client                                  xiaozhi-hub（systemd 用户服务，开机自启）
    ws://127.0.0.1:16689  ─┐                           0.0.0.0:16688  管理后台 / OTA / /kiosk/*
    http://127.0.0.1:16688 ─┤ xiaozhi-hub-tunnel        0.0.0.0:16689  语音 WebSocket
                            └─ ssh -L ──────────────►  127.0.0.1:16688 / 16689
  设备自带的 xiaozhi-hub：已停用
```

- **网关**：装在服务器 `andy@10.10.10.224:~/xiaozhi-hub-server`，管理后台 http://10.10.10.224:16688/admin 。更新：`bash ~/xiaozhi-hub-server/scripts/update.sh`。
- **为什么走 SSH 隧道**：网关的 `/kiosk/*` 接口（屏幕上切换模型、聊天记录、提醒的语音合成、待汇报）只接受网关本机发来的请求，从局域网直连会返回 403。设备通过隧道连到服务器的 `127.0.0.1`，客户端保持默认配置不用改，这些不认证的接口也不会暴露给局域网。
- **隧道密钥**：设备上的 `~/.ssh/hub_tunnel_ed25519`，在服务器 `authorized_keys` 里限制为 `restrict,port-forwarding,permitopen="127.0.0.1:16688",permitopen="127.0.0.1:16689",command="/bin/false"`，只能转发这两个端口，拿不到 shell。
- **重新配置**（重刷设备或换 IP 后）：

  ```bash
  DEVICE=<设备IP> remote-hub/setup-device.sh
  ```

  需要本机已能免密登录 `ubuntu@<设备IP>`（`~/.ssh/thinksmart_ed25519`）和 `andy@10.10.10.224`（`~/.ssh/id_ed25519_btrouter`）。

排查：

```bash
ssh ubuntu@<设备IP> 'systemctl status xiaozhi-hub-tunnel thinksmart-client --no-pager | head -20'
ssh andy@10.10.10.224 'tail -50 ~/xiaozhi-hub-server/main/xiaozhi-server/tmp/server.log'
```

## 刷机记录（2026-11-07）

- 原厂系统，走路线 A。整盘备份在 `flash/stock-full-emmc.bin`（7818182656 字节，SHA256 在同目录 `.sha256` 文件里），已被 `.gitignore` 排除。**请另存一份到移动硬盘，不要上传任何地方。**
- 从原厂 Android 进 EDL 可以用 `adb reboot edl`；刷完后只能按键进（拔电源 → 接 USB → 按住音量+和音量− → 插电源）。
- 这台 Mac 的直连 USB-C 口识别不到设备，EDL 要接在 CalDigit 扩展坞上。
- bootstrap（postmarketOS）里 `/sys/class/udc` 为空，USB 网络不可用，A6 改分区表是在设备触摸屏上登录 `pmos` 执行的。
- 刷机工具 edl 装在 `edl-src/` 和 `.edl-venv/`（已排除），重装：

  ```bash
  git clone https://github.com/bkerler/edl.git edl-src && (cd edl-src && git submodule update --init --recursive)
  python3.12 -m venv .edl-venv && .edl-venv/bin/pip install ./edl-src
  ```
