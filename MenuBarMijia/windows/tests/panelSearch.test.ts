import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { afterEach, beforeEach, describe, it } from "node:test";
import { setImmediate } from "node:timers/promises";
import { Window } from "happy-dom";
import { installWindow } from "./dom.js";
import { MenuMijiaWindowsApp } from "../src/ui/app.js";
import { panelQueryKind, PanelSearchIndex, searchEntries, searchMatches } from "../src/core/panelSearch.js";
import type { SmartDevice } from "../src/core/models.js";

const corpus = JSON.parse(readFileSync(new URL("../../Tests/Fixtures/panel_search_golden.json", import.meta.url), "utf8")) as {
  devices: { id: string; name: string; room: string }[];
  matches: { query: string; ids: string[] }[];
  kinds: { query: string; kind: string }[];
};

function light(id: string, name: string, room = "书房"): SmartDevice {
  return { id, name, roomId: room, roomName: room, model: "test.light", isOnline: true,
    kind: { kind: "light", entityId: id, isOn: false, brightnessPercent: 50, colorTempKelvin: null,
      minKelvin: 2700, maxKelvin: 6500, currentEffect: null, availableEffects: [] } };
}

describe("Cross-platform panel search corpus", () => {
  const devices = corpus.devices.map((row) => light(row.id, row.name, row.room));
  for (const row of corpus.matches) it(`matches ${row.query || "empty input"}`, () => {
    assert.deepEqual(searchMatches(row.query, searchEntries(devices)).map((entry) => entry.deviceId), row.ids);
  });
  for (const row of corpus.kinds) it(`routes ${row.query}`, () => {
    assert.equal(panelQueryKind(row.query, devices, []), row.kind);
  });
  it("matches context-sensitive pinyin, mixed Latin text and local aliases", () => {
    const devices = [light("cq", "重庆空调"), light("ch", "长虹空调"), light("music", "音乐灯"), light("mixed", "Ａqara 灯2")];
    const entries = searchEntries(devices);
    for (const [query, id] of [["cqkt", "cq"], ["chkt", "ch"], ["yyd", "music"], ["aqara d2", "mixed"], ["chongqing", "cq"]]) {
      assert.equal(searchMatches(query!, entries)[0]?.deviceId, id);
    }
    assert.equal(searchMatches("bedside", searchEntries(devices, { music: "Bedside" }))[0]?.deviceId, "music");
  });
  it("an index follows renamed aliases, moved rooms, removed devices and replacement snapshots", () => {
    const index = new PanelSearchIndex();
    assert.equal(index.matches("sfd", [light("lamp", "书房灯")], {})[0]?.deviceId, "lamp");
    const moved = light("lamp", "书房灯", "卧室");
    assert.deepEqual(index.matches("sfd", [moved], { lamp: "床头灯" }), []);
    assert.equal(index.matches("ws", [moved], { lamp: "床头灯" })[0]?.deviceId, "lamp");
    assert.deepEqual(index.matches("ctd", [], {}), []);
    assert.equal(index.matches("ydj", [light("lamp", "阅读角")], {})[0]?.deviceId, "lamp");
  });
});

let dom: Window;
let app: MenuMijiaWindowsApp;
function element<T extends HTMLElement = HTMLElement>(selector: string): T {
  const result = document.querySelector<T>(selector);
  assert.ok(result, `Missing ${selector}`);
  return result;
}
function input(text: string): HTMLInputElement {
  const field = element<HTMLInputElement>("#command-input");
  field.focus(); field.value = text;
  field.dispatchEvent(new Event("input", { bubbles: true }));
  return element<HTMLInputElement>("#command-input");
}
function key(key: string, options: Pick<KeyboardEventInit, "ctrlKey" | "isComposing" | "keyCode" | "repeat"> = {}): KeyboardEvent {
  const event = new window.KeyboardEvent("keydown", { key, bubbles: true, cancelable: true, ...options });
  (document.activeElement ?? window).dispatchEvent(event);
  return event;
}
function ids(): string[] { return [...document.querySelectorAll<HTMLElement>("[data-tile-id]")].map((tile) => tile.dataset.tileId!); }
function lightIsOn(id = "light.study_desk"): boolean {
  const device = app.devices.find((device) => device.id === id);
  assert.ok(device?.kind.kind === "light");
  return device.kind.isOn;
}

beforeEach(() => {
  dom = new Window({ url: "http://localhost/" }); installWindow(dom);
  Object.defineProperty(dom.navigator, "languages", { value: ["zh-CN"], configurable: true });
  document.body.innerHTML = '<div id="app"></div>';
  localStorage.setItem("menumijia.windows.settings.v2", JSON.stringify({ connectionMode: "demo", hasCompletedOnboarding: true }));
  app = new MenuMijiaWindowsApp(element("#app"));
});
afterEach(async () => { await dom.happyDOM.abort(); dom.close(); });

