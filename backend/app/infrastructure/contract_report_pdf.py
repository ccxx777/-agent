"""把已持久化的合同审查报告渲染成固定 PDF。

渲染只接收结构化报告，不读取原始合同文件。使用 ReportLab 的内置中文 CID
字体，避免把服务器上的字体路径或未脱敏合同内容暴露给 API。
"""

from __future__ import annotations

from io import BytesIO
from typing import Any
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

FONT_NAME = "STSong-Light"
pdfmetrics.registerFont(UnicodeCIDFont(FONT_NAME))


def _text(value: Any, limit: int | None = None) -> str:
    value_text = str(value or "").strip()
    if limit is not None:
        value_text = value_text[:limit]
    return escape(value_text).replace("\n", "<br/>")


def render_contract_report_pdf(report: dict[str, Any]) -> bytes:
    """将报告渲染为可下载的 PDF 字节。"""

    buffer = BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=18 * mm,
        leftMargin=18 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
        title=f"合同风险审查报告 {report.get('review_id', '')}",
        author="合同风险助手",
    )
    styles = getSampleStyleSheet()
    title = ParagraphStyle(
        "ContractTitle",
        parent=styles["Title"],
        fontName=FONT_NAME,
        fontSize=18,
        leading=25,
        alignment=TA_CENTER,
        textColor=colors.HexColor("#0f172a"),
        spaceAfter=10,
    )
    heading = ParagraphStyle(
        "ContractHeading",
        parent=styles["Heading2"],
        fontName=FONT_NAME,
        fontSize=12,
        leading=18,
        textColor=colors.HexColor("#1e3a8a"),
        spaceBefore=10,
        spaceAfter=5,
    )
    body = ParagraphStyle(
        "ContractBody",
        parent=styles["BodyText"],
        fontName=FONT_NAME,
        fontSize=9.5,
        leading=15,
        textColor=colors.HexColor("#1f2937"),
        spaceAfter=5,
    )
    muted = ParagraphStyle(
        "ContractMuted",
        parent=body,
        fontSize=8,
        leading=12,
        textColor=colors.HexColor("#64748b"),
    )

    story: list[Any] = [
        Paragraph("中国大陆劳动合同风险审查报告", title),
        Paragraph(
            f"报告编号：{_text(report.get('report_id') or report.get('review_id'))}　"
            f"版本：{_text(report.get('report_version', 1))}<br/>"
            f"生成时间：{_text(report.get('generated_at'))}<br/>"
            f"审查范围：{_text(report.get('scope'))}<br/>"
            f"状态：{_text(report.get('workflow_status'))}",
            body,
        ),
        Spacer(1, 4),
    ]

    findings = report.get("findings") or []
    summary_data = [
        ["风险项", "高风险", "中风险", "待确认"],
        [
            str(len(findings)),
            str(sum(item.get("risk_level") == "high" for item in findings)),
            str(sum(item.get("risk_level") == "medium" for item in findings)),
            str(sum(item.get("risk_level") == "unconfirmed" for item in findings)),
        ],
    ]
    summary_table = Table(summary_data, colWidths=[38 * mm] * 4)
    summary_table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, -1), FONT_NAME),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e0e7ff")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#1e3a8a")),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cbd5e1")),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    story.extend([Paragraph("一、风险概览", heading), summary_table])

    story.append(Paragraph("二、风险发现", heading))
    if not findings:
        story.append(Paragraph("当前报告没有结构化风险发现。这不等于合同不存在风险，请继续结合合同原文和专业意见核对。", body))
    for index, finding in enumerate(findings, 1):
        story.append(
            Paragraph(
                f"<b>{index}. {_text(finding.get('title'))}</b>　风险等级：{_text(finding.get('risk_level'))}",
                body,
            )
        )
        story.append(Paragraph(_text(finding.get("summary")), body))
        evidence = finding.get("evidence") or []
        if evidence:
            story.append(Paragraph(f"合同证据：{_text(evidence[0].get('quote'), 1200)}", body))
        if finding.get("recommendation"):
            story.append(Paragraph(f"修改建议：{_text(finding.get('recommendation'), 1000)}", body))
        if finding.get("question"):
            story.append(Paragraph(f"待确认：{_text(finding.get('question'), 800)}", body))

    pending = report.get("pending_questions") or []
    if pending:
        story.append(Paragraph("三、待确认问题", heading))
        for question in pending:
            story.append(Paragraph(f"- {_text(question, 1000)}", body))

    sources = [*(report.get("legal_sources") or []), *(report.get("case_sources") or [])]
    if sources:
        story.append(Paragraph("四、法律依据与案例来源", heading))
        for source in sources[:30]:
            citation = source.get("citation_label") or source.get("title") or "法律资料"
            story.append(
                Paragraph(
                    f"<b>{_text(source.get('source_level'))}级 {_text(citation)}</b><br/>"
                    f"{_text(source.get('quote'), 1000)}<br/>"
                    f"官方来源：{_text(source.get('official_url') or source.get('source'), 500)}",
                    body,
                )
            )

    warnings = report.get("warnings") or []
    if warnings:
        story.append(Paragraph("五、系统提示", heading))
        for warning in warnings:
            story.append(Paragraph(f"- {_text(warning, 1000)}", body))

    story.extend(
        [
            Paragraph("免责声明", heading),
            Paragraph(_text(report.get("disclaimer")), muted),
        ]
    )
    document.build(story)
    return buffer.getvalue()
