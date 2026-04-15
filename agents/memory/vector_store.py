"""
ChromaDB 向量存储层 — L1 HNSW 图
调优 HNSW 参数保证拓扑稳定，含模型版本检查。
"""

import logging
import os
from dataclasses import dataclass
from typing import Optional

from agents.memory.config import CHROMA_PERSIST_DIR, HNSW_CONFIG, EMBEDDING_CONFIG

logger = logging.getLogger("memory.vector_store")


class EmbeddingVersionMismatch(Exception):
    pass


@dataclass
class VectorHit:
    id: str
    content: str
    metadata: dict
    distance: float
    cosine_sim: float


class VectorStore:
    def __init__(self, collection_name: str):
        self.collection_name = collection_name
        self._client = None
        self._collection = None

    def _ensure_client(self):
        if self._client is not None:
            return
        try:
            import chromadb
            os.makedirs(CHROMA_PERSIST_DIR, exist_ok=True)
            self._client = chromadb.PersistentClient(path=CHROMA_PERSIST_DIR)
            metadata = {**HNSW_CONFIG, "embedding_model": EMBEDDING_CONFIG["model"], "embedding_dim": EMBEDDING_CONFIG["dim"]}
            self._collection = self._client.get_or_create_collection(
                name=self.collection_name,
                metadata=metadata,
            )
            stored_model = self._collection.metadata.get("embedding_model")
            if stored_model and stored_model != EMBEDDING_CONFIG["model"]:
                raise EmbeddingVersionMismatch(
                    f"Collection uses {stored_model}, current config is {EMBEDDING_CONFIG['model']}"
                )
            logger.info(f"VectorStore ready: {self.collection_name} ({self._collection.count()} docs)")
        except EmbeddingVersionMismatch:
            raise
        except Exception as e:
            logger.error(f"ChromaDB init failed: {e}")
            raise

    def add(self, doc_id: str, embedding: list[float], content: str, metadata: dict):
        self._ensure_client()
        self._collection.upsert(
            ids=[doc_id],
            embeddings=[embedding],
            documents=[content],
            metadatas=[metadata],
        )

    def query(self, embedding: list[float], n: int = 15) -> list[VectorHit]:
        self._ensure_client()
        results = self._collection.query(
            query_embeddings=[embedding],
            n_results=min(n, max(self._collection.count(), 1)),
            include=["documents", "metadatas", "distances"],
        )
        hits = []
        if not results or not results["ids"] or not results["ids"][0]:
            return hits
        for i, doc_id in enumerate(results["ids"][0]):
            distance = results["distances"][0][i]
            cosine_sim = 1.0 - distance
            hits.append(VectorHit(
                id=doc_id,
                content=results["documents"][0][i] if results["documents"] else "",
                metadata=results["metadatas"][0][i] if results["metadatas"] else {},
                distance=distance,
                cosine_sim=max(cosine_sim, 0.0),
            ))
        return hits

    def delete(self, doc_ids: list[str]):
        self._ensure_client()
        self._collection.delete(ids=doc_ids)

    def count(self) -> int:
        self._ensure_client()
        return self._collection.count()

    def get(self, doc_id: str) -> Optional[dict]:
        self._ensure_client()
        result = self._collection.get(ids=[doc_id], include=["documents", "metadatas"])
        if result and result["ids"]:
            return {
                "id": result["ids"][0],
                "content": result["documents"][0] if result["documents"] else "",
                "metadata": result["metadatas"][0] if result["metadatas"] else {},
            }
        return None
