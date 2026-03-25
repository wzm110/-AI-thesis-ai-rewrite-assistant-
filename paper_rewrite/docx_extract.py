"""Word .docx 正文提取。"""
from __future__ import annotations

from io import BytesIO

MAX_DOCX_BYTES = 15 * 1024 * 1024


def extract_plain_text_from_docx(data: bytes) -> str:
    """从 .docx 提取纯文本：按正文顺序遍历段落与表格，仅保留文字（图片无文本故自动忽略）。"""
    from docx import Document
    from docx.oxml.ns import qn
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    doc = Document(BytesIO(data))
    parts: list[str] = []

    body = doc.element.body
    for child in body.iterchildren():
        if child.tag == qn("w:p"):
            para = Paragraph(child, doc)
            t = para.text.strip()
            if t:
                parts.append(t)
        elif child.tag == qn("w:tbl"):
            tbl = Table(child, doc)
            for row in tbl.rows:
                for cell in row.cells:
                    for p in cell.paragraphs:
                        t = p.text.strip()
                        if t:
                            parts.append(t)

    return "\n\n".join(parts)
