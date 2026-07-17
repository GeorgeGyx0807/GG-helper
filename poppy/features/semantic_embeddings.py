"""Lazy, resource-bounded multilingual embeddings for local hybrid retrieval."""

from array import array
import base64
import json
import os
from pathlib import Path
import subprocess
import sys
from threading import RLock


DEFAULT_MODEL_ID = "intfloat/multilingual-e5-small"
DEFAULT_MODEL_REPOSITORY = "Xenova/multilingual-e5-small"
DEFAULT_MODEL_FILE = "onnx/model_quantized.onnx"


class SemanticEmbeddingService:
    """Use quantized multilingual ONNX embeddings with native fallback.

    The model is registered with FastEmbed but not loaded until the background
    indexer needs it. Query and passage prefixes follow the E5 contract. If the
    model cannot be downloaded or initialized, Poppy keeps FTS5 and the legacy
    macOS helper available instead of making the knowledge base unusable.
    """

    def __init__(self, helper=None, cache_dir=None, model_id=None, mode=None, threads=None):
        self.helper = Path(helper) if helper else self._discover_helper()
        self.cache_dir = Path(cache_dir).expanduser().resolve() if cache_dir else None
        self.model_id = str(model_id or os.environ.get("POPPY_EMBEDDING_MODEL") or DEFAULT_MODEL_ID)
        self.mode = str(mode or os.environ.get("POPPY_EMBEDDING_MODE") or "balanced").strip().lower()
        if self.mode not in {"off", "native", "balanced", "quality"}:
            self.mode = "balanced"
        self.threads = max(1, min(int(threads or os.environ.get("POPPY_EMBEDDING_THREADS") or 4), 6))
        self._model = None
        self._model_failed = False
        self._model_error = ""
        self._lock = RLock()

    @staticmethod
    def _discover_helper():
        configured = os.environ.get("POPPY_SEMANTIC_HELPER", "").strip()
        candidates = [Path(configured)] if configured else []
        bundle_root = getattr(sys, "_MEIPASS", "")
        if bundle_root:
            candidates.append(Path(bundle_root) / "poppy-semantic")
        for candidate in candidates:
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return candidate
        return None

    @property
    def available(self):
        if self.mode == "off":
            return False
        if self.mode == "native":
            return self.helper is not None
        if self._model is not None:
            return True
        if not self._model_failed:
            try:
                import fastembed  # noqa: F401
                return True
            except ImportError:
                pass
        return self.helper is not None

    @property
    def backend(self):
        if self._model is not None:
            return "fastembed"
        if self.mode == "native" or self._model_failed:
            return "macos-native" if self.helper is not None else "fts-only"
        return "fastembed-pending" if self.available else "fts-only"

    @property
    def last_error(self):
        return self._model_error

    def set_mode(self, mode):
        resolved = str(mode or "balanced").strip().lower()
        if resolved not in {"off", "native", "balanced", "quality"}:
            raise ValueError("embedding_mode must be off, native, balanced, or quality")
        with self._lock:
            if resolved != self.mode:
                self._model = None
                self._model_failed = False
                self._model_error = ""
            self.mode = resolved
        return self.status()

    def status(self):
        return {
            "mode": self.mode,
            "backend": self.backend,
            "model": self.model_id if self.mode not in {"off", "native"} else "",
            "available": self.available,
            "error": self.last_error,
        }

    def embed_many(self, texts):
        values = [str(text or "")[:12_000] for text in texts]
        if not values:
            return []
        if self.mode not in {"off", "native"}:
            model = self._ensure_model()
            if model is not None:
                try:
                    vectors = list(model.passage_embed([f"passage: {value}" for value in values], batch_size=16))
                    return [self._encoded(vector, self.model_id) for vector in vectors]
                except Exception as exc:
                    self._record_model_failure(exc)
        return self._native_embed(values)

    def embed_query(self, text):
        value = str(text or "")[:12_000]
        if not value:
            return self._empty()
        if self.mode not in {"off", "native"}:
            model = self._ensure_model()
            if model is not None:
                try:
                    vector = next(iter(model.query_embed(f"query: {value}")))
                    return self._encoded(vector, self.model_id)
                except Exception as exc:
                    self._record_model_failure(exc)
        native = self._native_embed([value])
        return native[0] if native else self._empty()

    def release(self):
        """Release ONNX model memory after a large indexing burst."""
        with self._lock:
            self._model = None

    def _ensure_model(self):
        if self._model is not None:
            return self._model
        if self._model_failed or self.mode in {"off", "native"}:
            return None
        with self._lock:
            if self._model is not None:
                return self._model
            if self._model_failed:
                return None
            try:
                from fastembed import TextEmbedding
                from fastembed.common.model_description import ModelSource, PoolingType

                if self.model_id == DEFAULT_MODEL_ID:
                    try:
                        TextEmbedding.add_custom_model(
                            model=DEFAULT_MODEL_ID,
                            pooling=PoolingType.MEAN,
                            normalization=True,
                            sources=ModelSource(hf=DEFAULT_MODEL_REPOSITORY),
                            dim=384,
                            model_file=DEFAULT_MODEL_FILE,
                            description="Quantized multilingual E5-small for Poppy",
                            license="MIT",
                            size_in_gb=0.13,
                            additional_files=[
                                "config.json",
                                "tokenizer.json",
                                "tokenizer_config.json",
                                "special_tokens_map.json",
                                "sentencepiece.bpe.model",
                            ],
                        )
                    except ValueError:
                        # FastEmbed raises when another instance already
                        # registered the same model in this process.
                        pass
                if self.cache_dir is not None:
                    self.cache_dir.mkdir(parents=True, exist_ok=True)
                self._model = TextEmbedding(
                    model_name=self.model_id,
                    cache_dir=str(self.cache_dir) if self.cache_dir else None,
                    threads=self.threads,
                    lazy_load=True,
                )
                return self._model
            except Exception as exc:
                self._record_model_failure(exc)
                return None

    def _record_model_failure(self, exc):
        with self._lock:
            self._model = None
            self._model_failed = True
            self._model_error = str(exc)[:1000]

    def _native_embed(self, values):
        if not values or self.helper is None or self.mode == "off":
            return [self._empty() for _ in values]
        try:
            completed = subprocess.run(
                [str(self.helper)],
                input=json.dumps({"texts": values}, ensure_ascii=False),
                capture_output=True,
                text=True,
                check=True,
                timeout=120,
            )
            payload = json.loads(completed.stdout)
            rows = list(payload.get("items") or [])
            if len(rows) != len(values):
                raise ValueError("invalid semantic helper response")
            return [
                {
                    "language": str(item.get("language") or ""),
                    "embedding": str(item.get("embedding") or ""),
                    "model": "macos-nlembedding",
                    "dimension": self._dimension(item.get("embedding")),
                    "vector": None,
                }
                for item in rows
            ]
        except (OSError, subprocess.SubprocessError, ValueError, TypeError, json.JSONDecodeError):
            return [self._empty() for _ in values]

    @staticmethod
    def _encoded(vector, model_id):
        values = [float(item) for item in vector]
        raw = array("f", values).tobytes()
        return {
            "language": "multilingual",
            "embedding": base64.b64encode(raw).decode("ascii"),
            "model": str(model_id),
            "dimension": len(values),
            "vector": values,
        }

    @staticmethod
    def _empty():
        return {"language": "", "embedding": "", "model": "", "dimension": 0, "vector": None}

    @staticmethod
    def _dimension(encoded):
        try:
            return len(base64.b64decode(str(encoded or ""))) // array("f").itemsize
        except (ValueError, TypeError):
            return 0

    @staticmethod
    def decode(encoded):
        try:
            values = array("f")
            values.frombytes(base64.b64decode(str(encoded or "")))
            return list(values)
        except (ValueError, TypeError):
            return []

    @classmethod
    def similarity(cls, first, second):
        left = cls.decode(first)
        right = cls.decode(second)
        if not left or len(left) != len(right):
            return 0.0
        # All default backends normalize vectors; dot product is cosine.
        return sum(a * b for a, b in zip(left, right))
