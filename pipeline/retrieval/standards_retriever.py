"""ChromaDB retrieval of standards text relevant to a triggered flag."""
import chromadb
from chromadb.utils import embedding_functions
from loguru import logger

import config
from models.flags import AuditFlag
from models.report import RetrievedChunk

_chroma_client = None
_embedding_fn = None


def get_chroma_client():
    global _chroma_client
    if _chroma_client is None:
        _chroma_client = chromadb.PersistentClient(path=config.CHROMA_DB_PATH)
    return _chroma_client


def _get_embedding_fn():
    global _embedding_fn
    if _embedding_fn is None:
        _embedding_fn = embedding_functions.SentenceTransformerEmbeddingFunction(
            model_name=config.EMBEDDING_MODEL_NAME
        )
    return _embedding_fn


def retrieve_for_flag(flag: AuditFlag, n_results: int = 3) -> list[RetrievedChunk]:
    """Query ChromaDB with the flag's standard_query."""
    try:
        client = get_chroma_client()
        collection = client.get_collection(
            config.CHROMA_COLLECTION_NAME, embedding_function=_get_embedding_fn()
        )
    except Exception as e:
        logger.error(f"Standards collection unavailable (run scripts/setup_standards.py first): {e}")
        return []

    try:
        results = collection.query(
            query_texts=[flag.standard_query],
            n_results=n_results,
            include=["documents", "metadatas", "distances"],
        )
    except Exception as e:
        logger.error(f"ChromaDB query failed for flag {flag.flag_id}: {e}")
        return []

    chunks = []
    documents = results.get("documents") or [[]]
    metadatas = results.get("metadatas") or [[]]
    distances = results.get("distances") or [[]]
    for doc, meta, dist in zip(documents[0], metadatas[0], distances[0]):
        chunks.append(
            RetrievedChunk(
                text=doc,
                source=meta.get("source_filename", "unknown"),
                page=meta.get("page_number", 0),
                score=1 - dist,
            )
        )
    return chunks
