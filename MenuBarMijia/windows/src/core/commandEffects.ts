import { readMiotPower, type DeviceCommand, type FacetControl, type SmartDevice } from "./models.js";

/**
 * What a sent command does to the device rows, and the commands that undo it.
 *
 * Both are pure domain rules, so they live in core beside the models instead of inside the
 * panel: `applyCommandToDevices` is the optimistic effect the panel shows after a successful
 * send, `inverseCommands` is what undo sends back. Tests call them without building the app.
 */

export type ConnectionMode = "miCloud" | "homeAssistant" | string;

/** A camera whose power changed, so the live view can reconnect or stop. */
export interface CameraPowerChange {
  readonly deviceId: string;
  readonly isOn: boolean;
}

/**
 * Applies `command` to every device row it addresses, in place.
 * Home Assistant keeps an off climate off when only its target temperature changes.
 */
export function applyCommandToDevices(
  devices: SmartDevice[],
  command: DeviceCommand,
  connectionMode: ConnectionMode,
  facets: Readonly<Record<string, readonly FacetControl[]>> = {},
): CameraPowerChange[] {
  const cameraPower: CameraPowerChange[] = [];
  for (const dev of devices) {
    const k = dev.kind;
    switch (command.kind) {
      case "setLightPower":
        if (k.kind === "light" && k.entityId === command.entityId) {
          k.isOn = command.isOn;
        }
        break;
      case "setLightBrightness":
        if (k.kind === "light" && k.entityId === command.entityId) {
          k.isOn = command.percent > 0;
          k.brightnessPercent = command.percent;
        }
        break;
      case "setLightColorTemp":
        if (k.kind === "light" && k.entityId === command.entityId) {
          k.isOn = true;
          k.colorTempKelvin = command.kelvin;
          k.currentEffect = null;
        }
        break;
      case "setLightEffect":
        if (k.kind === "light" && k.entityId === command.entityId) {
          k.isOn = true;
          k.currentEffect = command.effect;
        }
        break;
      case "setClimatePower":
        if (k.kind === "climate" && k.entityId === command.entityId) {
          k.isOn = command.isOn;
          k.hvacMode = command.isOn ? command.resumeMode : "off";
        }
        break;
      case "setClimateMode":
        if (k.kind === "climate" && k.entityId === command.entityId) {
          k.isOn = command.hvacMode !== "off";
          k.hvacMode = command.hvacMode;
        }
        break;
      case "setClimateTemperature":
        if (k.kind === "climate" && k.entityId === command.entityId) {
          if (connectionMode !== "homeAssistant" || k.hvacMode !== "off") {
            k.isOn = true;
          }
          k.targetTemperature = command.temperature;
        } else if (k.kind === "appliance" && k.temperatureEntityId === command.entityId) {
          // An appliance reports its programme; the target is the only setting shown ahead of it.
          k.targetTemperature = command.temperature;
        }
        break;
      case "setClimateFanMode":
        if (k.kind === "climate" && k.entityId === command.entityId) {
          k.fanMode = command.fanMode;
        } else if (k.kind === "appliance" && k.fanLevel?.entityId === command.entityId) {
          k.fanLevel.current = command.fanMode;
        }
        break;
      case "setFanPower":
        if (k.kind === "fan" && k.entityId === command.entityId) {
          k.isOn = command.isOn;
        }
        break;
      case "setFanPercentage":
        if (k.kind === "fan" && k.entityId === command.entityId) {
          k.isOn = command.percent > 0;
          k.percentage = command.percent;
          const fav = k.availablePresetModes.find((m) => m.toLowerCase() === "favorite");
          if (fav) k.presetMode = fav;
        }
        break;
      case "setFanPresetMode":
        if (k.kind === "fan" && k.entityId === command.entityId) {
          k.isOn = true;
          k.presetMode = command.presetMode;
        }
        break;
      case "setFanOscillating":
        if (k.kind === "fan" && k.entityId === command.entityId) {
          k.isOn = true;
          k.isOscillating = command.oscillating;
        }
        break;
      case "setSwitch":
        if (k.kind === "plug" && k.switchEntityId === command.entityId && !command.entityId.startsWith("button.")) {
          k.isOn = command.isOn;
        } else if (k.kind === "camera") {
          if (k.entityId === command.entityId) {
            k.isOn = command.isOn;
            cameraPower.push({ deviceId: dev.id, isOn: command.isOn });
          } else if (k.motionDetectionEntityId === command.entityId) {
            k.isMotionDetectionOn = command.isOn;
          }
        } else if (k.kind === "speaker") {
          if (k.muteSwitchEntityId === command.entityId) k.isMuted = command.isOn;
          else if (k.sleepModeEntityId === command.entityId) k.isSleepMode = command.isOn;
        } else if (k.kind === "appliance") {
          // Power may live on a companion service, and the lamp is its own switch (#4).
          if (k.powerEntityId === command.entityId) k.isOn = command.isOn;
          else if (k.light?.entityId === command.entityId) k.light.isOn = command.isOn;
        } else if (k.kind === "generic" && k.powerControl?.target?.entityId === command.entityId) {
          k.isOn = command.isOn;
        }
        break;
      case "mediaSetVolume":
        if (k.kind === "speaker" && k.mediaEntityId === command.entityId) {
          k.volumePercent = Math.round(Math.max(0, Math.min(1, command.level)) * 100);
        }
        break;
      case "setSelectOption":
        if (k.kind === "camera" && k.entityId === command.entityId) {
          k.nightShot = command.option;
        } else if (k.kind === "fridge" && k.modeEntityId === command.entityId) {
          k.currentMode = command.option;
        } else if (k.kind === "appliance" && k.modeEntityId === command.entityId) {
          k.currentMode = command.option;
        } else if (k.kind === "cover" && k.entityId === command.entityId) {
          const option = command.option.toLowerCase();
          if (k.openDescription?.toLowerCase() === option) {
            k.positionPercent = 100;
            k.motion = "opening";
          } else if (k.closeDescription?.toLowerCase() === option) {
            k.positionPercent = 0;
            k.motion = "closing";
          } else if (k.pauseDescription?.toLowerCase() === option) {
            k.motion = "stopped";
          }
        }
        break;
      case "setNumberValue":
        if (k.kind === "fridge") {
          if (k.fridgeTargetEntityId === command.entityId) {
            k.fridgeTargetTemp = command.value;
          } else if (k.freezerTargetEntityId === command.entityId) {
            k.freezerTargetTemp = command.value;
          }
        }
        break;
      case "vacuumAction":
        if (k.kind === "vacuum" && k.entityId === command.entityId) {
          k.status =
            command.action === "start"
              ? "cleaning"
              : command.action === "pause"
                ? "paused"
                : "returning";
        }
        break;
      case "pressButton":
        // An appliance reports the programme it started; nothing is shown ahead of it (#3, #4).
        break;
      case "setMiotProperty": {
        // The drawer's row shows the value the write asked for until the next snapshot (#5).
        for (const control of facets[dev.id] ?? []) {
          const target = control.target;
          if (target?.kind !== "property" || target.entityId !== command.entityId || target.piid !== command.piid) {
            continue;
          }
          control.value = command.value;
        }
        if (k.kind === "generic") {
          // Only the card's own switch changes the power state; a mode write on the same service
          // must not flip it (#6).
          const target = k.powerControl?.target;
          const isOn = readMiotPower(command.value);
          if (
            target?.kind === "property" &&
            target.entityId === command.entityId &&
            target.piid === command.piid &&
            isOn !== null
          ) {
            k.isOn = isOn;
            k.powerControl!.value = command.value;
          }
        }
        break;
      }
      case "activateScene":
        break;
    }
  }
  return cameraPower;
}

