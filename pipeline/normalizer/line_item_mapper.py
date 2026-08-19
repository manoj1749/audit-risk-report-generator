"""Three-stage line item mapping: exact match -> fuzzy match -> embedding match."""
import re
from pathlib import Path

from loguru import logger
from rapidfuzz import fuzz

import config
from models.financial import ExtractedDocument, MappedLineItem, NoteSection, TableData
from pipeline.extractor.excel_extractor import extract_excel
from pipeline.normalizer.schema import CANONICAL_SCHEMA
from utils.text_utils import clean_label, extract_note_ref, parse_indian_number

_FACE_STATEMENT_SHEET_TYPES = {"balance_sheet", "pnl", "cash_flow"}
_NOTE_COLUMN_HEADER_PATTERN = re.compile(r"^(note|notes|schedule|schedules)\s*(no\.?|number)?$", re.IGNORECASE)
_LEADING_DIGIT_SPLIT_PATTERN = re.compile(r"^(\d{1,2})\s+([\d,]+\.?\d*|\([\d,]+\.?\d*\))$")


def _recover_split_leading_digit(cell_text: str) -> float | None:
    """A PDF rendering quirk occasionally inserts a stray space right after a
    number's leading 1-2 digits — confirmed on a real filing, a table cell
    literally reads "6 6,152.58" where the true value is 66,152.58, not two
    separate figures. parse_indian_number correctly refuses this as an
    ambiguous multi-token cell (see its own docstring — that guard exists
    for genuinely different concatenated values, e.g. several columns'
    worth of figures merged into one cell by a bad table-grid detection).
    This is a narrower, separate recovery that only fires for exactly this
    shape: a bare 1-2 digit token immediately followed by a properly
    comma/decimal-formatted number, not the general multi-value case."""
    match = _LEADING_DIGIT_SPLIT_PATTERN.match(cell_text.strip())
    if not match:
        return None
    return parse_indian_number(match.group(1) + match.group(2))

_embedding_model = None
_canonical_flat: list[tuple[str, str]] | None = None
_canonical_embeddings = None


def _get_embedding_model():
    global _embedding_model
    if _embedding_model is None:
        from sentence_transformers import SentenceTransformer

        _embedding_model = SentenceTransformer(config.EMBEDDING_MODEL_NAME)
    return _embedding_model


def _get_canonical_embeddings():
    """Compute (once, cached) embeddings for every canonical variant string."""
    global _canonical_flat, _canonical_embeddings
    if _canonical_embeddings is None:
        flat: list[tuple[str, str]] = []
        for key, variants in CANONICAL_SCHEMA.items():
            for variant in variants:
                flat.append((key, variant))
        model = _get_embedding_model()
        texts = [v for _, v in flat]
        embeddings = model.encode(texts, normalize_embeddings=True)
        _canonical_flat = flat
        _canonical_embeddings = embeddings
    return _canonical_flat, _canonical_embeddings


def _cosine_sim(a, b) -> float:
    import numpy as np

    return float(np.dot(a, b))


def _log_unmapped(raw_label: str) -> None:
    try:
        with open(config.UNMAPPED_ITEMS_LOG, "a") as f:
            f.write(f"{raw_label}\n")
    except OSError as e:
        logger.warning(f"Could not write to unmapped items log: {e}")


