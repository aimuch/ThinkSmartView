import SwiftUI
import AppKit
import MijiaCore

// MARK: - Metrics

/// One owner for the room-order list geometry. The drag derives a row index from these numbers, so
/// a row that changes height without changing here would land in the wrong slot (standards §10).
enum RoomOrderMetrics {
    static let rowHeight: CGFloat = 30
    static let rowSpacing: CGFloat = 4
    static var rowStep: CGFloat { rowHeight + rowSpacing }
    static let cornerRadius: CGFloat = 9
    /// The band at each edge of the visible list that starts auto-scrolling (§5.4).
    static let edgeTrigger: CGFloat = 24
    static let minAutoScrollSpeed: CGFloat = 120
    static let maxAutoScrollSpeed: CGFloat = 600
    /// A press that travels less than this is a click, not a pick-up: no long press first (§5.4).
    static let dragStartThreshold: CGFloat = 3
    /// The trailing column the 24 pt rename button owns: a press there renames instead of dragging,
    /// and everything else on the row is a pick-up target (§5.3).
    static let trailingActionColumn: CGFloat = 30

    /// 600 pt/s at the very edge falling linearly to 120 pt/s at the inner end of the band; 0
    /// outside it. Stopping the moment the pointer leaves the band is what keeps a fast drag from
    /// overshooting the row the user aimed at.
    static func autoScrollSpeed(distanceToEdge: CGFloat) -> CGFloat {
        guard distanceToEdge >= 0, distanceToEdge < edgeTrigger else { return 0 }
        let progress = 1 - distanceToEdge / edgeTrigger
        return minAutoScrollSpeed + (maxAutoScrollSpeed - minAutoScrollSpeed) * progress
    }

    /// The row under a point, in list-content coordinates. The 4 pt gap after each row belongs to
    /// that row, and a point past the last row belongs to none of them.
    static func rowIndex(atContentY y: CGFloat, rowCount: Int) -> Int? {
        guard rowCount > 0, y >= 0 else { return nil }
        let index = Int(floor(y / rowStep))
        return index < rowCount ? index : nil
    }

    /// The slot the picked-up row would land in, given where its top edge is now. Travelling half a
    /// row flips it to the neighbour, which is the "crosses the neighbour's midline" rule of §5.4;
    /// because it only reads the pointer, one press can travel from row 16 to row 1 without the
    /// row's own position feeding back into the decision.
    static func targetIndex(rowTop: CGFloat, rowCount: Int) -> Int? {
        guard rowCount > 0 else { return nil }
        let raw = Int((rowTop + rowStep / 2) / rowStep)
        return min(max(raw, 0), rowCount - 1)
    }
}

// MARK: - Backing scroll view

/// Hands the list its backing `NSScrollView`. SwiftUI's `ScrollView` publishes neither the offset
/// nor a way to drive it per frame, which is what edge auto-scroll and "keep the focused row in
/// view" need; the repo already bridges the same way for horizontal wheel scrolling (#146 §5.4).
final class RoomOrderScrollProbe: NSView {
    var onScrollView: ((NSScrollView) -> Void)?

    #if DEBUG
    /// Test hook. Edge auto-scroll and the pointer-to-row mapping both depend on this probe finding
    /// the backing `NSScrollView`; when it was attached outside the scrolling content it silently
    /// resolved to `nil` and the whole drag worked in stale coordinates. The panel snapshot test
    /// asserts this is non-nil, mirroring how `LayoutUIState.cardGlobalFrames` exposes geometry to
    /// UI tests.
    private(set) static var lastResolvedScrollView: NSScrollView?
    #endif

    override func viewDidMoveToWindow() {
        super.viewDidMoveToWindow()
        publish()
    }

    override func viewDidMoveToSuperview() {
        super.viewDidMoveToSuperview()
        publish()
    }

    override func layout() {
        super.layout()
        publish()
    }

