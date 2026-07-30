"""生成合同事实确认模块的技术流程图。

本图只描述确认层，不调用模型，也不替代合同法律判断。它强调：
1. 原始提取值和证据不可被用户输入覆盖；
2. 用户只能通过五类明确动作改变有效事实状态；
3. ``correct`` 必须重新在脱敏合同中定位证据，找不到时转为补充；
4. 每次提交使用 revision 和 request_id 做并发控制及幂等审计。
"""

from html import escape
from pathlib import Path


WIDTH = 1800
HEIGHT = 1180
OUTPUT = Path(__file__).with_name("contract-fact-confirmation-flow.svg")


def add(lines: list[str], value: str) -> None:
    lines.append(value)


def text(
    lines: list[str],
    x: float,
    y: float,
    value: str,
    *,
    size: int = 14,
    fill: str = "#111827",
    weight: int = 400,
    anchor: str = "start",
    role: str = "label",
) -> None:
    add(
        lines,
        f'<text data-graph-role="{role}" x="{x}" y="{y}" font-size="{size}px" '
        f'fill="{fill}" font-weight="{weight}" text-anchor="{anchor}">{escape(value)}</text>',
    )


def card(
    lines: list[str],
    node_id: str,
    x: int,
    y: int,
    width: int,
    title: str,
    subtitle: str,
    *,
    fill: str,
    stroke: str,
    badge: str,
    badge_fill: str,
    status: str,
    status_fill: str,
    status_text: str,
    height: int = 112,
) -> None:
    bounds = f"{x} {y} {x + width} {y + height}"
    add(
        lines,
        f'<g id="{node_id}" data-node-id="{node_id}" data-graph-role="node" '
        f'data-graph-bounds="{bounds}">',
    )
    add(
        lines,
        f'<rect data-node-id="{node_id}" x="{x}" y="{y}" width="{width}" height="{height}" '
        f'rx="12" fill="{fill}" stroke="{stroke}" stroke-width="1.6"/>',
    )
    add(lines, f'<circle data-node-id="{node_id}" cx="{x + 32}" cy="{y + 34}" r="20" fill="{badge_fill}"/>')
    text(lines, x + 32, y + 39, badge, size=10, fill="#ffffff", weight=700, anchor="middle", role="node")
    text(lines, x + 67, y + 32, title, size=15, weight=600, role="node")
    text(lines, x + 67, y + 60, subtitle, size=12, fill="#4b5563", role="node")
    add(
        lines,
        f'<rect data-node-id="{node_id}" x="{x + width - 104}" y="{y + height - 31}" width="90" height="21" '
        f'rx="11" fill="{status_fill}"/>',
    )
    text(
        lines,
        x + width - 59,
        y + height - 16,
        status,
        size=10,
        fill=status_text,
        weight=600,
        anchor="middle",
        role="node",
    )
    add(lines, "</g>")


def decision(lines: list[str], node_id: str, cx: int, cy: int, half_width: int, half_height: int, title: str) -> None:
    bounds = f"{cx - half_width} {cy - half_height} {cx + half_width} {cy + half_height}"
    add(
        lines,
        f'<g id="{node_id}" data-node-id="{node_id}" data-graph-role="decision" '
        f'data-graph-bounds="{bounds}">',
    )
    add(
        lines,
        f'<polygon points="{cx},{cy - half_height} {cx + half_width},{cy} '
        f'{cx},{cy + half_height} {cx - half_width},{cy}" fill="#fff7ed" stroke="#ea580c" stroke-width="1.7"/>',
    )
    text(lines, cx, cy - 3, title, size=13, weight=600, anchor="middle", role="node")
    text(lines, cx, cy + 18, "本地校验", size=11, fill="#9a3412", anchor="middle", role="node")
    add(lines, "</g>")


def edge(
    lines: list[str],
    edge_id: str,
    path: str,
    *,
    source: str,
    target: str,
    color: str,
    marker: str,
    label: str | None = None,
    label_x: int | None = None,
    label_y: int | None = None,
    dashed: bool = False,
) -> None:
    dash = ' stroke-dasharray="7,5"' if dashed else ""
    add(
        lines,
        f'<path id="{edge_id}" data-edge-id="{edge_id}" data-graph-role="edge" '
        f'data-source="{source}" data-target="{target}" d="{path}" fill="none" '
        f'stroke="{color}" stroke-width="2"{dash} marker-end="url(#{marker})"/>',
    )
    if label:
        text(lines, label_x or 0, label_y or 0, label, size=11, fill=color, weight=600, anchor="middle")


