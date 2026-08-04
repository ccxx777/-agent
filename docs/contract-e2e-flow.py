"""生成合同审查 E2E 正式门禁流程图。

图示对应服务器最近一次真实回归：
上传 → 解析/脱敏 → 事实确认 → Workflow → A 级法律检索 → 报告
→ 同 session 报告问答 → 历史查询 → 删除与隐私校验。

该脚本只生成文档图，不访问 Backend、Qdrant 或 PostgreSQL，也不会读取 data/。
"""

from __future__ import annotations

from html import escape
from pathlib import Path

WIDTH = 1680
HEIGHT = 1160
OUTPUT = Path(__file__).with_name("contract-e2e-flow.svg")


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
) -> None:
    add(
        lines,
        f'<text x="{x}" y="{y}" font-size="{size}px" fill="{fill}" '
        f'font-weight="{weight}" text-anchor="{anchor}">{escape(value)}</text>',
    )


def multiline(
    lines: list[str],
    x: float,
    y: float,
    values: list[str],
    *,
    size: int = 12,
    fill: str = "#4b5563",
    weight: int = 400,
    line_height: int = 17,
) -> None:
    add(
        lines,
        f'<text x="{x}" y="{y}" font-size="{size}px" fill="{fill}" '
        f'font-weight="{weight}">',
    )
    for index, value in enumerate(values):
        dy = 0 if index == 0 else line_height
        add(lines, f'<tspan x="{x}" dy="{dy}px">{escape(value)}</tspan>')
    add(lines, "</text>")


def pill(
    lines: list[str],
    x: int,
    y: int,
    label: str,
    *,
    fill: str,
    text_fill: str,
    width: int = 92,
) -> None:
    add(
        lines,
        f'<rect x="{x}" y="{y}" width="{width}" height="22" rx="11" '
        f'fill="{fill}" stroke="none"/>',
    )
    text(lines, x + width / 2, y + 15, label, size=11, fill=text_fill, weight=600, anchor="middle")


def card(
    lines: list[str],
    node_id: str,
    x: int,
    y: int,
    width: int,
    title: str,
    subtitle: list[str],
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
    add(
        lines,
        f'<g id="{node_id}" data-graph-role="node">'
        f'<rect x="{x}" y="{y}" width="{width}" height="{height}" rx="11" '
        f'fill="{fill}" stroke="{stroke}" stroke-width="1.6"/>',
    )
    add(lines, f'<circle cx="{x + 28}" cy="{y + 30}" r="17" fill="{badge_fill}"/>')
    text(lines, x + 28, y + 35, badge, size=10, fill="#ffffff", weight=700, anchor="middle")
    text(lines, x + 55, y + 29, title, size=15, fill="#111827", weight=650)
    multiline(lines, x + 55, y + 52, subtitle, size=11, fill="#4b5563", line_height=16)
    pill(
        lines,
        x + width - 105,
        y + height - 31,
        status,
        fill=status_fill,
        text_fill=status_text,
        width=92,
    )
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
    dashed: bool = False,
    label: str | None = None,
    label_x: int | None = None,
    label_y: int | None = None,
    label_anchor: str = "middle",
) -> None:
    dash = ' stroke-dasharray="8,6"' if dashed else ""
    add(
        lines,
        f'<path id="{edge_id}" data-graph-role="edge" data-source="{source}" '
        f'data-target="{target}" d="{path}" fill="none" stroke="{color}" '
        f'stroke-width="2.2"{dash} marker-end="url(#{marker})"/>',
    )
    if label:
        text(
            lines,
            label_x or 0,
            label_y or 0,
            label,
            size=11,
            fill=color,
            weight=650,
            anchor=label_anchor,
        )


