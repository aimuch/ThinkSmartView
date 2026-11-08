# go2rtc 云台补丁（MenuBarMijia）

本目录固定一份可重现的 go2rtc 补丁，让米家摄像头的**既有单条 P2P 会话**也能发送云台电机命令。
应用只在探测到该补丁端点时才显示拖动控制；官方 v1.9.15 二进制的行为完全不变。

## 基座与归属

| 项 | 值 |
| --- | --- |
| 上游 | `https://github.com/AlexxIT/go2rtc` |
| 基座 tag | `v1.9.15` |
| 基座 commit | `7c05066c90ab6ad1fccb506d5f0408aa31ebe60f` |
| 补丁文件 | `patches/0001-xiaomi-ptz.patch` |
| 补丁 SHA-256 | `25a3c3a879e4ffe721364634cc88f9c34f63e5d914b21f021103563123807994` |
| 许可 | MIT（保留 go2rtc 版权声明；补丁本身同样以 MIT 提供） |

补丁的思路来自社区真机报告与上游 PR #2531（`bcamarneiro`，commit `1a056d7c29` / `e23a21b9ee`），但这里只保留单会话电机命令与租约端点，没有引入 ONVIF 相关代码。

## 补丁做了什么

1. **同会话电机命令**：`pkg/xiaomi/miss` 在流已经建立的 CS2/TUTK 会话上发送
   `cmdMotorReq 0x112` + `{"operation":1..4}`（`-1001` 停止）。不新建 P2P 会话，
   因为实测部分摄像机只允许一个会话，第二条会断开视频。
2. **命令通道持续读取**：后台 loop 排空 channel 0，解析 `0x1001` 包裹的 `0x113`
   位置响应，按大端读取内层命令号并裁掉尾部 `\0`。这修掉了上游「约 23 秒后缓冲满、
   整条 P2P 会话被丢」的问题。
3. **stop 不被 ACK 阻塞且可重试**：CS2 UDP 的普通命令会持锁重试 5 次、每次 1 秒；补丁新增
   可中断等待与 `WriteCommandNoWait`，停止路径先 `AbortWaits`，然后做最多 3 次、间隔 60ms
   的单包写入（不等待 ACK），不会被丢包的 move 拖住，也不会再有 5s 锁等待。写入失败会返回
   `stopSent=false` + `stopError=write_failed`，不以 HTTP 200 假装成功。
4. **桥端会话/租约**：`POST /api/xiaomi/ptz`（仅 loopback、POST、拒绝带 `Origin` 的请求）：
   - `{"action":"begin","src":…}` → `{"epoch":…,"session":…,"mode":…}`；会话号由桥单调分配。
   - `{"action":"move","src":…,"epoch":…,"session":…,"seq":…,"direction":"left|right|up|down"}`
   - `{"action":"heartbeat",…,"seq":…}` 续租；`seq` 不递增的请求会被拒绝，迟到包不能覆盖新方向。
   - `{"action":"stop","src":…,"epoch":…,"session":…}`
   - `stoppedThrough` 是每条流永久单调高水位：任何 `session <= stoppedThrough` 的迟到 move
     永久拒绝；`begin` 尚未收到 move 的会话被 stop 时也会留下 tombstone。桥重启换 epoch，
     旧 token 全部失效。
   - 应用每 250ms 续租；桥端 700ms 未续租即视为失联并停机；单次按住 8s 硬上限。
   - 步进型机型（029a02、CW400、CW500 等）每 380ms 重发一步，方向切换立即发新步；连续型
     （C300/C400 等）只发一次方向，松手/到期发 `-1001`；**未分类机型**每次按住只发一次方向，
     松手/到期必发停止。
   - 响应里 `stopConfirmed` 恒为 false：协议没有可信的停止回执。
5. **能力表是证据表，不是品牌表**：未收录型号返回 409，不产生任何设备动作。

## 构建

需要 Go 1.26+、git、lipo（macOS）：

```bash
bash scripts/go2rtc-ptz/build.sh
```

产物在 `scripts/go2rtc-ptz/out/`（已被 gitignore）：

- `go2rtc-universal`：arm64 + x86_64，供 `scripts/build_app.sh` 打入应用包；
- `go2rtc-darwin-arm64`、`go2rtc-darwin-amd64`；
- `go2rtc-windows-amd64.exe`（可选，Windows 端接线前先人工验证）。

`build.sh` 只克隆上游、打补丁、构建，不发布、不覆盖 `~/Library/Application Support/MenuBarMijia/bin/go2rtc`。

应用打包必须显式要求云台桥；默认包不含：

```bash
PTZ_BRIDGE=1 SKIP_RESTART=1 bash scripts/build_app.sh
```

`PTZ_BRIDGE=1` 时若 `out/go2rtc-universal` 缺失会先调用 `build.sh`，构建失败或不是双架构就
直接失败，不会静默回退到官方二进制。`SKIP_RESTART=1` 同时阻止打包脚本 killall 和 open。

## 验证

```bash
bash scripts/go2rtc-ptz/verify.sh
```

会重新克隆基座、应用补丁，运行 `go test -race ./internal/xiaomi ./pkg/xiaomi/miss ./pkg/xiaomi/miss/cs2 ./pkg/tutk` 与
`go build ./...`。本机已验证（2026-11-07，Go 1.26.10）：

- 补丁零冲突；
- `go build -trimpath` 成功；
- 新增单测与既有 `pkg/onvif` 单测通过；
- 官方 v1.9.15 `GET /api/xiaomi/ptz` → 404，补丁版 → 405（应用据此判断是否启用拖动）。

## 真机验证（未完成，需用户设备）

`chuangmi.camera.061a01`（小米智能摄像机 3 Pro 云台版）在表里标为 `unknown`：
按住只发一次方向、松手必发停止。首次真机验证时应确认：

1. 一步是否自停；若持续旋转，确认 `-1001` 能停；
2. 若确认是步进型，把该型号改成 `ptzStep`（可连续按住）；
3. 若确认是连续型且 stop 有效，改成 `ptzContinuous`；
4. 记录失败/无响应时 UI 的提示文案与桥端日志。

在这些验证完成前，文档与 UI 不得声称该型号「已验证」。
