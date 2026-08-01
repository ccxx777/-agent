"""生成“JSON 文件与 PostgreSQL 存储边界”架构图。

这张图只描述当前项目真实存在的持久化边界：
* JSON/JSONL 主要是法律语料、离线评测结果和断点诊断工件；
* PostgreSQL 保存生产会话、合同元数据、脱敏正文、结构化结果和报告；
* 原始合同仍在私有文件目录，Qdrant 只保存已治理知识库的检索数据。
"""

from __future__ import annotations

from html import escape
from pathlib import Path


WIDTH = 1440
HEIGHT = 960
BG = "#ffffff"
TEXT = "#111827"
MUTED = "#6b7280"
BLUE = "#2563eb"
GREEN = "#16a34a"
PURPLE = "#9333ea"
ORANGE = "#ea580c"
GRAY = "#6b7280"


def esc(value: str) -> str:
    return escape(value, quote=True)


lines: list[str] = []


def add(value: str) -> None:
    lines.append(value)


def text(x: float, y: float, value: str, *, size: int = 14, fill: str = TEXT,
         weight: int = 400, anchor: str = "start", opacity: float = 1.0) -> None:
    add(
        f'<text x="{x}" y="{y}" fill="{fill}" font-size="{size}px" '
        f'font-weight="{weight}" text-anchor="{anchor}" opacity="{opacity}">{esc(value)}</text>'
    )


def rect(x: float, y: float, w: float, h: float, *, fill: str = "#ffffff",
         stroke: str = "#d1d5db", radius: int = 10, dash: str = "") -> None:
    dash_attr = f' stroke-dasharray="{dash}"' if dash else ""
    add(
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{radius}" '
        f'fill="{fill}" stroke="{stroke}" stroke-width="1.5"{dash_attr}/>'
    )


def container(x: float, y: float, w: float, h: float, title: str, subtitle: str,
             fill: str, stroke: str) -> None:
    rect(x, y, w, h, fill=fill, stroke=stroke, radius=14, dash="7,5")
    text(x + 18, y + 28, title, size=17, weight=600)
    text(x + 18, y + 50, subtitle, size=11, fill=MUTED)


def card(x: float, y: float, w: float, h: float, title: str, subtitle: str,
         *, fill: str = "#ffffff", stroke: str = "#d1d5db", accent: str = BLUE,
         note: str | None = None) -> None:
    rect(x, y, w, h, fill=fill, stroke=stroke, radius=10)
    add(f'<rect x="{x}" y="{y}" width="6" height="{h}" rx="3" fill="{accent}"/>')
    text(x + 18, y + 29, title, size=14, weight=600)
    text(x + 18, y + 52, subtitle, size=11, fill=MUTED)
    if note:
        text(x + 18, y + h - 17, note, size=11, fill=accent, weight=600)


def cylinder_icon(cx: float, cy: float, color: str) -> None:
    add(f'<ellipse cx="{cx}" cy="{cy - 18}" rx="20" ry="7" fill="{color}" opacity="0.9"/>')
    add(f'<rect x="{cx - 20}" y="{cy - 18}" width="40" height="36" fill="{color}"/>')
    add(f'<ellipse cx="{cx}" cy="{cy + 18}" rx="20" ry="7" fill="{color}"/>')
    add(f'<ellipse cx="{cx}" cy="{cy - 5}" rx="20" ry="7" fill="none" stroke="#ffffff" stroke-width="1" opacity="0.55"/>')


def file_icon(x: float, y: float, color: str) -> None:
    add(f'<path d="M{x},{y} h24 l10,10 v34 h-34 z" fill="{color}" opacity="0.95"/>')
    add(f'<path d="M{x + 24},{y} v10 h10" fill="none" stroke="#ffffff" stroke-width="1.2" opacity="0.75"/>')
    add(f'<line x1="{x + 7}" y1="{y + 24}" x2="{x + 27}" y2="{y + 24}" stroke="#ffffff" stroke-width="1.2" opacity="0.8"/>')
    add(f'<line x1="{x + 7}" y1="{y + 31}" x2="{x + 24}" y2="{y + 31}" stroke="#ffffff" stroke-width="1.2" opacity="0.8"/>')


def edge(d: str, color: str, marker: str, source: str, target: str,
         label: str | None = None, label_x: float = 0, label_y: float = 0,
         dash: str = "") -> None:
    dash_attr = f' stroke-dasharray="{dash}"' if dash else ""
    add(
        f'<path d="{d}" fill="none" stroke="{color}" stroke-width="2"{dash_attr} '
        f'marker-end="url(#{marker})" data-graph-role="edge" '
        f'data-source="{esc(source)}" data-target="{esc(target)}"/>'
    )
    if label:
        # 标签放在空白走廊，并加轻微白底，避免与线重叠。
        width = max(80, len(label) * 12 + 14)
        rect(label_x - 6, label_y - 15, width, 22, fill="#ffffff", stroke="none", radius=4)
        text(label_x, label_y, label, size=11, fill=color, weight=600)


