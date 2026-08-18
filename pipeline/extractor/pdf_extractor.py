"""PDF and image extraction: pdfplumber for typed PDFs, PaddleOCR fallback for
scanned PDFs and for standalone image uploads (JPG/PNG/etc.)."""
import re
import time

import pdfplumber
from loguru import logger

from models.financial import ExtractedDocument, PageContent, TableData
from utils.text_utils import detect_company_name, detect_period

_ocr_engine = None

# Text-line table-reconstruction fallback (see _reconstruct_table_from_text):
# a bare 1-2 digit integer with no comma/decimal reads as a note reference
# ("19", "5", "14a"), not a value — real ₹ lakh figures in these statements
# are virtually always either 3+ digits, or carry a decimal/comma/parens.
_VALUE_TOKEN_PATTERN = re.compile(r"^\(?-?[\d,]+(\.\d+)?\)?$|^[-–—]$|^nil$", re.IGNORECASE)
_NOTE_REF_TOKEN_PATTERN = re.compile(r"^\d{1,3}[a-zA-Z]?$")


def _is_value_token(tok: str) -> bool:
    if not _VALUE_TOKEN_PATTERN.match(tok):
        return False
    bare_digits = re.match(r"^-?(\d+)$", tok)
    return not (bare_digits and len(bare_digits.group(1)) <= 2)


def _is_note_ref_token(tok: str) -> bool:
    return bool(_NOTE_REF_TOKEN_PATTERN.match(tok)) and not _is_value_token(tok)


def _parse_text_line(line: str) -> tuple[str, str | None, list[str]] | None:
    """Reconstruct (label, note_ref, [current, [prior]]) from one line of
    plain extracted text, by taking up to 2 trailing value-shaped tokens and
    (optionally) one note-reference token before them, treating everything
    else as the label."""
    tokens = line.split()
    if len(tokens) < 2:
        return None
    values: list[str] = []
    i = len(tokens)
    while i > 0 and len(values) < 2 and _is_value_token(tokens[i - 1]):
        values.insert(0, tokens[i - 1])
        i -= 1
    if not values:
        return None
    note_ref = None
    if i > 0 and _is_note_ref_token(tokens[i - 1]):
        # Subtotal/total rows never carry their own note reference in these
        # statements — so a note-ref-shaped token immediately before one
        # isn't a stray note ref at all, it's the true leading digit of the
        # value, split off by a PDF rendering/kerning quirk (confirmed on a
        # real filing: "Total assets 6 6,152.58" and "Total revenue... 1
        # 6,636.52" both actually read 66,152.58 / 16,636.52 — treating
        # "6"/"1" as note_ref silently truncated the real total by roughly
        # an order of magnitude). Reattach it to the value instead of
        # dropping it or leaving it stuck in the label.
        if "total" in " ".join(tokens[:i - 1]).lower():
            values[0] = tokens[i - 1] + values[0]
        else:
            note_ref = tokens[i - 1]
        i -= 1
    label = " ".join(tokens[:i]).strip()
    if len(label) < 3:
        return None
    return label, note_ref, values


