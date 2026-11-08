import { installWindow } from "./dom.js";
import assert from "node:assert/strict";
import { afterEach, beforeEach, describe, it } from "node:test";
import { setImmediate } from "node:timers/promises";
import { readFileSync } from "node:fs";
import { Window } from "happy-dom";
import { ScheduledCommandStore, SCHEDULED_COMMANDS_STORAGE_KEY } from "../src/core/scheduledCommands.js";
import type { DeviceIntent, ScheduledCommand } from "../src/core/models.js";
import { MenuMijiaWindowsApp } from "../src/ui/app.js";
import { processScheduledCommands, scheduledCommandIdentity, scheduledCommandsReady } from "../src/ui/scheduledCommands.js";
import { HomeAssistantSynthesizer, homeAssistantIdentity } from "../src/core/homeAssistant.js";

const now = 1_800_000_000_000;
function timer(id = "timer", fireTimestampMs = now + 10_000): ScheduledCommand {
  return {
    id, command: "关闭书房台灯", reply: "关闭书房台灯", delaySeconds: 10,
    fireTimestampMs, intents: [{ targetId: "light.study_desk", operation: { kind: "power", isOn: false } }],
  };
}
function storage() {
  const values = new Map<string, string>();
  return { values, getItem: (key: string) => values.get(key) ?? null, setItem: (key: string, value: string) => { values.set(key, value); } };
}

describe("Scheduled command persistence", () => {
  it("restores future timers and moves every expired timer to missed even inside the runtime grace", () => {
    const disk = storage();
    const first = new ScheduledCommandStore(disk, now);
    first.queue("demo").scheduledCommands = [timer("future"), timer("expired", now - 1), timer("equal", now)];
    first.queue("demo").missedScheduledCommands = [timer("already-missed")];
    assert.equal(first.persist(), true);
    const restored = new ScheduledCommandStore(disk, now);
    assert.deepEqual(restored.queue("demo").scheduledCommands.map((item) => item.id), ["future"]);
    assert.deepEqual(restored.queue("demo").missedScheduledCommands.map((item) => item.id), ["already-missed", "expired", "equal"]);
    assert.deepEqual(restored.takeDue("demo", true, now), []);
    assert.deepEqual(new ScheduledCommandStore(disk, now).queue("demo"), restored.queue("demo"));
  });

  it("takes a due task once, persists removal before execution, and holds sleep-late or unready tasks", () => {
    const disk = storage();
    const queue = new ScheduledCommandStore(disk, now);
    queue.queue("demo").scheduledCommands = [timer("normal"), timer("sleep", now - 60_001)];
    queue.queue("other-account").scheduledCommands = [timer("other", now)];
    assert.equal(queue.persist(), true);
    assert.deepEqual(queue.takeDue("demo", true, now + 10_000)?.map((item) => item.id), ["normal"]);
    assert.deepEqual(queue.takeDue("demo", true, now + 10_000), []);
    const restored = new ScheduledCommandStore(disk, now + 10_000);
    assert.deepEqual(restored.queue("demo").missedScheduledCommands.map((item) => item.id), ["sleep"]);
    assert.deepEqual(restored.queue("other-account").missedScheduledCommands.map((item) => item.id), ["other"]);
    queue.queue("demo").scheduledCommands.push(timer("not-ready", now));
    assert.deepEqual(queue.takeDue("demo", false, now), []);
    assert.equal(queue.queue("demo").missedScheduledCommands.at(-1)?.id, "not-ready");
  });

  it("does not execute if its removal cannot be persisted", () => {
    const disk = storage();
    const queue = new ScheduledCommandStore(disk, now);
    queue.queue("demo").scheduledCommands.push(timer());
    queue.persist();
    disk.setItem = () => { throw new Error("disk unavailable"); };
    assert.equal(queue.takeDue("demo", true, now + 10_000), null);
    assert.equal(queue.queue("demo").missedScheduledCommands.length, 1);
  });

  it("never writes after a storage read failure and preserves consecutive unreadable originals", () => {
    let writes = 0;
    const unavailable = new ScheduledCommandStore({
      getItem: () => { throw new Error("unreadable"); },
      setItem: () => { writes += 1; },
    }, now);
    unavailable.queue("demo").scheduledCommands.push(timer());
    assert.equal(unavailable.persist(), false);
    assert.equal(writes, 0);
    const disk = storage();
    disk.setItem(SCHEDULED_COMMANDS_STORAGE_KEY, "first broken original");
    assert.equal(new ScheduledCommandStore(disk, now).persist(), true);
    disk.setItem(SCHEDULED_COMMANDS_STORAGE_KEY, "second broken original");
    assert.equal(new ScheduledCommandStore(disk, now).persist(), true);
    assert.ok([...disk.values.values()].includes("first broken original"));
    assert.ok([...disk.values.values()].includes("second broken original"));
  });

  for (const raw of ["broken-json", JSON.stringify({ version: 99, sources: [] }), JSON.stringify({ version: 1, sources: [{ identity: "demo", scheduledCommands: [{ ...timer(), id: '\"><img src=x>' }], missedScheduledCommands: [] }] }), JSON.stringify({ version: 1, sources: [{ identity: "demo", scheduledCommands: [{ ...timer(), intents: undefined }], missedScheduledCommands: [] }] }), JSON.stringify({ version: 1, sources: [{ identity: "demo", scheduledCommands: [{ ...timer(), intents: [{ targetId: "id", operation: { kind: "power", isOn: "false" } }] }], missedScheduledCommands: [] }] })]) {
    it(`preserves unreadable data and runs no timers for ${raw.slice(0, 36)}`, () => {
      const disk = storage();
      disk.setItem(SCHEDULED_COMMANDS_STORAGE_KEY, raw);
      const queue = new ScheduledCommandStore(disk, now);
      assert.deepEqual(queue.takeDue("demo", true, now + 100_000), []);
      assert.equal(disk.getItem(SCHEDULED_COMMANDS_STORAGE_KEY), raw);
      queue.queue("demo").scheduledCommands.push(timer());
      assert.equal(queue.persist(), true);
      assert.equal(disk.getItem(`${SCHEDULED_COMMANDS_STORAGE_KEY}.unreadable`), raw);
      assert.equal(new ScheduledCommandStore(disk, now).queue("demo").scheduledCommands.length, 1);
    });
  }
});

