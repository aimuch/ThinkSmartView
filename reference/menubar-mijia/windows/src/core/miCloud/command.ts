import type { DeviceCommand } from "../models.js";
import type { MiotActionCall, MiotPropWrite } from "./client.js";
import {
  hvacModeFromDescription,
  isMainsBreaker,
  miotShortName,
  parseMiotActionAddress,
  parseMiotAddress,
  type MiotDeviceSpec,
  type MiotProperty,
  type MiotService,
} from "./spec.js";
import { rawFromPercent } from "./percentScale.js";
import { APPLIANCE_SERVICE_NAMES, coverSubtype } from "./synthesis.js";

export type MiotWrite =
  | { readonly kind: "setProps"; readonly writes: readonly MiotPropWrite[] }
  | { readonly kind: "action"; readonly call: MiotActionCall };

export const MiotCommandMapper = {
  map(command: DeviceCommand, spec: MiotDeviceSpec): MiotWrite {
    // A mains breaker is read-only whatever the spec allows: no path may switch it (#265).
    if (isMainsBreaker(spec)) throw new Error("Unsupported device: electronic-valve");
    if (command.kind === "pressButton") {
      // A no-argument action carries its own address, so it never resolves a service first.
      const action = parseMiotActionAddress(command.entityId);
      if (!action) throw new Error(`Invalid MIoT action address: ${command.entityId}`);
      const actionService = spec.services.find((s) => s.iid === action.siid);
      const declared = actionService?.actions.find((a) => a.iid === action.aiid);
      if (!actionService || !declared) throw new Error(`Unsupported action: ${command.entityId}`);
      if ((declared.in?.length ?? 0) > 0) throw new Error(`Action needs inputs: ${command.entityId}`);
      return {
        kind: "action",
        call: { did: action.did, siid: action.siid, aiid: action.aiid, inputs: [] },
      };
    }
    const addr = parseMiotAddress(command.entityId);
    if (!addr) throw new Error(`Invalid MIoT address: ${command.entityId}`);
    const service = spec.services.find((s) => s.iid === addr.siid);
    if (!service) throw new Error(`Service ${addr.siid} not found`);

    const findProp = (short: string, searchAll = false) => {
      const local = service.properties.find((p) => miotShortName(p.type) === short && p.access.includes("write"));
      if (local) return { service, prop: local };
      if (searchAll) {
        const others = [
          ...spec.services.filter((s) => miotShortName(s.type) === "fan-control"),
          ...spec.services.filter((s) => s.iid !== service.iid),
        ];
        for (const s of others) {
          const p = s.properties.find((pr) => miotShortName(pr.type) === short && pr.access.includes("write"));
          if (p) return { service: s, prop: p };
        }
      }
      throw new Error(`Unsupported property: ${short}`);
    };

    const findListProp = (short: string, description: string, searchAll = false) => {
      const services = [
        service,
        ...(searchAll
          ? [
              ...spec.services.filter((s) => miotShortName(s.type) === "fan-control"),
              ...spec.services.filter((s) => miotShortName(s.type) === "air-purifier-favorite"),
              ...spec.services.filter((s) => miotShortName(s.type) === "air-fresh-favorite"),
              ...spec.services.filter((s) => s.iid !== service.iid),
            ]
          : []),
      ];
      for (const s of services) {
        for (const p of s.properties) {
          if (miotShortName(p.type) === short && p.access.includes("write")) {
            const item = p.valueList?.find(
              (i) => i.description.toLowerCase() === description.toLowerCase()
            );
            if (item) return { service: s, prop: p, item };
          }
        }
      }
      return null;
    };

    /**
     * A writable property on the addressed service first, then on a companion service the shared
     * appliance card reads: a gas water heater's switch lives on `boiler` while the card addresses
     * `water-heater` (#300), and a cleanfly dryer does the same (#3).
     */
    const findApplianceProp = (short: string) => {
      const local = service.properties.find(
        (p) => miotShortName(p.type) === short && p.access.includes("write"),
      );
      if (local) return { service, prop: local };
      for (const other of spec.services) {
        if (!APPLIANCE_SERVICE_NAMES.includes(miotShortName(other.type))) continue;
        const prop = other.properties.find(
          (p) => miotShortName(p.type) === short && p.access.includes("write"),
        );
        if (prop) return { service: other, prop };
      }
      return null;
    };

    /** A value-list match on one of the appliance card's services (#4). */
    const findApplianceListProp = (short: string, description: string) => {
      for (const other of spec.services) {
        if (!APPLIANCE_SERVICE_NAMES.includes(miotShortName(other.type))) continue;
        for (const prop of other.properties) {
          if (miotShortName(prop.type) !== short || !prop.access.includes("write")) continue;
          const item = prop.valueList?.find(
            (i) => i.description.toLowerCase() === description.toLowerCase(),
          );
          if (item) return { service: other, prop, item };
        }
      }
      return null;
    };

    const coerce = (prop: MiotProperty, val: number): number => {
      let n = val;
      if (prop.valueRange && prop.valueRange.length >= 2) {
        const lo = prop.valueRange[0] ?? n;
        const hi = prop.valueRange[1] ?? n;
        n = Math.min(hi, Math.max(lo, n));
      }
      if (prop.format.startsWith("int") || prop.format.startsWith("uint")) {
        return Math.round(n);
      }
      const step = prop.valueRange?.[2];
      if (step && step > 0) {
        const base = prop.valueRange?.[0] ?? 0;
        n = base + Math.round((n - base) / step) * step;
      }
      return n;
    };

    const powerOnPrefix = (): MiotPropWrite[] => {
      const onP = service.properties.find((p) => miotShortName(p.type) === "on" && p.access.includes("write"));
      return onP ? [{ did: addr.did, siid: service.iid, piid: onP.iid, value: true }] : [];
    };

    switch (command.kind) {
      case "setLightPower":
      case "setClimatePower":
      case "setFanPower": {
        const { prop } = findProp("on");
        return {
          kind: "setProps",
          writes: [{ did: addr.did, siid: service.iid, piid: prop.iid, value: command.isOn }],
        };
      }
      case "setLightBrightness": {
        if (command.percent <= 0) {
          const { prop } = findProp("on");
          return {
            kind: "setProps",
            writes: [{ did: addr.did, siid: service.iid, piid: prop.iid, value: false }],
          };
        }
        const { prop } = findProp("brightness");
        const raw = rawFromPercent(command.percent, prop, "proportional");
        return {
          kind: "setProps",
          writes: [
            ...powerOnPrefix(),
            { did: addr.did, siid: service.iid, piid: prop.iid, value: coerce(prop, raw) },
          ],
        };
      }
      case "setLightColorTemp": {
        const { prop } = findProp("color-temperature");
        return {
          kind: "setProps",
          writes: [
            ...powerOnPrefix(),
            { did: addr.did, siid: service.iid, piid: prop.iid, value: coerce(prop, command.kelvin) },
          ],
        };
      }
      case "setLightEffect": {
        const found = findListProp("mode", command.effect);
        if (!found) throw new Error(`Unsupported effect: ${command.effect}`);
        return {
          kind: "setProps",
          writes: [
            ...powerOnPrefix(),
            { did: addr.did, siid: found.service.iid, piid: found.prop.iid, value: found.item.value },
          ],
        };
      }
      case "setClimateMode": {
        const { prop: onProp } = findProp("on");
        if (command.hvacMode === "off") {
          return {
            kind: "setProps",
            writes: [{ did: addr.did, siid: service.iid, piid: onProp.iid, value: false }],
          };
        }
        const writes: MiotPropWrite[] = [
          { did: addr.did, siid: service.iid, piid: onProp.iid, value: true },
        ];
        const modeProp = service.properties.find(
          (p) => miotShortName(p.type) === "mode" && p.access.includes("write")
        );
        const item = modeProp?.valueList?.find(
          (i) => hvacModeFromDescription(i.description) === command.hvacMode
        );
        if (modeProp && item) {
          writes.push({ did: addr.did, siid: service.iid, piid: modeProp.iid, value: item.value });
        }
        return { kind: "setProps", writes };
      }
      case "setClimateTemperature": {
        const found = findApplianceProp("target-temperature");
        if (!found) throw new Error("Unsupported property: target-temperature");
        return {
          kind: "setProps",
          writes: [
            {
              did: addr.did,
              siid: found.service.iid,
              piid: found.prop.iid,
              value: coerce(found.prop, command.temperature),
            },
          ],
        };
      }
      case "setClimateFanMode": {
        const found =
          findApplianceListProp("fan-level", command.fanMode) ??
          findListProp("fan-level", command.fanMode, true);
        if (!found) throw new Error(`Unsupported fan mode: ${command.fanMode}`);
        return {
          kind: "setProps",
          writes: [{ did: addr.did, siid: found.service.iid, piid: found.prop.iid, value: found.item.value }],
        };
      }
      case "setFanPercentage": {
        if (command.percent <= 0) {
          const { prop } = findProp("on");
          return {
            kind: "setProps",
            writes: [{ did: addr.did, siid: service.iid, piid: prop.iid, value: false }],
          };
        }
        let speedFound: { service: MiotService; prop: MiotProperty } | null = null;
        const directRange = service.properties.find(
          (p) => miotShortName(p.type) === "fan-level" && p.access.includes("write") && p.valueRange !== undefined
        );
        if (directRange) {
          speedFound = { service, prop: directRange };
        }
        if (!speedFound) {
          const favServices = [
            ...spec.services.filter((s) => miotShortName(s.type) === "air-purifier-favorite"),
            ...spec.services.filter((s) => miotShortName(s.type) === "air-fresh-favorite"),
            ...spec.services.filter((s) => miotShortName(s.type) === "motor-speed"),
            ...spec.services.filter((s) => miotShortName(s.type) === "custom-service"),
            ...spec.services.filter((s) => miotShortName(s.type) === "fan-control"),
          ];
          const candidates = ["fan-level", "favorite-fan-level", "favorite-level", "favorite-speed"];
          for (const name of candidates) {
            if (speedFound) break;
            for (const s of favServices) {
              const p = s.properties.find((pr) => miotShortName(pr.type) === name && pr.access.includes("write"));
              if (p) {
                speedFound = { service: s, prop: p };
                break;
              }
            }
          }
        }
        if (!speedFound) {
          const directList = service.properties.find(
            (p) => miotShortName(p.type) === "fan-level" && p.access.includes("write")
          );
          if (directList) speedFound = { service, prop: directList };
        }
        if (!speedFound) throw new Error("Unsupported property: fan-level");

        const raw = rawFromPercent(command.percent, speedFound.prop, "speed");
        const writeVal = coerce(speedFound.prop, raw);

        const prefix = [...powerOnPrefix()];
        if (speedFound.service.iid !== service.iid) {
          const favMode = findListProp("mode", "Favorite");
          if (favMode) {
            prefix.push({ did: addr.did, siid: favMode.service.iid, piid: favMode.prop.iid, value: favMode.item.value });
          }
        }
        return {
          kind: "setProps",
          writes: [
            ...prefix,
            { did: addr.did, siid: speedFound.service.iid, piid: speedFound.prop.iid, value: writeVal },
          ],
        };
      }
      case "setFanPresetMode": {
        const found = findListProp("mode", command.presetMode) ?? findListProp("fan-level", command.presetMode, true);
        if (!found) throw new Error(`Unsupported preset: ${command.presetMode}`);
        return {
          kind: "setProps",
          writes: [{ did: addr.did, siid: found.service.iid, piid: found.prop.iid, value: found.item.value }],
        };
      }
      case "setFanOscillating": {
        const { prop } = findProp("horizontal-swing");
        return {
          kind: "setProps",
          writes: [{ did: addr.did, siid: service.iid, piid: prop.iid, value: command.oscillating }],
        };
      }
      case "setSwitch": {
        const sShort = miotShortName(service.type);
        const targetProp =
          service.properties.find(
            (p) => miotShortName(p.type) === "on" && p.access.includes("write")
          ) ??
          (sShort === "speaker"
            ? service.properties.find(
                (p) => miotShortName(p.type) === "mute" && p.access.includes("write")
              )
            : undefined) ??
          (sShort === "intelligent-speaker"
            ? service.properties.find(
                (p) => miotShortName(p.type) === "sleep-mode" && p.access.includes("write")
              )
            : undefined) ??
          service.properties.find(
            (p) => miotShortName(p.type) === "motion-detection" && p.access.includes("write")
          ) ??
          service.properties.find(
            (p) => miotShortName(p.type) === "physical-controls-locked" && p.access.includes("write")
          );
        if (targetProp) {
          return {
            kind: "setProps",
            writes: [{ did: addr.did, siid: service.iid, piid: targetProp.iid, value: command.isOn }],
          };
        }
        // A bath heater publishes its heat as `heating` instead of `on` (#4), and an appliance's
        // only writable switch may live on a companion service (#300).
        const appliancePower = findApplianceProp("on") ?? findApplianceProp("heating");
        if (appliancePower) {
          return {
            kind: "setProps",
            writes: [
              {
                did: addr.did,
                siid: appliancePower.service.iid,
                piid: appliancePower.prop.iid,
                value: command.isOn,
              },
            ],
          };
        }
        const candidates = command.isOn
          ? ["start-cook", "resume-cook"]
          : ["cancel-cooking", "pause"];
        for (const name of candidates) {
          for (const s of [service, ...spec.services.filter((sv) => sv.iid !== service.iid)]) {
            const act = s.actions.find(
              (a) => miotShortName(a.type) === name && (a.in?.length ?? 0) === 0
            );
            if (act) {
              return {
                kind: "action",
                call: { did: addr.did, siid: s.iid, aiid: act.iid, inputs: [] },
              };
            }
          }
        }
        throw new Error("Unsupported property: on");
      }
      case "setSelectOption": {
        const found =
          findListProp("motor-control", command.option) ??
          findListProp("mode", command.option) ??
          findApplianceListProp("mode", command.option) ??
          findListProp("night-shot", command.option);
        if (!found) throw new Error(`Unsupported select option: ${command.option}`);
        return {
          kind: "setProps",
          writes: [{ did: addr.did, siid: found.service.iid, piid: found.prop.iid, value: found.item.value }],
        };
      }
      case "setNumberValue": {
        const targetPosition = service.properties.find(
          (p) => miotShortName(p.type) === "target-position" && p.access.includes("write"),
        );
        if (targetPosition) {
          // The card speaks 0…100; flip for an airer whose raw position is distance lowered (#299).
          const sub = coverSubtype(miotShortName(service.type));
          const percent = sub === "clothes-airer" ? 100 - command.value : command.value;
          const raw = rawFromPercent(percent, targetPosition, "proportional");
          return {
            kind: "setProps",
            writes: [{ did: addr.did, siid: service.iid, piid: targetPosition.iid, value: coerce(targetPosition, raw) }],
          };
        }
        const found = findApplianceProp("target-temperature");
        if (!found) throw new Error("Unsupported property: target-temperature");
        return {
          kind: "setProps",
          writes: [
            {
              did: addr.did,
              siid: found.service.iid,
              piid: found.prop.iid,
              value: coerce(found.prop, command.value),
            },
          ],
        };
      }
      case "setMiotProperty": {
        const prop = service.properties.find((p) => p.iid === command.piid);
        if (!prop || !prop.access.includes("write")) throw new Error(`Unsupported property: ${command.piid}`);
        const value =
          typeof command.value === "boolean"
            ? command.value
            : typeof command.value === "number"
              ? coerce(prop, command.value)
              : command.value;
        return { kind: "setProps", writes: [{ did: addr.did, siid: service.iid, piid: prop.iid, value }] };
      }
      case "vacuumAction": {
        const candidates =
          command.action === "start"
            ? ["start-sweep", "start-clean", "start-sweep-mop", "start-mop"]
            : command.action === "pause"
              ? ["pause-sweeping", "pause", "stop-sweeping", "stop-clean"]
              : ["start-charge", "stop-and-gocharge", "return-to-charge", "go-charging"];
        for (const name of candidates) {
          for (const s of [service, ...spec.services.filter((sv) => sv.iid !== service.iid)]) {
            const act = s.actions.find((a) => miotShortName(a.type) === name);
            if (act) {
              return {
                kind: "action",
                call: { did: addr.did, siid: s.iid, aiid: act.iid, inputs: [] },
              };
            }
          }
        }
        throw new Error(`Unsupported vacuum action: ${command.action}`);
      }
      case "mediaSetVolume": {
        if (miotShortName(service.type) !== "speaker" || !Number.isFinite(command.level)) {
          throw new Error("Unsupported property: volume");
        }
        const { prop } = findProp("volume");
        const raw = rawFromPercent(command.level * 100, prop, "proportional");
        return {
          kind: "setProps",
          writes: [{ did: addr.did, siid: service.iid, piid: prop.iid, value: coerce(prop, raw) }],
        };
      }
      case "activateScene":
        throw new Error("Scenes are not Miot device properties");
      default: {
        const _exhaustive: never = command;
        return _exhaustive;
      }
    }
  },
};
