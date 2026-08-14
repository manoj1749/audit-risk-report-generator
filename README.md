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
build of the same Qwen2.5-7B-Instruct model) everywhere else — see
[`HOSTING.md`](HOSTING.md) for deploying the llama.cpp path to a free Hugging Face Space.

## Setup (run once)

**Quick start (macOS):** after cloning, place the ZIP files from Monali in
`data/raw_zips/`, then either double-click **`Launch Audit Risk Report
Generator.command`** in Finder, or run `./run.sh` — it checks/installs
everything (interpreter, poppler, venv, Python deps, standards index),
launches the app, and opens it in your browser. Re-running it is fast and
safe; it skips anything already set up.

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
[`HOSTING.md`](HOSTING.md) for the free Hugging Face Spaces deployment, which
uses the llama.cpp/GGUF backend instead.

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

```
audit-risk-report-generator/
├── app.py                          # Gradio entry point
├── config.py                       # Settings, paths, constants
├── pipeline/
│   ├── extractor/                  # Layer 1: PDF / Excel / DOCX extraction
│   ├── segmenter/                  # Layer 2: note boundaries + cross-reference graph
│   ├── normalizer/                 # Layer 3: canonical schema + line-item mapping
│   ├── analytics/                  # Layer 4: movements, ratios, flags, consistency checks
│   ├── retrieval/                  # Layer 5a: ChromaDB standards retrieval
│   └── generator/                  # Layer 5b: prompt building + local mlx-lm generation
├── models/                         # Pydantic models — the contract between layers
├── scripts/
│   ├── setup_standards.py          # One-time: chunk + embed + index standards corpus
│   └── test_extraction.py          # Dev script: test Layers 1-2 on a PDF
├── export/
│   └── docx_exporter.py            # Export report as formatted DOCX
├── utils/
│   └── text_utils.py               # parse_indian_number and label-cleaning utilities
└── data/
    ├── raw_zips/                   # Place standards ZIP files here before setup
    ├── standards/                  # Unzipped standards PDFs
    ├── chroma_db/                  # Persisted ChromaDB index
    └── uploads/                    # Temp storage for uploaded files
```

## What this does not do

- Does not use an LLM to extract or interpret numbers — numbers are extracted deterministically.
- Does not hallucinate figures — every number in an observation comes from extracted document data.
- Does not hallucinate standard references — citations come from retrieved chunks of the actual standards corpus.
- The disclaimer on every report is real: this is a preliminary desk-based analytical review, not a substitute for fieldwork.
