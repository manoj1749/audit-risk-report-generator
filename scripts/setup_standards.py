"""One-time setup: unzip standards corpus, chunk, embed, and index into ChromaDB.

Idempotent — safe to run multiple times. Files already indexed (by
source_filename) are skipped; chunks are additionally upserted keyed by
source_filename + chunk_index so re-runs never duplicate data.

Run with: python scripts/setup_standards.py
"""
import sys
import time
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import chromadb
import pdfplumber
from chromadb.utils import embedding_functions
from loguru import logger
from tqdm import tqdm

import config

CHUNK_SIZE_WORDS = int(config.CHUNK_SIZE_TOKENS * config.TOKENS_TO_WORDS)
CHUNK_OVERLAP_WORDS = int(config.CHUNK_OVERLAP_TOKENS * config.TOKENS_TO_WORDS)
EMBED_BATCH_SIZE = 32


def unzip_all() -> None:
    raw_zips_dir = Path(config.RAW_ZIPS_DIR)
    standards_dir = Path(config.STANDARDS_DIR)
    zip_files = list(raw_zips_dir.glob("*.zip"))

    if not zip_files:
        logger.info(f"No ZIP files found in {raw_zips_dir}")
        return

    for zip_path in zip_files:
        logger.info(f"Unzipping {zip_path.name}...")
        try:
            with zipfile.ZipFile(zip_path, "r") as zf:
                zf.extractall(standards_dir)
        except zipfile.BadZipFile as e:
            logger.error(f"Could not unzip {zip_path.name}: {e}")


def _chunk_words(words: list[str], chunk_size: int, overlap: int) -> list[list[str]]:
    if not words:
        return []
    chunks = []
    step = max(chunk_size - overlap, 1)
    i = 0
    while i < len(words):
        chunk = words[i:i + chunk_size]
        if chunk:
            chunks.append(chunk)
        if i + chunk_size >= len(words):
            break
        i += step
    return chunks


def extract_and_chunk_pdf(pdf_path: Path) -> list[tuple[str, int, int]]:
    """Return list of (chunk_text, page_number, chunk_index)."""
    chunks: list[tuple[str, int, int]] = []
    chunk_idx = 0
    try:
        with pdfplumber.open(pdf_path) as pdf:
            for page_num, page in enumerate(pdf.pages, start=1):
                text = page.extract_text() or ""
                words = text.split()
                if not words:
                    continue
                for chunk_words in _chunk_words(words, CHUNK_SIZE_WORDS, CHUNK_OVERLAP_WORDS):
                    chunks.append((" ".join(chunk_words), page_num, chunk_idx))
                    chunk_idx += 1
    except Exception as e:
        logger.error(f"Failed to extract {pdf_path.name}: {e}")
    return chunks


def _already_indexed(collection, source_filename: str) -> bool:
    try:
        result = collection.get(where={"source_filename": source_filename}, limit=1)
        return len(result.get("ids", [])) > 0
    except Exception:
        return False


def main() -> None:
    start_time = time.time()
    unzip_all()

    pdf_files = sorted(Path(config.STANDARDS_DIR).rglob("*.pdf"))
    if not pdf_files:
        logger.warning(f"No PDF files found under {config.STANDARDS_DIR}")
        return

    logger.info(f"Loading embedding model {config.EMBEDDING_MODEL_NAME}...")
    embedding_fn = embedding_functions.SentenceTransformerEmbeddingFunction(
        model_name=config.EMBEDDING_MODEL_NAME
    )

    client = chromadb.PersistentClient(path=config.CHROMA_DB_PATH)
    collection = client.get_or_create_collection(
        config.CHROMA_COLLECTION_NAME, embedding_function=embedding_fn
    )

    # Determine which files still need indexing, and gather their chunks.
    pending_chunks: list[tuple[str, str, int, int]] = []  # (source_filename, text, page, chunk_idx)
    files_to_index = []
    for pdf_path in pdf_files:
        source_filename = pdf_path.name
        if _already_indexed(collection, source_filename):
            logger.info(f"Skipping already-indexed file: {source_filename}")
            continue
        chunks = extract_and_chunk_pdf(pdf_path)
        if not chunks:
            logger.warning(f"No text extracted from {source_filename}")
            continue
        files_to_index.append(source_filename)
        for text, page_num, chunk_idx in chunks:
            pending_chunks.append((source_filename, text, page_num, chunk_idx))

    total_chunks_indexed = 0
    for batch_start in tqdm(range(0, len(pending_chunks), EMBED_BATCH_SIZE), desc="Embedding + indexing"):
        batch = pending_chunks[batch_start: batch_start + EMBED_BATCH_SIZE]
        ids = [f"{fn}::{idx}" for fn, _text, _page, idx in batch]
        documents = [text for _fn, text, _page, _idx in batch]
        metadatas = [
            {"source_filename": fn, "page_number": page, "chunk_index": idx}
            for fn, _text, page, idx in batch
        ]
        collection.upsert(ids=ids, documents=documents, metadatas=metadatas)
        total_chunks_indexed += len(batch)

    elapsed = time.time() - start_time
    print("\n── Standards indexing summary ──")
    print(f"Files indexed:  {len(files_to_index)}")
    print(f"Total chunks:   {total_chunks_indexed}")
    print(f"Time taken:     {elapsed:.1f}s")


if __name__ == "__main__":
    main()
