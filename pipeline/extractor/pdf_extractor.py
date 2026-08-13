"""PDF and image extraction: pdfplumber for typed PDFs, PaddleOCR fallback for
scanned PDFs and for standalone image uploads (JPG/PNG/etc.)."""
import pdfplumber
from loguru import logger

from models.financial import ExtractedDocument, PageContent, TableData
from utils.text_utils import detect_company_name, detect_period

_ocr_engine = None


def _get_ocr_engine():
    """Lazily initialize a single shared PaddleOCR instance (model load is expensive)."""
    global _ocr_engine
    if _ocr_engine is None:
        from paddleocr import PaddleOCR

        _ocr_engine = PaddleOCR(use_textline_orientation=True, lang="en")
    return _ocr_engine


def detect_pdf_type(pdf_path: str) -> str:
    """Returns 'typed' or 'scanned'."""
    with pdfplumber.open(pdf_path) as pdf:
        sample_pages = pdf.pages[: min(5, len(pdf.pages))]
        text_found = sum(1 for p in sample_pages if len(p.extract_text() or "") > 50)
        return "typed" if text_found >= 3 else "scanned"


def _clean_table_rows(rows: list[list[str | None]]) -> list[list[str | None]]:
    """Remove blank rows and repeated header rows; strip whitespace from cells."""
    cleaned: list[list[str | None]] = []
    header_signature = None
    for i, row in enumerate(rows):
        stripped = [
            (cell.strip().replace("\n", " ") if isinstance(cell, str) else cell)
            for cell in row
        ]
        if all(cell is None or str(cell).strip() == "" for cell in stripped):
            continue
        if i == 0:
            header_signature = tuple(stripped)
        elif header_signature is not None and tuple(stripped) == header_signature:
            continue
        cleaned.append(stripped)
    return cleaned


def _extract_tables_from_page(page, page_num: int) -> list[TableData]:
    tables: list[TableData] = []
    try:
        raw_tables = page.extract_tables(
            table_settings={"vertical_strategy": "lines", "horizontal_strategy": "lines"}
        )
        if not raw_tables:
            raw_tables = page.extract_tables(
                table_settings={"vertical_strategy": "text", "horizontal_strategy": "text"}
            )
    except Exception as e:
        logger.warning(f"Table extraction failed on page {page_num}: {e}")
        raw_tables = []

    for raw in raw_tables or []:
        cleaned = _clean_table_rows(raw)
        if not cleaned:
            continue
        headers = [str(h) if h is not None else "" for h in cleaned[0]]
        rows = cleaned[1:]
        tables.append(TableData(headers=headers, rows=rows, page_num=page_num))
    return tables


def _merge_multipage_tables(pages: list[PageContent]) -> None:
    """Merge continuation tables across consecutive pages sharing identical headers."""
    for i in range(len(pages) - 1):
        current_page = pages[i]
        next_page = pages[i + 1]
        if not current_page.tables or not next_page.tables:
            continue
        last_table = current_page.tables[-1]
        first_table_next = next_page.tables[0]
        if last_table.headers == first_table_next.headers:
            last_table.rows.extend(first_table_next.rows)
            next_page.tables.pop(0)


def _extract_typed_pdf(pdf_path: str) -> list[PageContent]:
    pages: list[PageContent] = []
    with pdfplumber.open(pdf_path) as pdf:
        for i, page in enumerate(pdf.pages):
            raw_text = page.extract_text(x_tolerance=3, y_tolerance=3) or ""
            tables = _extract_tables_from_page(page, i + 1)
            pages.append(
                PageContent(page_num=i + 1, raw_text=raw_text, tables=tables, ocr=False)
            )
    _merge_multipage_tables(pages)
    return pages


def _ocr_image_to_text(image) -> str:
    import numpy as np

    ocr = _get_ocr_engine()
    results = ocr.predict(np.array(image))
    lines: list[str] = []
    for page_result in results or []:
        lines.extend(page_result.get("rec_texts", []))
    return "\n".join(lines)


def _extract_scanned_pdf(pdf_path: str) -> list[PageContent]:
    try:
        from pdf2image import convert_from_path
    except ImportError as e:
        logger.error(f"OCR dependencies not available: {e}")
        raise

    images = convert_from_path(pdf_path, dpi=200)
    pages: list[PageContent] = []
    for i, image in enumerate(images):
        text = _ocr_image_to_text(image)
        pages.append(
            PageContent(page_num=i + 1, raw_text=text, tables=[], ocr=True)
        )
    return pages


def extract_pdf(pdf_path: str) -> ExtractedDocument:
    """Main entry point: detect PDF type and extract accordingly."""
    pdf_type = detect_pdf_type(pdf_path)

    if pdf_type == "typed":
        pages = _extract_typed_pdf(pdf_path)
        extraction_method = "pdfplumber"
    else:
        logger.info(f"{pdf_path} detected as scanned; falling back to PaddleOCR")
        pages = _extract_scanned_pdf(pdf_path)
        extraction_method = "paddleocr"

    full_text = "\n".join(p.raw_text for p in pages)
    first_pages_text = "\n".join(p.raw_text for p in pages[:3])

    return ExtractedDocument(
        pages=pages,
        full_text=full_text,
        extraction_method=extraction_method,
        company_name=detect_company_name(first_pages_text),
        period=detect_period(first_pages_text),
        total_pages=len(pages),
    )


def extract_image(image_path: str) -> ExtractedDocument:
    """Extract text from a standalone image (JPG/PNG/etc.) via PaddleOCR.

    Table structure cannot be reliably reconstructed from a flat OCR pass, so
    (consistent with the scanned-PDF path) no TableData is produced here —
    only raw text, which note segmentation and the LLM narrative step can
    still use.
    """
    from PIL import Image

    image = Image.open(image_path).convert("RGB")
    text = _ocr_image_to_text(image)

    return ExtractedDocument(
        pages=[PageContent(page_num=1, raw_text=text, tables=[], ocr=True)],
        full_text=text,
        extraction_method="paddleocr",
        company_name=detect_company_name(text),
        period=detect_period(text),
        total_pages=1,
    )