let dom: Window;
const windows: Window[] = [];
beforeEach(() => {
  dom = new Window({ url: "http://localhost/" });
  windows.push(dom);
  installWindow(dom);
  Object.defineProperty(dom.navigator, "languages", { value: ["zh-CN"], configurable: true });
  document.body.innerHTML = '<div id="app"></div>';
  // Timer ticks are driven by processScheduledCommands with an explicit clock.
  Object.defineProperty(dom, "setInterval", { value: () => 0, configurable: true });
  localStorage.setItem("menumijia.windows.settings.v2", JSON.stringify({ connectionMode: "demo", hasCompletedOnboarding: true, enableGrokBall: false, autoCheckUpdates: false }));
});
afterEach(async () => {
  for (const instance of windows.splice(0)) {
    await instance.happyDOM.abort();
    instance.close();
  }
});
function app(): MenuMijiaWindowsApp {
  const root = document.querySelector<HTMLElement>("#app");
  assert.ok(root);
  return new MenuMijiaWindowsApp(root);
}
async function restart(): Promise<MenuMijiaWindowsApp> {
  const saved = Array.from({ length: localStorage.length }, (_, index) => localStorage.key(index)).flatMap((key) => key ? [[key, localStorage.getItem(key) ?? ""]] : []);
  await dom.happyDOM.abort();
  dom.close();
  dom = new Window({ url: "http://localhost/" });
  windows.push(dom);
  installWindow(dom);
  Object.defineProperty(dom.navigator, "languages", { value: ["zh-CN"], configurable: true });
  document.body.innerHTML = '<div id="app"></div>';
  Object.defineProperty(dom, "setInterval", { value: () => 0, configurable: true });
  for (const [key, value] of saved) if (key !== undefined && value !== undefined) localStorage.setItem(key, value);
  const instance = app();
  await setImmediate();
  return instance;
}
function click(selector: string): void {
  const control = document.querySelector<HTMLElement>(selector);
  assert.ok(control, selector);
  control.click();
}
function fakeExecutor(instance: MenuMijiaWindowsApp): DeviceIntent[][] {
  const calls: DeviceIntent[][] = [];
  instance.executeIntents = async (intents) => { calls.push([...intents]); return true; };
  return calls;
}

