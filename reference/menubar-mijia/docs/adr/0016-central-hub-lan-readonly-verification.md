# ADR-0016：中枢网关与多模网关的局域网通道只读验证，结论是不依赖 Home Assistant 无法覆盖 BLE / Mesh 子设备

日期：2026-11-07　状态：已接受（对应 #21；关联 ADR-0007、ADR-0014、ADR-0008、#10、#22）

## 背景

ADR-0007 把局域网直连列为暂缓，并留下一个问题：BLE、Zigbee、Mesh 子设备要经过网关，网关的本地协议不是 miIO，混插环境下哪些设备能直连只能逐型号实测。#21 要求对 `xiaomi.gateway.hub1`、`xiaomi.gateway.lm001` 两个中枢网关型号与 `lumi.gateway.mgl03` 多模网关做只读验证，回答一件事：

不依赖 Home Assistant，MenuBarMijia 能否用自己的局域网通道读到 BLE / Mesh 子设备。

ADR-0014 已经从 OAuth2 身份角度给出结论。本 ADR 补上局域网协议层的证据与可复现的验证工具。验证不接触真实设备状态，全部为只读探测。

## 型号核对

| 票面型号 | 一手来源的结论 |
| --- | --- |
| `xiaomi.gateway.hub1` | 真实型号。MIoT 规格 `urn:miot-spec-v2:device:gateway:0000A019:xiaomi-hub1:3`，产品描述「智能本地中枢，超级蓝牙 Mesh 网关」，2022-03 上市。 |
| `xiaomi.gateway.lm001` | 两个一手来源都查不到。`home.miot-spec.com` 的 `xiaomi.gateway` 前缀共 11 个型号，没有 `lm001`；`ha_xiaomi_home` wiki 的中枢网关型号表也没有。按未确认处理，命名最接近的真实型号是 `lumi.gateway.mgl001`（Xiaomi Smart Home Hub 2）。 |
| `lumi.gateway.mgl03` | 真实型号，小米智能多模网关，规格 `urn:miot-spec-v2:device:gateway:0000A019:lumi-mgl03:3`，产品描述为带 Wi-Fi、Zigbee 与 BLE 模块的网关。 |

`ha_xiaomi_home` wiki 列出的中枢网关型号为 `xiaomi.gateway.hub1`、`xiaomi.controller.oh10p`、`xiaomi.router.rn04`、`xiaomi.router.rp01`、`xiaomi.router.rp02`、`xiaomi.router.rp04`、`xsmart.gateway.plc04`。中枢能力也存在于路由器里，不只网关壳。

## 通道一：miIO（UDP 54321）

事实，全部来自上游实现：

- `ha_xiaomi_home` 的 `miot/miot_lan.py` 在模块开头写明 `MIoT lan device control, only support MIoT SPEC-v2 WiFi devices.`。
- 线上 v3 的 `lumi.gateway.mgl03` 规格列出的是网关自身的服务，包括指示灯、防区、警报音量与自定义模式，没有子设备枚举服务。本仓库 `MiCloudTests` 夹具里那份规格只有一个空的 `gateway` 服务，`MiotSynthesizer.supportedServiceName(for:)` 返回 `nil`。
- 局域网发现报文是 32 字节的 Hello，前四字节 `21 31 00 20`，其余为 `0xff`（`python-miio` 的 `MiIOProtocol.discover`）或 `MDID` 加虚拟 did（`ha_xiaomi_home` 的 `MIoTLan` 探针）。端口 54321。本工具只发一次，不重试。
- 请求加密用设备 token 派生的 AES-128-CBC，`key = md5(token)`，`iv = md5(key + token)`，载荷 PKCS#7 填充，整包 md5 写在帧头第 16 到 32 字节。**每个 IP 设备一个 token，token 来自云端设备列表。**
- `MIoTLan` 只接受已知 did 的设备的保活帧。token 未知时 Hello 应答只能给出 did、时间戳与校验字段，读不到任何属性。
- 上游 README 的原话是局域网控制「can only control IP devices (devices connected to the router via WiFi or ethernet cable)」，且「cannot control BLE Mesh, ZigBee, etc. devices」。同一个 README 还写明，局域网里存在中枢网关时，即使打开局域网控制也不会生效。

推断：

