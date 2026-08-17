"""Settings, paths, and constants for audit-risk-report-generator. All paths are relative to project root."""
import os
import platform
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parent

CHROMA_DB_PATH = str(PROJECT_ROOT / os.getenv("CHROMA_DB_PATH", "data/chroma_db"))
STANDARDS_DIR = str(PROJECT_ROOT / os.getenv("STANDARDS_DIR", "data/standards"))
UPLOADS_DIR = str(PROJECT_ROOT / os.getenv("UPLOADS_DIR", "data/uploads"))
RAW_ZIPS_DIR = str(PROJECT_ROOT / "data/raw_zips")
UNMAPPED_ITEMS_LOG = str(PROJECT_ROOT / "data/unmapped_items.log")

CHROMA_COLLECTION_NAME = "audit_standards"
EMBEDDING_MODEL_NAME = "BAAI/bge-m3"


def _default_llm_backend() -> str:
    """mlx only runs on Apple Silicon; everywhere else (incl. Hugging Face Spaces'
    Linux CPU boxes) falls back to the portable llama.cpp/GGUF backend."""
    return "mlx" if platform.system() == "Darwin" and platform.machine() == "arm64" else "llama_cpp"


# Local, free, open-weight narrative generation (Layer 5 only) — runs in-process,
# no API key, no external service. Two interchangeable backends behind the same
# ObservationResult contract; pipeline/generator/observation_gen.py dispatches on this.
LLM_BACKEND = os.getenv("LLM_BACKEND", _default_llm_backend())  # "mlx" | "llama_cpp"

# mlx backend — Apple Silicon only (fast, Metal-accelerated).
LOCAL_LLM_MODEL = os.getenv("LOCAL_LLM_MODEL", "mlx-community/Qwen2.5-7B-Instruct-4bit")

# llama.cpp backend — portable CPU inference, used for non-Mac hosting (e.g. HF Spaces).
# Same base model (Qwen2.5-7B-Instruct), GGUF quantization instead of mlx's.
LOCAL_LLM_GGUF_REPO = os.getenv("LOCAL_LLM_GGUF_REPO", "Qwen/Qwen2.5-7B-Instruct-GGUF")
# Upstream re-uploaded this quant as two shards. from_pretrained() only
# auto-downloads the exact `filename` given — the sibling shard(s) must be
# listed explicitly via additional_files, or llama.cpp fails to load the
# (incomplete) model directory.
LOCAL_LLM_GGUF_FILE = os.getenv(
    "LOCAL_LLM_GGUF_FILE", "qwen2.5-7b-instruct-q4_k_m-00001-of-00002.gguf"
)
LOCAL_LLM_GGUF_ADDITIONAL_FILES = [
    f for f in os.getenv(
        "LOCAL_LLM_GGUF_ADDITIONAL_FILES", "qwen2.5-7b-instruct-q4_k_m-00002-of-00002.gguf"
    ).split(",") if f
]
# If set and the file exists, the model loads straight from this local path —
# no Hugging Face Hub API call at all (see observation_gen.py — from_pretrained()
# hits the Hub's rate-limited API on every call even when the file's already
# local). Set by the Dockerfile to the fixed path it copies the baked-in GGUF
# to; empty/unset everywhere else (local dev, HF Spaces) so from_pretrained()
# still handles the first-run download there.
LOCAL_LLM_MODEL_PATH = os.getenv("LOCAL_LLM_MODEL_PATH", "")
LOCAL_LLM_CTX_TOKENS = int(os.getenv("LOCAL_LLM_CTX_TOKENS", "4096"))
# os.cpu_count() can over-report on some container platforms (reads the host's
# full core count rather than what's actually allocated to this instance),
# causing llama.cpp to oversubscribe threads and slow generation down instead
# of speeding it up. Override via env var to pin this to the real allocation
# (e.g. match Cloud Run's --cpu flag); defaults to os.cpu_count() for local use.
LOCAL_LLM_N_THREADS = int(os.getenv("LOCAL_LLM_N_THREADS", str(os.cpu_count() or 4)))
# 0 = CPU-only (default — matches every existing llama.cpp deployment target,
# e.g. HOSTING.md's Hugging Face Spaces free CPU tier, which has no GPU).
# -1 = offload every layer to GPU. Only set to -1 where a GPU is actually
# attached (e.g. Cloud Run's --gpu=1, via LOCAL_LLM_N_GPU_LAYERS in deploy.sh).
LOCAL_LLM_N_GPU_LAYERS = int(os.getenv("LOCAL_LLM_N_GPU_LAYERS", "0"))
# CPU-only parallel generation: 1 (default) preserves the original single-
# instance, fully-sequential behavior everywhere (local Mac/mlx dev, HF
# Spaces) — zero behavior change unless explicitly overridden. >1 spins up
# that many independent Llama instances (llama_cpp backend only; mlx is
# unaffected) and processes that many flags concurrently, each instance
# getting LOCAL_LLM_N_THREADS // LOCAL_LLM_N_WORKERS threads so the total
# never exceeds the real CPU allocation. See observation_gen.py.
LOCAL_LLM_N_WORKERS = int(os.getenv("LOCAL_LLM_N_WORKERS", "1"))
# Prompt-lookup speculative decoding (llama_cpp backend only) — see
# observation_gen.py's _new_draft_model(). Defaults OFF: tried and confirmed
# broken on this deployment's llama-cpp-python (0.3.x) — every
# create_chat_completion call crashes with "could not broadcast input array
# from shape (77856768,) into shape (0,)" (a logits-array shape mismatch
# from draft_model forcing logits_all=True), reproduced twice independently
# (with and without a grammar constraint, so it's draft_model itself, not a
# grammar interaction — see github.com/abetlen/llama-cpp-python/issues/1770
# for the same underlying pattern from another user). Left as an opt-in flag
# rather than deleted in case a future llama-cpp-python release fixes it.
LOCAL_LLM_SPECULATIVE_DECODING = os.getenv("LOCAL_LLM_SPECULATIVE_DECODING", "0") == "1"

