# ADR-0011：全设备覆盖，按控制形状建模，解析阶梯永远有落点，型号覆盖层数据化并双端共享

日期：2026-11-05　状态：提议（待维护者审阅，对应 #297，总括单 #188）

## 背景

米家直连路线（ADR-0002）的合成器按品类工作：`MiotSynthesizer.primary(for:)`（Windows：`primaryMiotService`）先用设备类型名找同名服务，找不到就按注册表顺序让任意一个已知服务认领，再不行就丢弃设备。

[调研](../research/2026-11-05-full-device-coverage.md)对 miot-spec.org 全部 14,927 个已发布型号、177 个设备类型按这套逻辑重放。结果是约 68% 的型号类型对得上并有卡片，约 24%（≈3,500 个型号）被静默丢弃但规格里有可控项，约 2.8% 被误判成别的设备类型（驱虫器画成扫地机，投影仪画成音箱）。这些数字是每类至多抽 6 份规格外推出来的，只代表数量级。#295 把同样的分类做成了双端测试基线（`Tests/Fixtures/miot_coverage_golden.json`），以后用它衡量。

丢弃有三个原因，各自需要一个决定：

1. 品类建模补不完。有丢弃的类型有 134 个，前 20 个只占丢弃量的八成。
2. 解析只有一级。类型名没对上，设备要么被别的服务错误认领，要么消失。
3. 型号怪癖写在代码里，而且要写两份。每加一个型号特例，就要改 Swift 和 TypeScript 两份上千行的合成器，ADR-0006 承认的双端漂移成本也跟着涨。#148、#154 都是 Windows 端规格映射写错。

## 备选方案

1. **继续按品类补 builder。** 每个品类一个手写卡片，精致度最高。134 个类型补不完，长尾永远是空白。误判问题也不会消失，因为回退认领还在。
2. **只做通用兜底。** 所有属性按"格式 + 读写"自动生成控件（官方 `ha_xiaomi_home` 的属性级兜底）。覆盖面最大，但洗衣机会变成 14 个平铺的开关和滑块，不如不显示。写操作的安全也无从把关。
3. **形状 + 阶梯 + 通用控件 + 数据化覆盖层。** 头部类型按少数几种"控制形状"建模，长尾靠通用控件，型号特例进一份双端共享的数据文件。参考了 homebridge-miot 的解析阶梯和 al-one 的型号通配符覆盖（只参考做法，独立实现，不复制代码）。

## 决定

选择方案 3。

### 1. 按控制形状建模，不按品类

一个形状由"能力谓词 + 渲染"组成。规格命中哪个谓词，就用哪种卡片。

| 形状 | 谓词（概要） | 头部类型 |
| --- | --- | --- |
| 电机 Cover | 可写 `motor-control` 含开/关语义，或可写范围型 `target-position` | 窗帘、晾衣架、电机控制器、开窗器 |
| 家电 Appliance | `mode`、`status`、`fault`、`target-temperature`、`left-time`、无参起停动作中至少两项，`on` 可选 | 洗衣机、烘干机、洗碗机、热水器、浴霸、油烟机 |
| 读数 Readout | 没有可写控件，有可读数值或枚举 | 净水器、体重秤、路由器、网关 |
| 动作 Actions | 以无参动作为主，几乎没有属性 | 遥控器、电视、机顶盒 |
| 复合 Composite | 一个设备命中多个形状：主卡片取最强形状，其余作为抽屉内控件 | 鱼缸、喂食器、浴霸 |
| 通用 Generic | 以上都不命中，但有可控面 | 长尾 |

现有的 `light / climate / fan / plug / vacuum / speaker / camera / sensor / fridge` builder 不动。它们是精致的类型层。形状谓词只是把它们现在的触发条件写明白。

### 2. 解析阶梯，永远有落点

```
1. 型号覆盖        共享数据按型号或通配符命中，直接给出形状、绑定、排除与怪癖
2. 类型 + 兼容表   设备类型名找首选服务；回退认领必须查兼容表
3. 形状检测        在整份规格里找形状谓词
4. 通用控件        剩余属性按"格式 + 读写"生成控件，过滤噪声
5. 兜底卡片        没有可用属性：显示"该设备没有向云端开放可控项"和型号，供一键反馈
```

第 2 级的兼容表替代现在"任何已知服务都能认领"的回退，这是修误判（#296）的方式。不在兼容表里的回退一律不认领，交给第 3 级。第 5 级让"未显示设备"只剩真正没有规格的设备。

### 3. 通用控件规则

采用官方集成已验证的"格式 + 读写"归类：

| 规格 | 控件 |
| --- | --- |
| 可写 `bool` | 开关 |
| 可写且有 `value-list` | 下拉 |
| 可写且有 `value-range` | 滑块（步长取规格值） |
| 可写，但既无枚举也无范围 | 跳过 |
| 只读数值、枚举或布尔 | 读数 |
| 无参动作 | 按钮 |
| 有参动作 | 参数全是枚举或范围才生成，否则跳过 |
| 字符串属性 | 跳过（多为 JSON、时间段串、内部配置） |

再补上官方没做的过滤层：