def map_line_item(raw_label: str) -> tuple[str | None, float, str]:
    """Map a raw line-item label to a canonical key. Returns (canonical_key, confidence, method)."""
    cleaned = clean_label(raw_label)
    if not cleaned:
        return None, 0.0, "unknown"

    # A bare "Total" (Schedule III's balance sheet commonly labels its two
    # grand-total rows just "Total", relying on the ASSETS / EQUITY AND
    # LIABILITIES section heading above for meaning, not repeating it on the
    # total line itself) has no textual content to disambiguate which total
    # it is. Left to fuzzy/embedding matching it was landing on "total_income"
    # (a P&L key) purely because that's the closest single-word canonical
    # variant, silently corrupting the real total_assets figure with a
    # confident-looking but wrong match. Table-level structural inference in
    # _map_rows handles this instead, so refuse to guess here.
    if cleaned == "total":
        return None, 0.0, "unknown"

    # Negation override: Schedule III's standard trade payables disclosure format is
    # "(a) dues to micro/small enterprises" vs "(b) dues to creditors OTHER THAN micro
    # enterprises and small enterprises" -- the (b) variant literally contains the words
    # "micro enterprises and small enterprises", so fuzzy matching (which can't detect
    # negation) scores it as a near-exact match to the MSE variant, when it means the
    # opposite. Route explicitly before fuzzy matching ever sees it.
    if re.search(r"other than\s+micro", cleaned):
        return "trade_payables_others", 1.0, "exact"
    if (
        "micro" in cleaned and "enterprise" in cleaned and "other than" not in cleaned
        and ("due" in cleaned or "payable" in cleaned)
    ):
        return "trade_payables_mse", 1.0, "exact"

    # Stage 1: exact match
    for key, variants in CANONICAL_SCHEMA.items():
        if cleaned in variants:
            return key, 1.0, "exact"

    # Stage 2: fuzzy match
    best_key = None
    best_score = 0.0
    for key, variants in CANONICAL_SCHEMA.items():
        for variant in variants:
            score = fuzz.WRatio(cleaned, variant)
            if score > best_score:
                best_score = score
                best_key = key

    if best_score > config.FUZZY_MATCH_THRESHOLD:
        return best_key, best_score / 100, "fuzzy"

    # Stage 3: embedding match, only attempted in the 60-85 fuzzy band
    if config.FUZZY_LOWER_BOUND <= best_score <= config.FUZZY_MATCH_THRESHOLD:
        try:
            flat, canonical_embeddings = _get_canonical_embeddings()
            model = _get_embedding_model()
            query_embedding = model.encode([cleaned], normalize_embeddings=True)[0]
            best_sim = -1.0
            best_sim_key = None
            for (key, _variant), emb in zip(flat, canonical_embeddings):
                sim = _cosine_sim(query_embedding, emb)
                if sim > best_sim:
                    best_sim = sim
                    best_sim_key = key
            if best_sim > config.EMBEDDING_MATCH_THRESHOLD:
                return best_sim_key, best_sim, "embedding"
        except Exception as e:
            logger.warning(f"Embedding match unavailable, treating as unknown: {e}")

    _log_unmapped(raw_label)
    return None, 0.0, "unknown"


def _find_note_column_indices(headers: list[str]) -> set[int]:
    """Column indices whose header identifies them as a note-reference column
    (e.g. "Note No.", "Note", "Schedule") rather than a value column."""
    return {
        i for i, h in enumerate(headers)
        if h and _NOTE_COLUMN_HEADER_PATTERN.match(h.strip())
    }


def _parse_table_rows(table: TableData) -> list[tuple[str, float | None, float | None, str | None]]:
    """Extract (label, current, prior, note_ref) tuples from a table's rows.

    A note-reference column (identified by its header, e.g. "Note No.") is
    excluded from the value scan entirely. Without this, a bare note number
    like "5" or "20" is just as parseable as a real figure, so it gets read
    as the current-period value and every real value shifts one column to
    the right — silently dropping the true prior-period figure. This was
    found by comparing extracted values against a company's own note
    disclosures: a "current" value of 5 for an ROU asset was literally the
    row's Note 5 reference, not a balance.
    """
    results = []
    note_col_indices = _find_note_column_indices(table.headers)
    for row in table.rows:
        if not row:
            continue
        label_cell = row[0]
        if label_cell is None or not str(label_cell).strip():
            continue
        label = str(label_cell).strip()
        if len(label) < 3 or parse_indian_number(label) is not None:
            continue

        note_ref = extract_note_ref(label)
        numeric_values: list[float] = []
        for i, cell in enumerate(row):
            if i == 0:
                continue
            if i in note_col_indices:
                if note_ref is None and cell is not None and str(cell).strip():
                    note_ref = str(cell).strip()
                continue
            if cell is None:
                continue
            val = parse_indian_number(cell)
            if val is None:
                val = _recover_split_leading_digit(str(cell))
            if val is not None:
                numeric_values.append(val)
            elif note_ref is None:
                maybe_ref = extract_note_ref(str(cell))
                if maybe_ref and len(str(cell).strip()) <= 6:
                    note_ref = maybe_ref

        current = numeric_values[0] if len(numeric_values) >= 1 else None
        prior = numeric_values[1] if len(numeric_values) >= 2 else None
        results.append((label, current, prior, note_ref))
    return results