    private func publish() {
        guard let scrollView = enclosingScrollView else { return }
        #if DEBUG
        Self.lastResolvedScrollView = scrollView
        #endif
        onScrollView?(scrollView)
    }

    override func hitTest(_ point: NSPoint) -> NSView? { nil }
}

private struct RoomOrderScrollAttachment: NSViewRepresentable {
    let onReady: (NSScrollView) -> Void

    func makeNSView(context: Context) -> RoomOrderScrollProbe {
        let probe = RoomOrderScrollProbe()
        probe.onScrollView = onReady
        return probe
    }

    func updateNSView(_ nsView: RoomOrderScrollProbe, context: Context) {
        nsView.onScrollView = onReady
    }
}

// MARK: - Row data

private struct RoomOrderRowData: Identifiable, Equatable {
    let id: String
    let title: String
    let originalName: String
    let deviceCount: Int
    /// A room that joined while the session is open (§5.8).
    let isNew: Bool
    /// Rooms take a local alias; device kinds have fixed names.
    let canRename: Bool
}

private struct RoomOrderDrag {
    let id: String
    /// Where inside the picked-up row the pointer grabbed it, in content coordinates.
    let grabFromTop: CGFloat
    /// Latest pointer position in the list's own (viewport) space.
    var pointerY: CGFloat
}

// MARK: - The list

/// The room-order list that replaces the device grid while the panel is in `roomOrder` (§5.1).
///
/// Rows are 30 pt tall at a fixed pitch, so the drag converts a pointer position into a row index
/// arithmetically instead of measuring frames: the target never depends on where the row currently
/// sits, which is what lets one continuous press travel from row 16 to row 1 while the list scrolls
/// under the pointer.
@MainActor
struct RoomOrderEditor: View {
    let store: HomeStore

    @Environment(\.effectiveReduceMotion) private var reduceMotion
    @State private var ui = LayoutUIState.shared
    @State private var drag: RoomOrderDrag?
    @State private var backing: NSScrollView?
    @State private var viewport: CGSize = .zero
    @State private var autoScrollSpeed: CGFloat = 0
    @State private var autoScrollTask: Task<Void, Never>?

    private static let space = "room_order_list"
    private static let tick: Double = 0.016

    var body: some View {
        let rows = rowDatas()
        VStack(alignment: .leading, spacing: 0) {
            listHeader(rows: rows)

            ScrollViewReader { proxy in
                ScrollView(.vertical, showsIndicators: false) {
                    VStack(spacing: RoomOrderMetrics.rowSpacing) {
                        ForEach(Array(rows.enumerated()), id: \.element.id) { index, row in
                            rowView(row, index: index, rows: rows)
                                .id(row.id)
                        }
                    }
                    .padding(.horizontal, 13)
                    .padding(.bottom, 12)
                    // The probe has to live *inside* the scrolling content: `enclosingScrollView` is
                    // only non-nil for a view in the document view (same rule the repo's
                    // `horizontalWheelScroll()` follows). On the ScrollView itself it resolves to nil,
                    // and then edge auto-scroll and the pointer-to-row mapping both silently break.
                    .background(
                        RoomOrderScrollAttachment { scrollView in
                            // Reported from AppKit layout passes; hop off the current update to avoid
                            // mutating SwiftUI state while it is reading.
                            DispatchQueue.main.async {
                                if backing !== scrollView { backing = scrollView }
                            }
                        }
                    )
                }
                .defaultScrollAnchor(.top)
                .coordinateSpace(name: Self.space)
                .background(
                    GeometryReader { geo in
                        Color.clear
                            .onAppear { viewport = geo.size }
                            .onChange(of: geo.size) { _, size in viewport = size }
                    }
                )
                .simultaneousGesture(dragGesture)
                // ↑/↓ and the VoiceOver moves can land on a row scrolled out of the list: move just
                // enough to bring it back, never re-center a row that is already visible. Keyed on the
                // row's *rank*, not its id: ⌘↑/⌘↓/⌘Home/⌘End move the focused row without changing
                // `focusedRoomId`, and an id-only hook would keep reordering an invisible row.
                .onChange(of: focusedRowIndex) { _, _ in
                    guard let focused = ui.focusedRoomId else { return }
                    withAnimation(motion) { proxy.scrollTo(focused, anchor: nil) }
                }
            }

            listFooter
        }
        // Leaving the mode mid-drag (Esc, Done, hiding the panel) tears the view down without an
        // `onEnded`, so the auto-scroll loop has to be stopped here or it keeps ticking at 60 fps.
        .onDisappear { stopAutoScroll() }
    }

