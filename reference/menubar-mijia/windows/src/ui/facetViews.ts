import { t } from "../core/i18n.js";
import { facetTitle, facetValue, formatReading } from "../core/miotFacetText.js";
import type { FacetControl } from "../core/models.js";

/**
 * 更多设置 rows (#5): one generic facet each, rendered from the shared `deviceProfiles.json`
 * rules. A facet outside the low-risk whitelist stays disabled until the user turns on
 * 允许写入高级/高风险设备属性, and a write that still counts as high risk asks once more.
 */

/** False for a write outside the low-risk whitelist while 允许写入高级属性 is off. */
export function facetWriteEnabled(control: FacetControl, allowAdvanced: boolean): boolean {
  return control.isWritable && control.target !== null && (control.writesByDefault || allowAdvanced);
}

/**
 * True when the write still needs a second tap (#5, #6): a facet outside the low-risk whitelist
 * that turns something on or up. Turning a device off, or choosing an off-ish option, only makes
 * the home safer, so it stays direct (macOS `ManualControlPolicy` facet rule).
 */
export function facetWriteIsHazardous(control: FacetControl, next: unknown): boolean {
  if (!control.isWritable || control.writesByDefault) return false;
  if (next === false || next === 0) return false;
  if (control.name === "mode" && typeof next === "number") {
    const option = control.options.find((item) => item.value === next);
    if (option && ["off", "close", "closed", "standby"].includes(option.label.trim().toLowerCase())) return false;
  }
  return true;
}

function row(control: FacetControl, deviceId: string, allowAdvanced: boolean, escapeHtml: (v: string) => string): string {
  const id = escapeHtml(deviceId);
  const controlId = escapeHtml(control.id);
  const label = escapeHtml(facetTitle(control));
  const value = escapeHtml(facetValue(control));
  const enabled = facetWriteEnabled(control, allowAdvanced);
  const disabledAttr = enabled ? "" : " disabled aria-disabled=\"true\"";
  const highRisk = !control.writesByDefault && control.isWritable;
  if (control.kind === "reading" || !control.isWritable) {
    return `<div class="cc-facet-row is-reading">
      <span class="cc-facet-label">${label}</span>
      <strong class="cc-facet-value">${value}</strong>
    </div>`;
  }
  if (control.kind === "select" && control.options.length > 0) {
    const current = typeof control.value === "number" ? Math.trunc(control.value) : null;
    return `<div class="cc-facet-row is-select">
      <span class="cc-facet-label">${label}</span>
      <div class="cc-segmented-pills">
        ${control.options
          .map(
            (option) => `<button class="cc-seg-pill ${current === option.value ? "is-active" : ""}"
              data-facet-select="${id}|${controlId}|${option.value}"${disabledAttr}
              aria-pressed="${current === option.value}">
              ${escapeHtml(option.label)}
            </button>`,
          )
          .join("")}
      </div>
    </div>`;
  }
  if (control.kind === "slider" && control.range) {
    const current = typeof control.value === "number" ? control.value : control.range.min;
    return `<div class="cc-facet-row is-slider">
      <span class="cc-facet-label">${label}</span>
      <input class="cc-range is-blue" type="range"
        min="${formatReading(control.range.min)}" max="${formatReading(control.range.max)}"
        step="${formatReading(control.range.step)}" value="${formatReading(current)}"
        data-facet-number="${id}|${controlId}" aria-label="${label}"
        aria-valuemin="${formatReading(control.range.min)}" aria-valuemax="${formatReading(control.range.max)}"
        aria-valuenow="${formatReading(current)}" aria-valuetext="${value}"${disabledAttr} />
      <strong class="cc-facet-value">${value}</strong>
    </div>`;
  }
  if (control.kind === "button") {
    return `<div class="cc-facet-row is-button">
      <button class="cc-seg-pill" data-facet-action="${id}|${controlId}"${disabledAttr}>
        ${label}
      </button>
    </div>`;
  }
  return `<div class="cc-facet-row is-toggle">
    <span class="cc-facet-label">${label}</span>
    <button class="cc-seg-pill ${control.value === true || control.value === 1 ? "is-active" : ""} ${highRisk ? "is-high-risk" : ""}"
      data-facet-toggle="${id}|${controlId}" aria-pressed="${control.value === true || control.value === 1}"${disabledAttr}>
      ${value}
    </button>
  </div>`;
}

/** The 更多设置 section appended to every detail drawer; empty when the device has no facets. */
export function renderMoreSettings(options: {
  readonly deviceId: string;
  readonly facets: readonly FacetControl[];
  readonly allowAdvanced: boolean;
  readonly escapeHtml: (value: string) => string;
}): string {
  if (options.facets.length === 0) return "";
  const highValue = options.facets.filter((control) => control.isHighValue);
  const rest = options.facets.filter((control) => !control.isHighValue);
  const ordered = [...highValue, ...rest];
  return `<div class="cc-facet-section">
    <div class="cc-slider-label"><span>${t("更多设置")}</span></div>
    ${ordered.map((control) => row(control, options.deviceId, options.allowAdvanced, options.escapeHtml)).join("")}
  </div>`;
}
