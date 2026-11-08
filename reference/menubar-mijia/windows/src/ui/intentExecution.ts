import type { MenuMijiaWindowsApp } from "./app.js";
import { t } from "../core/i18n.js";
import {
  IntentValidator,
  UserHabitMemory,
  type HabitLastAction,
  type HabitRecordingReceipt,
  type HabitSequenceEntry,
} from "../core/assistant.js";
import {
  isDeviceActiveOn,
  type DeviceCommand,
  type DeviceIntent,
  type SmartDevice,
} from "../core/models.js";
import type { UndoStep } from "./appTypes.js";
import { inverseCommands } from "../core/commandEffects.js";

export async function executeIntents(host: MenuMijiaWindowsApp, 
    intents: readonly DeviceIntent[],
    replyText: string,
    sourceLabel: string,
    options: { recordHabits?: boolean } = {},
  ): Promise<boolean> {
    const sourceRevision = host.sourceRevision;
    const commands: Array<{ deviceId: string; command: DeviceCommand; intentIndex: number }> = [];
    const candidateIntents: Array<{ intent: DeviceIntent; commandCount: number; succeededCount: number }> = [];
    let failed = 0;
    let failureMessage = "";
    for (const intent of intents) {
      if (intent.operation.kind === "activateScene") {
        const scene = host.scenes.find((s) => s.id === intent.targetId);
        if (scene) {
          const intentIndex = candidateIntents.length;
          candidateIntents.push({ intent, commandCount: 1, succeededCount: 0 });
          commands.push({ deviceId: scene.id, command: { kind: "activateScene", entityId: scene.entityId }, intentIndex });
        }
        continue;
      }
      const dev = host.devices.find((d) => d.id === intent.targetId);
      if (!dev) continue;
      if (!dev.isOnline) {
        failed += 1;
        failureMessage = `${dev.name} (${t("离线")})`;
        continue;
      }
      const intentCommands = IntentValidator.commandsForIntent(intent, dev);
      if (intentCommands.length === 0) {
        failed += 1;
        failureMessage = t("无法找到指令对应的设备");
        continue;
      }
      const intentIndex = candidateIntents.length;
      candidateIntents.push({ intent, commandCount: intentCommands.length, succeededCount: 0 });
      for (const command of intentCommands) {
        commands.push({ deviceId: dev.id, command, intentIndex });
      }
    }

    const steps: UndoStep[] = [];
    let succeeded = 0;
    let canUndo = true;
    for (const { deviceId, command, intentIndex } of commands) {
      if (sourceRevision !== host.sourceRevision) return false;
      // Scenes have no device row and no inverse; they simply run and count as applied (#113).
      if (command.kind === "activateScene") {
        if (await host.applyCommand(command)) {
          succeeded += 1;
          const entry = candidateIntents[intentIndex];
          if (entry) entry.succeededCount += 1;
        } else {
          failed += 1;
          failureMessage = host.replyBanner?.text ?? "设备未响应";
        }
        continue;
      }
      const device = host.devices.find((d) => d.id === deviceId);
      if (!device) {
        failed += 1;
        failureMessage = t("设备列表已更新，请重新执行");
        continue;
      }
      const isMomentaryButton = command.kind === "setSwitch" && command.entityId.startsWith("button.");
      const inverses = isMomentaryButton ? [] : inverseCommands(device, command);
      if (await host.applyCommand(command)) {
        succeeded += 1;
        const entry = candidateIntents[intentIndex];
        if (entry) entry.succeededCount += 1;
        if (!isMomentaryButton) {
          canUndo = canUndo && inverses.length > 0;
          if (inverses.length > 0) {
            steps.unshift({ deviceId, commands: inverses });
          }
        }
      } else {
        failed += 1;
        failureMessage = host.replyBanner?.text ?? "设备未响应";
      }
    }
    if (sourceRevision !== host.sourceRevision) return false;
    if (succeeded > 0) {
      if (failed === 0) host.grokBallCelebrationUntil = Date.now() + 2400;
      const succeededIntents = candidateIntents
        .filter((entry) => entry.succeededCount === entry.commandCount && entry.commandCount > 0)
        .map((entry) => entry.intent);
      // Only explicit user asks train habits, and only for intents that actually succeeded (#34/#113/#116).
      const habitReceipt = options.recordHabits && succeededIntents.length > 0 ? recordHabits(host, succeededIntents) : null;
      // Scene activations have no inverse: an undo action must exist only when at least one
      // device step is restorable, otherwise Undo would report success without doing anything
      // (Codex #116 re-review).
      host.lastUndoAction = canUndo && steps.length > 0 ? {
        summary: replyText,
        steps,
        expiresAtMs: Date.now() + 10 * 60 * 1000,
        habitReceipt: habitReceipt ?? undefined,
      } : null;
    }
    if (succeeded === 0 && failed === 0) {
      host.replyBanner = { text: t("无法找到指令对应的设备"), source: t("控制失败"), isError: true };
      host.render();
      return false;
    }
    host.replyBanner = failed > 0
      ? { text: t("已执行 {ok} 项，{failed} 项失败。{message}", { ok: succeeded, failed: failed, message: failureMessage }), source: t("控制失败"), isError: true }
      : { text: replyText, source: sourceLabel };
    host.render();
    return failed === 0;
  }

