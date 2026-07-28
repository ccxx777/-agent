"""生成合同上传模块的独立技术架构图。

脚本只生成文档图，不访问数据库、合同文件或运行中的服务。
"""

from html import escape
from pathlib import Path

WIDTH = 1600
HEIGHT = 1050
OUTPUT = Path(__file__).with_name("contract-upload-module.svg")


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
    height = 88
    add(
        lines,
        f'<g id="{node_id}" data-graph-role="node">'
        f'<rect x="{x}" y="{y}" width="{width}" height="{height}" rx="10" '
        f'fill="{fill}" stroke="{stroke}" stroke-width="1.6"/>',
    )
    add(lines, f'<circle cx="{x + 28}" cy="{y + 29}" r="17" fill="{badge_fill}"/>')
    text(lines, x + 28, y + 34, badge, size=10, fill="#ffffff", weight=700, anchor="middle")
    text(lines, x + 54, y + 28, title, size=15, weight=600)
    text(lines, x + 54, y + 52, subtitle, size=12, fill="#4b5563")
    add(lines, f'<rect x="{x + width - 82}" y="{y + 59}" width="68" height="20" rx="10" fill="{status_fill}"/>')
    text(lines, x + width - 48, y + 73, status, size=10, fill=status_text, weight=600, anchor="middle")
    add(lines, "</g>")


def decision(lines: list[str], node_id: str, cx: int, cy: int) -> None:
    points = f"{cx},{cy-45} {cx+86},{cy} {cx},{cy+45} {cx-86},{cy}"
    add(
        lines,
        f'<g id="{node_id}" data-graph-role="node"><polygon points="{points}" '
        'fill="#fff7ed" stroke="#fb923c" stroke-width="1.6"/>',
    )
    text(lines, cx, cy - 4, "是否需要 OCR？", size=13, weight=600, anchor="middle")
    text(lines, cx, cy + 17, "扫描 / 混合页", size=11, fill="#9a3412", anchor="middle")
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
) -> None:
    dash = ' stroke-dasharray="7,5"' if dashed else ""
    add(
        lines,
        f'<path id="{edge_id}" data-graph-role="edge" data-source="{source}" '
        f'data-target="{target}" d="{path}" fill="none" stroke="{color}" '
        f'stroke-width="2"{dash} marker-end="url(#{marker})"/>',
    )
    if label:
        text(lines, label_x or 0, label_y or 0, label, size=11, fill=color, weight=600, anchor="middle")


