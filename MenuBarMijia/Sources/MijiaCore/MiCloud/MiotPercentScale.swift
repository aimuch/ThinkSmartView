import Foundation

/// The one conversion between a MIoT property's raw value and the card's 0…100 percent.
///
/// The synthesizer decodes with it and `MiotCommandMapper` encodes with it, so a percent read off a
/// card writes back the raw value it came from. Before this module the two directions were written
/// separately and drifted: a fan whose levels are `[1, 2, 3]` read level 1 as 33 % and wrote 33 %
/// back as level 2.
public enum MiotPercentScale {
    public enum Curve: Sendable {
        /// `raw / max` when the range does not already end at 100 (brightness, volume, position).
        case proportional
        /// Fan speed: a range or level list starting above 1 maps its low end to 0 %, so an rpm
        /// range such as 300…2200 spreads across the slider.
        case speed
    }

    public static func percent(fromRaw raw: Double, property: MiotProperty, curve: Curve) -> Int {
        clampPercent(scaledPercent(raw: raw, property: property, curve: curve))
    }

    /// The raw value for `percent`, snapped to the nearest level for a value-list property.
    /// Range clamping and integer rounding stay with the writer's `coerce`, which knows the wire format.
    public static func raw(fromPercent percent: Double, property: MiotProperty, curve: Curve) -> Double {
        let p = Swift.max(0, Swift.min(100, percent)) / 100
        if curve == .speed {
            if let lo = property.rangeMin, let hi = property.rangeMax, hi > lo {
                return encodeSpeed(p, lo: lo, hi: hi)
            }
            if let list = property.valueList, !list.isEmpty {
                let values = list.map { Double($0.value) }
                guard let lo = values.min(), let hi = values.max(), hi > lo else { return values[0] }
                let target = encodeSpeed(p, lo: lo, hi: hi)
                return values.min(by: { abs($0 - target) < abs($1 - target) }) ?? values[0]
            }
        }
        if let max = property.rangeMax, max > 0, max != 100 { return p * max }
        return p * 100
    }

    private static func scaledPercent(raw: Double, property: MiotProperty, curve: Curve) -> Double {
        if curve == .speed {
            if let lo = property.rangeMin, let hi = property.rangeMax, hi > lo {
                return decodeSpeed(raw, lo: lo, hi: hi)
            }
            if let list = property.valueList, !list.isEmpty {
                let values = list.map { Double($0.value) }
                if let lo = values.min(), let hi = values.max(), hi > lo {
                    return decodeSpeed(raw, lo: lo, hi: hi)
                }
            }
        }
        if let max = property.rangeMax, max > 0, max != 100 { return raw / max * 100 }
        return raw
    }

    private static func decodeSpeed(_ raw: Double, lo: Double, hi: Double) -> Double {
        if lo > 1 { return (raw - lo) / (hi - lo) * 100 }
        return hi != 100 ? raw / hi * 100 : raw
    }

    private static func encodeSpeed(_ p: Double, lo: Double, hi: Double) -> Double {
        if lo > 1 { return lo + p * (hi - lo) }
        return hi != 100 ? p * hi : p * 100
    }

    private static func clampPercent(_ value: Double) -> Int {
        guard value.isFinite else { return 0 }
        return Swift.max(0, Swift.min(100, Int(value.rounded())))
    }
}
