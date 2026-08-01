"""生成文字聊天、合同上传与合同上下文问答合并后的统一会话数据流图。

这是一张基于当前代码实现的 data-flow 图。三条入口最终落到同一个用户
``session_id``；合同上传会把脱敏正文、结构化事实和风险报告写入该会话的
``contract_context``，因此用户可以先提问再上传合同，也可以上传后继续追问。
"""

from __future__ import annotations

from html import escape
from pathlib import Path


WIDTH = 2240
HEIGHT = 1660
OUTPUT = Path(__file__).with_name("chat-route-data-flow.svg")


def add(lines: list[str], value: str) -> None:
    lines.append(value)


def text(
    lines: list[str],
    x: int,
    y: int,
    value: str,
    *,
    size: int = 14,
    fill: str = "#172033",
    weight: int = 400,
    anchor: str = "start",
    role: str = "label",
    owner: str | None = None,
) -> None:
    owner_attr = f' data-owner="{owner}"' if owner else ""
    add(
        lines,
        f'<text data-graph-role="{role}"{owner_attr} x="{x}" y="{y}" '
        f'font-size="{size}px" fill="{fill}" font-weight="{weight}" '
        f'text-anchor="{anchor}">{escape(value)}</text>',
    )


def card(
    lines: list[str],
    node_id: str,
    x: int,
    y: int,
    title: str,
    subtitle: str,
    *,
    badge: str,
    fill: str = "#ffffff",
    stroke: str = "#cbd5e1",
    status: str | None = None,
    status_fill: str = "#e2e8f0",
    status_text: str = "#475569",
    width: int = 245,
    height: int = 116,
    subtitle_2: str | None = None,
) -> None:
    bounds = f"{x} {y} {x + width} {y + height}"
    add(
        lines,
        f'<g id="{node_id}" data-node-id="{node_id}" data-graph-role="node" '
        f'data-graph-bounds="{bounds}">',
    )
    add(
        lines,
        f'<rect data-node-id="{node_id}" x="{x}" y="{y}" width="{width}" '
        f'height="{height}" rx="14" fill="{fill}" stroke="{stroke}" stroke-width="1.6"/>',
    )
    add(lines, f'<circle data-node-id="{node_id}" cx="{x + 31}" cy="{y + 34}" r="20" fill="{stroke}"/>')
    text(lines, x + 31, y + 39, badge, size=10, fill="#ffffff", weight=700, anchor="middle", role="node")
    text(lines, x + 62, y + 30, title, size=15, weight=650, role="node")
    text(lines, x + 62, y + 57, subtitle, size=12, fill="#475569", role="node")
    if subtitle_2:
        text(lines, x + 62, y + 77, subtitle_2, size=12, fill="#475569", role="node")
    if status:
        pill_width = max(76, min(width - 24, 12 * len(status) + 24))
        pill_x = x + width - pill_width - 12
        pill_y = y + height - 31
        add(
            lines,
            f'<rect data-node-id="{node_id}" x="{pill_x}" y="{pill_y}" width="{pill_width}" '
            f'height="21" rx="11" fill="{status_fill}"/>',
        )
        text(
            lines,
            pill_x + pill_width // 2,
            pill_y + 15,
            status,
            size=10,
            fill=status_text,
            weight=650,
            anchor="middle",
            role="node",
        )
    add(lines, "</g>")


def callout(
    lines: list[str],
    node_id: str,
    x: int,
    y: int,
    width: int,
    title: str,
    body: list[str],
) -> None:
    height = 110
    bounds = f"{x} {y} {x + width} {y + height}"
    add(
        lines,
        f'<g id="{node_id}" data-node-id="{node_id}" data-graph-role="node" '
        f'data-graph-bounds="{bounds}">',
    )
    add(
        lines,
        f'<rect data-node-id="{node_id}" x="{x}" y="{y}" width="{width}" height="{height}" '
        'rx="14" fill="#fff7ed" stroke="#f97316" stroke-width="1.7" stroke-dasharray="8,6"/>',
    )
    text(lines, x + 20, y + 29, title, size=15, fill="#9a3412", weight=700, role="node")
    for index, line in enumerate(body):
        text(lines, x + 20, y + 56 + index * 19, line, size=12, fill="#7c2d12", role="node")
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
    label_x: int = 0,
    label_y: int = 0,
    dashed: bool = False,
) -> None:
    dash = ' stroke-dasharray="8,6"' if dashed else ""
    add(
        lines,
        f'<path id="{edge_id}" data-edge-id="{edge_id}" data-graph-role="edge" '
        f'data-source="{source}" data-target="{target}" d="{path}" fill="none" '
        f'stroke="{color}" stroke-width="2.2"{dash} marker-end="url(#{marker})"/>',
    )
    if label:
        text(lines, label_x, label_y, label, size=11, fill=color, weight=650, anchor="middle", owner=edge_id)


