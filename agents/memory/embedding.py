"""
混合 Embedding 客户端
策略: 本地 Ollama bge-m3 (优先) → 云端 API (SiliconFlow/OpenAI/DashScope) → 磁盘缓存兜底
支持单条/批量 embedding，全链路容错降级。
"""

import hashlib
import json
import logging
import os
import time
from typing import Optional

import httpx

from agents.memory.config import (
    OLLAMA_BASE_URL,
    EMBEDDING_CONFIG,
    CLOUD_EMBEDDING_CONFIG,
    EMBED_CACHE_DIR,
)

logger = logging.getLogger("memory.embedding")


class EmbedCache:
    """基于磁盘的 embedding 缓存，避免重复调用云端 API"""

    def __init__(self, cache_dir: str = EMBED_CACHE_DIR, ttl_hours: int = 24):
        self._dir = cache_dir
        self._ttl = ttl_hours * 3600
        os.makedirs(self._dir, exist_ok=True)
        self._mem: dict[str, list[float]] = {}

    def _key(self, text: str, model: str) -> str:
        return hashlib.sha256(f"{model}:{text}".encode()).hexdigest()[:24]

    def get(self, text: str, model: str) -> Optional[list[float]]:
        key = self._key(text, model)
        if key in self._mem:
            return self._mem[key]
        path = os.path.join(self._dir, f"{key}.json")
        if not os.path.exists(path):
            return None
        try:
            if time.time() - os.path.getmtime(path) > self._ttl:
                os.remove(path)
                return None
            with open(path, "r") as f:
                vec = json.load(f)
            self._mem[key] = vec
            return vec
        except Exception:
            return None

    def put(self, text: str, model: str, embedding: list[float]):
        key = self._key(text, model)
        self._mem[key] = embedding
        path = os.path.join(self._dir, f"{key}.json")
        try:
            with open(path, "w") as f:
                json.dump(embedding, f)
        except Exception:
            pass

    def prune(self, max_entries: int = 10000):
        """清理过期和超额缓存"""
        try:
            files = sorted(
                [os.path.join(self._dir, f) for f in os.listdir(self._dir) if f.endswith(".json")],
                key=os.path.getmtime,
            )
            for f in files:
                if time.time() - os.path.getmtime(f) > self._ttl:
                    os.remove(f)
            files = [f for f in files if os.path.exists(f)]
            if len(files) > max_entries:
                for f in files[: len(files) - max_entries]:
                    os.remove(f)
        except Exception:
            pass


class CloudEmbeddingClient:
    """云端 Embedding API 客户端（SiliconFlow / OpenAI / DashScope）"""

    def __init__(self, provider: str = ""):
        cfg = CLOUD_EMBEDDING_CONFIG
        self._provider = provider or cfg.get("provider", "siliconflow")
        self._pcfg = cfg.get("providers", {}).get(self._provider, {})
        self._api_key = self._pcfg.get("api_key", "")
        self._base_url = self._pcfg.get("base_url", "")
        self._model = self._pcfg.get("model", "")
        self._dim = self._pcfg.get("dim", 1024)

    @property
    def configured(self) -> bool:
        return bool(self._api_key and self._base_url)

    @property
    def model_name(self) -> str:
        return f"{self._provider}/{self._model}"

    @property
    def dim(self) -> int:
        return self._dim

    async def embed_batch(self, texts: list[str]) -> Optional[list[list[float]]]:
        if not self.configured:
            return None
        try:
            transport = httpx.AsyncHTTPTransport(proxy=None)
            async with httpx.AsyncClient(timeout=30, transport=transport) as client:
                resp = await client.post(
                    self._base_url,
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "Content-Type": "application/json",
                    },
                    json={"model": self._model, "input": texts},
                )
                resp.raise_for_status()
                data = resp.json()
                embeddings = [item["embedding"] for item in data.get("data", [])]
                if embeddings and len(embeddings) == len(texts):
                    return embeddings
                return None
        except Exception as e:
            logger.warning(f"Cloud embed ({self._provider}) failed: {e}")
            return None


