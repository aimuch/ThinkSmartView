# 0003. 核心库保持可在 Linux 上编译

状态：**已修订**（2026-10-27，审计 #40）。原文曾把「将来做 Windows/Linux 版时可直接复用设备合成、意图解析、习惯记忆」列为保留此约束的理由之一；该承诺已从本文删除，因为交付的 Windows 客户端是独立的 TypeScript 实现。双端分工与一致性约束见 [ADR-0006](0006-dual-platform-implementation.md)。

`MijiaCore` 只依赖 Foundation，外加少量苹果专属模块：`os.Logger`、`Security`（钥匙串）、`CryptoKit`。`scripts/linux-check/` 用很薄的替身（Logger 打印、内存钥匙串、swift-crypto）替换这几样，就能在 Linux 上编译并跑完全部单元测试，业务代码一行不改。我们决定把这一点当作约束保留下来：新代码进 `MijiaCore` 时不要引入 AppKit、SwiftUI 或其他苹果专属框架，平台相关的东西放在 App 层或用协议隔开。

这样做的好处只有一个：**核心逻辑可以脱离 macOS 验证**——CI 会跑 Linux 核心任务，没有 Mac 的贡献者也能跑合成、解析、定时与撤销的单元测试，并且核心层被强制保持平台无关。代价是替身需要跟着这些模块的用法同步维护，CI 的 Linux 任务会在它们失效时报错。

## 修订记录

- 2026-10-27：删除「为将来 Windows/Linux 版复用核心」的理由与承诺。Windows 版（`windows/`，Tauri v2 + TypeScript）重写了米家协议、加解密、设备合成、意图解析与 AI 请求，`MijiaCore` 没有被复用。一致性改由 `Tests/Fixtures/` 的共享语料 + 两端 CI 对比保证，见 ADR-0006。
