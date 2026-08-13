"""Dev script: test Layers 1-2 extraction on a PDF without running the full pipeline.

Usage: python scripts/test_extraction.py path/to/annual_report.pdf
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.extractor.pdf_extractor import extract_pdf
from pipeline.segmenter.note_parser import parse_notes


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python scripts/test_extraction.py <path/to/annual_report.pdf>")
        sys.exit(1)

    pdf_path = sys.argv[1]
    if not Path(pdf_path).exists():
        print(f"File not found: {pdf_path}")
        sys.exit(1)

    print(f"Extracting {pdf_path}...")
    extracted = extract_pdf(pdf_path)

    print("\n── Extraction summary ──")
    print(f"Total pages:      {extracted.total_pages}")
    print(f"Extraction method: {extracted.extraction_method}")
    print(f"Company name:     {extracted.company_name or '(not detected)'}")
    print(f"Period:           {extracted.period or '(not detected)'}")

    notes = parse_notes(extracted)
    print(f"\n── Notes found: {len(notes)} ──")
    for note_id, note in sorted(notes.items()):
        print(f"  {note.full_id}: {note.title[:80]}")

    print("\n── Sample of first 3 notes ──")
    for note in list(notes.values())[:3]:
        print(f"\n=== {note.full_id}: {note.title[:80]} (pages {note.page_start}-{note.page_end}) ===")
        print(note.raw_text[:500])
        print("...")


if __name__ == "__main__":
    main()