def build_svg() -> str:
    lines: list[str] = []
    add(
        lines,
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}" '
        f'viewBox="0 0 {WIDTH} {HEIGHT}" role="img" aria-labelledby="title desc">',
    )
    add(lines, '<title id="title">合同上传与 PDF、DOC、DOCX 解析模块</title>')
    add(
        lines,
        '<desc id="desc">展示合同 PDF、DOC、DOCX 从上传、私有存储、任务状态、文字解析、可选 OCR、脱敏到质量门禁的流程。</desc>',
    )
    add(
        lines,
        "<style>text{font-family:'Helvetica Neue',Helvetica,Arial,'PingFang SC','Microsoft YaHei','Microsoft JhengHei','SimHei',sans-serif;}</style>",
    )
    add(lines, "<defs>")
    for marker_id, color in (
        ("arrow-blue", "#2563eb"),
        ("arrow-green", "#059669"),
        ("arrow-purple", "#9333ea"),
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

    text(lines, 60, 48, "合同上传模块：PDF / DOC / DOCX 接入、解析与脱敏", size=26, weight=600)
    text(
        lines,
        60,
        78,
        "首版接入 PDF、DOC、DOCX；PDF 扫描/混合页进入可插拔 OCR，Word 文件提取正文后统一脱敏。",
        size=14,
        fill="#6b7280",
    )

    # Legend
    text(lines, 1120, 42, "图例", size=12, fill="#6b7280", weight=600)
    add(lines, '<line x1="1120" y1="60" x2="1150" y2="60" stroke="#2563eb" stroke-width="2" marker-end="url(#arrow-blue)"/>')
    text(lines, 1158, 64, "主流程", size=12, fill="#374151")
    add(lines, '<line x1="1240" y1="60" x2="1270" y2="60" stroke="#9333ea" stroke-width="2" stroke-dasharray="7,5" marker-end="url(#arrow-purple)"/>')
    text(lines, 1278, 64, "后台任务", size=12, fill="#374151")
    add(lines, '<line x1="1370" y1="60" x2="1400" y2="60" stroke="#ea580c" stroke-width="2" marker-end="url(#arrow-orange)"/>')
    text(lines, 1408, 64, "隐私 / OCR", size=12, fill="#374151")

    # Containers
    add(lines, '<rect data-graph-role="container" x="45" y="112" width="1510" height="210" rx="14" fill="#f8fbff" stroke="#bfdbfe" stroke-width="1.4"/>')
    add(lines, '<rect data-graph-role="container" x="45" y="365" width="1510" height="515" rx="14" fill="#fffdf8" stroke="#fed7aa" stroke-width="1.4"/>')
    text(lines, 70, 142, "在线接入层", size=16, fill="#1d4ed8", weight=600)
    text(lines, 70, 395, "私有文档处理层", size=16, fill="#9a3412", weight=600)

    # Edges are drawn before nodes.
    edge(lines, "e-user-api", "M240 225 H300", source="user", target="upload-api", color="#2563eb", marker="arrow-blue")
    edge(lines, "e-api-validate", "M500 225 H560", source="upload-api", target="validate", color="#2563eb", marker="arrow-blue")
    edge(lines, "e-validate-storage", "M780 225 H840", source="validate", target="private-storage", color="#059669", marker="arrow-green", label="原始文件", label_x=810, label_y=170)
    edge(lines, "e-validate-task", "M670 268 V310 H1080 V248 H1100", source="validate", target="task", color="#9333ea", marker="arrow-purple", dashed=True, label="创建任务", label_x=940, label_y=302)
    edge(lines, "e-storage-task", "M1040 225 H1100", source="private-storage", target="task", color="#059669", marker="arrow-green", label="路径 + SHA-256", label_x=1070, label_y=170)
    edge(lines, "e-task-parser", "M1200 268 V330 H1400 V365", source="task", target="pdf-parser", color="#9333ea", marker="arrow-purple", dashed=True, label="后台处理", label_x=1320, label_y=322)
    edge(lines, "e-parser-classify", "M1400 453 V485", source="pdf-parser", target="page-classifier", color="#2563eb", marker="arrow-blue")
    edge(lines, "e-classify-native", "M1280 529 H1170 V615", source="page-classifier", target="native-text", color="#2563eb", marker="arrow-blue", label="native", label_x=1215, label_y=555)
    edge(lines, "e-classify-ocr", "M1520 529 V615 H1350", source="page-classifier", target="ocr", color="#ea580c", marker="arrow-orange", label="scanned / hybrid", label_x=1430, label_y=605)
    edge(lines, "e-ocr-redact", "M1240 659 H1160 V750 H760 V703", source="ocr", target="redact", color="#ea580c", marker="arrow-orange", label="OCR 文本", label_x=1080, label_y=742)
    edge(lines, "e-native-redact", "M920 659 H870", source="native-text", target="redact", color="#2563eb", marker="arrow-blue", label="页文本", label_x=895, label_y=641)
    edge(lines, "e-redact-quality", "M650 659 H600", source="redact", target="quality", color="#ea580c", marker="arrow-orange", label="脱敏文本", label_x=625, label_y=641)
    edge(lines, "e-quality-workflow", "M380 659 H300 V760", source="quality", target="workflow", color="#059669", marker="arrow-green", label="ready", label_x=330, label_y=742)
    edge(lines, "e-quality-confirm", "M600 700 V820 H900", source="quality", target="confirmation", color="#ea580c", marker="arrow-orange", label="needs_confirmation", label_x=750, label_y=812)

    # Nodes
    node(lines, "user", 60, 180, 180, "用户", "上传待审合同", fill="#eff6ff", stroke="#93c5fd", badge="U", badge_fill="#2563eb", status="入口", status_fill="#dbeafe", status_text="#1d4ed8")
    node(lines, "upload-api", 300, 180, 200, "FastAPI 上传", "Bearer 鉴权 / multipart", fill="#eff6ff", stroke="#93c5fd", badge="API", badge_fill="#009688", status="已完成", status_fill="#dbeafe", status_text="#1d4ed8")
    node(lines, "validate", 560, 180, 220, "文件校验", "PDF / DOC / DOCX / 20MB / 50页", fill="#eff6ff", stroke="#93c5fd", badge="V", badge_fill="#2563eb", status="已完成", status_fill="#dbeafe", status_text="#1d4ed8")
    node(lines, "private-storage", 840, 180, 200, "私有存储", "原始合同文件，不进 Qdrant", fill="#f0fdf4", stroke="#86efac", badge="FS", badge_fill="#059669", status="已完成", status_fill="#dcfce7", status_text="#166534")
    node(lines, "task", 1100, 180, 200, "PostgreSQL 任务", "queued / extracting / ready", fill="#f0fdf4", stroke="#86efac", badge="PG", badge_fill="#336791", status="已完成", status_fill="#dcfce7", status_text="#166534")
    node(lines, "pdf-parser", 1280, 365, 240, "统一文档解析", "可恢复任务", fill="#faf5ff", stroke="#d8b4fe", badge="BG", badge_fill="#9333ea", status="MVP", status_fill="#ede9fe", status_text="#6b21a8")
    node(lines, "page-classifier", 1280, 485, 240, "格式与页级检查", "PDF / OOXML / antiword", fill="#eff6ff", stroke="#93c5fd", badge="DOC", badge_fill="#2563eb", status="已完成", status_fill="#dbeafe", status_text="#1d4ed8")
    node(lines, "native-text", 920, 615, 220, "原生文字页", "PDF/Word 文字提取", fill="#eff6ff", stroke="#93c5fd", badge="TXT", badge_fill="#2563eb", status="已完成", status_fill="#dbeafe", status_text="#1d4ed8")
    node(lines, "ocr", 1240, 615, 220, "OCR Provider", "DeepSeek OCR，可选", fill="#fff7ed", stroke="#fdba74", badge="OCR", badge_fill="#ea580c", status="可配置", status_fill="#fed7aa", status_text="#9a3412")
    node(lines, "redact", 650, 615, 220, "本地脱敏", "身份证 / 手机 / 银行卡", fill="#fff7ed", stroke="#fdba74", badge="PII", badge_fill="#ea580c", status="已完成", status_fill="#fed7aa", status_text="#9a3412")
    node(lines, "quality", 380, 615, 220, "文本质量门禁", "页级质量 / 需确认", fill="#fff7ed", stroke="#fdba74", badge="Q", badge_fill="#ea580c", status="已完成", status_fill="#fed7aa", status_text="#9a3412")
    node(lines, "workflow", 100, 760, 220, "LangGraph Workflow", "仅接收 ready 文本", fill="#f3f4f6", stroke="#9ca3af", badge="LG", badge_fill="#6b7280", status="下一步", status_fill="#e5e7eb", status_text="#4b5563")
    node(lines, "confirmation", 900, 760, 250, "用户确认 / 重传", "OCR 失败或页面不清晰", fill="#f3f4f6", stroke="#9ca3af", badge="?", badge_fill="#6b7280", status="下一步", status_fill="#e5e7eb", status_text="#4b5563")

    add(lines, '<rect x="70" y="890" width="1460" height="82" rx="10" fill="#ffffff" stroke="#d1d5db" stroke-width="1.2"/>')
    text(lines, 95, 919, "隐私边界", size=13, fill="#9a3412", weight=700)
    text(lines, 95, 944, "原始 PDF/DOC/DOCX 只保存在私有目录；脱敏后的页文本才允许进入 Embedding、Reranker、LLM 和日志。", size=12, fill="#4b5563")
    text(lines, 820, 919, "远程 OCR 提醒", size=13, fill="#9a3412", weight=700)
    text(lines, 820, 944, "开启远程 OCR 时原始扫描图片会离开服务器，privacy.external_raw_image_sent 会被记录。", size=12, fill="#4b5563")

    add(lines, "</svg>")
    return "\n".join(lines) + "\n"


def main() -> None:
    OUTPUT.write_text(build_svg(), encoding="utf-8", newline="\n")
    print(f"generated: {OUTPUT}")


if __name__ == "__main__":
    main()
