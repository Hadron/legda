"""PDF to plain text via Docling + pandoc (local only).

Docling converts the PDF to Markdown with form-feed page breaks; we decorate
each break with a ``[Page N]`` marker, then run pandoc to plain text. The
result is a single text string with page decorations, suitable for
``prepare_lines``.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

_PAGE_BREAK = "\x0c"


def _docling_markdown(path: Path, ocr: bool) -> str:
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.document_converter import (
        DocumentConverter,
        PdfFormatOption,
    )

    opts = PdfPipelineOptions(
        do_ocr=ocr,
        do_table_structure=True,
        generate_page_images=False,
        generate_picture_images=False,
    )
    conv = DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)}
    )
    result = conv.convert(str(path))
    if str(result.status.value) not in (
        "ConversionStatus.SUCCESS",
        "ConversionStatus.PARTIAL_SUCCESS",
    ):
        raise RuntimeError(f"Docling conversion failed: {result.status}")
    doc = result.document
    return doc.export_to_markdown(
        image_mode="placeholder", page_break_placeholder=_PAGE_BREAK
    )


def _decorate_pages(markdown: str) -> str:
    parts = markdown.split(_PAGE_BREAK)
    if not parts:
        return ""
    decorated = parts[0].strip()
    for i, part in enumerate(parts[1:], start=2):
        decorated += f"\n\n[Page {i}]\n\n" + part.strip()
    return decorated


def _pandoc_to_plain(markdown: str) -> str:
    if shutil.which("pandoc") is None:
        # Fallback: strip common markdown markers crudely.
        import re
        text = markdown
        text = re.sub(r"^#{1,6}\s*", "", text, flags=re.MULTILINE)
        text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
        text = re.sub(r"\*([^*]+)\*", r"\1", text)
        text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
        return text
    proc = subprocess.run(
        ["pandoc", "-f", "markdown", "-t", "plain", "--wrap=none"],
        input=markdown,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"pandoc failed: {proc.stderr}")
    return proc.stdout


def pdf_to_text(path: str | Path, ocr: bool = False) -> str:
    """Convert a PDF to a plain text string with page decorations."""
    p = Path(path)
    markdown = _docling_markdown(p, ocr=ocr)
    decorated = _decorate_pages(markdown)
    plain = _pandoc_to_plain(decorated)
    return plain.strip()
