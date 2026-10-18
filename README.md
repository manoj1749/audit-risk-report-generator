# audit-risk-report-generator

Automated audit risk assessment for Indian company financial statements (Ind AS / IFRS).

Upload an annual report (face financial statements + numbered notes to accounts) as
a PDF, Word document, or scanned/photographed image, and optionally an Excel workbook
with the same financial data — Excel is only needed if the balance sheet and P&L
aren't already legible in the primary document. audit-risk-report-generator runs the document through
a five-layer deterministic pipeline — extraction, note segmentation, line-item
normalization, analytical flag generation, and standards-grounded narrative
generation — and produces a structured audit risk report: observations rated
High/Medium/Low, each citing specific figures from the document and the relevant
Ind AS / Companies Act / ICAI provision.

Numbers are extracted and flagged deterministically. An LLM is used only in the
final step, to turn a triggered flag + retrieved standard text + note content into
a narrative sentence — it never invents figures or standard references. That model
runs locally and free, no API key and no external service required, via one of two
interchangeable backends (`config.LLM_BACKEND`, auto-detected): [mlx-lm](https://github.com/ml-explore/mlx-lm)
on Apple Silicon, or [llama.cpp](https://github.com/abetlen/llama-cpp-python) (a GGUF
build of the same Qwen2.5-7B-Instruct model) everywhere else, e.g. on the Google Cloud
Run deployment in `deploy.sh`.

## Setup (run once)

**Zero-install quick start (macOS):** on a brand new Mac with nothing installed
(not even git), download just **`Install and Run.command`** and double-click it.
It clones this repo, then hands off to `run.sh`, which installs everything else
(Command Line Tools, Homebrew, Python, poppler, the venv, Python deps, the
standards index) and launches the app in your browser. Right-click → Open the
first time (macOS will warn it's from an unidentified developer).

**Quick start if you've already cloned the repo:** place the ZIP files from
Monali in `data/raw_zips/`, then either double-click **`Launch Audit Risk
Report Generator.command`** in Finder, or run `./run.sh` — same setup as
above, just skipping the clone step. Re-running either is fast and safe; they
skip anything already set up.

Manual setup:

```bash
git clone <repo>
cd audit-risk-report-generator
pip install -r requirements.txt

# Copy .env.example to .env (defaults are fine — no keys needed)
cp .env.example .env

# Place ZIP files from Monali in data/raw_zips/
# Then index the standards corpus (one-time, takes ~10-20 mins)
python scripts/setup_standards.py
```

The narrative-generation model (~4.7GB, either backend) downloads automatically
from Hugging Face on first use — no separate step needed. Locally this defaults
to `mlx-lm` on Apple Silicon; for hosting on Linux (no Metal available), see
`deploy.sh` for the Google Cloud Run deployment, which uses the llama.cpp/GGUF
backend instead.

## Run

```bash
python app.py
```

Upload the annual report (required — PDF, Word `.docx`, or an image such as a scanned
page) and, optionally, the financial statements Excel workbook, then click
**Run Audit Risk Analysis**.

## Test extraction on a PDF without running the full pipeline

```bash
python scripts/test_extraction.py data/annual_report.pdf
```

Prints total pages, detected company name and period, the number of notes found,
their IDs and titles, and a sample of the first three notes — useful for verifying
extraction quality before running the full pipeline.

## Project structure

### Entry points

| File | Purpose |
| --- | --- |
| `app.py` | Gradio UI and entry point. Run with `python app.py`. |
| `config.py` | All settings, paths, and env-driven constants (LLM backend, data dirs, Cloud Run tuning). |
| `run.sh` | One-shot setup + launch for macOS: installs Command Line Tools, Homebrew, Python, poppler, the venv, Python deps, and the standards index, then starts the app. Safe to re-run. |
| `Install and Run.command` | Double-click installer for a totally blank Mac — clones this repo, then hands off to `run.sh`. The one file to send someone with nothing installed. |
| `Launch Audit Risk Report Generator.command` | Double-click launcher for a repo you've already cloned — just runs `run.sh`. |

### `pipeline/` — the five-layer analysis pipeline

| Module | Layer | Purpose |
| --- | --- | --- |
| `extractor/pdf_extractor.py` | 1 | PDF/image extraction: pdfplumber for typed PDFs, PaddleOCR fallback for scanned/photographed pages. |
| `extractor/docx_extractor.py` | 1 | Word extraction: mammoth for raw text, python-docx for structured tables. |
| `extractor/excel_extractor.py` | 1 | Excel extraction via openpyxl, preserving merged cells and comments. |
| `segmenter/note_parser.py` | 2 | Detects note boundaries in extracted text and assembles note sections. |
| `segmenter/xref_graph.py` | 2 | Cross-reference graph between notes (networkx). |
| `normalizer/schema.py` | 3 | Canonical financial line-item schema every extracted item maps onto. |
| `normalizer/line_item_mapper.py` | 3 | Three-stage mapping: exact match → fuzzy match → embedding match. |
| `normalizer/table_extractor.py` | 3 | Structured parsers for specific note tables the analytics layer needs. |
| `analytics/horizontal.py` | 4 | Year-over-year absolute and percentage movements per line item. |
| `analytics/ratios.py` | 4 | Ratio computation from mapped line items (safe-divided). |
| `analytics/flags.py` | 4 | Threshold-based analytical flag generation. |
| `analytics/consistency.py` | 4 | Cross-note consistency checks, run after all note tables are parsed. |
| `retrieval/standards_retriever.py` | 5a | ChromaDB retrieval of standards text relevant to a triggered flag. |
| `generator/prompt_builder.py` | 5b | Builds structured prompts from flag data + retrieved standard text. |
| `generator/templates.py` | 5b | Deterministic, template-based narrative text (default path). |
| `generator/observation_gen.py` | 5b | Turns a flag into a parsed observation; owns the local LLM call/backend switch. |

### Everything else

| Path | Purpose |
| --- | --- |
| `models/financial.py`, `flags.py`, `report.py` | Pydantic models — the typed contract passed between pipeline layers. |
| `export/docx_exporter.py` | Renders the final report as a formatted DOCX working paper. |
| `export/charts.py` | Chart generation for the report: risk distribution, key movements, ratios. |
| `export/report_storage.py` | Persists completed reports to GCS (Cloud Run only) so a dropped connection doesn't lose a finished run. |
| `utils/text_utils.py` | Shared text/numeric parsing (e.g. Indian number formats, label cleaning). |
| `utils/timezone.py` | IST timestamp helper. |
| `scripts/setup_standards.py` | One-time: chunk + embed + index the standards corpus from `data/raw_zips/`. |
| `scripts/test_extraction.py` | Dev script — test Layers 1–2 on a single PDF without running the full pipeline. |
| `scripts/fix_macos_mac_ver.py` | Auto-fix for a macOS bug where `platform.mac_ver()` returns empty, which breaks pip. Run automatically by `run.sh`; no-op where the bug doesn't exist. |
| `scripts/fix_macos_pyexpat.py` | Auto-fix for a Homebrew Python build where `pyexpat` is linked against a libexpat symbol newer than the OS has, which also breaks pip. Same auto/no-op behavior. |
| `scripts/bootstrap_pip.py` | Fallback used by `run.sh` if normal venv creation fails on pip's own self-install bug — installs pip directly from a PyPI wheel instead. |
| `docs/full_technical_reference.docx`, `how_it_works.docx` | Standalone reference documents (not needed to run the app). |
| `requirements.txt` | Python dependencies. |
| `.env.example` | Template for `.env` — copy it; defaults work with no keys needed. |
| `Dockerfile` | CPU-only Cloud Run image (the one actually deployed). |
| `Dockerfile.gpu`, `cloudbuild.gpu.yaml` | Experimental GPU (CUDA/RTX Pro 6000) build for Cloud Run — kept for a future GPU quota approval; not currently deployed. |
| `deploy.sh` | Builds and deploys the CPU image to Google Cloud Run. Not run automatically — review and run by hand. |

### `data/` (gitignored except `.gitkeep` placeholders — generated or downloaded locally)

| Path | Purpose |
| --- | --- |
| `data/raw_zips/` | Place the standards ZIP files here before running `scripts/setup_standards.py`. |
| `data/standards/` | Unzipped standards PDFs. |
| `data/chroma_db/` | Persisted ChromaDB standards index. |
| `data/uploads/` | Temp storage for uploaded files during a run. |
| `data/extraction_cache/` | Per-file OCR/extraction cache so re-running the same document skips the slow extraction step. |

## What this does not do

- Does not use an LLM to extract or interpret numbers — numbers are extracted deterministically.
- Does not hallucinate figures — every number in an observation comes from extracted document data.
- Does not hallucinate standard references — citations come from retrieved chunks of the actual standards corpus.
- The disclaimer on every report is real: this is a preliminary desk-based analytical review, not a substitute for fieldwork.