    // MARK: Rows

    /// Rank of the focused row, used as the scroll trigger.
    private var focusedRowIndex: Int? {
        guard let focused = ui.focusedRoomId else { return nil }
        return rowDatas().firstIndex { $0.id == focused }
    }

    /// The rooms or kinds the list is ordering right now.
    private var items: [RoomArea] { RoomOrderKeyboard.items(store: store, ui: ui) }

    private var isKinds: Bool { ui.orderTarget == .kinds }

    private func rowDatas() -> [RoomOrderRowData] {
        guard let draft = ui.activeOrderDraft else { return [] }
        let items = items
        let hidden = store.hiddenDeviceIds
        let kinds = isKinds
        return draft.visibleOrder(for: items).compactMap { id in
            guard let item = items.first(where: { $0.id == id }) else { return nil }
            let name = kinds ? item.name : L10n.displayRoomName(item.name)
            let count = store.devices.filter { device in
                guard !hidden.contains(device.id) else { return false }
                return kinds ? device.kind.tag.rawValue == id : device.roomId == id
            }.count
            return RoomOrderRowData(
                id: id,
                title: RoomPrefs.alias(for: id, in: draft.aliases) ?? name,
                originalName: name,
                deviceCount: count,
                isNew: draft.isFresh(id),
                canRename: !kinds
            )
        }
    }

    @ViewBuilder
    private func rowView(_ row: RoomOrderRowData, index: Int, rows: [RoomOrderRowData]) -> some View {
        let isDragging = drag?.id == row.id
        let offset = dragOffset(forDragging: isDragging, at: index)
        RoomOrderRowView(
            row: row,
            index: index,
            total: rows.count,
            isDragging: isDragging,
            isFocused: ui.focusedRoomId == row.id,
            dragOffset: offset,
            onRename: { layoutOpenRoom(row.id) },
            onShift: { shift(row.id, by: $0) },
            onMoveEdge: { moveEdge(row.id, toFront: $0) }
        )
    }

    /// Keeps the picked-up row under the pointer: its layout box already sits in the slot the order
    /// gives it now, so only the drawing moves.
    private func dragOffset(forDragging isDragging: Bool, at index: Int) -> CGFloat {
        guard isDragging, let drag else { return 0 }
        return contentY(drag.pointerY) - drag.grabFromTop - CGFloat(index) * RoomOrderMetrics.rowStep
    }

    // MARK: Header & footer