def _reconstruct_table_from_text(text: str, page_num: int) -> TableData | None:
    """Line-by-line fallback for when pdfplumber's grid-based table
    extraction can't find real column boundaries on a page — confirmed on
    real user-submitted filings where the "lines" strategy returned a
    structurally-shaped table with every row's label column empty (columns
    separated by whitespace alignment, not drawn ruling lines, so pdfplumber
    misjudges the boundaries), and the "text" strategy fragmented single
    words across spurious columns instead. Plain text extraction is
    unaffected by either failure mode, so reconstructing rows from it
    directly recovers the page.

    Deliberately conservative about calling this a real statement page: a
    handful of narrative sentences that happen to end in a number (a
    section reference, a date) shouldn't get treated as a data table, so
    this requires both a minimum row count and that a healthy fraction of
    the page's actual lines parsed as rows."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    rows: list[list[str | None]] = []
    for line in lines:
        parsed = _parse_text_line(line)
        if parsed is None:
            continue
        label, note_ref, values = parsed
        row = [label, note_ref, *values] + ([None] if len(values) < 2 else [])
        rows.append(row)
    if len(rows) < 5 or len(rows) / max(len(lines), 1) < 0.25:
        return None
    return TableData(headers=["Particulars", "Note", "Current", "Prior"], rows=rows, page_num=page_num)


def _label_fill_ratio(rows: list[list]) -> float:
    if not rows:
        return 0.0
    filled = sum(1 for r in rows if r and r[0] not in (None, "") and str(r[0]).strip())
    return filled / len(rows)


def _get_ocr_engine():
    """Lazily initialize a single shared PaddleOCR instance (model load is expensive)."""
    global _ocr_engine
    if _ocr_engine is None:
        # PaddleX (which paddleocr 3.x is built on) enables its oneDNN/MKL-DNN
        # CPU backend by default, and that backend has a real crash on Cloud
        # Run's x86_64 CPUs for at least one op in the PP-OCRv6 pipeline:
        # "(Unimplemented) ConvertPirAttribute2RuntimeAttribute not support
        # [pir::ArrayAttribute<pir::DoubleAttribute>]" — confirmed on a real
        # scanned-PDF upload. Doesn't reproduce on Apple Silicon (a different
        # backend entirely, never touches this code path), so this has to be
        # disabled unconditionally rather than only where it's known to
        # crash. Must be set before paddleocr/paddlex is ever imported — it's
        # read once into a module-level constant at import time.
        import os

        os.environ.setdefault("PADDLE_PDX_ENABLE_MKLDNN_BYDEFAULT", "False")
        from paddleocr import PaddleOCR

        _ocr_engine = PaddleOCR(
            use_textline_orientation=True,
            # Doc-orientation-classify and unwarping correct phone-camera
            # skew/warp — irrelevant for flatbed/scanner-produced filing
            # PDFs, and each is a full extra model pass per page. Real
            # Cloud Run measurement without these two knobs tuned: ~150s/page
            # on an 8-vCPU instance (oneDNN disabled, see above), too slow
            # for a 40+ page scan to finish inside Cloud Run's 3600s request
            # ceiling.
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            # "small" instead of the default "medium" det/rec tier: PaddleX's
            # own PP-OCRv6 benchmarks show small matching the older
            # v5-mobile tier's latency with better accuracy than medium.
            text_detection_model_name="PP-OCRv6_small_det",
            text_recognition_model_name="PP-OCRv6_small_rec",
            lang="en",
        )
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

    # Neither pdfplumber strategy found a usable grid (either nothing at
    # all, or a table whose label column came back empty for every row —
    # see _reconstruct_table_from_text for how/why). Try rebuilding from
    # plain text instead of silently losing whatever's on this page.
    if not tables or all(_label_fill_ratio(t.rows) < 0.5 for t in tables):
        text = page.extract_text(x_tolerance=3, y_tolerance=3) or ""
        reconstructed = _reconstruct_table_from_text(text, page_num)
        best_existing = max((_label_fill_ratio(t.rows) for t in tables), default=0.0)
        if reconstructed and _label_fill_ratio(reconstructed.rows) > best_existing:
            logger.info(
                f"Page {page_num}: table-grid extraction produced empty/no labels, "
                "reconstructed rows from plain text instead"
            )
            return [reconstructed]

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

    with pdfplumber.open(pdf_path) as pdf:
        total = len(pdf.pages)
    logger.info(f"OCR: {total} page(s) to process (previously silent — no per-page progress at all)")

    pages: list[PageContent] = []
    for i in range(1, total + 1):
        page_t0 = time.time()
        # One page at a time, not the whole document up front: rendering
        # every page's image buffer simultaneously before OCR even starts
        # (the previous behavior) is real, avoidable memory pressure on a
        # many-page scan — confirmed contributing to a silent OOM-pattern
        # kill on a real ~40-page filing.
        [image] = convert_from_path(pdf_path, dpi=200, first_page=i, last_page=i)
        text = _ocr_image_to_text(image)
        pages.append(
            PageContent(page_num=i, raw_text=text, tables=[], ocr=True)
        )
        logger.info(f"OCR: page {i}/{total} done in {time.time() - page_t0:.1f}s")
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