/** The commands that put `dev` back the way it was before `command`; empty when not reversible. */
export function inverseCommands(dev: SmartDevice, command: DeviceCommand): DeviceCommand[] {
  const k = dev.kind;
  switch (command.kind) {
    case "setLightPower":
      return k.kind === "light" ? [{ kind: "setLightPower", entityId: k.entityId, isOn: k.isOn }] : [];
    case "setLightBrightness":
      return k.kind === "light" && k.brightnessPercent !== null ? [
        { kind: "setLightBrightness", entityId: k.entityId, percent: k.brightnessPercent },
        { kind: "setLightPower", entityId: k.entityId, isOn: k.isOn },
      ] : [];
    case "setLightColorTemp":
      return k.kind === "light" && k.colorTempKelvin !== null ? [
        { kind: "setLightColorTemp", entityId: k.entityId, kelvin: k.colorTempKelvin },
        ...(k.currentEffect !== null ? [{ kind: "setLightEffect" as const, entityId: k.entityId, effect: k.currentEffect }] : []),
        { kind: "setLightPower", entityId: k.entityId, isOn: k.isOn },
      ] : [];
    case "setLightEffect":
      if (k.kind !== "light") return [];
      if (k.currentEffect !== null) return [
        { kind: "setLightEffect", entityId: k.entityId, effect: k.currentEffect },
        { kind: "setLightPower", entityId: k.entityId, isOn: k.isOn },
      ];
      return k.colorTempKelvin !== null ? [
        { kind: "setLightColorTemp", entityId: k.entityId, kelvin: k.colorTempKelvin },
        { kind: "setLightPower", entityId: k.entityId, isOn: k.isOn },
      ] : [];
    case "setClimatePower":
    case "setClimateMode":
      return k.kind === "climate" ? [{ kind: "setClimateMode", entityId: k.entityId, hvacMode: k.isOn ? k.hvacMode : "off" }] : [];
    case "setClimateTemperature":
      return k.kind === "climate" ? [
        { kind: "setClimateTemperature", entityId: k.entityId, temperature: k.targetTemperature },
        { kind: "setClimatePower", entityId: k.entityId, isOn: k.isOn, resumeMode: k.hvacMode },
      ] : [];
    case "setClimateFanMode":
      return k.kind === "climate" && k.fanMode !== null ? [{ kind: "setClimateFanMode", entityId: k.entityId, fanMode: k.fanMode }] : [];
    case "setFanPower":
      return k.kind === "fan" ? [{ kind: "setFanPower", entityId: k.entityId, isOn: k.isOn }] : [];
    case "setFanPercentage":
      return k.kind === "fan" && k.percentage !== null ? [
        { kind: "setFanPercentage", entityId: k.entityId, percent: k.percentage },
        ...(k.presetMode !== null ? [{ kind: "setFanPresetMode" as const, entityId: k.entityId, presetMode: k.presetMode }] : []),
        { kind: "setFanPower", entityId: k.entityId, isOn: k.isOn },
      ] : [];
    case "setFanPresetMode":
      return k.kind === "fan" && k.presetMode !== null ? [
        { kind: "setFanPresetMode", entityId: k.entityId, presetMode: k.presetMode },
        { kind: "setFanPower", entityId: k.entityId, isOn: k.isOn },
      ] : [];
    case "setFanOscillating":
      return k.kind === "fan" && k.isOscillating !== null ? [
        { kind: "setFanOscillating", entityId: k.entityId, oscillating: k.isOscillating },
        { kind: "setFanPower", entityId: k.entityId, isOn: k.isOn },
      ] : [];
    case "setSwitch":
      if (k.kind === "plug") {
        if (command.entityId.startsWith("button.")) return [];
        return [{ kind: "setSwitch", entityId: command.entityId, isOn: command.entityId === k.childLockEntityId ? k.isChildLocked : k.isOn }];
      }
      if (k.kind === "camera") {
        const prevOn = command.entityId === k.motionDetectionEntityId ? Boolean(k.isMotionDetectionOn) : k.isOn;
        return [{ kind: "setSwitch", entityId: command.entityId, isOn: prevOn }];
      }
      if (k.kind === "speaker") {
        if (command.entityId === k.muteSwitchEntityId) return [{ kind: "setSwitch", entityId: command.entityId, isOn: k.isMuted }];
        if (command.entityId === k.sleepModeEntityId) return [{ kind: "setSwitch", entityId: command.entityId, isOn: k.isSleepMode }];
      }
      return [];
    case "setSelectOption":
      if (k.kind === "camera" && k.nightShot !== null) {
        return [{ kind: "setSelectOption", entityId: command.entityId, option: k.nightShot }];
      }
      if (k.kind === "fridge") {
        return [{ kind: "setSelectOption", entityId: command.entityId, option: k.currentMode }];
      }
      return [];
    case "setNumberValue":
      if (k.kind === "fridge") {
        if (command.entityId === k.fridgeTargetEntityId) {
          return [{ kind: "setNumberValue", entityId: command.entityId, value: k.fridgeTargetTemp }];
        }
        if (command.entityId === k.freezerTargetEntityId) {
          return [{ kind: "setNumberValue", entityId: command.entityId, value: k.freezerTargetTemp }];
        }
      }
      return [];
    case "mediaSetVolume":
      return k.kind === "speaker" ? [{ kind: "mediaSetVolume", entityId: k.mediaEntityId, level: k.volumePercent / 100 }] : [];
    case "vacuumAction":
    case "pressButton":
    case "setMiotProperty":
    case "activateScene":
      // A cycle action, an appliance programme write and a 更多设置 write carry no previous
      // value on the card, so undo does not offer them (#5).
      return [];
    default: {
      const exhaustive: never = command;
      return exhaustive;
    }
  }
  }
