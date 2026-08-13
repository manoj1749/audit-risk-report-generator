"""Settings, paths, and constants for audit-risk-report-generator. All paths are relative to project root."""
import os
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

# Local, free, open-weight narrative generation (Layer 5 only) — runs in-process
# via Apple's mlx-lm, no API key, no external service. Requires Apple Silicon.
LOCAL_LLM_MODEL = os.getenv("LOCAL_LLM_MODEL", "mlx-community/Qwen2.5-7B-Instruct-4bit")
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