class EmbeddingClient:
    """
    混合 Embedding 客户端
    调用链: 磁盘缓存 → 本地 Ollama → 云端 API → 返回 None (降级)
    """

    def __init__(self, model: str = "", base_url: str = ""):
        self.model = model or EMBEDDING_CONFIG["model"]
        self.base_url = base_url or OLLAMA_BASE_URL
        self._url = f"{self.base_url}/api/embed"
        self._local_available: Optional[bool] = None

        self._cache = EmbedCache(
            ttl_hours=CLOUD_EMBEDDING_CONFIG.get("cache_ttl_hours", 24),
        )

        self._cloud: Optional[CloudEmbeddingClient] = None
        if CLOUD_EMBEDDING_CONFIG.get("enabled"):
            self._cloud = CloudEmbeddingClient()
            if self._cloud.configured:
                logger.info(f"Cloud embedding fallback: {self._cloud.model_name}")
            else:
                logger.info("Cloud embedding enabled but no API key, fallback disabled")
                self._cloud = None

        self._stats = {"local_ok": 0, "local_fail": 0, "cloud_ok": 0, "cloud_fail": 0, "cache_hit": 0}

    async def embed(self, text: str) -> Optional[list[float]]:
        result = await self.embed_batch([text])
        return result[0] if result else None

    async def embed_batch(self, texts: list[str]) -> Optional[list[list[float]]]:
        results: list[Optional[list[float]]] = [None] * len(texts)
        uncached_indices: list[int] = []

        for i, text in enumerate(texts):
            cached = self._cache.get(text, self.model)
            if cached:
                results[i] = cached
                self._stats["cache_hit"] += 1
            else:
                uncached_indices.append(i)

        if not uncached_indices:
            return results

        uncached_texts = [texts[i] for i in uncached_indices]
        embeddings = await self._local_embed(uncached_texts)

        if embeddings is None and self._cloud:
            embeddings = await self._cloud_embed(uncached_texts)

        if embeddings and len(embeddings) == len(uncached_texts):
            for idx, emb in zip(uncached_indices, embeddings):
                results[idx] = emb
                self._cache.put(texts[idx], self.model, emb)
            return results

        if any(r is not None for r in results):
            return results

        return None

    async def _local_embed(self, texts: list[str]) -> Optional[list[list[float]]]:
        try:
            transport = httpx.AsyncHTTPTransport(proxy=None)
            async with httpx.AsyncClient(timeout=30, transport=transport) as client:
                resp = await client.post(
                    self._url,
                    json={"model": self.model, "input": texts},
                )
                resp.raise_for_status()
                data = resp.json()
                embeddings = data.get("embeddings")
                if embeddings:
                    self._local_available = True
                    self._stats["local_ok"] += 1
                    return embeddings
                logger.warning("Ollama returned empty embeddings")
                return None
        except httpx.ConnectError:
            if self._local_available is not False:
                logger.warning(f"Ollama unreachable at {self.base_url}, trying cloud fallback")
                self._local_available = False
            self._stats["local_fail"] += 1
            return None
        except Exception as e:
            logger.warning(f"Local embedding failed: {e}")
            self._stats["local_fail"] += 1
            return None

    async def _cloud_embed(self, texts: list[str]) -> Optional[list[list[float]]]:
        if not self._cloud:
            return None
        result = await self._cloud.embed_batch(texts)
        if result:
            self._stats["cloud_ok"] += 1
            logger.info(f"Cloud embedding ({self._cloud.model_name}) used for {len(texts)} texts")
        else:
            self._stats["cloud_fail"] += 1
        return result

    @property
    def available(self) -> bool:
        return self._local_available is not False or (self._cloud is not None and self._cloud.configured)

    @property
    def dim(self) -> int:
        return EMBEDDING_CONFIG["dim"]

    @property
    def stats(self) -> dict:
        return dict(self._stats)

    def get_status(self) -> dict:
        return {
            "local_model": self.model,
            "local_available": self._local_available,
            "cloud_provider": self._cloud.model_name if self._cloud else "none",
            "cloud_configured": self._cloud.configured if self._cloud else False,
            "stats": self.stats,
        }
