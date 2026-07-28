"""生成合同事实提取的详细流程图。

图中明确区分确定性规则、模型候选结果、本地证据定位和状态门禁；
它是文档资产生成脚本，不访问数据库、真实合同或外部模型。
"""

from html import escape
from pathlib import Path

WIDTH = 1800
HEIGHT = 1120
OUTPUT = Path(__file__).with_name("contract-fact-extraction-flow.svg")


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
    height = 100
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
    add(lines, f'<circle data-node-id="{node_id}" cx="{x + 31}" cy="{y + 32}" r="19" fill="{badge_fill}"/>')
    text(lines, x + 31, y + 37, badge, size=10, fill="#ffffff", weight=700, anchor="middle", role="node")
    text(lines, x + 65, y + 31, title, size=15, weight=600, role="node")
    text(lines, x + 65, y + 57, subtitle, size=12, fill="#4b5563", role="node")
    add(
        lines,
        f'<rect data-node-id="{node_id}" x="{x + width - 94}" y="{y + 70}" width="80" height="21" '
        f'rx="11" fill="{status_fill}"/>',
    )
    text(lines, x + width - 54, y + 85, status, size=10, fill=status_text, weight=600, anchor="middle", role="node")
    add(lines, "</g>")


def decision(lines: list[str], node_id: str, cx: int, cy: int, half_width: int, half_height: int) -> None:
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
    text(lines, cx, cy - 4, "证据足够?", size=13, weight=600, anchor="middle", role="node")
    text(lines, cx, cy + 16, "confidence ≥ 0.65", size=11, fill="#9a3412", anchor="middle", role="node")
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
    add(lines, '<title id="title">合同事实提取详细流程</title>')
    add(
        lines,
        '<desc id="desc">展示脱敏文本如何经过正则与关键词条款切分、批量上下文构造、LLM 候选事实 JSON、Schema 校验、本地证据定位、事实规范化、置信度门禁、冲突检测和 PostgreSQL 持久化。</desc>',
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

    text(lines, 60, 48, "合同事实提取详细流程", size=28, weight=600)
    text(lines, 60, 80, "规则负责结构，模型负责语义，本地代码负责证据与不确定性门禁", size=14, fill="#6b7280")

    text(lines, 1280, 43, "图例", size=12, fill="#6b7280", weight=600)
    add(lines, '<line x1="1280" y1="64" x2="1310" y2="64" stroke="#2563eb" stroke-width="2" marker-end="url(#arrow-blue)"/>')
    text(lines, 1320, 68, "确定性流程", size=12, fill="#374151")
    add(lines, '<line x1="1430" y1="64" x2="1460" y2="64" stroke="#9333ea" stroke-width="2" marker-end="url(#arrow-purple)"/>')
    text(lines, 1470, 68, "模型/变换", size=12, fill="#374151")
    add(lines, '<line x1="1580" y1="64" x2="1610" y2="64" stroke="#059669" stroke-width="2" marker-end="url(#arrow-green)"/>')
    text(lines, 1620, 68, "证据/持久化", size=12, fill="#374151")

    # Lanes reserve an explicit gap for the vertical LLM output and fact result routes.
    add(lines, '<rect data-graph-role="container" x="45" y="105" width="1710" height="260" rx="14" fill="#f8fbff" stroke="#bfdbfe" stroke-width="1.4"/>')
    add(lines, '<rect data-graph-role="container" x="45" y="400" width="1710" height="300" rx="14" fill="#f8fafc" stroke="#d1d5db" stroke-width="1.4"/>')
    add(lines, '<rect data-graph-role="container" x="45" y="735" width="1710" height="320" rx="14" fill="#fffdf8" stroke="#fed7aa" stroke-width="1.4"/>')
    text(lines, 70, 135, "① 确定性条款切分 → 模型候选", size=16, fill="#1d4ed8", weight=600)
    text(lines, 70, 430, "② 候选 JSON → 本地证据定位与事实规范化", size=16, fill="#374151", weight=600)
    text(lines, 70, 765, "③ 状态门禁 → 冲突检测 → 可追溯结果", size=16, fill="#9a3412", weight=600)

    # Edges are drawn before nodes to keep labels and cards readable.
    edge(lines, "e-pages-split", "M300 225 H380", source="redacted-pages", target="clause-splitter", color="#2563eb", marker="arrow-blue", label="脱敏页文本", label_x=340, label_y=207)
    edge(lines, "e-split-schema", "M620 225 H700", source="clause-splitter", target="clause-schema", color="#9333ea", marker="arrow-purple", label="条款块", label_x=660, label_y=207)
    edge(lines, "e-schema-batch", "M940 225 H1020", source="clause-schema", target="batch-context", color="#2563eb", marker="arrow-blue", label="clause_id / page", label_x=980, label_y=155)
    edge(lines, "e-batch-llm", "M1260 225 H1340", source="batch-context", target="llm-extractor", color="#9333ea", marker="arrow-purple", label="脱敏条款 prompt", label_x=1300, label_y=155)
    edge(lines, "e-llm-candidate", "M1500 275 V490", source="llm-extractor", target="candidate-json", color="#9333ea", marker="arrow-purple", label="facts JSON", label_x=1545, label_y=385)
    edge(lines, "e-candidate-schema", "M1350 540 H1240", source="candidate-json", target="schema-validation", color="#9333ea", marker="arrow-purple", label="候选结构", label_x=1295, label_y=520)
    edge(lines, "e-schema-evidence", "M1000 540 H940", source="schema-validation", target="evidence-locator", color="#2563eb", marker="arrow-blue", label="合法字段", label_x=970, label_y=520)
    edge(lines, "e-evidence-normalizer", "M680 540 H620", source="evidence-locator", target="fact-normalizer", color="#059669", marker="arrow-green", label="证据列表", label_x=650, label_y=520)
    edge(lines, "e-normalizer-facts", "M360 540 H300", source="fact-normalizer", target="fact-list", color="#2563eb", marker="arrow-blue", label="ContractFact", label_x=330, label_y=465)
    edge(lines, "e-facts-gate", "M190 590 V650 H60 V825 H290", source="fact-list", target="status-gate", color="#2563eb", marker="arrow-blue")
    edge(lines, "e-gate-confirmed", "M490 825 H580", source="status-gate", target="confirmed-facts", color="#059669", marker="arrow-green", label="是 / ready", label_x=535, label_y=790)
    edge(lines, "e-gate-needs-confirmation", "M390 870 V930", source="status-gate", target="needs-confirmation", color="#ea580c", marker="arrow-orange", label="否 / 追问", label_x=450, label_y=905)
    edge(lines, "e-confirmed-contradictions", "M840 820 H950", source="confirmed-facts", target="contradiction-check", color="#059669", marker="arrow-green")
    edge(lines, "e-needs-contradictions", "M520 980 H900 V870 H950", source="needs-confirmation", target="contradiction-check", color="#ea580c", marker="arrow-orange", label="仍需确认", label_x=700, label_y=965)
    edge(lines, "e-contradictions-questions", "M1090 870 V930", source="contradiction-check", target="confirmation-questions", color="#ea580c", marker="arrow-orange", label="冲突字段", label_x=1150, label_y=905)
    edge(lines, "e-questions-db", "M1230 980 H1340", source="confirmation-questions", target="postgres", color="#059669", marker="arrow-green", label="facts + questions", label_x=1285, label_y=960)

    # Top lane nodes.
    node(lines, "redacted-pages", 80, 175, 220, "脱敏页文本", "ContractPage / PII 已清理", fill="#eff6ff", stroke="#93c5fd", badge="TXT", badge_fill="#2563eb", status="输入", status_fill="#dbeafe", status_text="#1d4ed8")
    node(lines, "clause-splitter", 380, 175, 240, "条款切分器", "逐行 + 编号/标题正则", fill="#eff6ff", stroke="#93c5fd", badge="RE", badge_fill="#2563eb", status="本地", status_fill="#dbeafe", status_text="#1d4ed8")
    node(lines, "clause-schema", 700, 175, 240, "ContractClause", "clause_id / type / page range", fill="#faf5ff", stroke="#d8b4fe", badge="S", badge_fill="#9333ea", status="结构", status_fill="#ede9fe", status_text="#6b21a8")
    node(lines, "batch-context", 1020, 175, 240, "批量上下文", "最多 6 条 / 字符上限", fill="#faf5ff", stroke="#d8b4fe", badge="B", badge_fill="#9333ea", status="变换", status_fill="#ede9fe", status_text="#6b21a8")
    node(lines, "llm-extractor", 1340, 175, 320, "LLM 候选事实提取", "主体 / 期限 / 工资 / 社保 / …", fill="#faf5ff", stroke="#d8b4fe", badge="LLM", badge_fill="#9333ea", status="可开关", status_fill="#ede9fe", status_text="#6b21a8")

    # Middle lane nodes.
    node(lines, "candidate-json", 1350, 490, 300, "候选 facts JSON", "value / quote / clause_ids / confidence", fill="#faf5ff", stroke="#d8b4fe", badge="J", badge_fill="#9333ea", status="模型输出", status_fill="#ede9fe", status_text="#6b21a8")
    node(lines, "schema-validation", 1000, 490, 240, "Schema 校验", "Pydantic；无效项丢弃并记录 warning", fill="#eff6ff", stroke="#93c5fd", badge="V", badge_fill="#2563eb", status="本地", status_fill="#dbeafe", status_text="#1d4ed8")
    node(lines, "evidence-locator", 680, 490, 260, "EvidenceLocator", "exact → 空白规范化 fallback", fill="#f0fdf4", stroke="#86efac", badge="E", badge_fill="#059669", status="本地", status_fill="#dcfce7", status_text="#166534")
    node(lines, "fact-normalizer", 360, 490, 260, "FactNormalizer", "日期/文本清理 + 证据门禁", fill="#eff6ff", stroke="#93c5fd", badge="N", badge_fill="#2563eb", status="本地", status_fill="#dbeafe", status_text="#1d4ed8")
    node(lines, "fact-list", 80, 490, 220, "ContractFact[]", "status / evidence / confidence", fill="#f0fdf4", stroke="#86efac", badge="F", badge_fill="#059669", status="中间结果", status_fill="#dcfce7", status_text="#166534")
    add(lines, '<rect data-graph-role="note" x="900" y="630" width="800" height="46" rx="9" fill="#ffffff" stroke="#d1d5db" stroke-width="1.2"/>')
    text(lines, 925, 658, "隐私边界：只向模型发送脱敏文本；引用必须回到本地页文本重新定位，找不到证据不会自动确认。", size=12, fill="#4b5563", role="note")

    # Bottom lane nodes.
    decision(lines, "status-gate", 390, 825, 100, 45)
    node(lines, "confirmed-facts", 580, 770, 260, "确认事实", "有证据且 confidence ≥ 0.65", fill="#f0fdf4", stroke="#86efac", badge="OK", badge_fill="#059669", status="confirmed", status_fill="#dcfce7", status_text="#166534")
    node(lines, "needs-confirmation", 260, 930, 260, "待确认事实", "缺证据 / 低置信度 / 需追问", fill="#fff7ed", stroke="#fdba74", badge="?", badge_fill="#ea580c", status="needs_confirmation", status_fill="#fed7aa", status_text="#9a3412")
    node(lines, "contradiction-check", 950, 770, 280, "同名事实冲突检查", "category + name 分组比较 normalized_value", fill="#fff7ed", stroke="#fdba74", badge="C", badge_fill="#ea580c", status="本地", status_fill="#fed7aa", status_text="#9a3412")
    node(lines, "confirmation-questions", 950, 930, 280, "确认问题生成", "missing / contradicted / evidence 缺失", fill="#f3f4f6", stroke="#9ca3af", badge="Q", badge_fill="#6b7280", status="可追问", status_fill="#e5e7eb", status_text="#4b5563")
    node(lines, "postgres", 1340, 930, 300, "PostgreSQL JSONB", "extraction_status + extraction_result", fill="#f0fdf4", stroke="#86efac", badge="PG", badge_fill="#336791", status="持久化", status_fill="#dcfce7", status_text="#166534")

    add(lines, '<rect data-graph-role="note" x="70" y="310" width="1100" height="36" rx="8" fill="#ffffff" stroke="#d1d5db" stroke-width="1.2"/>')
    text(lines, 92, 333, "开关分支：CONTRACT_EXTRACTION_ENABLED=false 时保存条款并直接进入 needs_confirmation，不调用模型。", size=12, fill="#4b5563", role="note")

    add(lines, "</svg>")
    return "\n".join(lines) + "\n"


def main() -> None:
    OUTPUT.write_text(build_svg(), encoding="utf-8", newline="\n")
    print(f"generated: {OUTPUT}")


if __name__ == "__main__":
    main()
