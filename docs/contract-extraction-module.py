"""生成合同条款切分、结构化事实提取和证据定位图。

这是文档图生成脚本，只描述当前已经实现的服务边界，不访问数据库、真实合同或外部模型。
"""

from html import escape
from pathlib import Path

WIDTH = 1600
HEIGHT = 900
OUTPUT = Path(__file__).with_name("contract-extraction-module.svg")


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
) -> None:
    height = 94
    bounds = f"{x} {y} {x + width} {y + height}"
    add(
        lines,
        f'<g id="{node_id}" data-node-id="{node_id}" data-graph-role="node" '
        f'data-graph-bounds="{bounds}">',
    )
    add(
        lines,
        f'<rect data-node-id="{node_id}" x="{x}" y="{y}" width="{width}" height="{height}" '
        f'rx="10" fill="{fill}" stroke="{stroke}" stroke-width="1.6"/>',
    )
    add(lines, f'<circle data-node-id="{node_id}" cx="{x + 29}" cy="{y + 31}" r="18" fill="{badge_fill}"/>')
    text(lines, x + 29, y + 36, badge, size=10, fill="#ffffff", weight=700, anchor="middle", role="node")
    text(lines, x + 58, y + 30, title, size=15, weight=600, role="node")
    text(lines, x + 58, y + 55, subtitle, size=12, fill="#4b5563", role="node")
    add(lines, f'<rect data-node-id="{node_id}" x="{x + width - 92}" y="{y + 65}" width="78" height="21" rx="11" fill="{status_fill}"/>')
    text(lines, x + width - 53, y + 80, status, size=10, fill=status_text, weight=600, anchor="middle", role="node")
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
        'data-visual-theme="flat-icon" data-diagram-type="data-flow" data-semantic-profile="generic" '
        'data-quality-profile="standard" data-min-node-gap="40" data-min-container-gutter="20" '
        'data-min-label-clearance="4" data-min-segment-length="16">',
    )
    add(lines, '<title id="title">合同条款与事实提取：结构化 Schema、证据定位和确认边界</title>')
    add(
        lines,
        '<desc id="desc">展示脱敏页文本经过确定性条款切分、模型候选事实提取、本地证据定位、事实规范化和 PostgreSQL 持久化的流程；法律风险规则不在本模块内。</desc>',
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
        ("arrow-gray", "#6b7280"),
    ):
        add(
            lines,
            f'<marker id="{marker_id}" markerWidth="10" markerHeight="7" refX="9" refY="3.5" orient="auto">'
            f'<polygon points="0 0, 10 3.5, 0 7" fill="{color}"/></marker>',
        )
    add(lines, "</defs>")
    add(lines, f'<rect data-graph-role="background" width="{WIDTH}" height="{HEIGHT}" fill="#ffffff"/>')

    text(lines, 60, 48, "合同条款与事实提取模块", size=27, weight=600)
    text(lines, 60, 78, "只处理已经脱敏的合同文本；事实与法律风险判断保持独立", size=14, fill="#6b7280")

    text(lines, 1130, 43, "图例", size=12, fill="#6b7280", weight=600)
    add(lines, '<line x1="1130" y1="64" x2="1160" y2="64" stroke="#2563eb" stroke-width="2" marker-end="url(#arrow-blue)"/>')
    text(lines, 1170, 68, "主要数据流", size=12, fill="#374151")
    add(lines, '<line x1="1260" y1="64" x2="1290" y2="64" stroke="#9333ea" stroke-width="2" marker-end="url(#arrow-purple)"/>')
    text(lines, 1300, 68, "模型/变换", size=12, fill="#374151")
    add(lines, '<line x1="1400" y1="64" x2="1430" y2="64" stroke="#059669" stroke-width="2" marker-end="url(#arrow-green)"/>')
    text(lines, 1440, 68, "持久化", size=12, fill="#374151")

    add(lines, '<rect data-graph-role="container" x="45" y="112" width="1510" height="275" rx="14" fill="#f8fbff" stroke="#bfdbfe" stroke-width="1.4"/>')
    add(lines, '<rect data-graph-role="container" x="45" y="420" width="1510" height="300" rx="14" fill="#f8fafc" stroke="#d1d5db" stroke-width="1.4"/>')
    add(lines, '<rect data-graph-role="container" x="45" y="750" width="1510" height="135" rx="14" fill="#fffdf8" stroke="#fed7aa" stroke-width="1.4" stroke-dasharray="7,5"/>')
    text(lines, 70, 142, "A. 脱敏文本 → 候选事实", size=16, fill="#1d4ed8", weight=600)
    text(lines, 70, 450, "B. 本地证据校验 → 状态与确认问题", size=16, fill="#374151", weight=600)
    text(lines, 70, 780, "边界：后续法律规则引擎读取事实，但不由本模块输出风险等级", size=14, fill="#9a3412", weight=600)

    # Edges are drawn before nodes so node cards keep labels readable.
    edge(lines, "e-pages-split", "M310 237 H380", source="redacted-pages", target="clause-splitter", color="#2563eb", marker="arrow-blue")
    edge(lines, "e-split-schema", "M600 237 H670", source="clause-splitter", target="clause-schema", color="#9333ea", marker="arrow-purple", label="ContractClause", label_x=635, label_y=165)
    edge(lines, "e-schema-batch", "M890 237 H960", source="clause-schema", target="batch-context", color="#2563eb", marker="arrow-blue")
    edge(lines, "e-batch-llm", "M1180 237 H1250", source="batch-context", target="llm-extractor", color="#9333ea", marker="arrow-purple", label="脱敏条款 JSON prompt", label_x=1215, label_y=165)
    edge(lines, "e-llm-locator", "M1370 284 V330 H430 V430 H410 V517 H380", source="llm-extractor", target="evidence-locator", color="#9333ea", marker="arrow-purple", label="候选 quote / clause_id", label_x=950, label_y=370)
    edge(lines, "e-pages-locator", "M200 284 V405 H60 V517 H150", source="redacted-pages", target="evidence-locator", color="#2563eb", marker="arrow-blue", label="页文本", label_x=120, label_y=390)
    edge(lines, "e-locator-normalizer", "M300 564 V590 H430 V517 H460", source="evidence-locator", target="fact-normalizer", color="#2563eb", marker="arrow-blue", label="page / offset", label_x=400, label_y=620)
    edge(lines, "e-normalizer-gate", "M690 517 H770", source="fact-normalizer", target="status-gate", color="#2563eb", marker="arrow-blue", label="normalized fact", label_x=730, label_y=450)
    edge(lines, "e-gate-db", "M1000 517 H1080", source="status-gate", target="postgres", color="#059669", marker="arrow-green", label="JSONB result", label_x=1040, label_y=495)
    edge(lines, "e-gate-api", "M930 564 V620 H1320 V517 H1350", source="status-gate", target="api-confirm", color="#9333ea", marker="arrow-purple", label="缺失 / 冲突 / 低置信度", label_x=1170, label_y=643)
    edge(lines, "e-gate-rules", "M825 564 V700 H800 V750", source="status-gate", target="legal-rule-boundary", color="#6b7280", marker="arrow-gray", dashed=True, label="仅提供已确认事实", label_x=690, label_y=675)

    node(lines, "redacted-pages", 90, 190, 220, "脱敏页文本", "ContractPage / PII 已清理", fill="#eff6ff", stroke="#93c5fd", badge="TXT", badge_fill="#2563eb", status="输入", status_fill="#dbeafe", status_text="#1d4ed8")
    node(lines, "clause-splitter", 380, 190, 220, "确定性条款切分", "编号标题 + 常见条款标题", fill="#eff6ff", stroke="#93c5fd", badge="C", badge_fill="#2563eb", status="本地", status_fill="#dbeafe", status_text="#1d4ed8")
    node(lines, "clause-schema", 670, 190, 220, "ContractClause Schema", "clause_id / type / page range", fill="#faf5ff", stroke="#d8b4fe", badge="S", badge_fill="#9333ea", status="结构", status_fill="#ede9fe", status_text="#6b21a8")
    node(lines, "batch-context", 960, 190, 220, "批量上下文构造", "限制模型输入长度", fill="#faf5ff", stroke="#d8b4fe", badge="B", badge_fill="#9333ea", status="变换", status_fill="#ede9fe", status_text="#6b21a8")
    node(lines, "llm-extractor", 1250, 190, 240, "结构化事实提取", "JSON 候选，不做风险判定", fill="#faf5ff", stroke="#d8b4fe", badge="LLM", badge_fill="#9333ea", status="可开关", status_fill="#ede9fe", status_text="#6b21a8")
    node(lines, "evidence-locator", 150, 470, 230, "EvidenceLocator", "exact / normalized + 字符偏移", fill="#eff6ff", stroke="#93c5fd", badge="E", badge_fill="#2563eb", status="本地", status_fill="#dbeafe", status_text="#1d4ed8")
    node(lines, "fact-normalizer", 460, 470, 230, "FactNormalizer", "日期/文本清理 + 证据门禁", fill="#eff6ff", stroke="#93c5fd", badge="N", badge_fill="#2563eb", status="本地", status_fill="#dbeafe", status_text="#1d4ed8")
    node(lines, "status-gate", 770, 470, 230, "事实状态门禁", "confirmed / missing / contradicted", fill="#fff7ed", stroke="#fdba74", badge="G", badge_fill="#ea580c", status="确认", status_fill="#fed7aa", status_text="#9a3412")
    node(lines, "postgres", 1080, 470, 220, "PostgreSQL 结果", "extraction_status / JSONB", fill="#f0fdf4", stroke="#86efac", badge="PG", badge_fill="#336791", status="持久化", status_fill="#dcfce7", status_text="#166534")
    node(lines, "api-confirm", 1350, 470, 180, "详情 API", "事实 + 证据 + 问题", fill="#eff6ff", stroke="#93c5fd", badge="API", badge_fill="#009688", status="输出", status_fill="#dbeafe", status_text="#1d4ed8")
    node(lines, "legal-rule-boundary", 600, 770, 400, "后续法律规则引擎（不在本模块）", "读取结构化事实与证据，不让 LLM 单独决定风险等级", fill="#f3f4f6", stroke="#9ca3af", badge="R", badge_fill="#6b7280", status="待开发", status_fill="#e5e7eb", status_text="#4b5563")

    add(lines, '<rect data-graph-role="legend" x="1040" y="770" width="480" height="52" rx="9" fill="#ffffff" stroke="#d1d5db" stroke-width="1.2"/>')
    text(lines, 1060, 793, "隐私门禁：模型只接收脱敏文本；日志不记录合同原文", size=12, fill="#4b5563", role="legend")
    text(lines, 1060, 811, "没有本地证据的候选事实不会视为已确认", size=12, fill="#4b5563", role="legend")

    add(lines, "</svg>")
    return "\n".join(lines) + "\n"


def main() -> None:
    OUTPUT.write_text(build_svg(), encoding="utf-8", newline="\n")
    print(f"generated: {OUTPUT}")


if __name__ == "__main__":
    main()
