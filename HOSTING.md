# Hosting on Hugging Face Spaces (free)

Free hosting for this app is constrained by one thing: the narrative LLM is a
quantized 7B model (~4.7GB) plus an OCR engine and an embedding model on top.
Most free PaaS tiers (Render, Railway, Fly.io) cap out around 512MB-1GB RAM —
not enough. **Hugging Face Spaces' free CPU tier gives 2 vCPU / 16GB RAM** and
needs no credit card. That's what this guide sets up.

Two things don't carry over unchanged from local dev:

1. `mlx-lm` only runs on Apple Silicon (Metal). HF Spaces run on Linux, so the
   app auto-switches to a `llama.cpp` backend serving a GGUF build of the same
   base model (`Qwen2.5-7B-Instruct`) — see `config.LLM_BACKEND` and
   `pipeline/generator/observation_gen.py`. Nothing to configure; the backend
   is auto-detected by platform.
2. The UI is Gradio, not Streamlit — HF's Space-creation wizard currently only
   offers **Static** (no Python), **Gradio** (free), or **Docker** (gated
   behind account verification on some accounts). Gradio is the one that's
   both free and can run this Python backend, so `app.py` targets it directly
   — same UI locally and hosted, no separate app to maintain.

## 1. Create the Space

1. Go to https://huggingface.co/new-space
2. Pick an owner/name, set **SDK: Gradio**, **hardware: CPU basic (free)**, visibility as you like.
3. This gives you a new git repo at `https://huggingface.co/spaces/<you>/<space-name>`, separate from this GitHub repo. HF auto-generates a `README.md` there with YAML frontmatter (`sdk: gradio`, `app_file: app.py`, etc.) — keep that frontmatter block, you can append this project's README content below it.

## 2. Clone it and copy the project in

```bash
git clone https://huggingface.co/spaces/<you>/<space-name>
cd <space-name>

# Copy the app in from this repo (adjust source path)
rsync -av --exclude='.git' --exclude='.venv' --exclude='context' --exclude='test' \
  --exclude='data/raw_zips' --exclude='data/uploads' \
  /path/to/audit-risk-report-generator/ ./

# Keep the HF-generated README.md's frontmatter; merge in the project README content
# below it by hand (or just leave HF's short auto-generated one as-is).
```

`packages.txt` and `requirements.txt` are already prepared at the project root
for this — `packages.txt` installs the apt-level deps PaddleOCR/pdf2image need
(`libgl1`, `poppler-utils`, etc.), and `requirements.txt` already carries
platform markers so `llama-cpp-python` (not `mlx-lm`) installs on HF's Linux boxes.

## 3. Ship a pre-built standards index (skip re-indexing on every boot)

`data/standards/` (105 PDFs, ~46MB) and `data/chroma_db/` (the embedded index,
~77MB) are gitignored in the GitHub repo (regenerated locally via
`scripts/setup_standards.py`) — but for the Space, commit the **already-built**
versions directly so the app boots instantly instead of re-running OCR/embedding
on every cold start:

```bash
git lfs install
git lfs track "*.pdf" "*.bin" "*.sqlite3" "*.pickle"
git add .gitattributes data/standards data/chroma_db
git add app.py config.py packages.txt requirements.txt pipeline models export utils scripts .env.example README.md
git commit -m "Deploy: portable llama.cpp backend + prebuilt standards index"
git push
```

(123MB total is well within HF's free git-lfs storage for a Space.)

## 4. First boot

Push triggers a build. Watch it under the Space's **Logs** tab. On first
request, `llama_cpp.Llama.from_pretrained(...)` downloads the GGUF
(`Qwen/Qwen2.5-7B-Instruct-GGUF`, `qwen2.5-7b-instruct-q4_k_m.gguf`, ~4.7GB)
into the container's HF cache and keeps it in memory for the process's
lifetime — so the *first* report after a cold start is slow (multi-minute
download + CPU inference is slower than mlx/Metal was locally, expect ~1-3 min
per observation instead of seconds), subsequent ones in the same session are fine.

No secrets/env vars are required — no API key is used anywhere in this app.

## 5. Free-tier caveats

- **Sleeps on inactivity.** Free Spaces spin down after ~48h idle (or sooner
  with low traffic) and cold-start on the next visit, which re-triggers the
  GGUF download unless it's cached in a way that survives — on the free tier
  it generally isn't guaranteed to. If cold-start latency becomes annoying,
  the fix is HF's paid "persistent storage" add-on for the Space, not a code change.
- **CPU-only.** Inference is materially slower than the Metal-accelerated
  local Mac setup. Fine for a background/batch report tool; not snappy interactive chat.
- **Public by default** unless you set the Space to private — the app has no
  auth of its own, so anyone with the URL can upload documents and run reports
  if the Space is public.