- BLE / Mesh / Zigbee 子设备没有 IP 地址，也就没有可寻址的 UDP 54321 端点。它们的状态只能从网关拿到。
- 网关自己的 miIO 应答即使存在，也只是网关这一个 IP 设备的应答。在 `ha_xiaomi_home` 与 `python-miio` 里都不存在通过网关 miIO 转发子设备读写的方法，网关的 MIoT 规格也不枚举子设备。子设备在云端设备列表里是各自独立的 did，由云端逐条返回。
- 因此「量产固件通过 miIO 提供通用子设备只读转发」这件事，在两个可审阅的实现里都没有依据。这一点按「未发现支持」记录，不引申为对全部固件版本的否定。

未测量：本仓库没有中枢网关与多模网关实机，Hello 应答与端口连通性由 ADR 末尾的探测脚本在用户网络上复测。上游代码的预期是中枢网关不响应 miIO 设备控制，它只提供下面的 MIPS 通道。这是推断，不是实测。

## 通道二：中枢网关本地 MIPS（TCP 8883，mTLS）

事实：

- 中枢网关在网络里广播 mDNS 服务 `_miot-central._tcp.local.`（`miot/miot_mdns.py`），服务属性里带家庭 `group_id` 与端口。
- 客户端是 MQTT v5，走 TLS。`miot/miot_mips.py` 的 `MipsLocalClient` 签名为 `port: int = 8883`，连接时调用 `tls_set(tls_version=ssl.PROTOCOL_TLS_CLIENT, ca_certs=ca_file, certfile=cert_file, keyfile=key_file)`。三件证书缺一不可。
- 客户端证书来自云端。`miot/miot_cloud.py` 的 `get_central_cert_async` 向 `/app/v2/ha/oauth/get_central_crt` 提交本机生成的 CSR，换回证书。这条接口用 `ha_xiaomi_home` 的 OAuth2 `access_token` 鉴权。
- CA 是集成内置的 `mihome_ca.cert`，`MIoTCert.verify_ca_cert_async` 用写死的 SHA-256 校验，见 `miot/miot_storage.py`。
- 中枢网关通道只在中国区启用，`miot/const.py` 为 `SUPPORT_CENTRAL_GATEWAY_CTRL: list = ['cn']`。
- 上游 wiki 说明，本地实时性依赖设备把 `properties_changed` 上报给本地 broker。2021 年前的 Wi-Fi 设备大多只会上报到云端，接中枢也不会有本地推送。
- 只要扫到中枢网关服务，`MIoTLan` 就会主动关闭 miIO 局域网控制（`__on_mips_service_change` 里打印 `find central service, deinit miot lan`）。上游把中枢通道当作优选路径。

推断：

- 8883 的 mTLS 握手要求客户端证书。没有 `ha.api.io.mi.com` 的 OAuth2 会话就换不到证书，握手在证书阶段失败。这正是 ADR-0014 的结论在局域网层的表现。
- 没有这一步，客户端连 MQTT CONNECT 都发不出去，读不到 `devListChange`，也就拿不到 BLE / Mesh 子设备的属性。

未测量：8883 的端口状态与握手失败原因同样由探测脚本在用户网络复测。脚本只做一次 TLS 握手，不发 MQTT CONNECT，不建立会话。

## 通道三：多模网关的本地通道

`lumi.gateway.mgl03` 经常被和旧款网关的局域网 API 混为一谈。两者不是一回事。

### 旧款米家与 Aqara 网关的 LUMI 协议（UDP 9898）

事实：

- Home Assistant 的 `xiaomi_aqara` 集成对接旧款网关。文档支持 `lumi.gateway.v2` 与 `lumi.gateway.v3`，并把 `lumi.gateway.aqhm01` 明确列在不支持里，原因是无法在米家 App 打开开发者模式。
- 这套 LUMI Gateway LAN Communication API 用组播 `224.0.0.50:4321` 发现网关，之后在 UDP 9898 上收发 JSON，`cmd` 为 `read` 或 `write`，需要网关的局域网 key。
- 它管理的子设备是 Zigbee 子设备。这款硬件不承担 BLE / Mesh 子设备的接入。

结论：这条路对 #21 不适用。型号不对，协议也不覆盖 BLE / Mesh。

### 多模网关 `lumi.gateway.mgl03`

事实：

- 开源组件 `XiaomiGateway3` 在多模网关一代与二代的默认固件上工作，需要网关 token。固件 1.5.5 及以上的多模网关一代还需要 key。
- 从该组件第 3 版开始，集成会在网关上安装常驻程序 `openmiio_agent`。安装说明写明它会自动「open Telnet on gateway」「download and run latest openmiio_agent binary」「run the binary after gateway restarts」。
- `openmiio_agent` 的 `central` 模块负责接收 BLE / Mesh 的本地更新，`mqtt` 模块把网关的 MQTT 暴露在 `1883` 端口。组件还提供「Gateway run FTP」「Gateway reboot」「OpenMiIO reload」这类控制命令。
- 该组件对 BLE 与 Mesh 的支持标注为「some models」，可靠性取决于网关固件与硬件版本。
- 更彻底的做法是在网关上装自定义固件，或通过 Telnet 直接改写运行时。