# Template-based generation (see pipeline/generator/templates.py):
# observation/recommendation/standard_reference are built deterministically
# from the flag's evidence instead of asking the model to write them, for
# any flag_id with a registered template (currently all ~21 flag types in
# flags.py/consistency.py). Defaults on — this is now the primary path, not
# an experiment; the old full-generation call remains as a fallback for any
# flag_id without a template and is used for everything when this is off.
LOCAL_LLM_USE_TEMPLATES = os.getenv("LOCAL_LLM_USE_TEMPLATES", "1") == "1"
# Only relevant when LOCAL_LLM_USE_TEMPLATES is on: whether to make the one
# remaining model call per flag (checking whether the note text explains
# the templated fact, adding one sentence if not — see
# observation_gen.py's _generate_addendum). Off entirely skips loading the
# model at all for the templated path, for maximum speed with zero
# LLM-derived qualitative commentary; on keeps that one piece of the old
# narrative quality.
LOCAL_LLM_NARRATIVE_ADDENDUM = os.getenv("LOCAL_LLM_NARRATIVE_ADDENDUM", "1") == "1"

LOCAL_LLM_MAX_TOKENS = int(os.getenv("LOCAL_LLM_MAX_TOKENS", "800"))
LOCAL_LLM_TEMPERATURE = float(os.getenv("LOCAL_LLM_TEMPERATURE", "0.0"))

# Chunking (word-count approximation, 1 token ~= 0.75 words)
CHUNK_SIZE_TOKENS = 500
CHUNK_OVERLAP_TOKENS = 100
TOKENS_TO_WORDS = 0.75

# Line item mapping thresholds
FUZZY_MATCH_THRESHOLD = 85
EMBEDDING_MATCH_THRESHOLD = 0.82
FUZZY_LOWER_BOUND = 60

for _dir in (CHROMA_DB_PATH, STANDARDS_DIR, UPLOADS_DIR, RAW_ZIPS_DIR):
    Path(_dir).mkdir(parents=True, exist_ok=True)
