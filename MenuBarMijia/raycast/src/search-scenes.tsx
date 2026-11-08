import { Action, ActionPanel, Color, Icon, List } from "@raycast/api";
import { useCallback, useEffect, useState } from "react";
import { loadSnapshot, type RaycastSnapshot } from "./snapshot";
import { errorMessage, runCommandWithToast } from "./url";

export default function SearchScenes() {
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
      searchBarPlaceholder="Search scenes and quick actions"
      navigationTitle="Scenes & Quick Actions"
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
        <List.Section title="Quick Actions">
          {(snapshot?.quickActions ?? []).map((action) => (
            <List.Item
              key={`quick-${action.id}`}
              title={action.name}
              subtitle={action.subtitle}
              keywords={[action.id, action.pinyinInitials]}
              icon={{ source: Icon.Bolt, tintColor: action.isEnabled ? Color.Yellow : Color.SecondaryText }}
              accessories={action.isEnabled ? [] : [{ tag: { value: "Nothing to do", color: Color.SecondaryText } }]}
              actions={
                <ActionPanel>
                  <Action
                    title="Run"
                    icon={Icon.Bolt}
                    onAction={() => {
                      void runCommandWithToast("scene", [["name", `quick:${action.id}`]]).then(reload);
                    }}
                  />
                  <Action.CopyToClipboard title="Copy Quick Action ID" content={action.id} />
                  <Action title="Refresh" icon={Icon.ArrowClockwise} onAction={reload} />
                </ActionPanel>
              }
            />
          ))}
        </List.Section>
      )}
      {!error && (snapshot?.scenes.length ?? 0) > 0 ? (
        <List.Section title="Scenes">
          {(snapshot?.scenes ?? []).map((scene) => (
            <List.Item
              key={`scene-${scene.id}`}
              title={scene.name}
              subtitle={scene.roomName ?? "Home Assistant scene"}
              keywords={[scene.id, scene.entityId, scene.pinyinInitials, scene.pinyinFull]}
              icon={{ source: Icon.Stars, tintColor: Color.Blue }}
              actions={
                <ActionPanel>
                  <Action
                    title="Run"
                    icon={Icon.Stars}
                    onAction={() => {
                      void runCommandWithToast("scene", [["name", scene.id]]).then(reload);
                    }}
                  />
                  <Action.CopyToClipboard title="Copy Entity ID" content={scene.entityId} />
                  <Action title="Refresh" icon={Icon.ArrowClockwise} onAction={reload} />
                </ActionPanel>
              }
            />
          ))}
        </List.Section>
      ) : null}
    </List>
  );
}