结论：`lumi.gateway.mgl03` 的 BLE / Mesh 本地读取，开源界的做法要开 Telnet、往网关写文件、跑常驻进程，甚至换固件。这改变了网关的运行态，属于侵入式做法，违反本仓库的只读与非侵入原则。本仓库不采用这条路径。

未测量：本仓库没有 mgl03 实机。上述机制来自 `XiaomiGateway3` 与 `openmiio_agent` 的官方说明，未在真机上复现。

## 结论

| 通道 | 能看到 BLE / Mesh 子设备 | 只读探测 | 实际阻塞点 |
| --- | --- | --- | --- |
| miIO，UDP 54321 | 不能 | 能探测端口与 Hello，仅对有 token 的 IP 设备能读属性 | 子设备没有 IP，也就没有可寻址的端点 |
| 中枢网关本地 MIPS，TCP 8883 | 能 | 能探测端口与 TLS 握手 | 客户端证书按 OAuth2 会话签发，第三方拿不到 |
| 旧款网关 LUMI 协议，UDP 9898 | 不能，只覆盖 Zigbee 子设备 | 协议层有 `read`，型号也不匹配 | 只有 `lumi.gateway.v2`、`v3` 支持，`aqhm01` 官方标注不支持 |
| 多模网关 `lumi.gateway.mgl03` | 能，靠 `openmiio_agent` | 不可只读 | 要开 Telnet、往网关装载常驻程序，可能改固件 |

四条通道合起来回答 #21 的问题：**不依赖 Home Assistant，自有局域网通道覆盖不了 BLE / Mesh 子设备。** 云端 API 能读到这些子设备，但它们的状态走云端轮询，不是局域网直连。

## 决定

不推进任何自研的网关局域网通道。

1. 免 Home Assistant 的直连后端继续只服务云端 API 能覆盖的设备。BLE / Mesh / Zigbee 子设备在云端设备列表里本来就是独立 did，`MiCloudProvider` 现有链路已经能读能控，不需要经过网关的局域网通道。
2. 需要本地实时推送或本地子设备控制时，走用户的 Home Assistant 加 `ha_xiaomi_home`，由 `HAWebSocketProvider` 订阅实体和事件，再由 ADR-0008 去重合并。中枢网关的 mTLS 证书、MQTT broker、设备上报能力全部由官方集成处理。
3. 不内嵌 `ha_xiaomi_home` 的 OAuth2 客户端身份、CSR 换证接口或内置 CA。理由见 ADR-0014，本 ADR 不重复论证。
4. 不实现旧款网关的 LUMI 9898 私有协议，也不通过 Telnet 往任何网关写文件或装程序。前者需要网关专属 key，读写共用一个 `cmd` 通道，只读与写入的边界靠调用方自律；后者改写网关运行态，超出只读验证的范围。
5. ADR-0007 的「只读 spike」以本票交付的 `scripts/probe_lan_gateway.py` 收尾。它把 Hello 构造、应答解析、token 派生、PKCS#7 填充、只读 RPC 与 8883 握手探测做成可重跑的工具，替换针对单个 Wi-Fi 插座的手工 spike。

## 后果

- **覆盖面不缩水**。免网关用户继续靠云直连管好所有子设备，只是状态延迟仍是秒级轮询。
- **本地能力有明确归属**。想要毫秒级推送与本地子设备控制的用户，只有 Home Assistant 一条路，产品文档可以直接这么写，不再留「以后可能支持」的模糊空间。
- **安全面收窄**。仓库里不存在任何能对设备发写指令的局域网代码路径。探测工具的方法白名单写死在 `ALLOWED_READONLY_METHODS`，写方法在构造报文前抛 `PermissionError`。
- **已知代价**。中枢网关的 `getDevList` 在固件初始化阶段可能超时，这是上游已知问题（`XiaoMi/ha_xiaomi_home` issue #1791），部署门槛仍留在 Home Assistant 一侧，不转嫁给本应用。
- **型号表需要修正**。#21 原文的 `xiaomi.gateway.lm001` 查无此型号，后续引用统一按 `xiaomi.gateway.hub1` 与 `lumi.gateway.mgl03` 表述。若确有 `lm001` 硬件，以真机 miot-spec 型号串为准重新核对。

