# Reverted to CPU-only: Cloud Run's GPU quota for this project is hard-capped
# at 0 by Google (self-service override attempts get
# COMMON_QUOTA_CONSUMER_OVERRIDE_TOO_HIGH, max=0) — GPU allocation requires a
# manual quota-increase request/approval through the Cloud Console that can't
# be done via CLI and won't complete same-day on a fresh free-trial project.
# See deploy.sh for the request-a-quota-increase note if picking GPU back up.
FROM python:3.11-slim

# Same apt deps as HOSTING.md's Hugging Face path (packages.txt): libgl1/libglib2.0-0
# for PaddleOCR, poppler-utils for pdf2image. unzip+curl are for the standards-index
# fetch below.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 libglib2.0-0 libgomp1 poppler-utils unzip curl \
    build-essential cmake \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    LLM_BACKEND=llama_cpp \
    GRADIO_SERVER_NAME=0.0.0.0

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    # sentence-transformers pulls in torch; installing the CPU-only wheel
    # first stops pip from defaulting to the CUDA build (which drags in
    # several GB of unused nvidia-*/triton packages — this is a CPU-only
    # Cloud Run instance, no GPU present).
    && pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu \
    && pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir gdown

COPY . .

# Standards corpus (ICAI/MCA-licensed, kept out of git — see .gitignore) is
# fetched the same way run.sh does it locally: a prebuilt data/standards +
# data/chroma_db archive from Drive. Baked in at build time so a cold Cloud
# Run instance never re-runs the ~10-20min chunk/embed/index step.
ARG STANDARDS_INDEX_DRIVE_ID=1hdLopPijZDlzZNNcFlFcA3cDqTVrrwe6
RUN gdown "https://drive.google.com/uc?id=${STANDARDS_INDEX_DRIVE_ID}" -O /tmp/standards.zip \
    && unzip -q -o /tmp/standards.zip -d /app \
    && rm -f /tmp/standards.zip \
    && test -f /app/data/chroma_db/chroma.sqlite3

# Pre-download the local LLM (~4.7GB GGUF, both split shards) straight to a
# fixed path via huggingface_hub (not `Llama.from_pretrained`, which hits the
# HF Hub API on every single invocation even when the file's already local —
# that API has a hard 500-req/5min per-IP rate limit that repeated
# deploys/restarts from one egress IP already hit once, breaking generation
# outright with a 429). LOCAL_LLM_MODEL_PATH (below) points runtime straight
# at this file — see observation_gen.py.
RUN mkdir -p /app/models && python -c "\
from huggingface_hub import hf_hub_download; \
hf_hub_download(repo_id='Qwen/Qwen2.5-7B-Instruct-GGUF', filename='qwen2.5-7b-instruct-q4_k_m-00001-of-00002.gguf', local_dir='/app/models'); \
hf_hub_download(repo_id='Qwen/Qwen2.5-7B-Instruct-GGUF', filename='qwen2.5-7b-instruct-q4_k_m-00002-of-00002.gguf', local_dir='/app/models')"
RUN python -c "\
from sentence_transformers import SentenceTransformer; \
SentenceTransformer('BAAI/bge-m3')"
ENV LOCAL_LLM_MODEL_PATH=/app/models/qwen2.5-7b-instruct-q4_k_m-00001-of-00002.gguf

# Cloud Run injects $PORT (default 8080) and expects the server bound to it on
# 0.0.0.0 — app.py reads $PORT itself, this EXPOSE is documentation only.
EXPOSE 8080

CMD ["python", "app.py"]
