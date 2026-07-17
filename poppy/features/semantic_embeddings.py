"""Optional macOS-native sentence embeddings for local hybrid retrieval."""

from array import array
import base64
from functools import lru_cache
import json
import os
from pathlib import Path
import subprocess
import sys


class SemanticEmbeddingService:
    def __init__(self, helper=None):
        self.helper = Path(helper) if helper else self._discover_helper()

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
        return self.helper is not None

    def embed_many(self, texts):
        values = [str(text or "")[:3000] for text in texts]
        if not values or self.helper is None:
            return [{"language": "", "embedding": ""} for _ in values]
        try:
            completed = subprocess.run(
                [str(self.helper)],
                input=json.dumps(values, ensure_ascii=False),
                capture_output=True,
                text=True,
                timeout=max(10, min(120, len(values) * 2)),
                check=True,
            )
            rows = json.loads(completed.stdout)
            if not isinstance(rows, list) or len(rows) != len(values):
                raise ValueError("invalid semantic helper response")
            return [
                {
                    "language": str(item.get("language") or ""),
                    "embedding": str(item.get("embedding") or ""),
                }
                if isinstance(item, dict)
                else {"language": "", "embedding": ""}
                for item in rows
            ]
        except (OSError, subprocess.SubprocessError, ValueError, TypeError, json.JSONDecodeError):
            return [{"language": "", "embedding": ""} for _ in values]

    @lru_cache(maxsize=256)
    def embed_query(self, text):
        return self.embed_many([str(text or "")])[0]

    @staticmethod
    def similarity(first, second):
        if not first or not second:
            return 0.0
        try:
            left = array("b", base64.b64decode(first))
            right = array("b", base64.b64decode(second))
        except (ValueError, TypeError):
            return 0.0
        if not left or len(left) != len(right):
            return 0.0
        dot = sum(int(a) * int(b) for a, b in zip(left, right))
        left_norm = sum(int(value) ** 2 for value in left) ** 0.5
        right_norm = sum(int(value) ** 2 for value in right) ** 0.5
        if not left_norm or not right_norm:
            return 0.0
        return max(-1.0, min(1.0, dot / (left_norm * right_norm)))
