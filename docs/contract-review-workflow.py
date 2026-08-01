"""生成合同审查 Workflow v0.1 详细图。

蓝色表示当前已实现的确定性节点，青色表示可复用/外部资料适配，灰色表示
尚未完成治理或专家验收的工作。报告会持久化并回到同一 session，图只描述数据和控制流，不代表法律结论。
"""

from html import escape
from pathlib import Path

WIDTH = 1500
HEIGHT = 900
OUTPUT = Path(__file__).with_name("contract-review-workflow.svg")


def add(lines: list[str], value: str) -> None:
    lines.append(value)


def label(
    lines: list[str],
    x: float,
    y: float,
    value: str,
    *,
    size: int = 14,
    fill: str = "#111827",
    weight: int = 400,
    anchor: str = "start",
) -> None:
    add(
        lines,
        f'<text x="{x}" y="{y}" font-size="{size}px" fill="{fill}" '
        f'font-weight="{weight}" text-anchor="{anchor}">{escape(value)}</text>',
    )


def lines_text(
    lines: list[str],
    x: float,
    y: float,
    values: list[str],
    *,
    size: int = 13,
    fill: str = "#374151",
    weight: int = 400,
    anchor: str = "middle",
    gap: int = 19,
) -> None:
    add(
        lines,
        f'<text x="{x}" y="{y}" font-size="{size}px" fill="{fill}" '
        f'font-weight="{weight}" text-anchor="{anchor}">',
    )
    for index, value in enumerate(values):
        dy = 0 if index == 0 else gap
        add(lines, f'<tspan x="{x}" dy="{dy}px">{escape(value)}</tspan>')
    add(lines, "</text>")


def card(
    lines: list[str],
    node_id: str,
    x: int,
    y: int,
    width: int,
    height: int,
    title: str,
    body: list[str],
    *,
    fill: str,
    stroke: str,
    badge: str,
    badge_fill: str,
    status: str,
    status_fill: str,
    status_text: str,
) -> None:
    add(
        lines,
        f'<g id="{node_id}" data-graph-role="node"><rect x="{x}" y="{y}" '
        f'width="{width}" height="{height}" rx="12" fill="{fill}" '
        f'stroke="{stroke}" stroke-width="1.7"/>',
    )
    add(lines, f'<circle cx="{x + 31}" cy="{y + 34}" r="18" fill="{badge_fill}"/>')
    label(lines, x + 31, y + 39, badge, size=10, fill="#ffffff", weight=700, anchor="middle")
    label(lines, x + 60, y + 31, title, size=16, weight=650)
    lines_text(lines, x + 60, y + 55, body, size=12, anchor="start", gap=17)
    add(
        lines,
        f'<rect x="{x + width - 92}" y="{y + height - 31}" width="78" height="22" '
        f'rx="11" fill="{status_fill}"/>',
    )
    label(lines, x + width - 53, y + height - 16, status, size=11, fill=status_text, weight=650, anchor="middle")
    add(lines, "</g>")


def decision(lines: list[str], node_id: str, cx: int, cy: int) -> None:
    points = f"{cx},{cy-54} {cx+92},{cy} {cx},{cy+54} {cx-92},{cy}"
    add(
        lines,
        f'<g id="{node_id}" data-graph-role="node"><polygon points="{points}" '
        'fill="#f3f4f6" stroke="#9ca3af" stroke-width="1.7"/>',
    )
    lines_text(lines, cx, cy - 7, ["事实是否", "已确认？"], size=14, weight=650)
    add(lines, "</g>")


def edge(
    lines: list[str],
    edge_id: str,
    path: str,
    source: str,
    target: str,
    *,
    color: str = "#2563eb",
    marker: str = "arrow-blue",
    dashed: bool = False,
    text_value: str | None = None,
    text_x: int = 0,
    text_y: int = 0,
    anchor: str = "middle",
) -> None:
    dash = ' stroke-dasharray="8,6"' if dashed else ""
    add(
        lines,
        f'<path id="{edge_id}" data-graph-role="edge" data-source="{source}" '
        f'data-target="{target}" d="{path}" fill="none" stroke="{color}" '
        f'stroke-width="2.2"{dash} marker-end="url(#{marker})"/>',
    )
    if text_value:
        label(lines, text_x, text_y, text_value, size=11, fill=color, weight=650, anchor=anchor)