export async function executeSingleCommand(host: MenuMijiaWindowsApp, 
    device: SmartDevice,
    command: DeviceCommand,
    summary: string,
    intent?: DeviceIntent,
    readBackPower = false,
  ): Promise<void> {
    const sourceRevision = host.sourceRevision;
    const inverses = inverseCommands(device, command);
    if (!(await host.applyCommand(command, readBackPower))) return;
    if (sourceRevision !== host.sourceRevision) return;
    // Momentary buttons (Wake-on-LAN) complete without reporting an on/off state to compare.
    const isMomentaryButton = command.kind === "setSwitch" && command.entityId.startsWith("button.");
    // A cover motor write has no boolean power to read back; the accepted write is the
    // confirmation and applyCommand has already moved the card's optimistic position/motion.
    const isCoverMotorWrite = command.kind === "setSelectOption" && device.kind.kind === "cover";
    if (readBackPower && !isMomentaryButton && !isCoverMotorWrite && !readBackMatches(host, device.id, intent)) {
      host.replyBanner = { text: t("未能读回设备状态"), source: t("控制失败"), isError: true };
      host.render();
      return;
    }
    host.grokBallCelebrationUntil = Date.now() + 2400;
    const habitReceipt = intent ? recordHabits(host, [intent]) : null;
    host.lastUndoAction = inverses.length > 0 ? {
      summary,
      steps: [{ deviceId: device.id, commands: inverses }],
      expiresAtMs: Date.now() + 10 * 60 * 1000,
      habitReceipt: habitReceipt ?? undefined,
    } : null;
    host.replyBanner = { text: summary, source: t("实时控制") };
    host.render();
  }

export function readBackMatches(host: MenuMijiaWindowsApp, deviceId: string, intent?: DeviceIntent): boolean {
    if (!intent || intent.operation.kind !== "power") return true;
    const current = host.devices.find((item) => item.id === deviceId);
    return current !== undefined && isDeviceActiveOn(current) === intent.operation.isOn;
  }

  /** Records manual card actions into the habit memory for the current time bucket (#113). */
export function recordHabits(host: MenuMijiaWindowsApp, intents: readonly DeviceIntent[]): HabitRecordingReceipt | null {
    if (!host.settings.habitLearningEnabled || intents.length === 0) return null;
    const memory = new UserHabitMemory(host.settings.habitEntries, host.habitSequences(), host.lastHabitAction());
    const receipt = memory.record(intents, host.devices, host.scenes, new Date());
    if (receipt) saveHabitMemory(host, memory);
    return receipt;
  }

  /** Sequence entries and lastAction persist alongside the entries (ADR-0009). */
export function habitSequences(): HabitSequenceEntry[] {
    const raw = localStorage.getItem("menumijia.windows.habit.sequences.v1");
    if (!raw) return [];
    try {
      const parsed = JSON.parse(raw) as { sequences?: HabitSequenceEntry[]; lastAction?: HabitLastAction | null };
      return Array.isArray(parsed.sequences) ? parsed.sequences : [];
    } catch {
      return [];
    }
  }

export function lastHabitAction(): HabitLastAction | null {
    const raw = localStorage.getItem("menumijia.windows.habit.sequences.v1");
    if (!raw) return null;
    try {
      const parsed = JSON.parse(raw) as { lastAction?: HabitLastAction | null };
      return parsed.lastAction ?? null;
    } catch {
      return null;
    }
  }

export function saveHabitMemory(host: MenuMijiaWindowsApp, memory: UserHabitMemory): void {
    host.settings.habitEntries = [...memory.entries];
    host.saveSettings();
    try {
      localStorage.setItem("menumijia.windows.habit.sequences.v1", JSON.stringify({
        sequences: memory.sequences,
        lastAction: memory.lastAction,
      }));
    } catch {
      // Quota errors only cost the sequence context, never the entries.
    }
  }

export function unrecordHabits(host: MenuMijiaWindowsApp, receipt: HabitRecordingReceipt): void {
    const memory = new UserHabitMemory(host.settings.habitEntries, host.habitSequences(), host.lastHabitAction());
    memory.unrecord(receipt);
    saveHabitMemory(host, memory);
  }