describe("Timer app wiring", () => {
  it("rebuilds the app with future timers and executes the saved intents exactly once", async () => {
    let instance = app();
    await instance.handleNaturalCommand("10分钟后关闭书房台灯");
    const expected = structuredClone(instance.scheduledCommands[0]);
    assert.ok(expected);
    instance = await restart();
    instance.settings.learnedShortcuts = [{ phrase: expected.command, reply: "different", learnedAt: "now", intents: [{ targetId: "light.living_ceiling", operation: { kind: "power", isOn: true } }] }];
    const calls = fakeExecutor(instance);
    assert.deepEqual(instance.scheduledCommands, [expected]);
    processScheduledCommands(instance, expected.fireTimestampMs);
    processScheduledCommands(instance, expected.fireTimestampMs);
    assert.deepEqual(calls, [expected.intents]);
    instance = await restart();
    assert.equal(instance.scheduledCommands.length + instance.missedScheduledCommands.length, 0);
  });

  for (const action of ["run", "dismiss", "cancel", "cancel-all"]) {
    it(`persists ${action} through app recreation`, async () => {
      let instance = app();
      await instance.handleNaturalCommand("10分钟后关闭书房台灯");
      const task = instance.scheduledCommands[0];
      assert.ok(task);
      const calls = fakeExecutor(instance);
      if (action === "run" || action === "dismiss") {
        processScheduledCommands(instance, task.fireTimestampMs + 60_001);
        instance.render();
        click(action === "run" ? "[data-run-missed]" : "[data-dismiss-missed]");
      } else if (action === "cancel") click("[data-cancel-timer]");
      else await instance.handleNaturalCommand("取消定时");
      assert.equal(calls.length, action === "run" ? 1 : 0);
      instance = await restart();
      assert.equal(instance.scheduledCommands.length + instance.missedScheduledCommands.length, 0);
    });
  }

  it("keeps overdue timers awaiting confirmation across repeated restarts", async () => {
    let instance = app();
    await instance.handleNaturalCommand("10分钟后关闭书房台灯");
    const task = instance.scheduledCommands[0];
    assert.ok(task);
    instance.scheduledCommands = [{ ...task, fireTimestampMs: Date.now() - 1 }];
    instance.scheduledCommandStore.persist();
    instance = await restart();
    assert.equal(instance.scheduledCommands.length, 0);
    assert.equal(instance.missedScheduledCommands.length, 1);
    assert.ok(document.querySelector("[data-run-missed]"));
    instance = await restart();
    assert.equal(instance.missedScheduledCommands.length, 1);
  });

  it("isolates demo, backend, endpoint, HA credential digest and Xiaomi account/region", async () => {
    const instance = app();
    await instance.handleNaturalCommand("10分钟后关闭书房台灯");
    const original = instance.scheduledCommands[0];
    assert.ok(original);
    instance.settings.connectionMode = "homeAssistant";
    instance.settings.haBaseURL = "http://localhost:8123";
    instance.settings.haToken = "synthetic-account-a";
    const a = scheduledCommandIdentity(instance.settings);
    assert.ok(a);
    assert.doesNotMatch(a, /synthetic-account-a/);
    instance.scheduledCommands.push({ ...original, id: "ha-a" });
    instance.scheduledCommandStore.persist();
    instance.settings.haToken = "synthetic-account-b";
    assert.notEqual(scheduledCommandIdentity(instance.settings), a);
    assert.equal(instance.scheduledCommands.length, 0);
    instance.settings.haToken = "synthetic-account-a";
    instance.settings.haBaseURL = "http://localhost:8124";
    assert.equal(instance.scheduledCommands.length, 0);
    instance.settings.connectionMode = "xiaomiCloud";
    instance.settings.miSession = { userId: "synthetic-a", cUserId: "", ssecurity: "", serviceToken: "", passToken: "", region: "cn" };
    const mi = scheduledCommandIdentity(instance.settings);
    instance.settings.miSession = { ...instance.settings.miSession, userId: "synthetic-b" };
    assert.notEqual(scheduledCommandIdentity(instance.settings), mi);
    instance.settings.miSession = { ...instance.settings.miSession, userId: "synthetic-a", region: "us" };
    assert.notEqual(scheduledCommandIdentity(instance.settings), mi);
    const calls = fakeExecutor(instance);
    processScheduledCommands(instance, original.fireTimestampMs);
    assert.equal(calls.length, 0);
    instance.settings.connectionMode = "demo";
    assert.equal(instance.missedScheduledCommands.length, 1);
  });

  it("waits for credential hydration and a successful matching HA snapshot, including after source invalidation", async () => {
    const snapshot: unknown = JSON.parse(readFileSync("../Tests/Fixtures/ha_snapshot.json", "utf8"));
    let releaseCredentials: (value: string) => void = () => {};
    let releaseSnapshot: (value: unknown) => void = () => {};
    const credentials = new Promise<string>((resolve) => { releaseCredentials = resolve; });
    const home = new Promise<unknown>((resolve) => { releaseSnapshot = resolve; });
    let snapshots = 0;
    const invoke = async (command: string, args: Record<string, unknown> = {}) => {
      if (command === "secure_store_get") return args.name === "ha_token" ? credentials : null;
      if (command === "ha_snapshot") { snapshots += 1; return home; }
      return undefined;
    };
    Object.defineProperty(window, "__TAURI_INTERNALS__", { value: { invoke }, configurable: true });
    const identity = homeAssistantIdentity("http://localhost:8123", "synthetic-token");
    localStorage.setItem("menumijia.windows.settings.v2", JSON.stringify({ connectionMode: "homeAssistant", haBaseURL: "http://localhost:8123", hasCompletedOnboarding: true, autoCheckUpdates: false, enableGrokBall: false, cachedHAHome: { identity, home: HomeAssistantSynthesizer.synthesize(snapshot) } }));
    const instance = app();
    const calls = fakeExecutor(instance);
    assert.equal(scheduledCommandsReady(instance), false);
    releaseCredentials("synthetic-token");
    await setImmediate();
    assert.equal(snapshots, 1);
    assert.equal(scheduledCommandsReady(instance), false, "a cached home is not a successful snapshot");
    const owned = scheduledCommandIdentity(instance.settings);
    assert.ok(owned);
    const makeTask = (id: string): ScheduledCommand => ({
      ...timer(id, Date.now()),
      intents: [{ targetId: "study-light", operation: { kind: "power", isOn: false } }],
    });
    instance.scheduledCommands.push(makeTask("due-before-snapshot"));
    instance.scheduledCommandStore.persist();
    processScheduledCommands(instance);
    assert.equal(calls.length, 0);
    assert.equal(instance.missedScheduledCommands.length, 1);
    releaseSnapshot(snapshot);
    await setImmediate();
    assert.equal(scheduledCommandsReady(instance), true);
    instance.scheduledCommands.push(makeTask("after-snapshot"));
    processScheduledCommands(instance);
    assert.deepEqual(calls, [[{ targetId: "study-light", operation: { kind: "power", isOn: false } }]]);
    instance.invalidateSourceControl();
    assert.equal(scheduledCommandsReady(instance), false);
    await instance.refreshHADevices();
    assert.equal(scheduledCommandsReady(instance), true);
    assert.equal(snapshots, 2);
  });

  it("shows a failed durable removal and lets the user retry safely after storage recovers", async () => {
    let instance = app();
    await instance.handleNaturalCommand("10分钟后关闭书房台灯");
    const task = instance.scheduledCommands[0];
    assert.ok(task);
    const calls = fakeExecutor(instance);
    const setItem = localStorage.setItem.bind(localStorage);
    Object.defineProperty(localStorage, "setItem", { configurable: true, value: () => { throw new Error("unavailable"); } });
    processScheduledCommands(instance, task.fireTimestampMs);
    assert.equal(calls.length, 0);
    assert.match(document.body.textContent ?? "", /定时指令无法保存/);
    assert.equal(instance.missedScheduledCommands.length, 1);
    Object.defineProperty(localStorage, "setItem", { configurable: true, value: setItem });
    click("[data-run-missed]");
    assert.equal(calls.length, 1);
    instance = await restart();
    assert.equal(instance.scheduledCommands.length + instance.missedScheduledCommands.length, 0);
  });

  it("resolves an AI timer when it is created and never consults AI again after restart", async () => {
    let modelCalls = 0;
    const invoke = async (name: string) => {
      if (name !== "native_http") return undefined;
      modelCalls += 1;
      const content = JSON.stringify({ actions: [{ device: "d3", op: "power", value: false }], reply: "已关闭书房台灯" });
      return { status: 200, headers: {}, body: JSON.stringify({ choices: [{ message: { content } }] }) };
    };
    Object.defineProperty(window, "__TAURI_INTERNALS__", { value: { invoke }, configurable: true });
    let instance = app();
    instance.settings.assistantBaseURL = "http://localhost:1234/v1";
    instance.settings.assistantModel = "controlled-model";
    const initialCalls = fakeExecutor(instance);
    await instance.handleNaturalCommand("10分钟后帮我让书桌适合看星星");
    assert.equal(initialCalls.length, 0);
    assert.equal(modelCalls, 1);
    assert.equal(instance.scheduledCommands.length, 1);
    const task = instance.scheduledCommands[0];
    assert.ok(task?.intents?.length);
    instance = await restart();
    const restoredCalls = fakeExecutor(instance);
    processScheduledCommands(instance, task.fireTimestampMs);
    assert.deepEqual(restoredCalls, [task.intents]);
    assert.equal(modelCalls, 1);
  });

  it("never turns an uncertain model timer into an immediate suggestion button", async () => {
    const invoke = async (name: string) => {
      if (name !== "native_http") return undefined;
      const answers = { target: { choice: "d3", confidence: 0.9 }, op: { choice: "power_off", confidence: 0.55 }, temp: { choice: "none", confidence: 0.99 } };
      return { status: 200, headers: {}, body: JSON.stringify({ answers }) };
    };
    Object.defineProperty(window, "__TAURI_INTERNALS__", { value: { invoke }, configurable: true });
    const instance = app();
    instance.settings.assistantBaseURL = "http://localhost:1234/v1";
    instance.settings.assistantModel = "~typesafe/jev-latest";
    const calls = fakeExecutor(instance);
    await instance.handleNaturalCommand("10分钟后帮我让书桌适合看星星");
    assert.equal(calls.length, 0);
    assert.equal(instance.scheduledCommands.length, 0);
    assert.equal(instance.pendingSuggestions.length, 0);
    assert.equal(document.querySelector("[data-suggestion-id]"), null);
    assert.match(document.body.textContent ?? "", /定时指令需要明确的设备和操作，未设定定时/);
  });

  it("retains the delay through cloud consent instead of executing the continuation immediately", async () => {
    let modelCalls = 0;
    const invoke = async (name: string) => {
      if (name !== "native_http") return undefined;
      modelCalls += 1;
      const content = JSON.stringify({ actions: [{ device: "d3", op: "power", value: false }], reply: "已关闭书房台灯" });
      return { status: 200, headers: {}, body: JSON.stringify({ choices: [{ message: { content } }] }) };
    };
    Object.defineProperty(window, "__TAURI_INTERNALS__", { value: { invoke }, configurable: true });
    const instance = app();
    instance.settings.assistantBaseURL = "https://api.deepseek.com/v1";
    instance.settings.assistantModel = "controlled-model";
    instance.settings.openRouterApiKey = "synthetic-test-key";
    instance.settings.assistantKeyEndpoint = "api.deepseek.com";
    const calls = fakeExecutor(instance);
    await instance.handleNaturalCommand("10分钟后帮我让书桌适合看星星");
    assert.equal(modelCalls, 0);
    assert.equal(instance.pendingCloudConsent?.utterance, "10分钟后帮我让书桌适合看星星");
    click("#btn-cloud-allow");
    await setImmediate();
    assert.equal(modelCalls, 1);
    assert.equal(calls.length, 0);
    assert.equal(instance.scheduledCommands.length, 1);
    assert.equal(instance.scheduledCommands[0]?.delaySeconds, 600);
  });

  it("never reinterprets an unresolved legacy timer with AI", async () => {
    const instance = app();
    const calls = fakeExecutor(instance);
    await instance.runScheduledCommand({ ...timer(), intents: undefined });
    assert.equal(calls.length, 0);
    assert.equal(instance.pendingCloudConsent, null);
  });
});
