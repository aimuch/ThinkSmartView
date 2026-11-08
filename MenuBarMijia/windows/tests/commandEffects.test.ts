import assert from "node:assert/strict";
import { describe, it } from "node:test";
import { applyCommandToDevices, inverseCommands } from "../src/core/commandEffects.js";
import type { DeviceCommand, SmartDevice } from "../src/core/models.js";

// The effect a sent command has on the rows and the commands that undo it, without the app.
function light(): SmartDevice {
  return {
    id: "lamp", name: "台灯", roomId: "study", roomName: "书房", model: "yeelink.light.lamp4", isOnline: true,
    kind: {
      kind: "light", entityId: "miot:lamp:2", isOn: false, brightnessPercent: 40, colorTempKelvin: 3000,
      minKelvin: 2700, maxKelvin: 6500, currentEffect: null, availableEffects: [],
    },
  };
}

function fan(): SmartDevice {
  return {
    id: "fan", name: "净化器", roomId: "study", roomName: "书房", model: "zhimi.airp.mb5", isOnline: true,
    kind: {
      kind: "fan", entityId: "miot:fan:2", isOn: true, percentage: 20, presetMode: "Auto",
      availablePresetModes: ["Auto", "Favorite"], isOscillating: null,
    },
  };
}

function climate(): SmartDevice {
  return {
    id: "ac", name: "空调", roomId: "bed", roomName: "主卧", model: "xiaomi.aircondition.mc5", isOnline: true,
    kind: {
      kind: "climate", entityId: "climate.ac", isOn: false, hvacMode: "off", targetTemperature: 24,
      currentTemperature: 26, minTemp: 16, maxTemp: 30, step: 1, availableModes: ["off", "cool", "heat"],
      fanMode: null, availableFanModes: [],
    } as SmartDevice["kind"],
  };
}

describe("Command effects (core)", () => {
  it("brightness powers the light and only touches the addressed row", () => {
    const devices = [light(), fan()];
    applyCommandToDevices(devices, { kind: "setLightBrightness", entityId: "miot:lamp:2", percent: 70 }, "miCloud");
    const k = devices[0]!.kind;
    assert.ok(k.kind === "light" && k.isOn && k.brightnessPercent === 70);
    assert.deepEqual(devices[1], fan());
  });

  it("a fan speed switches to the Favorite preset", () => {
    const devices = [fan()];
    applyCommandToDevices(devices, { kind: "setFanPercentage", entityId: "miot:fan:2", percent: 60 }, "miCloud");
    const k = devices[0]!.kind;
    assert.ok(k.kind === "fan" && k.percentage === 60 && k.presetMode === "Favorite");
  });

  it("Home Assistant keeps an off climate off when only the target changes", () => {
    const command: DeviceCommand = { kind: "setClimateTemperature", entityId: "climate.ac", temperature: 22 };
    const ha = [climate()];
    applyCommandToDevices(ha, command, "homeAssistant");
    assert.ok(ha[0]!.kind.kind === "climate" && !ha[0]!.kind.isOn && ha[0]!.kind.targetTemperature === 22);
    const cloud = [climate()];
    applyCommandToDevices(cloud, command, "miCloud");
    assert.ok(cloud[0]!.kind.kind === "climate" && cloud[0]!.kind.isOn);
  });

  it("reports camera power changes so the live view can follow", () => {
    const camera: SmartDevice = {
      id: "cam", name: "摄像头", roomId: "door", roomName: "门口", model: "chuangmi.camera", isOnline: true,
      kind: { kind: "camera", entityId: "miot:cam:2", isOn: true } as SmartDevice["kind"],
    };
    const changes = applyCommandToDevices([camera], { kind: "setSwitch", entityId: "miot:cam:2", isOn: false }, "miCloud");
    assert.deepEqual(changes, [{ deviceId: "cam", isOn: false }]);
  });

  it("undoing a brightness change on an off light restores the level and keeps it off", () => {
    assert.deepEqual(inverseCommands(light(), { kind: "setLightBrightness", entityId: "miot:lamp:2", percent: 70 }), [
      { kind: "setLightBrightness", entityId: "miot:lamp:2", percent: 40 },
      { kind: "setLightPower", entityId: "miot:lamp:2", isOn: false },
    ]);
  });

  it("a vacuum action has no inverse", () => {
    assert.deepEqual(inverseCommands(fan(), { kind: "vacuumAction", entityId: "x", action: "start" }), []);
  });
});
