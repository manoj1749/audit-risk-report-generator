"""Three-stage line item mapping: exact match -> fuzzy match -> embedding match."""
from pathlib import Path

from loguru import logger
from rapidfuzz import fuzz

import config
from models.financial import ExtractedDocument, MappedLineItem, TableData
from pipeline.extractor.excel_extractor import extract_excel
from pipeline.normalizer.schema import CANONICAL_SCHEMA
from utils.text_utils import clean_label, extract_note_ref, parse_indian_number

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


def _parse_table_rows(table: TableData) -> list[tuple[str, float | None, float | None, str | None]]:
    """Extract (label, current, prior, note_ref) tuples from a table's rows."""
    results = []
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
        for cell in row[1:]:
            if cell is None:
                continue
            val = parse_indian_number(cell)
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


def _map_rows(rows: list[tuple[str, float | None, float | None, str | None]],
               mapped: dict[str, MappedLineItem], idx_start: int) -> int:
    idx = idx_start
    for label, current, prior, note_ref in rows:
        canonical_key, confidence, method = map_line_item(label)
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


def map_all_items(extracted: ExtractedDocument, excel_path: str | None = None) -> dict[str, MappedLineItem]:
    """Map every line item found in the PDF's tables (and optional Excel workbook) to the canonical schema."""
    mapped: dict[str, MappedLineItem] = {}
    idx = 0

    for page in extracted.pages:
        for table in page.tables:
            idx = _map_rows(_parse_table_rows(table), mapped, idx)

    if excel_path and Path(excel_path).exists():
        workbook = extract_excel(excel_path)
        for sheet in workbook.sheets:
            table = TableData(headers=sheet.headers, rows=[list(r) for r in sheet.rows], page_num=0)
            idx = _map_rows(_parse_table_rows(table), mapped, idx)

    logger.info(
        f"Mapped {sum(1 for m in mapped.values() if m.canonical_key)} of {len(mapped)} line items"
    )
    return mapped
