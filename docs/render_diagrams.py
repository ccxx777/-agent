"""在没有 Cairo DLL 的 Windows 环境中，为本目录 SVG 生成带中文字体的 PNG 预览。

该脚本只用于文档图像构建，不参与 Backend 运行时。它使用系统 SimHei 字体，
避免 SVG 直接栅格化时中文变成方框；提交前仍以 SVG 作为可编辑源文件。
"""

from __future__ import annotations

import argparse
import tempfile
from pathlib import Path

import fitz
from reportlab.graphics import renderPDF
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from svglib.svglib import svg2rlg


def _apply_font(obj: object, font_name: str) -> None:
    if hasattr(obj, "fontName"):
        obj.fontName = font_name  # type: ignore[attr-defined]
    for child in getattr(obj, "contents", ()) or ():
        _apply_font(child, font_name)


def render(svg_path: Path, png_path: Path, width: int) -> None:
    drawing = svg2rlg(str(svg_path))
    if drawing is None:
        raise RuntimeError(f"无法解析 SVG: {svg_path}")

    font_path = Path(r"C:\Windows\Fonts\simhei.ttf")
    if not font_path.exists():
        raise RuntimeError(f"找不到中文字体: {font_path}")
    font_name = "DiagramSimHei"
    pdfmetrics.registerFont(TTFont(font_name, str(font_path)))
    _apply_font(drawing, font_name)

    png_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="diagram-render-") as temp_dir:
        pdf_path = Path(temp_dir) / "diagram.pdf"
        renderPDF.drawToFile(drawing, str(pdf_path))
        document = fitz.open(str(pdf_path))
        try:
            page = document[0]
            scale = width / max(float(drawing.width), 1.0)
            pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
            pixmap.save(str(png_path))
        finally:
            document.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("svg", nargs="+", type=Path)
    parser.add_argument("--width", type=int, default=1920)
    args = parser.parse_args()
    for svg_path in args.svg:
        render(svg_path, svg_path.with_suffix(".png"), args.width)
        print(f"rendered: {svg_path.with_suffix('.png')}")


if __name__ == "__main__":
    main()
