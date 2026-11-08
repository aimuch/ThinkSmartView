import type { MiotProperty } from "./spec.js";

/**
 * The one conversion between a MIoT property's raw value and the card's 0…100 percent, mirroring
 * macOS `MiotPercentScale`. Synthesis decodes with it and the command mapper encodes with it, so a
 * percent read off a card writes back the raw value it came from.
 *
 * `proportional`: `raw / max` unless the range already ends at 100 (brightness, volume, position).
 * `speed`: a fan range or level list starting above 1 maps its low end to 0 %.
 */
export type PercentCurve = "proportional" | "speed";

function speedBounds(prop: MiotProperty): { lo: number; hi: number } | null {
  const range = prop.valueRange;
  if (range && range.length >= 2 && (range[1] ?? 0) > (range[0] ?? 0)) {
    return { lo: range[0] ?? 0, hi: range[1] ?? 100 };
  }
  const list = prop.valueList;
  if (list && list.length > 0) {
    const vals = list.map((i) => i.value);
    const lo = Math.min(...vals);
    const hi = Math.max(...vals);
    if (hi > lo) return { lo, hi };
  }
  return null;
}

function proportionalMax(prop: MiotProperty): number | null {
  const max = prop.valueRange?.[1];
  return max !== undefined && max > 0 && max !== 100 ? max : null;
}

export function percentFromRaw(raw: number, prop: MiotProperty, curve: PercentCurve): number {
  let scaled: number;
  const bounds = curve === "speed" ? speedBounds(prop) : null;
  if (bounds) {
    const { lo, hi } = bounds;
    scaled = lo > 1 ? ((raw - lo) / (hi - lo)) * 100 : hi !== 100 ? (raw / hi) * 100 : raw;
  } else {
    const max = proportionalMax(prop);
    scaled = max !== null ? (raw / max) * 100 : raw;
  }
  if (!Number.isFinite(scaled)) return 0;
  return Math.max(0, Math.min(100, Math.round(scaled)));
}

/** The raw value for `percent`, snapped to the nearest level for a value-list speed property. */
export function rawFromPercent(percent: number, prop: MiotProperty, curve: PercentCurve): number {
  const p = Math.max(0, Math.min(100, percent)) / 100;
  if (curve === "speed") {
    const hasRange = prop.valueRange && prop.valueRange.length >= 2 && (prop.valueRange[1] ?? 0) > (prop.valueRange[0] ?? 0);
    const list = prop.valueList;
    const bounds = speedBounds(prop);
    if (!hasRange && list && list.length > 0) {
      if (!bounds) return list[0]!.value;
      const target = bounds.lo > 1 ? bounds.lo + p * (bounds.hi - bounds.lo) : p * bounds.hi;
      let best = list[0]!;
      for (const item of list) {
        if (Math.abs(item.value - target) < Math.abs(best.value - target)) best = item;
      }
      return best.value;
    }
    if (bounds) {
      const { lo, hi } = bounds;
      return lo > 1 ? lo + p * (hi - lo) : hi !== 100 ? p * hi : p * 100;
    }
  }
  const max = proportionalMax(prop);
  return max !== null ? p * max : p * 100;
}
