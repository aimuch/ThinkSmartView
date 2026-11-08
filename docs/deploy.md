# 部署

```
ThinkSmart View (10.10.10.192)                       服务器 Dev (10.10.10.224)
  小麦 client 0.1.6xx                                  小麦网关 server（systemd 用户服务 xiaozhi-hub）
    ws://127.0.0.1:16689   ─┐                            0.0.0.0:16688  管理后台 / OTA / /kiosk/*
    http://127.0.0.1:16688  ─┤ xiaozhi-hub-tunnel         0.0.0.0:16689  语音 WebSocket
    更新源 127.0.0.1:16690  ─┘ ssh -L ────────────────►  127.0.0.1:16688 / 16689
                                                         127.0.0.1:16690  thinksmart-releases（本仓库发布的客户端）
  设备自带的 xiaozhi-hub：已停用                          ~/repos/xiaozhi-hub-server.git（本仓库拆出的网关）
  米家：设备直接连小米云（扫码登录）
```

- **网关**：装在 `andy@10.10.10.224:~/xiaozhi-hub-server`（安装路径沿用上游，没随目录改名），管理后台 http://10.10.10.224:16688/admin 。
- **为什么走 SSH 隧道**：网关的 `/kiosk/*` 接口（切换模型、聊天记录、提醒语音、费用、唤醒核对）只接受网关本机的请求，局域网直连返回 403。走隧道后设备在服务器上就是 127.0.0.1，这些不认证的接口也不会暴露给局域网。
- **隧道密钥**：设备上的 `~/.ssh/hub_tunnel_ed25519`，服务器 `authorized_keys` 里限制为 `restrict,port-forwarding,permitopen="127.0.0.1:16688",permitopen="127.0.0.1:16689",permitopen="127.0.0.1:16690",command="/bin/false"`：只能转发这三个端口，拿不到 shell。
- **重新配置设备**（重刷或换 IP 后）：

  ```bash
  DEVICE=<设备IP> deploy/setup-device.sh
  ```

  会装隧道（`deploy/xiaozhi-hub-tunnel.service`）、停掉设备自带的网关，并把更新源改到服务器上的发布服务（`deploy/thinksmart-releases.service`）。需要本机能免密登录 `ubuntu@<设备IP>`（`~/.ssh/thinksmart_ed25519`）和 `andy@10.10.10.224`（`~/.ssh/id_ed25519_btrouter`）。
- **网关后台「在线设备」**只算正在对话的连接：设备叫醒时才连，没人说话约 2 分钟后断开，平时显示没有设备在线是正常的。

## 排查

```bash
ssh ubuntu@10.10.10.192 'systemctl status xiaozhi-hub-tunnel thinksmart-client --no-pager | head -20; journalctl -u thinksmart-client -n 50 --no-pager'
ssh andy@10.10.10.224 'tail -50 ~/xiaozhi-hub-server/main/xiaozhi-server/tmp/server.log; systemctl --user status xiaozhi-hub thinksmart-releases --no-pager | head'
```
