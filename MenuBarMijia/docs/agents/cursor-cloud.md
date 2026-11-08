## Cursor Cloud specific instructions

Cloud agents are Linux. `scripts/build_app.sh`, `scripts/install_local_app.sh`, and a repo-root `swift test` need the macOS SDK (CI uses macos-26). The Windows Tauri installer (`cargo test`, `npx tauri build`) stays on Windows CI.

On Linux, `bash scripts/check.sh --skip-snapshots` (the pre-commit hook) runs `scripts/linux-check/run.sh` instead of `swift test`. That is the same MijiaCore suite as CI's `linux-core` job (Swift 6.1). It still runs the Windows frontend tests when `windows/node_modules` is present.

Other Linux checks:

- `bash scripts/check.sh --lint-only` — secret scan and mechanical lints only.
- `bash scripts/test_nas_setup.sh` — NAS packaging safety. It stubs network commands; no NAS host is required.
- `npm ci --prefix windows`, then `npm test --prefix windows` and `npm run build --prefix windows` — Windows frontend (Node 22). `npm run build` is not part of the pre-commit hook.

`npm run dev --prefix windows` serves the tray UI at port 1420 (`strictPort`). The dev server is started on boot. First launch shows onboarding; “Preview the demo” / “先看演示预览” opens the offline demo home, and clicking a device card toggles it. Mi Cloud QR login and Home Assistant need a real account and are not part of this setup.

Swift's clang selects the newest GCC. This image has GCC 14, so `libstdc++-14-dev` must be installed or `swift-crypto` fails with `cstdio` file not found. The environment install script installs Swift 6.1.3 and that package. No secrets are required for the checks above.