    private func listHeader(rows: [RoomOrderRowData]) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            HStack(alignment: .center, spacing: 6) {
                if showsTargetPicker {
                    targetPicker
                } else {
                    Text(isKinds ? L10n.tr("类型顺序", "Kind order") : L10n.tr("房间顺序", "Room order"))
                        .font(.system(size: DesignTokens.TypeSize.body, weight: .semibold))
                        .foregroundStyle(.primary)
                }
                Spacer(minLength: 6)
                Text(isKinds
                    ? L10n.tr("\(rows.count) 个类型", "\(rows.count) kinds")
                    : L10n.tr("\(rows.count) 个房间", "\(rows.count) rooms"))
                    .font(.system(size: DesignTokens.TypeSize.caption, weight: .semibold))
                    .monospacedDigit()
                    .foregroundStyle(.secondary)
            }
            // The long explanation lives here once; the status line above only says 调整房间顺序 (§5.9).
            Text(isKinds
                ? L10n.tr("拖动调整类型筛选的显示顺序", "Drag to set the kind filter order")
                : L10n.tr("拖动调整首页显示顺序", "Drag to set the home order"))
                .font(.system(size: DesignTokens.TypeSize.caption, weight: .medium))
                .foregroundStyle(.secondary)
        }
        .padding(.horizontal, 13)
        .padding(.top, 10)
        .padding(.bottom, 6)
    }

    /// 房间 | 类型, shown only when both lists have something to order.
    private var showsTargetPicker: Bool {
        store.rooms.count >= 2 && store.kindChips.count >= 2
    }

    private var targetPicker: some View {
        Picker(selection: Binding(
            get: { ui.orderTarget },
            set: { switchTarget(to: $0) }
        )) {
            Text(L10n.tr("房间", "Rooms")).tag(OrderListTarget.rooms)
            Text(L10n.tr("类型", "Kinds")).tag(OrderListTarget.kinds)
        } label: {
            Text(L10n.tr("排序对象", "Order"))
        }
        .pickerStyle(.segmented)
        .labelsHidden()
        .controlSize(.small)
        .fixedSize()
        .accessibilityHint(Text(L10n.tr("在房间顺序和类型顺序之间切换", "Switch between room order and kind order")))
    }

    private func switchTarget(to target: OrderListTarget) {
        guard target != ui.orderTarget else { return }
        stopAutoScroll()
        drag = nil
        withAnimation(motion) {
            ui.switchOrderTarget(
                to: target,
                items: target == .rooms ? store.rooms : KindOrderPrefs.items(for: store.kindChips)
            )
        }
    }

    private var listFooter: some View {
        HStack {
            Button {
                restoreDefaultOrder()
            } label: {
                Label(L10n.tr("恢复默认顺序", "Restore default order"), systemImage: "arrow.counterclockwise")
                    .font(.system(size: DesignTokens.TypeSize.caption, weight: .medium))
            }
            .buttonStyle(PillActionButtonStyle())
            .accessibilityHint(Text(isKinds
                ? L10n.tr("把类型恢复成默认顺序", "Restore the kinds to their default order")
                : L10n.tr(
                    "把当前家庭的房间恢复成默认顺序，不改动名称",
                    "Restore this home's rooms to their default order, leaving names untouched"
                )))
            .panelHelp(isKinds
                ? L10n.tr("恢复类型的默认顺序；点取消可整体回滚", "Restore the default kind order; Cancel rolls it all back")
                : L10n.tr(
                    "只重排当前可见房间的顺序，其他家庭和账号的顺序保留；点取消可整体回滚",
                    "Re-ranks the visible rooms only, keeping other homes' order; Cancel rolls it all back"
                ))

            Spacer()
        }
        .padding(.horizontal, 13)
        .padding(.top, 4)
        .padding(.bottom, 8)
    }

    // MARK: Drag

    private var dragGesture: some Gesture {
        DragGesture(minimumDistance: RoomOrderMetrics.dragStartThreshold, coordinateSpace: .named(Self.space))
            .onChanged { value in
                if drag == nil {
                    beginDrag(with: value)
                } else {
                    continueDrag(with: value)
                }
            }
            .onEnded { _ in endDrag() }
    }

    private func beginDrag(with value: DragGesture.Value) {
        let rows = rowDatas()
        guard rows.count >= 2, viewport.height > 0 else { return }
        // The rename column answers a click, never a pick-up (§5.3). Kind rows have no rename button.
        guard isKinds || value.startLocation.x < viewport.width - RoomOrderMetrics.trailingActionColumn else { return }
        guard let index = RoomOrderMetrics.rowIndex(atContentY: contentY(value.startLocation.y), rowCount: rows.count) else { return }
        let grabFromTop = contentY(value.startLocation.y) - CGFloat(index) * RoomOrderMetrics.rowStep
        drag = RoomOrderDrag(id: rows[index].id, grabFromTop: grabFromTop, pointerY: value.location.y)
        updateEdgeSpeed(pointerY: value.location.y)
    }

    private func continueDrag(with value: DragGesture.Value) {
        guard var session = drag else { return }
        session.pointerY = value.location.y
        drag = session
        updateEdgeSpeed(pointerY: value.location.y)
        settleTarget()
    }

    private func endDrag() {
        stopAutoScroll()
        guard drag != nil else { return }
        // The old strip lost the last move inside its throttle window; settle once before letting go.
        settleTarget()
        withAnimation(motion) { drag = nil }
    }

    /// Moves the dragged row when its centre crosses a neighbour's midline. The target index comes
    /// from the pointer alone, so it stays put while the rows around it animate.
    private func settleTarget() {
        guard let session = drag else { return }
        let rows = rowDatas()
        guard let current = rows.firstIndex(where: { $0.id == session.id }) else {
            // The room vanished mid-drag: drop the gesture, keep the list stable (§5.8).
            drag = nil
            stopAutoScroll()
            return
        }
        let top = contentY(session.pointerY) - session.grabFromTop
        guard let target = RoomOrderMetrics.targetIndex(rowTop: top, rowCount: rows.count), target != current
        else { return }
        withAnimation(motion) {
            ui.updateActiveOrderDraft(items: items) { draft in
                draft.move(id: session.id, toVisibleIndex: target, visible: rows.map(\.id))
            }
        }
        NSHapticFeedbackManager.defaultPerformer.perform(.alignment, performanceTime: .now)
    }

    private func contentY(_ viewportY: CGFloat) -> CGFloat {
        viewportY + (backing?.contentView.bounds.origin.y ?? 0)
    }

    // MARK: Edge auto-scroll

    private func updateEdgeSpeed(pointerY: CGFloat) {
        guard viewport.height > 0 else { return }
        let speed: CGFloat
        if pointerY < RoomOrderMetrics.edgeTrigger {
            speed = -RoomOrderMetrics.autoScrollSpeed(distanceToEdge: max(0, pointerY))
        } else if pointerY > viewport.height - RoomOrderMetrics.edgeTrigger {
            speed = RoomOrderMetrics.autoScrollSpeed(distanceToEdge: max(0, viewport.height - pointerY))
        } else {
            speed = 0
        }
        autoScrollSpeed = speed
        if speed == 0 {
            stopAutoScroll()
        } else {
            startAutoScroll()
        }
    }

    private func startAutoScroll() {
        guard autoScrollTask == nil else { return }
        autoScrollTask = Task { @MainActor in
            while !Task.isCancelled {
                let speed = autoScrollSpeed
                if speed == 0 || drag == nil || ui.editMode != .roomOrder { break }
                scrollContent(by: speed * Self.tick)
                // The content moved under a stationary pointer, so the row under it may have changed.
                settleTarget()
                try? await Task.sleep(for: .seconds(Self.tick))
            }
            // The handle is cleared by `stopAutoScroll`; clearing it here could nil out a newer task
            // started between the cancellation and this line.
        }
    }

    private func stopAutoScroll() {
        autoScrollTask?.cancel()
        autoScrollTask = nil
        autoScrollSpeed = 0
    }

    private func scrollContent(by delta: CGFloat) {
        guard let backing, let document = backing.documentView else { return }
        let clip = backing.contentView
        let maxY = max(0, document.frame.height - clip.bounds.height)
        let next = min(max(0, clip.bounds.origin.y + delta), maxY)
        guard abs(next - clip.bounds.origin.y) > 0.01 else { return }
        clip.scroll(to: NSPoint(x: clip.bounds.origin.x, y: next))
        backing.reflectScrolledClipView(clip)
    }

    // MARK: Row actions

    private var motion: Animation? { reduceMotion ? nil : AppleMotion.snappySpring }

    private func layoutOpenRoom(_ id: String) {
        ui.renamingRoomId = id
    }

    private func shift(_ id: String, by delta: Int) {
        ui.focusedRoomId = id
        RoomOrderKeyboard.moveFocused(bySlots: delta, rooms: items, ui: ui, motion: motion)
    }

    private func moveEdge(_ id: String, toFront: Bool) {
        ui.focusedRoomId = id
        RoomOrderKeyboard.moveFocusedToEdge(toFront: toFront, rooms: items, ui: ui, motion: motion)
    }

    /// Provider order for the visible rooms only; aliases and other homes' slots are untouched, and
    /// the draft is still a draft, so 取消 can roll it back (§5.7).
    private func restoreDefaultOrder() {
        withAnimation(motion) {
            let items = items
            ui.updateActiveOrderDraft(items: items) { $0.resetOrder(rooms: items) }
        }
    }

}

