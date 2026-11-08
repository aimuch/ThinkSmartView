import { pinyin } from "pinyin-pro";
import { DelayPhrase, LocalIntentParser, UndoPhrase } from "./assistant.js";
import type { HomeScene, SmartDevice } from "./models.js";

export interface DeviceSearchEntry {
  readonly deviceId: string;
  readonly name: string;
  readonly room: string;
  readonly pinyin: string;
  readonly initials: string;
  readonly roomPinyin: string;
  readonly roomInitials: string;
}

export interface PanelSearchState {
  query: string;
  selectedDeviceId: string | null;
  explicitSelection: boolean;
  isComposing: boolean;
  compositionSourceRevision: number;
}

export function createPanelSearchState(): PanelSearchState {
  return { query: "", selectedDeviceId: null, explicitSelection: false, isComposing: false, compositionSourceRevision: 0 };
}

export function normalizeSearch(raw: string): string {
  return raw.normalize("NFKD").replace(/\p{M}/gu, "").replace(/[^\p{L}\p{N}]/gu, "").toLowerCase();
}

function pinyinForms(raw: string): { full: string; initials: string } {
  const full = normalizeSearch(pinyin(raw, { toneType: "none" }));
  const initials = normalizeSearch(raw.replace(/\p{Script=Han}+/gu, (han) =>
    pinyin(han, { toneType: "none", pattern: "first", type: "array" }).join(""),
  ));
  return { full, initials };
}

export function searchEntries(devices: readonly SmartDevice[], aliases: Readonly<Record<string, string>> = {}): DeviceSearchEntry[] {
  return devices.map((device) => searchEntry(device, aliases[device.id]?.trim() || device.name));
}

function searchEntry(device: SmartDevice, name: string): DeviceSearchEntry {
  const nameForms = pinyinForms(name);
  const roomForms = pinyinForms(device.roomName);
  return {
    deviceId: device.id, name: normalizeSearch(name), room: normalizeSearch(device.roomName),
    pinyin: nameForms.full, initials: nameForms.initials,
    roomPinyin: roomForms.full, roomInitials: roomForms.initials,
  };
}

export class PanelSearchIndex {
  private cached = new Map<string, { name: string; room: string; entry: DeviceSearchEntry }>();

  matches(query: string, devices: readonly SmartDevice[], aliases: Readonly<Record<string, string>>): DeviceSearchEntry[] {
    const next = new Map<string, { name: string; room: string; entry: DeviceSearchEntry }>();
    const entries = devices.map((device) => {
      const name = aliases[device.id]?.trim() || device.name;
      const cached = this.cached.get(device.id);
      const item = cached?.name === name && cached.room === device.roomName ? cached
        : { name, room: device.roomName, entry: searchEntry(device, name) };
      next.set(device.id, item);
      return item.entry;
    });
    this.cached = next;
    return searchMatches(query, entries);
  }
}

export function searchMatches(query: string, entries: readonly DeviceSearchEntry[]): DeviceSearchEntry[] {
  const q = normalizeSearch(query);
  if (!q) return [...entries];
  return entries.flatMap((entry, index) => {
    const fields = [entry.name, entry.pinyin, entry.initials, entry.room, entry.roomPinyin, entry.roomInitials];
    const scores = fields.flatMap((value, field) =>
      value.includes(q) ? [100 - field * 8 - (value.startsWith(q) ? 0 : 4)] : [],
    );
    return scores.length ? [{ entry, index, score: Math.max(...scores) }] : [];
  }).sort((a, b) => b.score - a.score || a.index - b.index).map(({ entry }) => entry);
}

export function panelQueryKind(raw: string, devices: readonly SmartDevice[], scenes: readonly HomeScene[], aliases: Readonly<Record<string, string>> = {}): "filter" | "command" {
  const query = raw.trim();
  const q = normalizeSearch(query);
  if (!q) return "filter";
  const names = devices.map((device) => normalizeSearch(aliases[device.id]?.trim() || device.name)).filter(Boolean);
  const rooms = devices.map((device) => normalizeSearch(device.roomName)).filter(Boolean);
  if (names.includes(q) || (q.length >= 2 && names.some((name) => name.startsWith(q)))) return "filter";
  if (rooms.some((room) => room.startsWith(q))) return "filter";
  if (LocalIntentParser.parse(query, [...devices], [...scenes]) || UndoPhrase.isUndo(query) || DelayPhrase.extract(query)) return "command";
  if (/^(打开|开启|关闭|关掉|关上|熄灭|请|帮我|帮忙|把|将|调|设|切|执行|启动|运行|全关|全开|turn|open|close|switch|set|make|please|run|start|activate|enable|disable|dim|brighten)/i.test(query)) return "command";
  if (/[0-9]+(\.[0-9]+)?\s*(%|％|度|℃|°c|°f|°|k|档|级)|(调到|调成|调至|调为|调低|调高|设为|设成|设置为|切换到|切到|改成|改为)\s*[0-9]+/i.test(query)) return "command";
  return "filter";
}