def build_svg() -> str:
    lines: list[str] = []
    add(
        lines,
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}" '
        f'viewBox="0 0 {WIDTH} {HEIGHT}" role="img" aria-labelledby="title desc" '
        'data-generator="fireworks-tech-graph" data-schema-version="1" data-style-id="1" '
        'data-visual-theme="flat-icon" data-diagram-type="flowchart" data-semantic-profile="generic" '
        'data-quality-profile="standard" data-min-node-gap="40" data-min-container-gutter="20" '
        'data-min-label-clearance="4" data-min-segment-length="16">',
    )
    add(lines, '<title id="title">合同事实确认模块流程</title>')
    add(
        lines,
        '<desc id="desc">展示合同事实提取结果如何进入确认表单，经过确认、修改、补充、不适用、暂不确认五类操作，完成证据校验、有效事实快照、版本控制和审计持久化。</desc>',
    )
    add(
        lines,
        "<style>text{font-family:'Helvetica Neue',Helvetica,Arial,'PingFang SC','Microsoft YaHei','Microsoft JhengHei','SimHei',sans-serif;}</style>",
    )
    add(lines, "<defs>")
    for marker_id, color in (
        ("arrow-blue", "#2563eb"),
        ("arrow-purple", "#9333ea"),
        ("arrow-green", "#059669"),
        ("arrow-orange", "#ea580c"),
        ("arrow-gray", "#6b7280"),
    ):
        add(
            lines,
            f'<marker id="{marker_id}" markerWidth="10" markerHeight="7" refX="9" refY="3.5" orient="auto">'
            f'<polygon points="0 0, 10 3.5, 0 7" fill="{color}"/></marker>',
        )
    add(lines, "</defs>")
    add(lines, f'<rect data-graph-role="background" width="{WIDTH}" height="{HEIGHT}" fill="#ffffff"/>')

    text(lines, 60, 48, "合同事实确认模块流程", size=28, weight=600)
    text(
        lines,
        60,
        80,
        "把模型识别结果变成用户可确认的事实表单，并保留原始值、用户值与证据来源",
        size=14,
        fill="#6b7280",
    )
    text(lines, 1310, 43, "图例", size=12, fill="#6b7280", weight=600)
    add(lines, '<line x1="1310" y1="64" x2="1340" y2="64" stroke="#2563eb" stroke-width="2" marker-end="url(#arrow-blue)"/>')
    text(lines, 1350, 68, "确定性流程", size=12, fill="#374151")
    add(lines, '<line x1="1460" y1="64" x2="1490" y2="64" stroke="#9333ea" stroke-width="2" marker-end="url(#arrow-purple)"/>')
    text(lines, 1500, 68, "用户/状态变换", size=12, fill="#374151")
    add(lines, '<line x1="1630" y1="64" x2="1660" y2="64" stroke="#059669" stroke-width="2" marker-end="url(#arrow-green)"/>')
    text(lines, 1670, 68, "持久化/门禁", size=12, fill="#374151")

    # Lanes.
    add(lines, '<rect data-graph-role="container" x="45" y="105" width="1710" height="210" rx="14" fill="#f8fbff" stroke="#bfdbfe" stroke-width="1.4"/>')
    add(lines, '<rect data-graph-role="container" x="45" y="350" width="1710" height="420" rx="14" fill="#faf8ff" stroke="#ddd6fe" stroke-width="1.4"/>')
    add(lines, '<rect data-graph-role="container" x="45" y="805" width="1710" height="335" rx="14" fill="#fffdf8" stroke="#fed7aa" stroke-width="1.4"/>')
    text(lines, 70, 135, "① 进入确认表单", size=16, fill="#1d4ed8", weight=600)
    text(lines, 70, 380, "② 五类用户操作与证据边界", size=16, fill="#6b21a8", weight=600)
    text(lines, 70, 835, "③ 有效事实快照、审计与法律分析门禁", size=16, fill="#9a3412", weight=600)

    # Edges first, so cards remain readable.
    edge(lines, "e-extraction-form", "M410 215 H505", source="extraction-result", target="confirmation-form", color="#2563eb", marker="arrow-blue", label="facts + questions", label_x=458, label_y=192)
    edge(lines, "e-form-actions", "M845 215 H935", source="confirmation-form", target="user-actions", color="#9333ea", marker="arrow-purple", label="展示原始值/证据", label_x=890, label_y=192)
    edge(lines, "e-actions-router", "M1435 260 V330 H905 V495", source="user-actions", target="action-router", color="#9333ea", marker="arrow-purple", label="选择一项操作", label_x=1180, label_y=315)

    edge(lines, "e-router-confirm", "M805 540 V480 H610", source="action-router", target="confirm-contract", color="#059669", marker="arrow-green", label="confirm", label_x=708, label_y=520)
    edge(lines, "e-router-correct", "M905 495 V455 H860", source="action-router", target="correct-search", color="#9333ea", marker="arrow-purple", label="correct", label_x=875, label_y=475)
    edge(lines, "e-router-supplement", "M1005 540 V590 H1115", source="action-router", target="supplement-user", color="#9333ea", marker="arrow-purple", label="supplement", label_x=1050, label_y=570)
    edge(lines, "e-router-na", "M905 585 V700 H610 V686", source="action-router", target="not-applicable", color="#6b7280", marker="arrow-gray", label="not_applicable", label_x=770, label_y=715)
    edge(lines, "e-router-defer", "M1005 585 V690 H1405 V743 H1395", source="action-router", target="defer-review", color="#6b7280", marker="arrow-gray", label="defer", label_x=1080, label_y=670)

    edge(lines, "e-correct-found", "M860 430 H1150", source="correct-search", target="evidence-found", color="#9333ea", marker="arrow-purple")
    edge(lines, "e-found-yes", "M1230 470 V485", source="evidence-found", target="corrected-contract", color="#059669", marker="arrow-green", label="是", label_x=1260, label_y=478)
    edge(lines, "e-found-no", "M1310 430 H1530 V485", source="evidence-found", target="correction-rejected", color="#ea580c", marker="arrow-orange", label="否：转补充", label_x=1410, label_y=415)

    edge(lines, "e-confirm-snapshot", "M610 500 H630 V750 H680 V840", source="confirm-contract", target="effective-snapshot", color="#059669", marker="arrow-green", label="合同原值", label_x=650, label_y=725)
    edge(lines, "e-corrected-snapshot", "M1010 533 H930 V790 H745 V840", source="corrected-contract", target="effective-snapshot", color="#059669", marker="arrow-green", label="合同修正值 + 新证据", label_x=820, label_y=775)
    edge(lines, "e-supplement-snapshot", "M1115 638 H1040 V790 H745 V840", source="supplement-user", target="effective-snapshot", color="#9333ea", marker="arrow-purple", label="用户补充值", label_x=980, label_y=775)
    edge(lines, "e-na-snapshot", "M610 638 H650 V790 H745 V840", source="not-applicable", target="effective-snapshot", color="#6b7280", marker="arrow-gray", label="无适用值", label_x=680, label_y=775)
    edge(lines, "e-defer-status", "M1395 743 H1580 V960 H1570", source="defer-review", target="status-gate", color="#6b7280", marker="arrow-gray", label="保留待处理", label_x=1510, label_y=770)
    edge(lines, "e-reject-supplement", "M1530 581 V620 H1405 V638 H1395", source="correction-rejected", target="supplement-user", color="#ea580c", marker="arrow-orange", label="不得伪造合同证据", label_x=1510, label_y=610, dashed=True)
    edge(lines, "e-snapshot-audit", "M880 895 H990", source="effective-snapshot", target="audit-event", color="#059669", marker="arrow-green", label="保留三层 provenance", label_x=935, label_y=872)
    edge(lines, "e-audit-revision", "M1200 895 H1300", source="audit-event", target="revision-store", color="#059669", marker="arrow-green", label="事件 + revision", label_x=1250, label_y=872)
    edge(lines, "e-revision-gate", "M1510 895 H1600 V960 H1500", source="revision-store", target="status-gate", color="#059669", marker="arrow-green", label="幂等/乐观锁", label_x=1580, label_y=930)
    edge(lines, "e-gate-ready", "M1570 1016 H1600", source="status-gate", target="legal-gate", color="#059669", marker="arrow-green", label="全部必答项已解决", label_x=1585, label_y=988)
    edge(lines, "e-gate-pending", "M1320 1016 H1270 V1080 H1210", source="status-gate", target="pending-questions", color="#ea580c", marker="arrow-orange", label="仍缺失/暂不确认", label_x=1260, label_y=1100)

    # Top lane.
    card(lines, "extraction-result", 90, 165, 320, "提取结果", "ContractFact[] + 结构化问题", fill="#eff6ff", stroke="#93c5fd", badge="F", badge_fill="#2563eb", status="输入", status_fill="#dbeafe", status_text="#1d4ed8")
    card(lines, "confirmation-form", 505, 165, 340, "事实确认表单", "工资 / 期限 / 社保 / … + 原始证据", fill="#faf5ff", stroke="#d8b4fe", badge="UI", badge_fill="#9333ea", status="用户可编辑", status_fill="#ede9fe", status_text="#6b21a8")
    card(lines, "user-actions", 935, 165, 500, "用户动作", "确认 · 修改 · 补充 · 不适用 · 暂不确认", fill="#faf5ff", stroke="#d8b4fe", badge="5", badge_fill="#9333ea", status="显式选择", status_fill="#ede9fe", status_text="#6b21a8")

    # Middle lane.
    decision(lines, "action-router", 905, 540, 100, 45, "动作类型")
    card(lines, "confirm-contract", 360, 430, 250, "确认", "采用原始提取值与原证据", fill="#f0fdf4", stroke="#86efac", badge="✓", badge_fill="#059669", status="contract", status_fill="#dcfce7", status_text="#166534", height=96)
    card(lines, "correct-search", 620, 370, 240, "修改", "在脱敏合同中重新定位用户值", fill="#faf5ff", stroke="#d8b4fe", badge="C", badge_fill="#9333ea", status="需证据", status_fill="#ede9fe", status_text="#6b21a8", height=96)
    decision(lines, "evidence-found", 1230, 430, 80, 40, "找到证据?")
    card(lines, "corrected-contract", 1010, 485, 280, "合同修正值", "更新有效值，不覆盖原始提取", fill="#f0fdf4", stroke="#86efac", badge="E", badge_fill="#059669", status="contract", status_fill="#dcfce7", status_text="#166534", height=96)
    card(lines, "correction-rejected", 1380, 485, 300, "证据未找到", "拒绝伪造证据，提示改用补充", fill="#fff7ed", stroke="#fdba74", badge="!", badge_fill="#ea580c", status="需补充", status_fill="#fed7aa", status_text="#9a3412", height=96)
    card(lines, "supplement-user", 1115, 590, 280, "补充", "保存用户值，来源标记为 user", fill="#faf5ff", stroke="#d8b4fe", badge="U", badge_fill="#9333ea", status="user", status_fill="#ede9fe", status_text="#6b21a8", height=96)
    card(lines, "not-applicable", 360, 590, 250, "标记不适用", "有效值为空，保留原因备注", fill="#f3f4f6", stroke="#9ca3af", badge="—", badge_fill="#6b7280", status="none", status_fill="#e5e7eb", status_text="#4b5563", height=96)
    card(lines, "defer-review", 1115, 695, 280, "暂不确认", "不进入法律结论，保留待办", fill="#f3f4f6", stroke="#9ca3af", badge="…", badge_fill="#6b7280", status="deferred", status_fill="#e5e7eb", status_text="#4b5563", height=96)

    add(lines, '<rect data-graph-role="note" x="70" y="735" width="1040" height="28" rx="8" fill="#ffffff" stroke="#d1d5db" stroke-width="1.2"/>')
    text(lines, 90, 754, "边界：用户不能直接编辑 page_no、quote 或 char_start/end；证据只能由本地定位器产生。", size=12, fill="#4b5563", role="note")

    # Bottom lane.
    card(lines, "effective-snapshot", 610, 840, 270, "有效事实快照", "effective_value + source + state", fill="#eff6ff", stroke="#93c5fd", badge="S", badge_fill="#2563eb", status="可供规则层", status_fill="#dbeafe", status_text="#1d4ed8")
    card(lines, "audit-event", 990, 840, 210, "确认事件", "append-only JSONB", fill="#f0fdf4", stroke="#86efac", badge="A", badge_fill="#059669", status="可追溯", status_fill="#dcfce7", status_text="#166534")
    card(lines, "revision-store", 1300, 840, 210, "版本控制", "base_revision + request_id", fill="#f0fdf4", stroke="#86efac", badge="R", badge_fill="#059669", status="幂等", status_fill="#dcfce7", status_text="#166534")
    card(lines, "status-gate", 1320, 960, 250, "确认状态门禁", "pending / in_progress / completed", fill="#fff7ed", stroke="#fdba74", badge="G", badge_fill="#ea580c", status="状态机", status_fill="#fed7aa", status_text="#9a3412")
    card(lines, "legal-gate", 1600, 960, 120, "法律分析", "仅接收已确认事实", fill="#f0fdf4", stroke="#86efac", badge="→", badge_fill="#059669", status="ready", status_fill="#dcfce7", status_text="#166534", height=112)
    card(lines, "pending-questions", 930, 1000, 280, "待补充问题", "缺失 / 冲突 / 暂不确认项", fill="#fff7ed", stroke="#fdba74", badge="?", badge_fill="#ea580c", status="阻断", status_fill="#fed7aa", status_text="#9a3412")

    add(lines, '<rect data-graph-role="note" x="70" y="1080" width="800" height="26" rx="8" fill="#ffffff" stroke="#d1d5db" stroke-width="1.2"/>')
    text(lines, 90, 1098, "原始值、用户值、证据来源分层保存；报告必须显示来源，不能把补充值伪装成合同事实。", size=12, fill="#4b5563", role="note")

    add(lines, "</svg>")
    return "\n".join(lines) + "\n"


def main() -> None:
    OUTPUT.write_text(build_svg(), encoding="utf-8", newline="\n")
    print(f"generated: {OUTPUT}")


if __name__ == "__main__":
    main()