describe("Panel keyboard on the rendered demo", () => {
  it("searches across room/kind filters, honours aliases and restores the cut after Escape", () => {
    app.selectRoom("room_living", "客厅"); app.render();
    assert.ok(!ids().includes("light.study_desk"));
    input("sftd"); assert.deepEqual(ids(), ["light.study_desk"]);
    assert.equal(element("[aria-current=true]").dataset.tileId, "light.study_desk");
    key("Escape"); assert.equal(app.panelSearch.query, "");
    assert.ok(!ids().includes("light.study_desk"));
    assert.equal(app.selectedRoom, "客厅");
    app.layout.aliases["light.study_desk"] = "床头阅读灯";
    input("ctydd"); assert.deepEqual(ids(), ["light.study_desk"]);
  });
  it("Return toggles the selected match, and repeated Return does not fire twice", async () => {
    input("sftd");
    key("Enter"); await setImmediate();
    assert.equal(lightIsOn(), false);
    key("Enter", { repeat: true }); await setImmediate();
    assert.equal(lightIsOn(), false);
    assert.equal(element<HTMLInputElement>("#command-input").value, "sftd");
  });
  it("Down selects a different device and preserves that identity through refresh", () => {
    app.devices = [light("one", "书房灯"), light("two", "书房台灯")]; app.render();
    input("书房"); const before = ids(); assert.ok(before.length > 1);
    key("ArrowDown");
    assert.equal(app.panelSearch.selectedDeviceId, before[1]);
    app.devices = app.devices.map((device) => structuredClone(device)); app.render();
    assert.equal(app.panelSearch.selectedDeviceId, before[1]);
    assert.equal(document.activeElement?.id, "command-input");
    assert.equal(element<HTMLInputElement>("#command-input").value, "书房");
  });
  it("a command with a matching device name runs as an instruction unless selection is explicit", async () => {
    app.devices = [light("open", "打开书房灯小助手")]; app.render();
    const commands: string[] = [];
    app.handleNaturalCommand = async (text) => { commands.push(text); };
    input("打开书房灯到50%"); key("Enter"); await setImmediate();
    assert.deepEqual(commands, ["打开书房灯到50%"]);
    assert.equal(app.panelSearch.query, "");
    input("打开"); key("ArrowDown"); key("Enter"); await setImmediate();
    assert.deepEqual(commands, ["打开书房灯到50%"]);
    assert.equal(app.devices[0]!.kind.kind === "light" && app.devices[0]!.kind.isOn, true);
  });
  it("IME composition keeps its DOM editor through renders and never executes Enter/Escape", () => {
    const field = input(""); const commands: string[] = [];
    app.handleNaturalCommand = async (text) => { commands.push(text); };
    field.dispatchEvent(new window.CompositionEvent("compositionstart", { bubbles: true }));
    field.value = "打开书房灯";
    field.dispatchEvent(new Event("input", { bubbles: true }));
    key("Enter", { isComposing: true }); key("Escape", { keyCode: 229 });
    app.render();
    assert.equal(element("#command-input"), field);
    assert.deepEqual(commands, []);
    field.dispatchEvent(new window.CompositionEvent("compositionend", { bubbles: true }));
    assert.equal(element<HTMLInputElement>("#command-input").value, "打开书房灯");
    assert.equal(document.activeElement?.id, "command-input");
  });
  for (const transition of ["blur", "window blur", "settings", "source", "hide input"] as const) {
    it(`ends an interrupted IME composition on ${transition}`, () => {
      const field = input("书");
      field.dispatchEvent(new window.CompositionEvent("compositionstart", { bubbles: true }));
      if (transition === "blur") field.blur();
      else if (transition === "window blur") window.dispatchEvent(new Event("blur"));
      else if (transition === "settings") app.openSettingsSheet();
      else if (transition === "source") { app.sourceRevision += 1; app.render(); }
      else { app.isShowingAssistantBar = false; app.render(); }
      assert.equal(app.panelSearch.isComposing, false);
      if (transition === "settings") assert.ok(document.querySelector(".cc-sheet"));
      app.activeSheet = "none"; app.isShowingAssistantBar = true; app.render();
      input("sftd"); assert.deepEqual(ids(), ["light.study_desk"]);
    });
  }
  it("offline and stale matches cannot be toggled from Return", async () => {
    let writes = 0;
    app.executeSingleCommand = async () => { writes += 1; };
    app.devices = app.devices.map((item) => item.id === "light.study_desk" ? { ...item, isOnline: false } : item);
    input("sftd"); key("Enter"); await setImmediate();
    assert.equal(lightIsOn(), true);
    assert.equal(writes, 0);
    app.settings.hideOfflineDevices = true; app.render(); assert.deepEqual(ids(), []);
    app.devices = app.devices.map((item) => ({ ...item, isOnline: true })); app.cloudValuesStale = true;
    app.settings.connectionMode = "xiaomiCloud"; app.render(); key("Enter"); await setImmediate();
    assert.equal(lightIsOn(), true);
    assert.equal(writes, 0);
  });
  it("Right expands then adjusts through the existing detailed range control", async () => {
    input("sftd"); key("ArrowRight");
    assert.equal(app.expandedDeviceId, "light.study_desk");
    assert.notEqual(document.activeElement?.id, "command-input");
    const before = Number(element<HTMLInputElement>("[data-brightness]").value);
    key("ArrowRight"); await setImmediate();
    assert.equal(Number(element<HTMLInputElement>("[data-brightness]").value), before + 5);
  });
  it("Right never toggles a card without a drawer or steals a caret in the middle of text", async () => {
    const field = input("sftd"); field.setSelectionRange(1, 1);
    key("ArrowRight"); assert.equal(app.expandedDeviceId, null);
    input("玄关智能插座"); key("ArrowRight"); await setImmediate();
    const plug = app.devices.find((device) => device.id === "plug.entry_socket")!;
    assert.equal(plug.kind.kind === "plug" && plug.kind.isOn, true);
  });
  it("focused drawer buttons retain Space, Return and arrow keys instead of toggling the selected device", async () => {
    input("sftd"); key("ArrowRight");
    const button = element<HTMLButtonElement>('[data-effect="Night"]'); button.focus();
    const selected = app.panelSearch.selectedDeviceId;
    assert.equal(key(" ").defaultPrevented, false);
    assert.equal(key("Enter").defaultPrevented, false);
    assert.equal(key("ArrowDown").defaultPrevented, false);
    assert.equal(app.panelSearch.selectedDeviceId, selected);
    await setImmediate(); assert.equal(lightIsOn(), true);
    button.click(); await setImmediate();
    const device = app.devices.find((item) => item.id === "light.study_desk");
    assert.equal(device?.kind.kind === "light" && device.kind.currentEffect, "Night");
  });
  it("a hidden camera kept alive does not intercept search, while a visible camera does", async () => {
    app.cameraLive.activeLiveCameraId = "camera.living";
    app.cameraLive.liveCameraHidden = true;
    input("sftd"); key("Enter"); await setImmediate();
    assert.equal(lightIsOn(), false);
    app.cameraLive.liveCameraHidden = false;
    key("Enter"); await setImmediate();
    assert.equal(lightIsOn(), false);
  });
  it("no-match sentences retain the existing natural-command fallback", async () => {
    const commands: string[] = []; app.handleNaturalCommand = async (text) => { commands.push(text); };
    input("我准备睡觉了"); key("Enter"); await setImmediate();
    assert.deepEqual(commands, ["我准备睡觉了"]);
    input("书房台灯"); element("#btn-send").click(); await setImmediate();
    assert.deepEqual(commands, ["我准备睡觉了", "书房台灯"]);
  });
  it("Return on a high-risk window opener arms the same card confirmation before a second press", async () => {
    app.devices = [{ id: "opener", name: "书房开窗器", roomId: "study", roomName: "书房", model: "test.opener", isOnline: true,
      kind: { kind: "cover", entityId: "miot:opener:2", subtype: "window-opener", positionPercent: 0,
        positionWritable: true, openDescription: "Open", closeDescription: "Close", pauseDescription: "Pause",
        batteryPercent: null, faultText: null, motion: "stopped" } }];
    app.render(); input("开窗器"); key("Enter"); await setImmediate();
    assert.equal(app.devices[0]!.kind.kind === "cover" && app.devices[0]!.kind.positionPercent, 0);
    assert.equal(app.pendingHighRisk?.deviceId, "opener");
    key("Enter", { repeat: true }); await setImmediate();
    assert.equal(app.devices[0]!.kind.kind === "cover" && app.devices[0]!.kind.positionPercent, 0);
    key("Enter"); await setImmediate();
    assert.equal(app.devices[0]!.kind.kind === "cover" && app.devices[0]!.kind.positionPercent, 100);
  });
  it("Ctrl+F opens the field but keys in another text editor do not control cards", () => {
    app.isShowingAssistantBar = false; app.render(); key("f", { ctrlKey: true });
    assert.equal(document.activeElement?.id, "command-input");
    const area = document.createElement("textarea"); document.body.append(area); area.focus();
    key("ArrowDown"); key("Enter"); key("Escape");
    assert.equal(app.panelSearch.selectedDeviceId, null);
    assert.equal(document.activeElement, area);
    app.isShowingAssistantBar = false; app.render(); area.focus(); key("/");
    assert.equal(app.isShowingAssistantBar, false);
  });
  it("Escape clears search before the next Escape hides the native panel", async () => {
    const calls: string[] = [];
    Object.defineProperty(window, "__TAURI_INTERNALS__", { value: { invoke: async (command: string) => { calls.push(command); } }, configurable: true });
    input("书房"); key("Escape"); assert.ok(!calls.includes("hide_panel"));
    key("Escape"); await setImmediate(); assert.ok(calls.includes("hide_panel"));
  });
});