add(f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {WIDTH} {HEIGHT}" width="{WIDTH}" height="{HEIGHT}">
<style>
text {{ font-family: 'Helvetica Neue', Helvetica, Arial, 'PingFang SC', 'Microsoft YaHei', 'Microsoft JhengHei', SimHei, sans-serif; }}
</style>
<defs>
  <marker id="arrow-blue" markerWidth="10" markerHeight="7" refX="9" refY="3.5" orient="auto"><polygon points="0 0, 10 3.5, 0 7" fill="{BLUE}"/></marker>
  <marker id="arrow-green" markerWidth="10" markerHeight="7" refX="9" refY="3.5" orient="auto"><polygon points="0 0, 10 3.5, 0 7" fill="{GREEN}"/></marker>
  <marker id="arrow-purple" markerWidth="10" markerHeight="7" refX="9" refY="3.5" orient="auto"><polygon points="0 0, 10 3.5, 0 7" fill="{PURPLE}"/></marker>
  <marker id="arrow-orange" markerWidth="10" markerHeight="7" refX="9" refY="3.5" orient="auto"><polygon points="0 0, 10 3.5, 0 7" fill="{ORANGE}"/></marker>
  <marker id="arrow-gray" markerWidth="10" markerHeight="7" refX="9" refY="3.5" orient="auto"><polygon points="0 0, 10 3.5, 0 7" fill="{GRAY}"/></marker>
</defs>
<rect width="{WIDTH}" height="{HEIGHT}" fill="{BG}"/>
''')

text(42, 45, "存储边界：JSON 文件 vs PostgreSQL 生产数据", size=26, weight=600)
text(42, 73, "合同正文、事实 JSON、风险报告和会话上下文的真实落点", size=14, fill=MUTED)

container(40, 112, 315, 720, "JSON / JSONL 文件工件", "离线输入、评测输出、断点状态；不是生产会话真相", "#eff6ff", "#93c5fd")
file_icon(66, 175, BLUE)
card(108, 160, 220, 112, "法律语料 prepared/", "*.json / *.jsonl / manifest.json", fill="#ffffff", stroke="#bfdbfe", accent=BLUE, note="data_worker 的入库输入")
file_icon(66, 330, GREEN)
card(108, 315, 220, 112, "评测与 Smoke 结果", "retrieval / generation / RAGAS", fill="#ffffff", stroke="#bbf7d0", accent=GREEN, note="离线评测输出")
file_icon(66, 485, PURPLE)
card(108, 470, 220, 112, "断点与诊断状态", "legal-ingest-state/*.json", fill="#ffffff", stroke="#ddd6fe", accent=PURPLE, note="resume / 调试")
rect(64, 640, 264, 142, fill="#fff7ed", stroke="#fdba74", radius=10)
text(80, 672, "边界提醒", size=14, fill=ORANGE, weight=600)
text(80, 700, "JSON 文件不会作为合同问答", size=12)
text(80, 721, "的生产上下文来源。", size=12)
text(80, 750, "生产合同数据走 PostgreSQL + 私有文件存储。", size=11, fill=MUTED)

container(390, 112, 350, 720, "Backend / LangGraph", "把文件、数据库记录组装成一次请求的上下文", "#f0fdf4", "#86efac")
card(420, 160, 290, 82, "合同上传 API", "创建 review_id / session_id", fill="#ffffff", stroke="#bbf7d0", accent=GREEN)
card(420, 280, 290, 106, "解析、脱敏、事实提取", "PDF / DOC / DOCX → 脱敏页文本 + facts JSON", fill="#ffffff", stroke="#bbf7d0", accent=PURPLE)
card(420, 450, 290, 106, "ChatService 上下文组装", "读取正文、确认事实、风险报告，形成 contract_context", fill="#ffffff", stroke="#bbf7d0", accent=BLUE)
card(420, 620, 290, 86, "Agent / LangGraph", "问题 → 法律检索 → 答案或风险说明", fill="#ffffff", stroke="#bbf7d0", accent=ORANGE)

container(775, 112, 345, 720, "PostgreSQL", "生产运行时的权限控制与持久化真相", "#eef2ff", "#93c5fd")
cylinder_icon(812, 172, "#336791")
card(840, 145, 255, 104, "contract_review_tasks", "元数据 + quality/privacy JSONB", fill="#ffffff", stroke="#c7d2fe", accent="#336791", note="extraction_result / confirmation_result")
card(840, 285, 255, 92, "contract_review_pages", "脱敏正文 redacted_text（按页）", fill="#ffffff", stroke="#c7d2fe", accent="#336791")
card(840, 413, 255, 92, "contract_review_reports", "报告 JSONB + report_version", fill="#ffffff", stroke="#c7d2fe", accent="#336791")
card(840, 541, 255, 106, "sessions + checkpoints", "用户归属、scope 版本、消息、摘要", fill="#ffffff", stroke="#c7d2fe", accent="#336791", note="LangGraph checkpoint 也在 PostgreSQL")
rect(800, 685, 295, 112, fill="#f0fdf4", stroke="#86efac", radius=10)
text(818, 715, "PostgreSQL 保存什么？", size=14, fill=GREEN, weight=600)
text(818, 741, "可恢复、可鉴权、可审计的生产数据", size=12)
text(818, 763, "不是原始合同二进制，也不是 JSON 文件目录。", size=11, fill=MUTED)

container(1155, 112, 245, 720, "其他存储", "不与 JSON / PostgreSQL 混为一谈", "#fff7ed", "#fdba74")
file_icon(1180, 185, ORANGE)
card(1220, 170, 150, 130, "私有文件目录", "original.pdf / .doc / .docx", fill="#ffffff", stroke="#fed7aa", accent=ORANGE, note="原始二进制")
cylinder_icon(1198, 405, "#dc244c")
card(1220, 365, 150, 130, "Qdrant", "法律语料向量 / 全文索引", fill="#ffffff", stroke="#fecdd3", accent="#dc244c", note="不写入私有合同")
rect(1175, 565, 200, 170, fill="#ffffff", stroke="#fed7aa", radius=10)
text(1192, 596, "不会进入共享 RAG", size=14, fill=ORANGE, weight=600)
text(1192, 624, "上传合同的原始文件、", size=12)
text(1192, 646, "脱敏前 OCR 图像和私有路径", size=12)
text(1192, 668, "不会写入 Qdrant。", size=12)
text(1192, 704, "问答只读取治理后的法律库。", size=11, fill=MUTED)

# 主要数据流：文件 → 处理 → PostgreSQL / 私有文件；问答读取 PostgreSQL。
edge("M 328,212 L 360,212 L 360,96 L 1140,96 L 1140,430 L 1220,430", GREEN, "arrow-green", "legal-prepared-json", "qdrant-legal", "data_worker 入库", 620, 91, "5,4")
edge("M 710,201 L 750,201 L 750,101 L 1160,101 L 1160,245 L 1220,245", ORANGE, "arrow-orange", "upload-api", "private-original", "原始二进制", 930, 96)
edge("M 710,327 L 775,327 L 775,197 L 840,197", PURPLE, "arrow-purple", "extractor", "contract-tasks", "metadata / JSONB", 724, 270)
edge("M 710,355 L 790,355 L 790,331 L 840,331", GREEN, "arrow-green", "extractor", "contract-pages", "脱敏正文", 730, 374, "5,4")
edge("M 710,372 L 780,372 L 780,459 L 840,459", PURPLE, "arrow-purple", "workflow", "contract-reports", "报告 JSONB", 720, 452)
edge("M 840,590 L 780,590 L 780,503 L 710,503", GREEN, "arrow-green", "postgres-context", "chat-context", "正文 + facts + report", 716, 580, "5,4")
edge("M 565,556 L 565,620", BLUE, "arrow-blue", "chat-context", "agent", "contract_context", 575, 594)
edge("M 710,663 L 755,663 L 755,594 L 840,594", GREEN, "arrow-green", "agent", "langgraph-checkpoint", "messages / summary", 735, 650, "5,4")
edge("M 420,663 L 380,663 L 380,371 L 328,371", PURPLE, "arrow-purple", "agent", "evaluation-json", "离线评测写入", 250, 520, "5,4")

# 图例与明确结论。
rect(40, 866, 1360, 60, fill="#f9fafb", stroke="#e5e7eb", radius=10)
text(58, 892, "图例", size=12, fill=MUTED, weight=600)
add(f'<line x1="102" y1="888" x2="142" y2="888" stroke="{BLUE}" stroke-width="2" marker-end="url(#arrow-blue)" data-graph-role="decoration"/>')
text(151, 892, "Agent 主流程", size=11, fill=MUTED)
add(f'<line x1="252" y1="888" x2="292" y2="888" stroke="{GREEN}" stroke-width="2" stroke-dasharray="5,4" marker-end="url(#arrow-green)" data-graph-role="decoration"/>')
text(301, 892, "存储读写", size=11, fill=MUTED)
add(f'<line x1="390" y1="888" x2="430" y2="888" stroke="{PURPLE}" stroke-width="2" marker-end="url(#arrow-purple)" data-graph-role="decoration"/>')
text(439, 892, "解析 / 结构化转换", size=11, fill=MUTED)
add(f'<line x1="575" y1="888" x2="615" y2="888" stroke="{ORANGE}" stroke-width="2" marker-end="url(#arrow-orange)" data-graph-role="decoration"/>')
text(624, 892, "原始文件边界", size=11, fill=MUTED)
text(820, 892, "一句话：JSON 是离线工件；PostgreSQL 是生产会话与合同结构化数据的真相源。", size=12, fill=TEXT, weight=600)

add("</svg>")

output = Path(__file__).with_suffix(".svg")
output.write_text("\n".join(lines), encoding="utf-8")
print(output)