// MARK: - One row

/// A row of the room-order list. Owns its own hover state so pointing at the list redraws one row
/// instead of the whole panel.
@MainActor
private struct RoomOrderRowView: View {
    let row: RoomOrderRowData
    let index: Int
    let total: Int
    let isDragging: Bool
    let isFocused: Bool
    let dragOffset: CGFloat
    let onRename: () -> Void
    let onShift: (Int) -> Void
    let onMoveEdge: (Bool) -> Void

    @Environment(\.effectiveReduceMotion) private var reduceMotion
    @State private var isHovered = false

    var body: some View {
        let shape = RoundedRectangle(cornerRadius: RoomOrderMetrics.cornerRadius, style: .continuous)
        ZStack {
            // The slot the row is currently ranked in: while the row floats, this is where it lands.
            if isDragging {
                VStack {
                    if dragOffset > 0 { Spacer() }
                    RoundedRectangle(cornerRadius: 1, style: .continuous)
                        .fill(Color.accentColor)
                        .frame(height: 2)
                    if dragOffset <= 0 { Spacer() }
                }
                .padding(.horizontal, 2)
                .allowsHitTesting(false)
            }

            HStack(spacing: 0) {
                label
                if row.canRename { renameButton }
            }
            .background(shape.fill(Color.primary.opacity(isHovered || isFocused ? 0.05 : 0)))
            .overlay {
                if isFocused {
                    shape.strokeBorder(Color.accentColor.opacity(0.75), lineWidth: 1.6)
                }
            }
            .offset(y: dragOffset)
            .shadow(
                color: .black.opacity(isDragging && !reduceMotion ? 0.18 : 0),
                radius: isDragging ? 8 : 0,
                y: isDragging ? 2 : 0
            )
            .scaleEffect(isDragging && !reduceMotion ? 1.02 : 1)
        }
        .frame(height: RoomOrderMetrics.rowHeight)
        .contentShape(Rectangle())
        .onHover { isHovered = $0 }
        .animation(reduceMotion ? nil : AppleMotion.pressFeedback, value: isHovered)
        .zIndex(isDragging ? 2 : 0)
        // Pointer-free paths for everything the drag and the keyboard do (§5.3, §5.5), plus the
        // provider name when an alias hides it.
        .contextMenu {
            Button { onShift(-1) } label: { Label(L10n.tr("上移", "Move Up"), systemImage: "arrow.up") }
                .disabled(index == 0)
            Button { onShift(1) } label: { Label(L10n.tr("下移", "Move Down"), systemImage: "arrow.down") }
                .disabled(index == total - 1)
            Button { onMoveEdge(true) } label: {
                Label(L10n.tr("移到最前", "Move to Front"), systemImage: "arrow.up.to.line")
            }
            .disabled(index == 0)
            Button { onMoveEdge(false) } label: {
                Label(L10n.tr("移到最后", "Move to End"), systemImage: "arrow.down.to.line")
            }
            .disabled(index == total - 1)
            if row.canRename {
                Divider()
                Button { onRename() } label: { Label(L10n.tr("重命名房间…", "Rename Room…"), systemImage: "pencil") }
            }
            if row.title != row.originalName {
                Divider()
                Text(L10n.tr("原名：\(row.originalName)", "Original name: \(row.originalName)"))
            }
        }
    }