def build_svg() -> str:
    lines: list[str] = []
    add(
        lines,
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}" '
        f'viewBox="0 0 {WIDTH} {HEIGHT}" role="img" aria-labelledby="title desc">',
    )
    add(lines, '<title id="title">合同审查 Workflow v0.1 详细流程</title>')
    add(
        lines,
        '<desc id="desc">合同上传和事实确认完成后，经过确认门禁、劳动合同范围检查、A 级法律检索、确定性规则卡片、B 级案例补充和结构化报告；报告写入 PostgreSQL 并绑定统一 session，未确认事实回到用户补充，资料库不可用时安全降级为 partial。</desc>',
    )
    add(lines, '<style>text{font-family:"Helvetica Neue",Helvetica,Arial,"PingFang SC","Microsoft YaHei","SimHei",sans-serif;}</style>')
    add(lines, "<defs>")
    for marker_id, color in (("arrow-blue", "#2563eb"), ("arrow-teal", "#0f766e"), ("arrow-purple", "#7c3aed"), ("arrow-gray", "#6b7280")):
        add(
            lines,
            f'<marker id="{marker_id}" markerWidth="11" markerHeight="8" refX="10" refY="4" orient="auto">'
            f'<polygon points="0 0, 11 4, 0 8" fill="{color}"/></marker>',
        )
    add(lines, "</defs>")
    add(lines, f'<rect data-graph-role="background" width="{WIDTH}" height="{HEIGHT}" fill="#ffffff"/>')

    label(lines, 52, 48, "合同审查 Workflow v0.1：从确认事实到可追溯报告", size=26, weight=650)
    label(lines, 52, 78, "规则先行、证据可回溯；事实不足时暂停，不把检索失败解释成无风险。", size=14, fill="#6b7280")

    label(lines, 1080, 38, "图例", size=12, fill="#6b7280", weight=650)
    add(lines, '<rect x="1080" y="49" width="14" height="14" rx="3" fill="#eff6ff" stroke="#93c5fd"/>')
    label(lines, 1103, 61, "已实现", size=12, fill="#374151")
    add(lines, '<rect x="1160" y="49" width="14" height="14" rx="3" fill="#f0fdfa" stroke="#5eead4"/>')
    label(lines, 1183, 61, "资料适配", size=12, fill="#374151")
    add(lines, '<rect x="1255" y="49" width="14" height="14" rx="3" fill="#f3f4f6" stroke="#9ca3af"/>')
    label(lines, 1278, 61, "待补齐", size=12, fill="#374151")
    add(lines, '<line x1="1360" y1="56" x2="1394" y2="56" stroke="#7c3aed" stroke-width="2" stroke-dasharray="8,6" marker-end="url(#arrow-purple)"/>')
    label(lines, 1403, 61, "回问", size=12, fill="#374151")

    # Main flow row.
    add(lines, '<rect data-graph-role="container" x="42" y="112" width="1416" height="312" rx="16" fill="#f8fbff" stroke="#bfdbfe" stroke-width="1.4"/>')
    label(lines, 70, 143, "主流程：确认事实 → 检索依据 → 规则计算 → 报告", size=16, fill="#1d4ed8", weight=650)

    edge(lines, "e-confirm-scope", "M300 245 H350", "confirmation", "scope", text_value="已确认", text_x=325, text_y=222)
    edge(lines, "e-scope-law", "M600 245 H650", "scope", "law", color="#0f766e", marker="arrow-teal")
    edge(lines, "e-law-rules", "M900 245 H950", "law", "rules", color="#0f766e", marker="arrow-teal")
    edge(lines, "e-rules-cases", "M1200 245 H1250", "rules", "cases", color="#6b7280", marker="arrow-gray", dashed=True, text_value="补充", text_x=1225, text_y=222)
    edge(lines, "e-cases-report", "M1370 305 V365 H1120 V500", "cases", "report", color="#6b7280", marker="arrow-gray")

    card(lines, "confirmation", 70, 185, 230, 120, "事实确认门禁", ["读取 original/user/effective", "五类动作 + revision", "未确认 → 只生成问题"], fill="#eff6ff", stroke="#93c5fd", badge="F", badge_fill="#2563eb", status="已实现", status_fill="#dbeafe", status_text="#1d4ed8")
    card(lines, "scope", 350, 185, 250, 120, "范围检查", ["全国通用劳动合同", "不做地方性判断", "不匹配 → out_of_scope"], fill="#eff6ff", stroke="#93c5fd", badge="S", badge_fill="#2563eb", status="已实现", status_fill="#dbeafe", status_text="#1d4ed8")
    card(lines, "law", 650, 185, 250, 120, "A 级法律检索", ["LEGAL_A_COLLECTION", "官方法律/行政法规/司法解释", "失败 → partial + warning"], fill="#f0fdfa", stroke="#5eead4", badge="A", badge_fill="#0f766e", status="已接入", status_fill="#ccfbf1", status_text="#0f766e")
    card(lines, "rules", 950, 185, 250, 120, "确定性规则卡片", ["17 个劳动合同主题", "证据事实 → 风险提示", "缺失使用 unconfirmed"], fill="#eff6ff", stroke="#93c5fd", badge="R", badge_fill="#2563eb", status="已实现", status_fill="#dbeafe", status_text="#1d4ed8")
    card(lines, "cases", 1250, 185, 180, 120, "B 级案例", ["官方案例补充", "可选解释来源"], fill="#f0fdfa", stroke="#5eead4", badge="B", badge_fill="#0f766e", status="适配", status_fill="#ccfbf1", status_text="#0f766e")

    # Decision / alternate route.
    add(lines, '<rect data-graph-role="container" x="42" y="450" width="1416" height="240" rx="16" fill="#fafafa" stroke="#d1d5db" stroke-width="1.4"/>')
    label(lines, 70, 481, "门禁与降级：保持可解释，不越过事实边界", size=16, fill="#374151", weight=650)
    edge(lines, "e-upload-confirm", "M185 645 V680 H600 V585 H608", "upload", "decision", color="#2563eb", marker="arrow-blue")
    edge(lines, "e-await-user", "M792 585 H1120 V510 H185 V525", "decision", "upload", color="#7c3aed", marker="arrow-purple", dashed=True, text_value="否：补充/修改/暂不确认", text_x=820, text_y=500)
    edge(lines, "e-ready-main", "M700 639 V705 H235 V770", "decision", "report", color="#0f766e", marker="arrow-teal", text_value="是：继续审查", text_x=715, text_y=684, anchor="start")
    edge(lines, "e-missing-legal", "M1110 610 H1190", "decision", "legal-missing", color="#6b7280", marker="arrow-gray", dashed=True, text_value="资料库缺失", text_x=1150, text_y=587)
    card(lines, "upload", 70, 525, 230, 120, "合同上传与提取", ["PDF / DOC / DOCX", "本地脱敏 + 条款/事实", "证据页码和偏移"], fill="#eff6ff", stroke="#93c5fd", badge="D", badge_fill="#2563eb", status="已实现", status_fill="#dbeafe", status_text="#1d4ed8")
    decision(lines, "decision", 700, 585)
    card(lines, "legal-missing", 1190, 525, 240, 120, "安全降级", ["保留事实层发现", "warnings 说明缺口", "workflow_status=partial"], fill="#f3f4f6", stroke="#9ca3af", badge="!", badge_fill="#6b7280", status="已实现", status_fill="#e5e7eb", status_text="#4b5563")

    # Output and pending work.
    add(lines, '<rect data-graph-role="container" x="42" y="715" width="1416" height="185" rx="16" fill="#ffffff" stroke="#d1d5db" stroke-width="1.4"/>')
    label(lines, 70, 746, "输出与后续工作", size=16, fill="#374151", weight=650)
    card(lines, "report", 70, 770, 330, 102, "结构化报告", ["风险事实 / 等级 / 依据", "修改建议 / 待确认问题", "免责声明与来源链"], fill="#eff6ff", stroke="#93c5fd", badge="R", badge_fill="#2563eb", status="已实现", status_fill="#dbeafe", status_text="#1d4ed8")
    card(lines, "persist", 440, 770, 300, 102, "报告持久化", ["版本 + PostgreSQL JSONB", "同一 session 可恢复/下载"], fill="#eff6ff", stroke="#93c5fd", badge="P", badge_fill="#2563eb", status="已实现", status_fill="#dbeafe", status_text="#1d4ed8")
    card(lines, "corpus", 780, 770, 300, 102, "法律资料治理", ["A 级版本/生效日期", "B 级官方案例待复核"], fill="#f0fdfa", stroke="#5eead4", badge="C", badge_fill="#0f766e", status="staging", status_fill="#ccfbf1", status_text="#0f766e")
    card(lines, "frontend", 1120, 770, 310, 102, "任务型前端", ["上传 / 事实表单 / 报告", "报告问答与恢复"], fill="#eff6ff", stroke="#93c5fd", badge="UI", badge_fill="#2563eb", status="已接入", status_fill="#dbeafe", status_text="#1d4ed8")

    add(lines, "</svg>")
    return "\n".join(lines) + "\n"


def main() -> None:
    OUTPUT.write_text(build_svg(), encoding="utf-8", newline="\n")
    print(f"generated: {OUTPUT}")


if __name__ == "__main__":
    main()
