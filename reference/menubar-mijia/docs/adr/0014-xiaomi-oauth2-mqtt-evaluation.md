# ADR-0014：不迁往 `ha_xiaomi_home` 的私有 OAuth2 与 MQTT 通道，保留扫码直连加 Home Assistant 双后端分工

日期：2026-11-07　状态：已接受（对应 #22，原 GitHub #171；关联 ADR-0001、ADR-0002、ADR-0004、ADR-0007、#10、#21）

## 背景

`MiCloudProvider`（ADR-0002）使用小米账号二维码扫码登录获取 `MiSession`（`sid=xiaomiio`），通过 `api.io.mi.com/app/*` 的 RC4 加密 HTTP 接口轮询设备状态并下发控制指令。相比轮询，小米官方 Home Assistant 集成 [`XiaoMi/ha_xiaomi_home`](https://github.com/XiaoMi/ha_xiaomi_home) 走「OAuth2 授权 + MIoT MQTT（MIPS）实时推送 + 中枢网关局域网通道」，状态延迟在百毫秒级，且能收到门铃按压等 MIoT 事件（见 #10、#308）。

#22（原 GitHub #171）要求评估：MenuBarMijia 的米家直连后端能否从现有的 `xiaomiio` 扫码会话迁到 `ha_xiaomi_home` 的官方 OAuth2 + MIoT MQTT 推送路线。

## 事实核对（基于 `XiaoMi/ha_xiaomi_home` 源码）

结合 [2026-11-05 生态对标调研](../research/2026-11-05-ecosystem-benchmark.md)对 `XiaoMi/ha_xiaomi_home` 的源码审计，该路线依赖三项专属于官方集成的前置条件：

1. **专属 OAuth2 客户端身份与中转换票端点**：
   - `ha_xiaomi_home` 使用固定 `OAUTH2_CLIENT_ID`（`2882303761520251711`）和固定回调地址 `http://homeassistant.local:8123` 向 `account.xiaomi.com/oauth2/authorize` 发起授权。
   - 授权码换取与刷新 `access_token` 不走公开的客户端自签名接口，而是请求小米专门为该集成部署的后端 `https://ha.api.io.mi.com/app/v2/ha/oauth/get_token`（客户端密钥保存在小米服务端，接口校验 `client_id` 与 `redirect_uri`）。
   - 小米开放平台未向独立开源桌面应用开放 `ha.api.io.mi.com` 的客户端注册，第三方无法申请自己的 `client_id` 或注册自定义回调地址（如 `menumijia://` 或任意回环端口）。
2. **云端 MQTT 代理绑定 OAuth2 会话**：
   - 云端推送客户端（`CloudMipsClient`）连接 `<region>-ha.mqtt.io.mi.com:8883`，以 `ha.api.io.mi.com` 签发的 OAuth2 `access_token` 作为凭证，并校验集成内置的专属 CA 证书链。
   - 现有的 `xiaomiio` 扫码会话（`serviceToken` / `ssecurity`）无法通过 `<region>-ha.mqtt.io.mi.com:8883` 的鉴权。
3. **中枢网关局域网通道依赖云端签发的客户端证书（关联 #21）**：
   - 中枢网关的局域网 MIPS 代理（端口 `8883`，mTLS）要求客户端出示由小米云端按用户 OAuth2 会话签发的客户端证书。没有 `ha.api.io.mi.com` 的 OAuth2 令牌，直连客户端无法走通中枢网关的 mTLS 握手。

## 备选方案

1. **借用 `ha_xiaomi_home` 的 `client_id` 并伪装 `homeassistant.local:8123` 回调**。
   - 桌面应用在本机临时监听 `8123` 端口，引导用户浏览器访问 `account.xiaomi.com` 并拦截跳回 `http://homeassistant.local:8123` 的授权码。
   - **否定原因**：
     1. 绝大多数普通 Mac / Windows 用户的本机没有把 `homeassistant.local` 解析到 `127.0.0.1`，若局域网内本就跑着一台真实的 Home Assistant（占用了 `homeassistant.local:8123`），回调会直接落到用户的网关上而不是桌面应用里，登录链路断裂。
     2. 多个桌面端共用官方集成的 `client_id` 属于未授权冒用，一旦触发小米云端限流、吊销或同一账号 MQTT 连接互踢，会导致所有直连用户及用户自己的 Home Assistant 集成同时瘫痪。
2. **保持现有双后端分工：`MiCloudProvider` 走 `xiaomiio` 扫码直连，MQTT 推送与中枢网关交由 `HAWebSocketProvider` 承接**。
   - 没有网关的普通用户继续用手机米家 App 扫码登录 `MiCloudProvider`（5 秒完成，无端口与域名要求，支持多账号跨区合并）。
   - 需要实时推送、猫眼门铃事件（#308）或中枢网关 BLE / Mesh 本地控制的进阶用户，在自己的 Home Assistant 中安装官方 `ha_xiaomi_home`，MenuBarMijia 通过 `HAWebSocketProvider`（ADR-0001）直接订阅其实体与事件流，并由跨源去重（ADR-0008）自动择优。

## 决定

选择方案 2：**不将 `MiCloudProvider` 迁往 `ha_xiaomi_home` 的私有 OAuth2 + MQTT 通道**。

1. `MiCloudProvider` 继续使用 `xiaomiio` 二维码扫码会话与 RC4 HTTP 接口，配合按场景自适应降频轮询（面板打开 6 秒、后台 60 秒、低功耗 180 秒、锁屏暂停）、写后即时追询、3 秒乐观锁与到期提前提示（ADR-0004、#181）。
2. 不在代码库中内嵌或借用 `ha_xiaomi_home` 的 `OAUTH2_CLIENT_ID`、私有 CA 证书或 `ha.api.io.mi.com` 换票接口，也不要求用户修改本机 `hosts` 把 `homeassistant.local` 指向本机。
3. 对依赖 MQTT 实时推送的能力（如门铃响铃弹窗 #308、中枢网关本地子设备），统一通过 `HAWebSocketProvider` 对接用户已部署的 Home Assistant + `ha_xiaomi_home`；云直连通道能否通过 `user/get_user_device_data` 轮询历史事件由 #10 在真机上独立验证。

## 后果

- **免网关体验保持零门槛**：用户扫码即可用，不会因 `homeassistant.local:8123` 解析失败、端口冲突或第三方 `client_id` 被封禁而无法登录。
- **合规与稳定性边界清晰**：不冒用小米官方为 Home Assistant 注册的专属凭据，也不会与用户家中已有的 `ha_xiaomi_home` 争抢同一 MQTT 会话。
- **已知代价**：纯云直连模式下的状态同步仍是秒级轮询而非毫秒级推送，且无法直接订阅 `doorbell-ring` 等只走 MQTT 的瞬态事件；需要实时事件的用户须接入 Home Assistant（或等待 #10 的历史事件轮询验证结论）。
- **重开条件**：仅当小米开放平台正式允许第三方桌面应用独立注册 MIoT OAuth2 `client_id`、支持自定义回环或自定义 URL Scheme 回调并开放公网 MQTT 接入规范时，才重新立项评估。

## 相关

- ADR-0001（Home Assistant 作为单一事实来源）、ADR-0002（双后端架构与米家云直连）、ADR-0004（不保存 `passToken`）、ADR-0007（局域网直连暂缓）、ADR-0008（双源去重合并）、ADR-0010（多米家账号会话）。
- #10（云端会话轮询猫眼事件真机探测）、#21（中枢网关局域网通道只读验证）、#308（Home Assistant 门铃事件自动弹窗）。