    /// The text part of the row is one VoiceOver element carrying the position and the four reorder
    /// actions; the rename button stays reachable as its own element.
    private var label: some View {
        HStack(spacing: 8) {
            Image(systemName: "line.3.horizontal")
                .font(.system(size: DesignTokens.TypeSize.caption, weight: .semibold))
                .foregroundStyle(.secondary)
                .frame(width: 20, height: 20)
                .accessibilityHidden(true)

            Text("\(index + 1)")
                .font(.system(size: DesignTokens.TypeSize.caption, weight: .semibold, design: .rounded))
                .monospacedDigit()
                .foregroundStyle(.secondary)
                .frame(width: 18, alignment: .trailing)
                .accessibilityHidden(true)

            Text(row.title)
                .font(.system(size: DesignTokens.TypeSize.body, weight: .medium))
                .foregroundStyle(.primary)
                .lineLimit(1)
                .panelHelp(tooltip)

            if row.isNew {
                Text(L10n.tr("新", "New"))
                    .font(.system(size: DesignTokens.TypeSize.micro, weight: .semibold))
                    .foregroundStyle(.secondary)
                    .padding(.horizontal, 5)
                    .padding(.vertical, 1.5)
                    .background(Color.primary.opacity(0.07), in: Capsule(style: .continuous))
                    .accessibilityHidden(true)
            }

            Spacer(minLength: 8)

            Text(deviceCountLabel)
                .font(.system(size: DesignTokens.TypeSize.control, weight: .medium))
                .monospacedDigit()
                .foregroundStyle(.secondary)
                .accessibilityHidden(true)
        }
        .padding(.horizontal, 8)
        .frame(maxWidth: .infinity, alignment: .leading)
        .contentShape(Rectangle())
        .accessibilityElement(children: .combine)
        .accessibilityLabel(Text(accessibilityLabel))
        .accessibilityAddTraits(isFocused ? .isSelected : [])
        .accessibilityAction(named: Text(L10n.tr("上移", "Move Up"))) { onShift(-1) }
        .accessibilityAction(named: Text(L10n.tr("下移", "Move Down"))) { onShift(1) }
        .accessibilityAction(named: Text(L10n.tr("移到最前", "Move to Front"))) { onMoveEdge(true) }
        .accessibilityAction(named: Text(L10n.tr("移到最后", "Move to End"))) { onMoveEdge(false) }
    }

