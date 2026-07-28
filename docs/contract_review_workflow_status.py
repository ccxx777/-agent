"""生成劳动合同审查助手的当前状态流图。

图中的蓝色/青色节点表示已经实现或可以直接复用的能力；
灰色节点表示首版合同审查产品需要补齐的开发内容。
该脚本只生成文档图，不会访问运行中的服务，也不会写入 data/。
"""

from html import escape
from pathlib import Path


WIDTH = 1600
HEIGHT = 1030
OUTPUT = Path(__file__).with_name("contract-review-workflow-status.svg")


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
    opacity: float = 1.0,
) -> None:
    add(
        lines,
        f'<text x="{x}" y="{y}" font-size="{size}px" fill="{fill}" '
        f'font-weight="{weight}" text-anchor="{anchor}" opacity="{opacity}">'
        f"{escape(value)}</text>",
    )


def multiline(
    lines: list[str],
    x: float,
    y: float,
    values: list[str],
    *,
    size: int = 13,
    fill: str = "#374151",
    weight: int = 400,
    anchor: str = "middle",
    line_height: int = 18,
) -> None:
    add(
        lines,
        f'<text x="{x}" y="{y}" font-size="{size}px" fill="{fill}" '
        f'font-weight="{weight}" text-anchor="{anchor}">',
    )
    for index, value in enumerate(values):
        dy = 0 if index == 0 else line_height
        add(lines, f'<tspan x="{x}" dy="{dy}px">{escape(value)}</tspan>')
    add(lines, "</text>")


def pill(
    lines: list[str],
    x: float,
    y: float,
    label: str,
    *,
    fill: str,
    text_fill: str,
    width: int = 78,
) -> None:
    add(
        lines,
        f'<rect x="{x}" y="{y}" width="{width}" height="22" rx="11" '
        f'fill="{fill}" stroke="none"/>',
    )
    text(lines, x + width / 2, y + 15, label, size=11, fill=text_fill, weight=600, anchor="middle")


def node(
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
    subtitle_y: int | None = None,
) -> None:
    height = 96
    add(
        lines,
        f'<g id="{node_id}" data-graph-role="node">'
        f'<rect x="{x}" y="{y}" width="{width}" height="{height}" rx="10" '
        f'fill="{fill}" stroke="{stroke}" stroke-width="1.6"/>',
    )
    add(lines, f'<circle cx="{x + 28}" cy="{y + 30}" r="17" fill="{badge_fill}"/>')
    text(lines, x + 28, y + 35, badge, size=11, fill="#ffffff", weight=700, anchor="middle")
    text(lines, x + 54, y + 29, title, size=15, fill="#111827", weight=600)
    multiline(
        lines,
        x + 54,
        y + (subtitle_y or 53),
        [subtitle],
        size=12,
        fill="#4b5563",
        anchor="start",
        line_height=16,
    )
    pill(
        lines,
        x + width - 94,
        y + height - 31,
        status,
        fill=status_fill,
        text_fill=status_text,
        width=78,
    )
    add(lines, "</g>")


def compact_node(
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
) -> None:
    height = 52
    add(
        lines,
        f'<g id="{node_id}" data-graph-role="node">'
        f'<rect x="{x}" y="{y}" width="{width}" height="{height}" rx="9" '
        f'fill="{fill}" stroke="{stroke}" stroke-width="1.6"/>',
    )
    add(lines, f'<circle cx="{x + 26}" cy="{y + 26}" r="15" fill="{badge_fill}"/>')
    text(lines, x + 26, y + 30, badge, size=9, fill="#ffffff", weight=700, anchor="middle")
    text(lines, x + 50, y + 22, title, size=12, fill="#111827", weight=600)
    text(lines, x + 50, y + 39, subtitle, size=11, fill="#4b5563")
    pill(
        lines,
        x + width - 74,
        y + 15,
        status,
        fill=status_fill,
        text_fill=status_text,
        width=64,
    )
    add(lines, "</g>")


def decision(
    lines: list[str],
    node_id: str,
    cx: int,
    cy: int,
    half_width: int,
    half_height: int,
) -> None:
    points = f"{cx},{cy-half_height} {cx+half_width},{cy} {cx},{cy+half_height} {cx-half_width},{cy}"
    add(
        lines,
        f'<g id="{node_id}" data-graph-role="node"><polygon points="{points}" '
        'fill="#f3f4f6" stroke="#9ca3af" stroke-width="1.6"/>',
    )
    multiline(lines, cx, cy - 5, ["事实是否", "足够？"], size=13, weight=600, fill="#374151")
    add(lines, "</g>")