def build_svg() -> str:
    lines: list[str] = []
    add(
        lines,
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}" '
        f'viewBox="0 0 {WIDTH} {HEIGHT}" role="img" aria-labelledby="title desc">',
    )
    add(lines, '<title id="title">合同审查 E2E 正式门禁流程</title>')
    add(
        lines,
        '<desc id="desc">展示服务器真实 API 回归从合同上传、解析脱敏、事实确认、合同审查 Workflow、ACTIVE A 级法律检索、报告生成、同 session 报告问答到历史查询、删除和隐私校验的完整闭环。</desc>',
    )
    add(
        lines,
        "<style>text{font-family:'Helvetica Neue',Helvetica,Arial,'PingFang SC','Microsoft YaHei','Microsoft JhengHei','SimHei',sans-serif;}</style>",
    )
    add(lines, "<defs>")
    for marker_id, color in (
        ("arrow-blue", "#2563eb"),
        ("arrow-teal", "#0f766e"),
        ("arrow-purple", "#7c3aed"),
        ("arrow-orange", "#ea580c"),
        ("arrow-gray", "#6b7280"),
    ):
        add(
            lines,
            f'<marker id="{marker_id}" markerWidth="11" markerHeight="8" refX="10" refY="4" orient="auto">'
            f'<polygon points="0 0, 11 4, 0 8" fill="{color}"/></marker>',
        )
    add(lines, "</defs>")
    add(lines, f'<rect data-graph-role="background" width="{WIDTH}" height="{HEIGHT}" fill="#ffffff"/>')

    text(lines, 58, 48, "合同审查 E2E 正式门禁：从上传到删除校验", size=27, weight=650)
    text(
        lines,
        58,
        80,
        "真实服务器 API 回归，验证合同隐私、事实确认、法律引用、报告会话和删除闭环。",
        size=14,
        fill="#6b7280",
    )

    # Gate badge and legend.
    add(lines, '<rect x="1110" y="25" width="500" height="48" rx="12" fill="#ecfdf5" stroke="#6ee7b7" stroke-width="1.4"/>')
    text(lines, 1130, 45, "正式门禁", size=11, fill="#047857", weight=700)
    text(lines, 1210, 45, "ACTIVE · Legal Smoke 10/10 · E2E passed", size=13, fill="#065f46", weight=650)
    text(lines, 1110, 96, "图例", size=12, fill="#6b7280", weight=650)
    add(lines, '<line x1="1150" y1="92" x2="1180" y2="92" stroke="#2563eb" stroke-width="2.2" marker-end="url(#arrow-blue)"/>')
    text(lines, 1190, 96, "请求/流程", size=11, fill="#374151")
    add(lines, '<line x1="1260" y1="92" x2="1290" y2="92" stroke="#0f766e" stroke-width="2.2" marker-end="url(#arrow-teal)"/>')
    text(lines, 1300, 96, "数据/门禁", size=11, fill="#374151")
    add(lines, '<line x1="1370" y1="92" x2="1400" y2="92" stroke="#7c3aed" stroke-width="2.2" stroke-dasharray="8,6" marker-end="url(#arrow-purple)"/>')
    text(lines, 1410, 96, "session 上下文", size=11, fill="#374151")
    add(lines, '<line x1="1510" y1="92" x2="1540" y2="92" stroke="#ea580c" stroke-width="2.2" marker-end="url(#arrow-orange)"/>')
    text(lines, 1550, 96, "清理/审计", size=11, fill="#374151")

    # Swim lanes.
    add(lines, '<rect data-graph-role="container" x="38" y="122" width="1604" height="290" rx="16" fill="#f8fbff" stroke="#bfdbfe" stroke-width="1.4"/>')
    add(lines, '<rect data-graph-role="container" x="38" y="438" width="1604" height="304" rx="16" fill="#f0fdfa" stroke="#99f6e4" stroke-width="1.4"/>')
    add(lines, '<rect data-graph-role="container" x="38" y="768" width="1604" height="236" rx="16" fill="#fffaf0" stroke="#fed7aa" stroke-width="1.4"/>')
    text(lines, 62, 153, "阶段 1｜上传、解析与隐私门禁", size=16, fill="#1d4ed8", weight=650)
    text(lines, 62, 469, "阶段 2｜事实确认、法律审查与报告", size=16, fill="#0f766e", weight=650)
    text(lines, 62, 799, "阶段 3｜同 session 回问、历史和删除验证", size=16, fill="#c2410c", weight=650)

    # Edges are rendered before cards.
    edge(lines, "e-upload-api", "M290 240 H330", source="test-script", target="upload-api", color="#2563eb", marker="arrow-blue")
    edge(lines, "e-api-task", "M550 240 H590", source="upload-api", target="task-storage", color="#2563eb", marker="arrow-blue")
    edge(lines, "e-task-parser", "M810 240 H850", source="task-storage", target="parser-redaction", color="#0f766e", marker="arrow-teal")
    edge(lines, "e-parser-poll", "M1070 240 H1110", source="parser-redaction", target="poll-terminal", color="#2563eb", marker="arrow-blue")
    edge(lines, "e-poll-confirm", "M1330 240 H1370", source="poll-terminal", target="fact-snapshot", color="#0f766e", marker="arrow-teal", label="终态", label_x=1350, label_y=176)
    edge(lines, "e-confirm-ready", "M1480 296 V420 H170 V505", source="fact-snapshot", target="confirmation-gate", color="#0f766e", marker="arrow-teal", label="确认快照", label_x=990, label_y=407)

    edge(lines, "e-ready-workflow", "M270 560 H350", source="confirmation-gate", target="workflow", color="#2563eb", marker="arrow-blue")
    edge(lines, "e-workflow-legal", "M570 560 H630", source="workflow", target="legal-retrieval", color="#0f766e", marker="arrow-teal")
    edge(lines, "e-legal-rules", "M850 560 H910", source="legal-retrieval", target="rules", color="#0f766e", marker="arrow-teal")
    edge(lines, "e-rules-report", "M1130 560 H1190", source="rules", target="report", color="#2563eb", marker="arrow-blue")
    edge(lines, "e-report-session", "M1410 560 H1470", source="report", target="session", color="#7c3aed", marker="arrow-purple", dashed=True, label="session_id", label_x=1440, label_y=485)
    edge(lines, "e-session-chat", "M1570 616 V735 H180 V840", source="session", target="report-chat", color="#7c3aed", marker="arrow-purple", dashed=True, label="同一 session", label_x=1190, label_y=714)
    edge(lines, "e-chat-history", "M290 896 H390", source="report-chat", target="history", color="#2563eb", marker="arrow-blue")
    edge(lines, "e-history-delete", "M610 896 H710", source="history", target="delete", color="#ea580c", marker="arrow-orange", label="用户删除", label_x=660, label_y=871)
    edge(lines, "e-delete-404", "M1010 896 H1030", source="delete", target="privacy-404", color="#ea580c", marker="arrow-orange", label="404", label_x=1020, label_y=871)
    edge(lines, "e-privacy-gate", "M1270 896 H1310", source="privacy-404", target="gate-result", color="#0f766e", marker="arrow-teal", label="哨兵未泄露", label_x=1290, label_y=871)

    # Stage 1 cards.
    card(lines, "test-script", 70, 185, 220, "E2E 测试脚本", ["contract_review_e2e.py", "脱敏劳动合同 DOCX"], fill="#eff6ff", stroke="#93c5fd", badge="T", badge_fill="#2563eb", status="启动", status_fill="#dbeafe", status_text="#1d4ed8")
    card(lines, "upload-api", 330, 185, 220, "上传 API", ["POST /api/contract-reviews", "创建 review_id / session_id"], fill="#eff6ff", stroke="#93c5fd", badge="API", badge_fill="#009688", status="202", status_fill="#dbeafe", status_text="#1d4ed8")
    card(lines, "task-storage", 590, 185, 220, "任务与私有存储", ["PostgreSQL 任务元数据", "原始文件不进 Qdrant"], fill="#f0fdfa", stroke="#5eead4", badge="DB", badge_fill="#0f766e", status="隔离", status_fill="#ccfbf1", status_text="#0f766e")
    card(lines, "parser-redaction", 850, 185, 220, "解析与脱敏", ["PDF / DOC / DOCX", "页级质量 + 隐私哨兵"], fill="#eff6ff", stroke="#93c5fd", badge="P", badge_fill="#2563eb", status="通过", status_fill="#dbeafe", status_text="#1d4ed8")
    card(lines, "poll-terminal", 1110, 185, 220, "轮询提取终态", ["extracting → needs_confirmation", "不把解析成功误判失败"], fill="#eff6ff", stroke="#93c5fd", badge="Q", badge_fill="#2563eb", status="终态", status_fill="#dbeafe", status_text="#1d4ed8")
    card(lines, "fact-snapshot", 1370, 185, 220, "事实确认快照", ["补充 9 项测试事实", "ready_for_legal_review=true"], fill="#f0fdfa", stroke="#5eead4", badge="F", badge_fill="#0f766e", status="门禁", status_fill="#ccfbf1", status_text="#0f766e")

    # Stage 2 cards.
    card(lines, "confirmation-gate", 50, 505, 220, "事实确认门禁", ["revision / supplement", "不传递原始合同给 Workflow"], fill="#eff6ff", stroke="#93c5fd", badge="F", badge_fill="#2563eb", status="通过", status_fill="#dbeafe", status_text="#1d4ed8")
    card(lines, "workflow", 350, 505, 220, "LangGraph Workflow", ["范围 → 法律检索 → 规则", "workflow_status=completed"], fill="#eff6ff", stroke="#93c5fd", badge="LG", badge_fill="#1c3c3c", status="完成", status_fill="#dbeafe", status_text="#1d4ed8")
    card(lines, "legal-retrieval", 630, 505, 220, "A 级法律检索", ["legal_labor_a_v1", "ACTIVE + Reranker 200 OK"], fill="#f0fdfa", stroke="#5eead4", badge="A", badge_fill="#0f766e", status="6 条", status_fill="#ccfbf1", status_text="#0f766e")
    card(lines, "rules", 910, 505, 220, "规则卡片计算", ["确定性风险触发", "输出 findings / pending"], fill="#eff6ff", stroke="#93c5fd", badge="R", badge_fill="#2563eb", status="2 条", status_fill="#dbeafe", status_text="#1d4ed8")
    card(lines, "report", 1190, 505, 220, "报告 JSON / PDF", ["report_id 持久化", "报告来源可追溯"], fill="#eff6ff", stroke="#93c5fd", badge="REP", badge_fill="#2563eb", status="8.7KB", status_fill="#dbeafe", status_text="#1d4ed8")
    card(lines, "session", 1470, 505, 150, "统一会话", ["session_id", "thread_id"], fill="#f5f3ff", stroke="#c4b5fd", badge="S", badge_fill="#7c3aed", status="复用", status_fill="#ede9fe", status_text="#6d28d9", height=112)

    # Stage 3 cards.
    card(lines, "report-chat", 70, 840, 220, "报告问答", ["contract_review 模式", "返回非空答案"], fill="#f5f3ff", stroke="#c4b5fd", badge="CHAT", badge_fill="#7c3aed", status="通过", status_fill="#ede9fe", status_text="#6d28d9")
    card(lines, "history", 390, 840, 220, "历史与会话查询", ["报告 / session 可查询", "归属校验通过"], fill="#eff6ff", stroke="#93c5fd", badge="H", badge_fill="#2563eb", status="通过", status_fill="#dbeafe", status_text="#1d4ed8")
    card(lines, "delete", 710, 840, 300, "删除审查任务", ["DELETE /api/contract-reviews/{review_id}", "删除后继续查询"], fill="#fff7ed", stroke="#fdba74", badge="DEL", badge_fill="#ea580c", status="执行", status_fill="#fed7aa", status_text="#c2410c")
    card(lines, "privacy-404", 1030, 840, 240, "404 + 隐私检查", ["删除后返回 404", "PDF / API 无哨兵值"], fill="#fff7ed", stroke="#fdba74", badge="✓", badge_fill="#ea580c", status="通过", status_fill="#fed7aa", status_text="#c2410c")
    card(lines, "gate-result", 1310, 840, 300, "正式门禁结果", ["Legal Smoke 10/10 · E2E passed", "external_ocr=false · ACTIVE"], fill="#ecfdf5", stroke="#6ee7b7", badge="OK", badge_fill="#059669", status="放行", status_fill="#bbf7d0", status_text="#047857")

    # Metrics and evidence strip.
    add(lines, '<rect x="50" y="1035" width="1580" height="78" rx="12" fill="#f8fafc" stroke="#cbd5e1" stroke-width="1.3"/>')
    text(lines, 74, 1063, "本次服务器实测", size=12, fill="#475569", weight=700)
    text(lines, 245, 1063, "workflow=completed  ·  findings=2  ·  legal_sources=6  ·  pending_questions=1", size=12, fill="#1e3a8a", weight=650)
    text(lines, 74, 1088, "耗时 103.99s  ·  poll=35  ·  report_chat=通过  ·  deletion=404  ·  privacy_sentinels=0", size=12, fill="#475569", weight=600)
    text(lines, 1110, 1088, "命令不含 --allow-pending-*", size=12, fill="#047857", weight=700)

    add(lines, "</svg>")
    return "\n".join(lines) + "\n"


def main() -> None:
    OUTPUT.write_text(build_svg(), encoding="utf-8", newline="\n")
    print(f"generated: {OUTPUT}")


if __name__ == "__main__":
    main()