    private var renameButton: some View {
        Button(action: onRename) {
            Image(systemName: "pencil")
                .font(.system(size: DesignTokens.TypeSize.caption, weight: .semibold))
                .foregroundStyle(.secondary)
                .frame(width: 24, height: 24)
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityLabel(Text(L10n.tr("重命名房间", "Rename Room")))
        .accessibilityHint(Text(L10n.tr("改这个房间在首页显示的名字", "Change the name this room shows on the home")))
        .panelHelp(L10n.tr("重命名这个房间", "Rename this room"))
    }

    private var deviceCountLabel: String {
        row.deviceCount == 1
            ? L10n.tr("1 台", "1 device")
            : L10n.tr("\(row.deviceCount) 台", "\(row.deviceCount) devices")
    }

    /// A long alias hides the provider name; the menu and tooltip still have to say what it was.
    private var tooltip: String {
        guard row.title != row.originalName else { return row.originalName }
        return L10n.tr(
            "\(row.title)（原名 \(row.originalName)）",
            "\(row.title) (was \(row.originalName))"
        )
    }

    private var accessibilityLabel: String {
        L10n.tr(
            "\(row.title)，\(deviceCountLabel)，第 \(index + 1) 个，共 \(total) 个",
            "\(row.title), \(deviceCountLabel), position \(index + 1) of \(total)"
        )
    }
}

// MARK: - Keyboard and VoiceOver moves

/// Row moves shared by the list view and the app-wide key monitor: both must run exactly one rule
/// for ↑↓, ⌘↑⌘↓, ⌘Home/⌘End and the VoiceOver actions (#146 §5.5).
@MainActor
enum RoomOrderKeyboard {
    /// Reduced motion drops the spring but keeps the reorder.
    static var defaultMotion: Animation? {
        NSWorkspace.shared.accessibilityDisplayShouldReduceMotion ? nil : AppleMotion.snappySpring
    }