def edge(
    lines: list[str],
    edge_id: str,
    path: str,
    *,
    color: str,
    marker: str,
    source: str,
    target: str,
    dashed: bool = False,
    label: str | None = None,
    label_x: int | None = None,
    label_y: int | None = None,
    label_anchor: str = "middle",
) -> None:
    dash = ' stroke-dasharray="7,5"' if dashed else ""
    add(
        lines,
        f'<path id="{edge_id}" data-graph-role="edge" data-source="{source}" '
        f'data-target="{target}" d="{path}" fill="none" stroke="{color}" '
        f'stroke-width="2"{dash} marker-end="url(#{marker})"/>',
    )
    if label is not None:
        text(
            lines,
            label_x or 0,
            label_y or 0,
            label,
            size=11,
            fill=color,
            weight=600,
            anchor=label_anchor,
        )


def build_svg() -> str:
    lines: list[str] = []
    add(
        lines,
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}" '
        f'viewBox="0 0 {WIDTH} {HEIGHT}" role="img" aria-labelledby="title desc">',
    )
    add(lines, "<title id=\"title\">劳动合同风险审查助手：当前状态与近期开发工作</title>")
    add(
        lines,
        '<desc id="desc">上方为已经完成的通用 RAG 能力；下方展示劳动合同审查 Workflow，其中合同上传、PDF/DOC/DOCX 文档解析、隐私脱敏和任务状态模块已完成，其余节点仍待补齐。</desc>',
    )
    add(
        lines,
        "<style>text{font-family:'Helvetica Neue',Helvetica,Arial,'PingFang SC','Microsoft YaHei','Microsoft JhengHei','SimHei',sans-serif;} </style>",
    )
    add(lines, "<defs>")
    for marker_id, color in (
        ("arrow-blue", "#2563eb"),
        ("arrow-teal", "#0f766e"),
        ("arrow-gray", "#6b7280"),
        ("arrow-purple", "#7c3aed"),
    ):
        add(
            lines,
            f'<marker id="{marker_id}" markerWidth="10" markerHeight="7" refX="9" refY="3.5" orient="auto">'
            f'<polygon points="0 0, 10 3.5, 0 7" fill="{color}"/></marker>',
        )
    add(lines, "</defs>")
    add(lines, f'<rect data-graph-role="background" width="{WIDTH}" height="{HEIGHT}" fill="#ffffff"/>')

    text(lines, 60, 48, "劳动合同风险审查助手：当前状态与近期开发工作", size=26, weight=600)
    text(
        lines,
        60,
        78,
        "先把可暂停、可追溯的审查任务骨架跑通；法律资料与人工复核作为并行输入接入。",
        size=14,
        fill="#6b7280",
    )

    # Legend
    text(lines, 1125, 38, "图例", size=12, fill="#6b7280", weight=600)
    add(lines, '<rect x="1125" y="49" width="14" height="14" rx="3" fill="#eff6ff" stroke="#93c5fd"/>')
    text(lines, 1147, 61, "已完成", size=12, fill="#374151")
    add(lines, '<rect x="1210" y="49" width="14" height="14" rx="3" fill="#f0fdfa" stroke="#5eead4"/>')
    text(lines, 1232, 61, "可复用", size=12, fill="#374151")
    add(lines, '<rect x="1300" y="49" width="14" height="14" rx="3" fill="#f3f4f6" stroke="#9ca3af"/>')
    text(lines, 1322, 61, "待开发", size=12, fill="#374151")
    add(lines, '<line x1="1390" y1="56" x2="1420" y2="56" stroke="#6b7280" stroke-width="2" stroke-dasharray="7,5" marker-end="url(#arrow-gray)"/>')
    text(lines, 1428, 61, "并行/异步", size=12, fill="#374151")

    # Swim lanes
    add(lines, '<rect data-graph-role="container" x="50" y="112" width="1500" height="290" rx="14" fill="#f8fbff" stroke="#bfdbfe" stroke-width="1.4"/>')
    add(lines, '<rect data-graph-role="container" x="50" y="430" width="1500" height="500" rx="14" fill="#fafafa" stroke="#d1d5db" stroke-width="1.4"/>')
    text(lines, 76, 144, "当前已完成 / 已验证：通用 Agent + RAG 主链", size=16, fill="#1d4ed8", weight=600)
    text(lines, 76, 470, "马上需要开发：劳动合同审查 Workflow（首版）", size=16, fill="#374151", weight=600)
    text(lines, 1320, 144, "当前线上能力", size=12, fill="#2563eb", weight=600, anchor="end")
    text(lines, 1475, 470, "灰色 = 近期开发项", size=12, fill="#6b7280", weight=600, anchor="end")

    # Existing online path edges are drawn before nodes.
    edge(lines, "e-query-api", "M260 238 H300", color="#2563eb", marker="arrow-blue", source="generic-query", target="fastapi")
    edge(lines, "e-api-graph", "M490 238 H530", color="#2563eb", marker="arrow-blue", source="fastapi", target="langgraph")
    edge(lines, "e-graph-retrieval", "M720 238 H760", color="#2563eb", marker="arrow-blue", source="langgraph", target="retrieval")
    edge(lines, "e-retrieval-qdrant", "M990 238 H1030", color="#0f766e", marker="arrow-teal", source="retrieval", target="qdrant", label="上下文", label_x=1010, label_y=211)
    edge(lines, "e-qdrant-answer", "M1240 238 H1270", color="#2563eb", marker="arrow-blue", source="qdrant", target="answer")
    edge(
        lines,
        "e-data-qdrant",
        "M300 348 V386 H1000 V276 H1030",
        color="#6b7280",
        marker="arrow-gray",
        source="data-worker",
        target="qdrant",
        dashed=True,
        label="增量文档同步",
        label_x=645,
        label_y=378,
    )

    # Future contract review flow edges.
    edge(lines, "e-upload-parse", "M270 558 H315", color="#6b7280", marker="arrow-gray", source="contract-upload", target="parse")
    edge(lines, "e-parse-task", "M515 558 H560", color="#6b7280", marker="arrow-gray", source="parse", target="review-task")
    edge(lines, "e-task-clause", "M760 558 H805", color="#6b7280", marker="arrow-gray", source="review-task", target="clause-facts")
    edge(lines, "e-clause-fact", "M1005 558 H1050", color="#6b7280", marker="arrow-gray", source="clause-facts", target="fact-decision")
    edge(
        lines,
        "e-fact-yes",
        "M1415 610 V640 H1030 V748 H200 V765",
        color="#0f766e",
        marker="arrow-teal",
        source="fact-decision",
        target="contract-workflow",
        label="是 / 事实足够",
        label_x=1340,
        label_y=632,
    )
    edge(
        lines,
        "e-fact-no",
        "M1505 558 H1530 V695 H1480",
        color="#7c3aed",
        marker="arrow-purple",
        source="fact-decision",
        target="clarification",
        label="否 / 需要补充",
        label_x=1514,
        label_y=641,
        label_anchor="end",
    )
    edge(lines, "e-workflow-legal", "M320 815 H370", color="#0f766e", marker="arrow-teal", source="contract-workflow", target="legal-rag")
    edge(lines, "e-legal-rules", "M610 815 H660", color="#6b7280", marker="arrow-gray", source="legal-rag", target="rule-engine")
    edge(lines, "e-rules-report", "M900 815 H950", color="#6b7280", marker="arrow-gray", source="rule-engine", target="risk-report")
    edge(lines, "e-report-ui", "M1190 815 H1240", color="#6b7280", marker="arrow-gray", source="risk-report", target="task-ui")

    # Existing nodes.
    node(lines, "generic-query", 80, 190, 180, "通用问题", "用户查询", fill="#eff6ff", stroke="#93c5fd", badge="Q", badge_fill="#2563eb", status="已完成", status_fill="#dbeafe", status_text="#1d4ed8")
    node(lines, "fastapi", 300, 190, 190, "FastAPI API", "会话 / 问答接口", fill="#eff6ff", stroke="#93c5fd", badge="API", badge_fill="#009688", status="已完成", status_fill="#dbeafe", status_text="#1d4ed8")
    node(lines, "langgraph", 530, 190, 190, "LangGraph Agent", "记忆压缩 + 回答编排", fill="#eff6ff", stroke="#93c5fd", badge="LG", badge_fill="#1c3c3c", status="已完成", status_fill="#dbeafe", status_text="#1d4ed8")
    node(lines, "retrieval", 760, 190, 230, "三层混合检索", "L1召回 → L2粗排 → L3精排", fill="#f0fdfa", stroke="#5eead4", badge="R", badge_fill="#0f766e", status="可复用", status_fill="#ccfbf1", status_text="#0f766e")
    node(lines, "qdrant", 1030, 190, 210, "Qdrant 检索库", "Dense / Sparse / BM25", fill="#f0fdfa", stroke="#5eead4", badge="Q", badge_fill="#dc244c", status="可复用", status_fill="#ccfbf1", status_text="#0f766e")
    node(lines, "answer", 1270, 190, 220, "答案 + 引用", "约束回答 / 拒答", fill="#eff6ff", stroke="#93c5fd", badge="A", badge_fill="#2563eb", status="已验证", status_fill="#dbeafe", status_text="#1d4ed8")
    compact_node(lines, "data-worker", 80, 322, 220, "Data Worker", "切分 / 写入向量", fill="#f0fdfa", stroke="#5eead4", badge="DW", badge_fill="#0f766e", status="已完成", status_fill="#ccfbf1", status_text="#0f766e")
    add(lines, '<rect x="320" y="322" width="650" height="44" rx="8" fill="#ffffff" stroke="#bfdbfe" stroke-width="1.2"/>')
    text(lines, 342, 340, "30题基线：Hit@1 83.33%  ·  Hit@3 90.00%  ·  MRR@3 86.67%", size=11, fill="#1e40af")
    text(lines, 342, 357, "v2检索约 1.04s  ·  RAGAS Faithfulness 0.908  ·  Context Relevance 0.892", size=11, fill="#1e40af")

    # Contract-specific nodes: blue means implemented in this iteration; gray remains future work.
    node(lines, "contract-upload", 80, 510, 190, "合同上传", "PDF / DOC / DOCX / SHA-256", fill="#eff6ff", stroke="#93c5fd", badge="DOC", badge_fill="#2563eb", status="已完成", status_fill="#dbeafe", status_text="#1d4ed8")
    node(lines, "parse", 315, 510, 200, "解析与质量检查", "PDF / OOXML / antiword / OCR 门禁", fill="#eff6ff", stroke="#93c5fd", badge="P", badge_fill="#2563eb", status="已完成", status_fill="#dbeafe", status_text="#1d4ed8")
    node(lines, "review-task", 560, 510, 200, "审查任务状态", "PostgreSQL / 后台任务 / 恢复", fill="#eff6ff", stroke="#93c5fd", badge="T", badge_fill="#2563eb", status="已完成", status_fill="#dbeafe", status_text="#1d4ed8")
    node(lines, "clause-facts", 805, 510, 200, "条款与事实提取", "结构化，不直接定级", fill="#f3f4f6", stroke="#9ca3af", badge="C", badge_fill="#6b7280", status="待开发", status_fill="#e5e7eb", status_text="#4b5563")
    node(lines, "fact-placeholder", 1050, 510, 220, "事实确认", "缺失信息可暂停审查", fill="#f3f4f6", stroke="#9ca3af", badge="F", badge_fill="#6b7280", status="待开发", status_fill="#e5e7eb", status_text="#4b5563")
    decision(lines, "fact-decision", 1415, 558, 90, 52)
    node(lines, "clarification", 1180, 655, 300, "补充问题 / 暂停", "用户补充事实后恢复任务", fill="#f3f4f6", stroke="#9ca3af", badge="?", badge_fill="#7c3aed", status="待开发", status_fill="#e5e7eb", status_text="#4b5563")
    node(lines, "contract-workflow", 80, 765, 240, "合同审查 Workflow", "LangGraph 节点解耦", fill="#f3f4f6", stroke="#9ca3af", badge="WG", badge_fill="#6b7280", status="需扩展", status_fill="#e5e7eb", status_text="#4b5563")
    node(lines, "legal-rag", 370, 765, 240, "法律检索适配", "复用混合召回骨架", fill="#f0fdfa", stroke="#5eead4", badge="R", badge_fill="#0f766e", status="待接入", status_fill="#e5e7eb", status_text="#4b5563")
    node(lines, "rule-engine", 660, 765, 240, "确定性规则引擎", "规则决定风险等级", fill="#f3f4f6", stroke="#9ca3af", badge="R", badge_fill="#6b7280", status="待开发", status_fill="#e5e7eb", status_text="#4b5563")
    node(lines, "risk-report", 950, 765, 240, "风险事实 + 建议", "等级、置信度、法律依据", fill="#f3f4f6", stroke="#9ca3af", badge="REP", badge_fill="#6b7280", status="待开发", status_fill="#e5e7eb", status_text="#4b5563")
    node(lines, "task-ui", 1240, 765, 240, "任务型 Web 前端", "上传 / 追问 / 报告", fill="#f3f4f6", stroke="#9ca3af", badge="UI", badge_fill="#6b7280", status="待开发", status_fill="#e5e7eb", status_text="#4b5563")

    # Parallel work notes.
    add(lines, '<rect x="80" y="875" width="700" height="42" rx="9" fill="#ffffff" stroke="#9ca3af" stroke-width="1.3" stroke-dasharray="7,5"/>')
    text(lines, 102, 901, "并行支线：法律 A 级来源、官方 B 级案例、规则卡与人工复核（不阻塞骨架开发）", size=12, fill="#4b5563", weight=600)
    add(lines, '<rect x="830" y="875" width="680" height="42" rx="9" fill="#ffffff" stroke="#d1d5db" stroke-width="1.3"/>')
    text(lines, 852, 901, "本轮已交付：上传 → PDF/DOC/DOCX 解析 → PII 脱敏 → 质量状态（OCR/条款审查仍待接入）", size=12, fill="#374151", weight=600)

    add(lines, "</svg>")
    return "\n".join(lines) + "\n"


def main() -> None:
    OUTPUT.write_text(build_svg(), encoding="utf-8", newline="\n")
    print(f"generated: {OUTPUT}")


if __name__ == "__main__":
    main()
