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
LOCAL_LLM_GGUF_FILE = os.getenv("LOCAL_LLM_GGUF_FILE", "qwen2.5-7b-instruct-q4_k_m.gguf")
LOCAL_LLM_CTX_TOKENS = int(os.getenv("LOCAL_LLM_CTX_TOKENS", "4096"))

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