def _map_rows(
    rows: list[tuple[str, float | None, float | None, str | None]],
    mapped: dict[str, MappedLineItem],
    idx_start: int,
    allowed_statement_type: str | None = None,
) -> int:
    """Map extracted rows to canonical keys.

    If `allowed_statement_type` is given (e.g. a sheet already classified as
    'cash_flow'), a label match is only accepted when the canonical key it
    resolved to actually belongs to that statement. This stops a cash flow
    adjustment line like "(Increase)/decrease in other financial assets" from
    being accepted as the balance sheet's "other financial assets" balance —
    same wording, entirely different figure.
    """
    from pipeline.normalizer.schema import CANONICAL_STATEMENT_TYPE

    idx = idx_start
    row_results: list[tuple[str, float | None, float | None, str | None, str | None, float, str]] = []
    for label, current, prior, note_ref in rows:
        canonical_key, confidence, method = map_line_item(label)
        if (
            canonical_key is not None
            and allowed_statement_type is not None
            and CANONICAL_STATEMENT_TYPE.get(canonical_key) != allowed_statement_type
        ):
            logger.debug(
                f"Rejecting cross-statement match: {label!r} -> {canonical_key} "
                f"(belongs to {CANONICAL_STATEMENT_TYPE.get(canonical_key)}, not {allowed_statement_type})"
            )
            canonical_key, confidence, method = None, 0.0, "unknown"
        row_results.append((label, current, prior, note_ref, canonical_key, confidence, method))

    # Structural fallback for a bare "Total" balance-sheet grand-total row
    # (map_line_item refuses to guess this from text alone -- see its
    # docstring). If this table already produced several confident
    # balance-sheet matches, it's a real balance sheet table, so the first
    # still-unmapped bare-"total" row in reading order is Total Assets: in a
    # standard Schedule III layout the ASSETS section (and its "Total" row)
    # always comes before the EQUITY AND LIABILITIES section's own "Total"
    # row. Confidence is deliberately low (0.6) so an actual textual "Total
    # Assets" match elsewhere in the document -- higher method priority --
    # still wins in _select_best.
    bs_key_count = sum(
        1 for *_r, key, _c, _m in row_results
        if key is not None and CANONICAL_STATEMENT_TYPE.get(key) == "balance_sheet"
    )
    if bs_key_count >= 3:
        for i, (label, current, prior, note_ref, canonical_key, confidence, method) in enumerate(row_results):
            if canonical_key is None and clean_label(label) == "total":
                row_results[i] = (label, current, prior, note_ref, "total_assets", 0.6, "structural")
                break

    for label, current, prior, note_ref, canonical_key, confidence, method in row_results:
        mapped[f"{idx}:{label}"] = MappedLineItem(
            raw_label=label,
            canonical_key=canonical_key,
            current_value=current,
            prior_value=prior,
            confidence=confidence,
            method=method,
            note_ref=note_ref,
        )
        idx += 1
    return idx


def map_all_items(
    extracted: ExtractedDocument,
    excel_path: str | None = None,
    notes: dict[str, NoteSection] | None = None,
) -> dict[str, MappedLineItem]:
    """Map every line item found in the face-statement tables (and optional Excel
    workbook) to the canonical schema.

    Scoping matters: a note-level table (a JV/subsidiary breakdown, an actuarial
    schedule, a related-party disclosure, an ageing schedule) very often has rows
    whose labels superficially match a canonical face-statement key (e.g. "Total
    Current Assets" inside a joint venture's own mini balance sheet) but whose
    figures belong to something else entirely. Those tables must never feed this
    generic mapper — they're handled by the dedicated structured parsers in
    table_extractor.py instead, each scoped to its own specific note. So:

    - Any PDF page that falls inside a parsed note's page range is skipped here.
      If `notes` isn't supplied (no notes section detected), no page is excluded —
      best-effort fallback for documents with no detectable notes structure.
    - Any Excel sheet not classified as balance_sheet/pnl/cash_flow is skipped,
      unless none of the sheets matched a known type (in which case naming may
      just be unusual, and excluding everything would be worse than the risk of
      including an unrecognized sheet).
    """
    mapped: dict[str, MappedLineItem] = {}
    idx = 0

    note_pages: set[int] = set()
    if notes:
        for note in notes.values():
            note_pages.update(range(note.page_start, note.page_end + 1))

    skipped_pages = 0
    for page in extracted.pages:
        if page.page_num in note_pages:
            skipped_pages += 1
            continue
        for table in page.tables:
            idx = _map_rows(_parse_table_rows(table), mapped, idx)
    if note_pages:
        logger.info(f"Excluded {skipped_pages} note-covered page(s) from face-statement mapping")

    if excel_path and not Path(excel_path).exists():
        logger.warning(f"Excel path given but not found on disk, skipping: {excel_path}")

    if excel_path and Path(excel_path).exists():
        workbook = extract_excel(excel_path)
        has_recognized_sheet = any(s.sheet_type in _FACE_STATEMENT_SHEET_TYPES for s in workbook.sheets)
        for sheet in workbook.sheets:
            if has_recognized_sheet and sheet.sheet_type not in _FACE_STATEMENT_SHEET_TYPES:
                logger.debug(
                    f"Skipping non-face-statement sheet '{sheet.sheet_name}' (type={sheet.sheet_type})"
                )
                continue
            table = TableData(headers=sheet.headers, rows=[list(r) for r in sheet.rows], page_num=0)
            statement_type = sheet.sheet_type if sheet.sheet_type in _FACE_STATEMENT_SHEET_TYPES else None
            idx = _map_rows(_parse_table_rows(table), mapped, idx, allowed_statement_type=statement_type)

    logger.info(
        f"Mapped {sum(1 for m in mapped.values() if m.canonical_key)} of {len(mapped)} line items"
    )
    return mapped
