import type { MenuMijiaWindowsApp } from "./app.js";
import { t } from "../core/i18n.js";
import { HomeAssistantClient, homeAssistantCommand } from "../core/homeAssistant.js";
import { MiCloudClient, MiotCommandMapper, propKeyString } from "../core/miCloudClient.js";
import { readMiotPower, type DeviceCommand, type DeviceKind, type FacetControl, type SmartDevice } from "../core/models.js";
import { applyCommandToDevices } from "../core/commandEffects.js";

function withReadPower(command: DeviceCommand, isOn: boolean): DeviceCommand {
  switch (command.kind) {
    case "setLightPower":
    case "setClimatePower":
    case "setFanPower":
    case "setSwitch":
      return { ...command, isOn };
    default:
      return command;
  }
}


export async function applyCommand(host: MenuMijiaWindowsApp, command: DeviceCommand, readBackPower = false): Promise<boolean> {
    const sourceRevision = host.sourceRevision;
    try {
      if (host.settings.connectionMode === "homeAssistant") command = homeAssistantCommand(command, host.devices);
      const readPower = await dispatchCloudCommand(host, command, readBackPower);
      if (typeof readPower === "boolean") command = withReadPower(command, readPower);
    } catch (error) {
      if (sourceRevision !== host.sourceRevision) return false;
      host.replyBanner = {
        text: error instanceof Error ? error.message : String(error),
        source: t("控制失败"),
        isError: true,
      };
      host.render();
      return false;
    }
    if (sourceRevision !== host.sourceRevision) return false;
    host.localControlRevision += 1;
    for (const change of applyCommandToDevices(host.devices, command, host.settings.connectionMode, host.deviceFacets)) {
      if (host.cameraLive.activeLiveCameraId !== change.deviceId || host.cameraLive.liveCameraHidden) continue;
      if (change.isOn) {
        host.cameraLive.reconnectCameraLiveView();
      } else {
        host.cameraLive.stopLiveTransport();
        host.cameraLive.liveCameraLoading = false;
      }
    }

    return true;
  }

/**
 * The card that owns a MIoT entity id, or null when none does. A card's own controls and every
 * 更多设置 row count: a plug's auxiliary control lives on a sibling service (#5), so the drawer
 * address must resolve to the card even though the card's main switch has another one.
 */
export function deviceOwningEntityId(
  devices: readonly SmartDevice[],
  facets: Readonly<Record<string, readonly FacetControl[]>>,
  entityId: string,
): string | null {
  const owner = devices.find((device) => {
    if (kindOwnsEntity(device.kind, entityId)) return true;
    return (facets[device.id] ?? []).some((facet) => facet.target?.entityId === entityId);
  });
  return owner?.id ?? null;
}

/** The entity ids a card's own controls address, before 更多设置 rows are considered. */
function kindOwnsEntity(kind: DeviceKind, entityId: string): boolean {
  switch (kind.kind) {
    case "plug":
      return kind.switchEntityId === entityId || kind.childLockEntityId === entityId;
    case "camera":
      return kind.entityId === entityId || kind.motionDetectionEntityId === entityId;
    case "fridge":
      return (
        kind.modeEntityId === entityId ||
        kind.fridgeTargetEntityId === entityId ||
        kind.freezerTargetEntityId === entityId
      );
    case "speaker":
      return [kind.mediaEntityId, kind.muteSwitchEntityId, kind.sleepModeEntityId].includes(entityId);
    case "appliance":
      // An appliance card addresses several services: main function, power, mode, temperature,
      // light, fan speed and the no-argument actions (#300, #3, #4).
      return [
        kind.entityId,
        kind.powerEntityId,
        kind.modeEntityId,
        kind.temperatureEntityId,
        kind.light?.entityId,
        kind.fanLevel?.entityId,
        kind.startAction?.entityId,
        kind.pauseAction?.entityId,
        kind.stopAction?.entityId,
      ].includes(entityId);
    case "generic":
      // A generic card has no primary service; its switch and 更多设置 rows own the addresses (#6).
      return kind.powerControl?.target?.entityId === entityId;
    case "sensor":
      return false;
    default:
      return kind.entityId === entityId;
  }
}

export async function dispatchCloudCommand(host: MenuMijiaWindowsApp, command: DeviceCommand, readBackPower = false): Promise<boolean | null> {
    const sourceRevision = host.sourceRevision;
    if (host.settings.connectionMode === "demo") return null;
    if (host.settings.connectionMode === "homeAssistant") {
      const client = new HomeAssistantClient(host.settings.haBaseURL, host.settings.haToken);
      await host.endpointSubmission;
      if (sourceRevision !== host.sourceRevision) throw new Error("Connection source changed");
      await client.execute(command);
      return null;
    }
    if (!host.settings.miSession) throw new Error(t("请先扫码登录米家"));
    const session = host.settings.miSession;
    const deviceId = deviceOwningEntityId(host.devices, host.deviceFacets, command.entityId);
    const dev = host.devices.find((d) => d.id === deviceId);
    if (!dev) throw new Error(t("无法找到指令对应的设备"));

    const spec = await host.specResolver.resolve({
      did: dev.id,
      name: dev.name,
      model: dev.model,
      isOnline: dev.isOnline,
      specType: null,
    });
    const mapped = MiotCommandMapper.map(command, spec);
    if (sourceRevision !== host.sourceRevision) throw new Error("Connection source changed");
    const client = new MiCloudClient(session, host.transport);
    if (mapped.kind === "setProps") {
      await client.setProps(mapped.writes);
      if (readBackPower) {
        const power = mapped.writes.find((write) => typeof write.value === "boolean");
        if (power) {
          let read: Map<string, unknown>;
          try {
            read = await client.getProps([power]);
          } catch (error) {
            if (sourceRevision === host.sourceRevision) markCloudValuesStale(host);
            throw error;
          }
          const value = read.get(propKeyString(power));
          const isOn = readMiotPower(value);
          if (isOn === null) {
            // The write may have landed; the old row is no longer a trusted reading.
            if (sourceRevision === host.sourceRevision) markCloudValuesStale(host);
            throw new Error(t("未能读回设备状态"));
          }
          return isOn;
        }
      }
    } else {
      await client.action(mapped.call);
    }
    return null;
  }

export function markCloudValuesStale(host: MenuMijiaWindowsApp): void {
    if (host.settings.connectionMode === "xiaomiCloud" && host.devices.length > 0) {
      host.cloudValuesStale = true;
    }
  }

export function markCloudValuesFresh(host: MenuMijiaWindowsApp): void {
    if (host.settings.connectionMode === "xiaomiCloud") host.cloudValuesStale = false;
  }
