import type { MenuMijiaWindowsApp } from "./app.js";
import { homeAssistantIdentity } from "../core/homeAssistant.js";
import { normalizeMiCloudRegion } from "../core/miCloudClient.js";
import { t } from "../core/i18n.js";
import type { ScheduledCommand } from "../core/models.js";
import type { PersistedSettings } from "./appTypes.js";

export function scheduledCommandIdentity(settings: PersistedSettings): string | null {
  switch (settings.connectionMode) {
    case "demo": return "demo";
    case "xiaomiCloud": return settings.miSession
      ? JSON.stringify(["xiaomiCloud", settings.miSession.userId, normalizeMiCloudRegion(settings.miSession.region)]) : null;
    case "homeAssistant": {
      if (!settings.haToken) return null;
      const identity = homeAssistantIdentity(settings.haBaseURL, settings.haToken);
      return identity ? JSON.stringify(["homeAssistant", identity]) : null;
    }
  }
}

export function scheduledCommandsReady(host: MenuMijiaWindowsApp): boolean {
  const identity = scheduledCommandIdentity(host.settings);
  if (identity === "demo") return true;
  return host.timerCredentialsReady && identity !== null &&
    host.timerSnapshot?.identity === identity && host.timerSnapshot.sourceRevision === host.sourceRevision;
}

export function markTimerSnapshotReady(host: MenuMijiaWindowsApp): void {
  const identity = scheduledCommandIdentity(host.settings);
  host.timerSnapshot = identity ? { identity, sourceRevision: host.sourceRevision } : null;
}

export function processScheduledCommands(host: MenuMijiaWindowsApp, nowMs = Date.now()): boolean {
  const count = host.scheduledCommands.length;
  const missed = host.missedScheduledCommands.length;
  const run = host.scheduledCommandStore.takeDue(
    scheduledCommandIdentity(host.settings), scheduledCommandsReady(host), nowMs,
  );
  if (run === null) {
    timerStorageFailure(host);
    return true;
  }
  for (const item of run) void host.runScheduledCommand(item);
  return count > 0 || host.missedScheduledCommands.length !== missed;
}

export function confirmMissedScheduledCommand(host: MenuMijiaWindowsApp, id: string): void {
  const item = host.missedScheduledCommands.find((entry) => entry.id === id);
  if (!item) return;
  if (!scheduledCommandsReady(host)) {
    host.replyBanner = { text: t("请等待设备同步完成后再执行定时指令"), source: t("定时任务"), isError: true };
    host.render();
    return;
  }
  const previous = host.missedScheduledCommands;
  host.missedScheduledCommands = previous.filter((entry) => entry.id !== id);
  if (!host.scheduledCommandStore.persist()) {
    host.missedScheduledCommands = previous;
    timerStorageFailure(host);
    return;
  }
  void host.runScheduledCommand(item);
  host.render();
}

export function cancelScheduledCommands(host: MenuMijiaWindowsApp, id?: string): number {
  const queue = host.scheduledCommandStore.queue(scheduledCommandIdentity(host.settings));
  const previous = { ...queue };
  queue.scheduledCommands = queue.scheduledCommands.filter((item) => id !== undefined && item.id !== id);
  queue.missedScheduledCommands = queue.missedScheduledCommands.filter((item) => id !== undefined && item.id !== id);
  const count = previous.scheduledCommands.length + previous.missedScheduledCommands.length -
    queue.scheduledCommands.length - queue.missedScheduledCommands.length;
  if (!host.scheduledCommandStore.persist()) {
    Object.assign(queue, previous);
    timerStorageFailure(host);
    return -1;
  }
  return count;
}

export function enqueueScheduledCommand(host: MenuMijiaWindowsApp, item: ScheduledCommand): boolean {
  if (!scheduledCommandsReady(host)) {
    host.replyBanner = { text: t("请等待设备同步完成后再执行定时指令"), source: t("定时任务"), isError: true };
    host.render();
    return false;
  }
  if (!item.intents?.length) return false;
  host.scheduledCommands.push(structuredClone(item));
  if (host.scheduledCommandStore.persist()) return true;
  host.scheduledCommands = host.scheduledCommands.filter((entry) => entry.id !== item.id);
  timerStorageFailure(host);
  return false;
}

function timerStorageFailure(host: MenuMijiaWindowsApp): void {
  host.replyBanner = { text: t("定时指令无法保存，请重试"), source: t("定时任务"), isError: true };
  host.render();
}