    /// The items of the list on screen: rooms, or the device kinds as id/name pairs.
    static func items(store: HomeStore, ui: LayoutUIState = .shared) -> [RoomArea] {
        ui.orderTarget == .rooms ? store.rooms : KindOrderPrefs.items(for: store.kindChips)
    }

    static func visibleIds(rooms: [RoomArea], ui: LayoutUIState = .shared) -> [String] {
        ui.activeOrderDraft?.visibleOrder(for: rooms) ?? []
    }

    /// ↑/↓ moves the focus, wrapping at both ends so the list can be walked in either direction.
    static func moveFocus(_ delta: Int, rooms: [RoomArea], ui: LayoutUIState = .shared) {
        let ids = visibleIds(rooms: rooms, ui: ui)
        guard !ids.isEmpty else {
            ui.focusedRoomId = nil
            return
        }
        let current = ui.focusedRoomId.flatMap { ids.firstIndex(of: $0) }
        var next = (current ?? (delta > 0 ? -1 : 0)) + delta
        if next < 0 { next = ids.count - 1 }
        if next >= ids.count { next = 0 }
        ui.focusedRoomId = ids[next]
        announce(rooms: rooms, ui: ui)
    }

    /// ⌘↑ / ⌘↓: one visible slot, keeping the focus on the row that moved.
    static func moveFocused(bySlots slots: Int, rooms: [RoomArea], ui: LayoutUIState = .shared, motion: Animation? = nil) {
        guard let id = ui.focusedRoomId else { return }
        let ids = visibleIds(rooms: rooms, ui: ui)
        guard let from = ids.firstIndex(of: id), ids.indices.contains(from + slots) else { return }
        withAnimation(motion ?? defaultMotion) {
            ui.updateActiveOrderDraft(items: rooms) { $0.move(id: id, toVisibleIndex: from + slots, visible: ids) }
        }
        NSHapticFeedbackManager.defaultPerformer.perform(.alignment, performanceTime: .now)
        announce(rooms: rooms, ui: ui)
    }

    /// ⌘Home / ⌘End.
    static func moveFocusedToEdge(toFront: Bool, rooms: [RoomArea], ui: LayoutUIState = .shared, motion: Animation? = nil) {
        guard let id = ui.focusedRoomId else { return }
        withAnimation(motion ?? defaultMotion) {
            ui.updateActiveOrderDraft(items: rooms) { draft in
                if toFront { draft.moveToFront(id: id) } else { draft.moveToEnd(id: id) }
            }
        }
        NSHapticFeedbackManager.defaultPerformer.perform(.alignment, performanceTime: .now)
        announce(rooms: rooms, ui: ui)
    }

    /// VoiceOver needs to hear the new position, not just see the row slide.
    static func announce(rooms: [RoomArea], ui: LayoutUIState = .shared) {
        guard let id = ui.focusedRoomId else { return }
        let ids = visibleIds(rooms: rooms, ui: ui)
        guard let index = ids.firstIndex(of: id), let room = rooms.first(where: { $0.id == id }) else { return }
        let title = ui.activeOrderDraft.flatMap { RoomPrefs.alias(for: id, in: $0.aliases) }
            ?? (ui.orderTarget == .kinds ? room.name : L10n.displayRoomName(room.name))
        let text = L10n.tr(
            "\(title)，第 \(index + 1) 个，共 \(ids.count) 个",
            "\(title), position \(index + 1) of \(ids.count)"
        )
        AccessibilityNotification.Announcement(text).post()
    }
}