def lane(lines: list[str], y: int, height: int, fill: str, stroke: str, title: str, title_fill: str, lane_id: str) -> None:
    add(
        lines,
        f'<rect id="{lane_id}" data-graph-role="container" data-node-id="{lane_id}" '
        f'x="40" y="{y}" width="2160" height="{height}" rx="18" fill="{fill}" stroke="{stroke}" stroke-width="1.5"/>',
    )
    text(lines, 70, y + 34, title, size=17, fill=title_fill, weight=700, role="container-title")


def build_svg() -> str:
    lines: list[str] = []
    add(
        lines,
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}" '
        f'viewBox="0 0 {WIDTH} {HEIGHT}" role="img" aria-labelledby="title desc" '
        'data-generator="fireworks-tech-graph" data-schema-version="1" data-style-id="1" '
        'data-visual-theme="flat-icon" data-diagram-type="data-flow" data-semantic-profile="generic" '
        'data-quality-profile="standard" data-min-node-gap="28" data-min-container-gutter="20" '
        'data-min-label-clearance="4" data-min-segment-length="16">',
    )
    add(lines, '<title id="title">统一会话中的合同审查、合同问答与通用聊天数据流</title>')
    add(
        lines,
        '<desc id="desc">展示文字问题和合同上传如何共享同一个 session_id，以及脱敏正文、事实 JSON 和风险报告如何组成合同上下文。</desc>',
    )
    add(
        lines,
        "<style>text{font-family:'Segoe UI','PingFang SC','Microsoft YaHei','Noto Sans CJK SC',Arial,sans-serif;} </style>",
    )
    add(lines, "<defs>")
    for marker_id, color in (
        ("arrow-blue", "#2563eb"),
        ("arrow-purple", "#7c3aed"),
        ("arrow-green", "#059669"),
        ("arrow-orange", "#ea580c"),
        ("arrow-gray", "#64748b"),
    ):
        add(
            lines,
            f'<marker id="{marker_id}" markerWidth="10" markerHeight="7" refX="9" refY="3.5" orient="auto">'
            f'<polygon points="0 0, 10 3.5, 0 7" fill="{color}"/></marker>',
        )
    add(lines, "</defs>")
    add(lines, f'<rect data-graph-role="background" width="{WIDTH}" height="{HEIGHT}" fill="#f8fafc"/>')

    text(lines, 60, 53, "统一会话数据流：文字提问 ↔ 合同上传 ↔ 合同追问", size=29, weight=750)
    text(
        lines,
        60,
        86,
        "文字问题和合同上传共享同一个 session_id；合同上下文包含脱敏正文、事实 JSON 和风险报告。",
        size=14,
        fill="#64748b",
    )
    # Common auth strip.
    add(lines, '<rect data-graph-role="container" x="60" y="104" width="2120" height="50" rx="12" fill="#eef2ff" stroke="#c7d2fe" stroke-width="1.2"/>')
    text(lines, 88, 135, "所有入口共用：Authorization Bearer → get_current_user → user_id 注入 → Service/Repository 按 user_id、session_id 做归属检查", size=13, fill="#3730a3", weight=650)
    text(lines, 1860, 135, "身份边界", size=12, fill="#4338ca", weight=700, anchor="middle")

    lane(lines, 180, 328, "#f0f7ff", "#bfdbfe", "1  合同上传 → 事实提取 → 合同审查 Workflow → 报告", "#1d4ed8", "lane-contract")
    lane(lines, 535, 390, "#faf7ff", "#ddd6fe", "2  合同上下文问答（与上传前文字共用 session_id）", "#6d28d9", "lane-report-chat")
    lane(lines, 980, 320, "#fffbeb", "#fed7aa", "3  没有合同绑定时的直接聊天 → 通用 / 法律知识问答", "#b45309", "lane-general-chat")
    lane(lines, 1340, 250, "#f8fafc", "#cbd5e1", "共享数据与持久化边界", "#475569", "lane-storage")

    # Draw edges first, so cards sit on top of connectors.
    # Contract lane (centres are y=284).
    edge(lines, "contract-upload", "M315 284 H355", source="c-user", target="c-upload-api", color="#2563eb", marker="arrow-blue")
    edge(lines, "contract-service", "M600 284 H640", source="c-upload-api", target="c-service", color="#2563eb", marker="arrow-blue")
    edge(lines, "contract-extract", "M885 284 H925", source="c-service", target="c-extract", color="#2563eb", marker="arrow-blue")
    edge(lines, "contract-confirm", "M1170 284 H1210", source="c-extract", target="c-confirm", color="#059669", marker="arrow-green")
    edge(lines, "contract-workflow", "M1455 284 H1495", source="c-confirm", target="c-workflow", color="#059669", marker="arrow-green")
    edge(lines, "contract-report", "M1740 284 H1780", source="c-workflow", target="c-report", color="#059669", marker="arrow-green")
    edge(lines, "contract-to-context", "M1902 346 V500 H1047 V590", source="c-report", target="r-context", color="#7c3aed", marker="arrow-purple", dashed=True)

    # Report chat lane (centres are y=647).
    edge(lines, "report-click", "M315 647 H355", source="r-user", target="r-frontend", color="#7c3aed", marker="arrow-purple")
    edge(lines, "report-request", "M600 647 H640", source="r-frontend", target="r-api", color="#7c3aed", marker="arrow-purple")
    edge(lines, "report-context", "M885 647 H925", source="r-api", target="r-context", color="#7c3aed", marker="arrow-purple")
    edge(lines, "report-graph", "M1170 647 H1210", source="r-context", target="r-graph", color="#7c3aed", marker="arrow-purple")
    edge(lines, "report-answer", "M1455 647 H1495", source="r-graph", target="r-generate", color="#ea580c", marker="arrow-orange")
    edge(lines, "report-response", "M1740 647 H1780", source="r-generate", target="r-response", color="#ea580c", marker="arrow-orange")
    edge(lines, "report-gap", "M1047 706 V760 H1260", source="r-context", target="report-gap", color="#ea580c", marker="arrow-orange", dashed=True)

    # General chat lane (centres are y=1090).
    edge(lines, "general-open", "M315 1090 H355", source="g-user", target="g-frontend", color="#b45309", marker="arrow-orange")
    edge(lines, "general-request", "M600 1090 H640", source="g-frontend", target="g-api", color="#b45309", marker="arrow-orange")
    edge(lines, "general-graph", "M885 1090 H925", source="g-api", target="g-graph", color="#b45309", marker="arrow-orange")
    edge(lines, "general-tool", "M1170 1090 H1210", source="g-graph", target="g-tool", color="#2563eb", marker="arrow-blue")
    edge(lines, "general-retrieval", "M1455 1090 H1495", source="g-tool", target="g-retrieval", color="#2563eb", marker="arrow-blue")
    edge(lines, "general-answer", "M1740 1090 H1780", source="g-retrieval", target="g-response", color="#2563eb", marker="arrow-blue")

    # Contract lane cards.
    card(lines, "c-user", 70, 230, "用户 / 前端", "选择 PDF、DOC、DOCX", badge="U", fill="#eff6ff", stroke="#60a5fa", status="入口", status_fill="#dbeafe", status_text="#1d4ed8")
    card(lines, "c-upload-api", 355, 230, "合同上传 API", "POST /api/contract-reviews", badge="API", fill="#eff6ff", stroke="#60a5fa", status="202 queued", status_fill="#dbeafe", status_text="#1d4ed8")
    card(lines, "c-service", 640, 230, "ContractReviewService", "私有存储 + 任务状态", badge="SVC", fill="#f5f3ff", stroke="#8b5cf6", status="异步", status_fill="#ede9fe", status_text="#6d28d9")
    card(lines, "c-extract", 925, 230, "解析 / 脱敏 / 提取", "原生文本或 OCR → clauses + facts", badge="DOC", fill="#f0fdf4", stroke="#34d399", status="完成后轮询", status_fill="#dcfce7", status_text="#047857", subtitle_2="证据定位保留 provenance")
    card(lines, "c-confirm", 1210, 230, "事实确认表单", "确认 / 修改 / 补充 / 不适用", badge="FACT", fill="#f0fdf4", stroke="#34d399", status="门禁", status_fill="#dcfce7", status_text="#047857")
    card(lines, "c-workflow", 1495, 230, "Contract Review Workflow", "规则评估 → A/B 法律检索", badge="WF", fill="#fff7ed", stroke="#fb923c", status="生成报告", status_fill="#ffedd5", status_text="#c2410c")
    card(lines, "c-report", 1780, 230, "报告页", "风险事实、依据、建议、待确认", badge="R", fill="#fff7ed", stroke="#fb923c", status="持久化", status_fill="#ffedd5", status_text="#c2410c")

    # Report chat cards.
    card(lines, "r-user", 70, 590, "报告页按钮", "点击“针对报告提问”", badge="UI", fill="#faf5ff", stroke="#a78bfa", status="绑定报告", status_fill="#ede9fe", status_text="#6d28d9")
    card(lines, "r-frontend", 355, 590, "前端 ChatPage", "mode=contract_review + review_id", badge="WEB", fill="#faf5ff", stroke="#a78bfa", status="同一 session", status_fill="#ede9fe", status_text="#6d28d9", subtitle_2="保留上传前的文字历史")
    card(lines, "r-api", 640, 590, "Chat API / ChatService", "校验 user_id + review_id + session_id", badge="API", fill="#faf5ff", stroke="#a78bfa", status="已鉴权", status_fill="#ede9fe", status_text="#6d28d9")
    card(lines, "r-context", 925, 590, "合同上下文装配", "脱敏正文 + facts JSON + report JSON", badge="CTX", fill="#f0fdf4", stroke="#34d399", status="统一 context", status_fill="#dcfce7", status_text="#047857", subtitle_2="get_task + get_report")
    card(lines, "r-graph", 1210, 590, "LangGraph session thread", "chatbot → 需要时调用法律检索", badge="G", fill="#f5f3ff", stroke="#8b5cf6", status="可调用法律", status_fill="#ede9fe", status_text="#6d28d9", subtitle_2="禁止通用 RAG 混入私有合同")
    card(lines, "r-generate", 1495, 590, "generate_answer", "contract_context + legal context", badge="LLM", fill="#f0fdf4", stroke="#34d399", status="合同可追问", status_fill="#dcfce7", status_text="#047857")
    card(lines, "r-response", 1780, 590, "统一会话回复", "继续问正文、事实、报告和法律依据", badge="OUT", fill="#f0fdf4", stroke="#34d399", status="用户可见", status_fill="#dcfce7", status_text="#047857")
    callout(
        lines,
        "report-gap",
        905,
        760,
        980,
        "统一上下文：上传后可直接追问合同事实",
        [
            "同一 session thread 保留上传前文字和上传后的合同上下文；工资等事实从 confirmed/corrected/supplemented 的有效值读取。",
            "合同正文仅使用脱敏 pages，法律问题才调用治理后的法律 RAG，避免私有合同进入共享知识库。",
        ],
    )

    # General chat cards.
    card(lines, "g-user", 70, 1032, "用户 / 直接聊天", "新建或恢复普通 session", badge="U", fill="#fffbeb", stroke="#f59e0b", status="无报告绑定", status_fill="#fef3c7", status_text="#b45309")
    card(lines, "g-frontend", 355, 1032, "前端 ChatPage", "mode=general 或 legal", badge="WEB", fill="#fffbeb", stroke="#f59e0b", status="无 review_id", status_fill="#fef3c7", status_text="#b45309")
    card(lines, "g-api", 640, 1032, "Chat API / ChatService", "使用 session_id 建立普通 thread", badge="API", fill="#fffbeb", stroke="#f59e0b", status="已鉴权", status_fill="#fef3c7", status_text="#b45309")
    card(lines, "g-graph", 925, 1032, "LangGraph chatbot", "决定是否调用检索工具", badge="G", fill="#f5f3ff", stroke="#8b5cf6", status="模式路由", status_fill="#ede9fe", status_text="#6d28d9")
    card(lines, "g-tool", 1210, 1032, "ModeAwareToolNode", "general: search_knowledge_base", badge="T", fill="#eff6ff", stroke="#60a5fa", status="允许工具", status_fill="#dbeafe", status_text="#1d4ed8", subtitle_2="legal: search_legal_knowledge_base")
    card(lines, "g-retrieval", 1495, 1032, "RetrievalService", "Embedding → Cascade Funnel → Top-3", badge="RAG", fill="#eff6ff", stroke="#60a5fa", status="共享库", status_fill="#dbeafe", status_text="#1d4ed8")
    card(lines, "g-response", 1780, 1032, "答案生成", "检索上下文 → ANSWER_PROMPT", badge="OUT", fill="#eff6ff", stroke="#60a5fa", status="无私有合同", status_fill="#dbeafe", status_text="#1d4ed8")

    # Storage cards.
    card(lines, "s-private", 70, 1405, "私有合同数据", "脱敏 pages / extraction_result", badge="FILE", fill="#f0fdf4", stroke="#34d399", status="用户隔离", status_fill="#dcfce7", status_text="#047857", width=460, height=112)
    card(lines, "s-postgres", 570, 1405, "PostgreSQL", "user_profiles · sessions · facts · reports", badge="DB", fill="#f0fdf4", stroke="#34d399", status="按 user_id", status_fill="#dcfce7", status_text="#047857", width=520, height=112)
    card(lines, "s-qdrant", 1130, 1405, "Qdrant 法律 RAG", "全国通用 A/B 法律资料（共享语料）", badge="Q", fill="#f8fafc", stroke="#64748b", status="共享知识", status_fill="#e2e8f0", status_text="#475569", width=520, height=112)
    card(lines, "s-checkpoint", 1690, 1405, "LangGraph Checkpoint", "统一 session thread（旧报告 thread 兼容）", badge="MEM", fill="#f5f3ff", stroke="#8b5cf6", status="按 thread_id", status_fill="#ede9fe", status_text="#6d28d9", width=470, height=112)

    # Legend and bottom conclusion.
    add(lines, '<rect data-graph-role="legend" x="60" y="1570" width="2120" height="48" rx="12" fill="#ffffff" stroke="#e2e8f0" stroke-width="1.2"/>')
    add(lines, '<line x1="90" y1="1594" x2="130" y2="1594" stroke="#2563eb" stroke-width="2.2" marker-end="url(#arrow-blue)"/>')
    text(lines, 142, 1599, "请求/流程", size=12, fill="#334155")
    add(lines, '<line x1="300" y1="1594" x2="340" y2="1594" stroke="#059669" stroke-width="2.2" marker-end="url(#arrow-green)"/>')
    text(lines, 352, 1599, "数据写入/门禁", size=12, fill="#334155")
    add(lines, '<line x1="540" y1="1594" x2="580" y2="1594" stroke="#ea580c" stroke-width="2.2" stroke-dasharray="8,6" marker-end="url(#arrow-orange)"/>')
    text(lines, 592, 1599, "当前断点/降级", size=12, fill="#9a3412")
    text(lines, 900, 1599, "统一规则：所有文字问题进入 session thread；上传合同是一次 context mutation；合同删除时清空 active_review_id / contract_context，不删除整条聊天历史。", size=12, fill="#475569", weight=650)
    add(lines, "</svg>")
    return "\n".join(lines)


if __name__ == "__main__":
    OUTPUT.write_text(build_svg(), encoding="utf-8")
    print(f"generated: {OUTPUT}")