## 只读探测工具怎么用

在网关所在的局域网里执行。脚本只用 Python 3 标准库，不装依赖。

```bash
python3 scripts/probe_lan_gateway.py --ip 192.168.1.10
python3 scripts/probe_lan_gateway.py --ip 192.168.1.10 --json
python3 scripts/probe_lan_gateway.py --ip 192.168.1.10 --token <32 位十六进制 token> --method miIO.info
```

读法：

- `udp hello: answered` 表示 54321 有 miIO 设备应答，`did` 与校验状态见下一段。`closed` 表示端口无监听，`timeout` 表示对端不响应。
- `checksum` 为 `md5(header+token) verified` 时才说明 token 与该设备配对。没有 token 时只能看到 `md5(header) only` 或 `unverified`。
- `tls port 8883: client_cert_required` 就是中枢网关的典型应答：端口开着，握手要求客户端证书。这是预期结果，不是故障。
- `read` 段只在传了 token 时出现，方法限定为 `miIO.info`、`get_properties`、`get_device_prop`、`get_device_list` 四个只读方法。

输出自动脱敏。IP 只留前两段，did 与 token 只给 sha256 指纹，属性值里的 IP、MAC、SSID 与凭据类键一律打码。没有关闭脱敏的开关，避免把探测结果贴到公开 issue 时带出家庭网络信息。

退出码 0 表示探测到 UDP 应答或 8883 有 TLS 监听，1 表示两项都没探到，2 表示参数错误。

回归测试：

```bash
python3 -m unittest discover -s scripts -p 'test_probe_lan_gateway.py'
```

覆盖 FIPS-197 与 NIST SP 800-38A 的 AES 向量、Hello 报文与校验、token 派生的固定向量、PKCS#7 边界、写方法拦截、脱敏输出，以及 127.0.0.1 上的假设备端到端读写与 TLS 端口分类。

## 重开条件

只有以下情况之一出现才重新评估：

1. 小米开放第三方应用注册 MIoT OAuth2 `client_id`，并且允许第三方调用中枢网关证书签发接口。
2. 小米公开中枢网关的局域网 broker 接入规范，包括客户端证书的签发方式与 CA 信任链。
3. `ha_xiaomi_home` 提供无需 OAuth2 身份的局域网桥接方式，或公开可复用的本地证书流程。

## 相关

- ADR-0007（局域网直连暂缓）、ADR-0014（不迁往私有 OAuth2 与 MQTT 通道）、ADR-0008（双源去重合并）、ADR-0001（Home Assistant 作为单一事实来源）。
- `scripts/probe_lan_gateway.py`（只读探测工具）、`scripts/test_probe_lan_gateway.py`（回归测试）。
- 一手来源：[`XiaoMi/ha_xiaomi_home`](https://github.com/XiaoMi/ha_xiaomi_home) 的 `miot/miot_lan.py`、`miot/miot_mips.py`、`miot/miot_mdns.py`、`miot/miot_cloud.py`、`miot/miot_storage.py`、`miot/const.py` 与该仓库 wiki 的 [Central hub gateway device models](https://github.com/XiaoMi/ha_xiaomi_home/wiki/Central-hub-gateway-device-models)；[`python-miio`](https://github.com/rytilahti/python-miio) 的 `miio/miioprotocol.py` 与 `miio/protocol.py`；[Aqara LUMI Gateway LAN Communication API](https://aqara.gitbooks.io/lumi-gateway-lan-communication-api/content/) 与 [Home Assistant `xiaomi_aqara` 支持的网关型号](https://www.home-assistant.io/integrations/xiaomi_aqara/)；多模网关的侵入式本地通道见 [`AlexxIT/XiaomiGateway3`](https://github.com/AlexxIT/XiaomiGateway3) 与 [`AlexxIT/openmiio_agent`](https://github.com/AlexxIT/openmiio_agent) 的安装说明；[`lumi.gateway.mgl03` 的 MIoT 规格](https://home.miot-spec.com/spec/lumi.gateway.mgl03) 与 [`xiaomi.gateway.hub1` 的 MIoT 规格](https://home.miot-spec.com/spec/xiaomi.gateway.hub1)。
- #10（云端会话轮询猫眼事件真机探测）、#22（OAuth2 与 MQTT 通道评估）、#308（Home Assistant 门铃事件自动弹窗）。
