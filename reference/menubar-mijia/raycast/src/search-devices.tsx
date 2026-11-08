import { Action, ActionPanel, Color, Icon, List } from "@raycast/api";
import { useCallback, useEffect, useState } from "react";
import { loadSnapshot, type RaycastDevice, type RaycastSnapshot } from "./snapshot";
import { errorMessage, runCommandWithToast, type CommandParams } from "./url";

const BRIGHTNESS_STEPS = [0, 10, 25, 50, 75, 100];
const COLOR_TEMP_STEPS = [2700, 3000, 3500, 4000, 5000, 6500];
const FAN_PERCENTS = [20, 40, 60, 80, 100];

export default function SearchDevices() {
  const [snapshot, setSnapshot] = useState<RaycastSnapshot>();
  const [error, setError] = useState<string>();
  const [isLoading, setIsLoading] = useState(true);

  const reload = useCallback(async () => {
    setIsLoading(true);
    try {
      setSnapshot(await loadSnapshot());
      setError(undefined);
    } catch (loadError) {
      setError(errorMessage(loadError));
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  return (
    <List
      isLoading={isLoading}
      searchBarPlaceholder="Search devices by name, room or pinyin initials"
      navigationTitle={snapshot ? `Devices · ${snapshot.connectionSummary}` : "Devices"}
    >
      {error ? (
        <List.EmptyView
          icon={Icon.Warning}
          title="Cannot read the MenuBarMijia snapshot"
          description={error}
          actions={
            <ActionPanel>
              <Action title="Try Again" icon={Icon.ArrowClockwise} onAction={reload} />
            </ActionPanel>
          }
        />
      ) : (
        (snapshot?.devices ?? []).map((device) => (
          <DeviceItem key={device.id} device={device} onDone={reload} />
        ))
      )}
    </List>
  );
}

function DeviceItem({ device, onDone }: { device: RaycastDevice; onDone: () => Promise<void> }) {
  const run = (route: string, params: CommandParams) => {
    void runCommandWithToast(route, params).then(onDone);
  };
  const capabilities = device.capabilities;
  const kelvin = kelvinOptions(device);
  const temperatures = temperatureOptions(device);

  return (
    <List.Item
      title={device.name}
      subtitle={device.roomName}
      keywords={[
        device.roomName,
        device.categoryTitle,
        device.pinyinInitials,
        device.roomPinyinInitials,
        device.pinyinFull,
        device.model,
      ]}
      icon={{ source: categoryIcon(device.category), tintColor: device.isActiveOn ? Color.Yellow : Color.SecondaryText }}
      accessories={[
        { text: device.stateText, tooltip: device.model },
        ...(device.isFavorite ? [{ tag: { value: "★", color: Color.Orange }, tooltip: "Favorite" }] : []),
        ...(device.isOnline ? [] : [{ tag: { value: "Offline", color: Color.Red } }]),
      ]}
      actions={
        <ActionPanel>
          {device.isPrimaryToggleable ? (
            <Action
              title={device.isActiveOn ? "Turn Off" : "Turn On"}
              icon={Icon.Power}
              onAction={() =>
                run("power", [
                  ["device", device.id],
                  ["on", device.isActiveOn ? "false" : "true"],
                ])
              }
            />
          ) : null}
          {capabilities.brightnessWritable ? (
            <ActionPanel.Submenu
              title="Set Brightness"
              icon={Icon.Sun}
              shortcut={{ modifiers: ["cmd"], key: "b" }}
            >
              {BRIGHTNESS_STEPS.map((percent) => (
                <Action
                  key={percent}
                  title={percent === 0 ? "Off (0%)" : `${percent}%`}
                  onAction={() =>
                    run("brightness", [
                      ["device", device.id],
                      ["percent", String(percent)],
                    ])
                  }
                />
              ))}
            </ActionPanel.Submenu>
          ) : null}
          {capabilities.colorWritable && kelvin.length > 0 ? (
            <ActionPanel.Submenu title="Set Colour Temperature" icon={Icon.CircleFilled}>
              {kelvin.map((value) => (
                <Action
                  key={value}
                  title={`${value} K`}
                  onAction={() =>
                    run("color-temp", [
                      ["device", device.id],
                      ["kelvin", String(value)],
                    ])
                  }
                />
              ))}
            </ActionPanel.Submenu>
          ) : null}
          {capabilities.temperatureWritable ? (
            <ActionPanel.Submenu title="Set Target Temperature" icon={Icon.Temperature}>
              {temperatures.map((value) => (
                <Action
                  key={value}
                  title={`${value} °C`}
                  onAction={() =>
                    run("temperature", [
                      ["device", device.id],
                      ["celsius", String(value)],
                    ])
                  }
                />
              ))}
            </ActionPanel.Submenu>
          ) : null}
          {capabilities.fanSpeedWritable ? (
            <ActionPanel.Submenu title="Set Fan Speed" icon={Icon.Wind}>
              {FAN_PERCENTS.map((percent) => (
                <Action
                  key={`percent-${percent}`}
                  title={`${percent}%`}
                  onAction={() =>
                    run("fan", [
                      ["device", device.id],
                      ["percent", String(percent)],
                    ])
                  }
                />
              ))}
              {capabilities.availablePresetModes.map((preset) => (
                <Action
                  key={`preset-${preset}`}
                  title={`Mode · ${preset}`}
                  onAction={() =>
                    run("fan", [
                      ["device", device.id],
                      ["preset", preset],
                    ])
                  }
                />
              ))}
            </ActionPanel.Submenu>
          ) : null}
          <Action
            title="Open Details"
            icon={Icon.AppWindow}
            onAction={() => run("focus", [["device", device.id]])}
          />
          <Action.CopyToClipboard title="Copy Device ID" content={device.id} />
          <Action title="Refresh" icon={Icon.ArrowClockwise} onAction={onDone} />
        </ActionPanel>
      }
    />
  );
}

function categoryIcon(category: string): Icon {
  switch (category) {
    case "light":
      return Icon.LightBulb;
    case "climate":
      return Icon.Snowflake;
    case "fan":
      return Icon.Wind;
    case "cover":
      return Icon.AppWindow;
    case "plug":
      return Icon.Plug;
    case "camera":
    case "tv":
    case "speaker":
      return Icon.Video;
    case "sensor":
      return Icon.Temperature;
    default:
      return Icon.Power;
  }
}

function kelvinOptions(device: RaycastDevice): number[] {
  const min = device.capabilities.minKelvin ?? COLOR_TEMP_STEPS[0];
  const max = device.capabilities.maxKelvin ?? COLOR_TEMP_STEPS[COLOR_TEMP_STEPS.length - 1];
  const inRange = COLOR_TEMP_STEPS.filter((value) => value >= min && value <= max);
  return inRange.length > 0 ? inRange : [min, max];
}

function temperatureOptions(device: RaycastDevice): number[] {
  const min = device.capabilities.minTemp ?? 16;
  const max = device.capabilities.maxTemp ?? 30;
  const step = device.capabilities.step && device.capabilities.step > 0 ? device.capabilities.step : 1;
  const options: number[] = [];
  for (let value = min; value <= max + 1e-9 && options.length < 40; value += step) {
    options.push(Math.round(value * 10) / 10);
  }
  return options;
}
