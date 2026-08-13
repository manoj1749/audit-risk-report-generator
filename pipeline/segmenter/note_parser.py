"""Detect note boundaries within an extracted document's text and assemble NoteSection objects."""
import re

from loguru import logger

from models.financial import ExtractedDocument, NoteSection, TableData

NOTE_HEADER_PATTERN = re.compile(
    r"^(?:Note|NOTE)\s+(\d+)\s*(?:[\(\[]\s*([a-zA-Z])\s*[\)\]])?\s*[:\.]?\s*(.+)?$",
    re.MULTILINE,
)

TRANSITION_PATTERN = re.compile(
    r"Notes\s+to\s+(?:\S+\s+){0,5}?(?:Financial\s+Statements?|Accounts)",
    re.IGNORECASE,
)


def _build_page_offsets(pages) -> list[tuple[int, int, int]]:
    """Return list of (page_num, start_offset, end_offset) into the joined full_text."""
    offsets = []
    cursor = 0
    for page in pages:
        start = cursor
        end = start + len(page.raw_text)
        offsets.append((page.page_num, start, end))
        cursor = end + 1  # +1 for the '\n' join separator
    return offsets


def _offset_to_page(offset: int, page_offsets: list[tuple[int, int, int]]) -> int:
    for page_num, start, end in page_offsets:
        if start <= offset <= end:
            return page_num
    return page_offsets[-1][0] if page_offsets else 1


def _tables_for_page_range(pages, page_start: int, page_end: int) -> list[TableData]:
    tables: list[TableData] = []
    for page in pages:
        if page_start <= page.page_num <= page_end:
            tables.extend(page.tables)
    return tables


def parse_notes(extracted: ExtractedDocument) -> dict[str, NoteSection]:
    """Parse note boundaries from the document's full text.

    Notes appearing before the "Notes to Financial Statements/Accounts"
    transition phrase (e.g. note references in face-statement headers) are
    ignored.
    """
    text = extracted.full_text
    transition_match = TRANSITION_PATTERN.search(text)
    search_start = transition_match.end() if transition_match else 0

    if transition_match is None:
        logger.warning("Could not find 'Notes to Financial Statements/Accounts' transition; "
                        "scanning entire document for note headers")

    matches = list(NOTE_HEADER_PATTERN.finditer(text, pos=search_start))
    if not matches:
        logger.warning("No note headers detected in document")
        return {}

    page_offsets = _build_page_offsets(extracted.pages)
    notes: dict[str, NoteSection] = {}

    for i, match in enumerate(matches):
        number = match.group(1)
        letter = (match.group(2) or "").lower()
        title = (match.group(3) or "").strip()
        note_id = f"{number}{letter}"
        full_id = f"Note {number}" + (f"({letter})" if letter else "")

        block_start = match.start()
        block_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        raw_text = text[block_start:block_end].strip()

        page_start = _offset_to_page(block_start, page_offsets)
        page_end = _offset_to_page(max(block_end - 1, block_start), page_offsets)

        if note_id in notes:
            # Note continues (e.g. split across pages producing a second header match);
            # merge rather than overwrite.
            existing = notes[note_id]
            existing.raw_text += "\n" + raw_text
            existing.page_end = max(existing.page_end, page_end)
            existing.tables.extend(_tables_for_page_range(extracted.pages, page_start, page_end))
            continue

        notes[note_id] = NoteSection(
            note_id=note_id,
            full_id=full_id,
            title=title or full_id,
            raw_text=raw_text,
            tables=_tables_for_page_range(extracted.pages, page_start, page_end),
            page_start=page_start,
            page_end=page_end,
        )

    return notes