- 服务级：`device-information`、`exception-card`、`device-custom` 和所有 `*-custom`。#295 的基线测试已经用这一组定义"可用属性"。
- 属性级：`bind-timestamp`、`trigger-status`、`*-list`（JSON 串）、内部时间戳。
- 展示级：默认只露出主卡片和不超过 N 个高价值控件，其余折叠进"更多设置"。N 和"高价值"名单放进数据文件，不写在代码里。

写操作的安全规则不变。通用控件默认只开放低风险名单（亮度、模式、风速一类）。高风险写入沿用 `ModelActionPolicy` 与 `Tests/Fixtures/confirmation_golden.json` 的确认表，本 ADR 不新增确认规则。

### 4. 型号覆盖层数据化，双端共享

新增一份共享配置 `deviceProfiles.json`，放法照搬 `fallbackSpecs.json`：macOS 打进 `Sources/MijiaCore/Resources/`，Windows 放在 `windows/src/core/miCloud/`，由测试断言两份逐字节相同（同 `MiotFallbackSpecTests.sameBytesAsWindows`）。macOS 与 Windows 各写一个薄解释器，不写型号特例代码。一致性由 `miot_golden.json` 的用例和 #295 的覆盖率基线守住。

字段草案：

```jsonc
{
  "version": 1,
  // 设备类型 → 形状与怪癖。没有条目的类型走第 3 级形状检测。
  "types": {
    "curtain": { "shape": "cover" },
    "airer":   { "shape": "cover", "quirks": { "positionReversed": true, "labels": { "open": "升", "close": "降" } } }
  },
  // 第 2 级：设备类型允许被哪些服务回退认领。不在表里就不认领。
  "compat": {
    "air-condition-outlet": ["air-conditioner"],
    "door": ["lock", "video-doorbell"]
  },
  // 第 1 级：型号或通配符覆盖。按声明顺序匹配，第一条命中生效。
  "models": [
    { "match": "*.fishbowl.*", "shape": "composite", "primaryService": "fish-tank",
      "highlight": ["water-pump", "pump-flux", "temperature"] },
    { "match": "090615.curtain.*", "exclude": ["motor-control"] }
  ],
  // 第 4 级的过滤层与展示上限。
  "noise": {
    "services": ["device-information", "exception-card", "device-custom", "*-custom"],
    "properties": ["bind-timestamp", "trigger-status", "*-list"]
  },
  "facets": { "maxVisible": 4, "highValue": ["mode", "target-*", "*-level", "brightness"], "lowRiskWrites": ["brightness", "mode", "fan-level"] }
}
```

字段名用规格里的 short name（URN 第 4 段），通配符只支持 `*`。`exclude`、`highlight` 引用属性或服务的 short name，不引用 iid，这样同一份配置能覆盖同族的多个型号。

### 5. 与 ADR-0006 的关系

不推翻 ADR-0006。双端仍各自实现合成器和卡片。本 ADR 只把"每个型号怪癖改两份大文件"降成"改一份数据"，所以 ADR-0006 记下的漂移成本只剩解释器本身。

能力可以分阶段落地。macOS 先实现形状和通用控件（#298 到 #308），Windows 由 #310 跟进。按 ADR-0006 的要求，先落一端的能力必须在 README「功能概览与双端对照」里标注。双端的覆盖率差异记在 `miot_coverage_golden.json` 的 `windowsDivergences`，例如 #314。

### 6. 与 #185 的分工

#185 要两件事，现在分开处理：

- **规格映射数据驱动**并入本 ADR。`deviceProfiles.json` 就是它的具体形式。`fallbackSpecs.json`（双端已共享，#231）保留为独立文件，因为它的输入不同：它在拿不到规格时按型号和名称推断一份规格，`deviceProfiles.json` 处理拿到规格之后的认领与展示。
- **确认规则**不并入。确认表已经由 `confirmation_golden.json` 双端共享，本 ADR 不改它。通用控件需要的"低风险名单"放在 `deviceProfiles.json` 的 `facets.lowRiskWrites`，只决定通用控件默认开不开放，不改变已有设备的确认行为。
- **从代码推导双端能力表**仍归 #185，改为接在 #295 的覆盖率基线上：能力表的 MIoT 部分从 `miot_coverage_golden.json` 生成，不再手工维护。这是 #185 剩下的唯一一项。

## 后果

- 覆盖率从"一个品类一张单"变成"一个形状一张单"，长尾由通用控件收口。#295 的基线让每张单都能说出覆盖率变化。
- 误判先修（#296）。兼容表上线后，不在表里的回退认领消失，基线里的"误判"一类应当归零。
- 新增型号怪癖只改数据。解释器需要双端各写一次，这是 ADR-0006 已经接受的成本。
- 通用控件的易用风险：控件平铺比不显示更糟。所以"高价值名单 + 折叠"必须和规则引擎同一张单落地（#303、#304），不能只做引擎。
- 规格里可写不等于云端允许写。低风险名单里的属性需要真机确认后再放进去。
- 不做：远程开锁、云台、双向语音（与此前结论一致）；小米私有蓝牙或 ZigBee 直连（ADR-0007 仍然延期）。
