from array import array
import base64
import time

from poppy.features.document_index import DocumentIndex
from poppy.features.document_extractors import DocumentExtractionError
from poppy.features.index_watcher import LibraryIndexWatcher
from poppy.features.vector_store import LanceVectorStore
from poppy.storage import DesktopDatabase


class CrossLanguageEmbeddings:
    available = True
    model_id = "test/multilingual"
    mode = "balanced"

    @staticmethod
    def _vector(text):
        folded = str(text).casefold()
        if "memory architecture" in folded or "记忆架构" in folded:
            return [1.0, 0.0, 0.0]
        if "database engine" in folded or "数据库引擎" in folded:
            return [0.0, 1.0, 0.0]
        return [0.0, 0.0, 1.0]

    def _encode(self, text):
        vector = self._vector(text)
        encoded = base64.b64encode(array("f", vector).tobytes()).decode("ascii")
        return {
            "language": "multilingual",
            "embedding": encoded,
            "model": self.model_id,
            "dimension": len(vector),
            "vector": vector,
        }

    def embed_many(self, texts):
        return [self._encode(text) for text in texts]

    def embed_query(self, text):
        return self._encode(text)

    @staticmethod
    def decode(encoded):
        values = array("f")
        values.frombytes(base64.b64decode(encoded))
        return list(values)

    @classmethod
    def similarity(cls, first, second):
        left, right = cls.decode(first), cls.decode(second)
        return sum(a * b for a, b in zip(left, right))

    def release(self):
        return None

    def status(self):
        return {"mode": self.mode, "model": self.model_id, "available": True}


def wait_until(predicate, timeout=4):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.03)
    raise AssertionError("condition did not become true")


def test_multilingual_lancedb_search_recalls_english_document_from_chinese_query(tmp_path):
    root = tmp_path / "library"
    root.mkdir()
    memory = root / "memory.md"
    distractor = root / "database.md"
    memory.write_text("# Memory Architecture\nA tiered memory architecture uses durable checkpoints.", encoding="utf-8")
    distractor.write_text("# Database Engine\nThe database engine uses a write ahead log.", encoding="utf-8")
    database = DesktopDatabase(tmp_path / "poppy.db")
    grant = database.add_grant(root)
    source = database.upsert_library_source(root, grant_id=grant["id"])
    vectors = LanceVectorStore(tmp_path / "vectors")
    index = DocumentIndex(database, semantic_service=CrossLanguageEmbeddings(), vector_store=vectors)

    index.reindex(source["id"])
    rows = index.search("记忆架构如何保存检查点？", [grant], limit=3)

    assert rows
    assert rows[0]["path"] == str(memory.resolve())
    assert rows[0]["semantic_score"] > 0.9
    assert vectors.last_error == ""


def test_notebook_scope_only_returns_selected_sources_and_documents(tmp_path):
    root = tmp_path / "library"
    root.mkdir()
    selected = root / "selected.md"
    excluded = root / "excluded.md"
    selected.write_text("shared phrase selected evidence", encoding="utf-8")
    excluded.write_text("shared phrase excluded evidence", encoding="utf-8")
    database = DesktopDatabase(tmp_path / "poppy.db")
    grant = database.add_grant(root)
    source = database.upsert_library_source(root, grant_id=grant["id"])
    index = DocumentIndex(database, semantic_service=CrossLanguageEmbeddings())
    index.reindex(source["id"])
    selected_document = database.get_document_by_path(selected)
    space = database.create_knowledge_space("Selected papers")
    database.set_knowledge_space_documents(space["id"], [selected_document["id"]])

    rows = index.search(
        "shared phrase",
        [grant],
        limit=10,
        scope={"kind": "notebook", "id": space["id"]},
    )

    assert rows
    assert {row["path"] for row in rows} == {str(selected.resolve())}


def test_model_change_forces_embedding_refresh_without_file_change(tmp_path):
    root = tmp_path / "library"
    root.mkdir()
    path = root / "paper.md"
    path.write_text("memory architecture", encoding="utf-8")
    database = DesktopDatabase(tmp_path / "poppy.db")
    grant = database.add_grant(root)
    source = database.upsert_library_source(root, grant_id=grant["id"])
    semantic = CrossLanguageEmbeddings()
    index = DocumentIndex(database, semantic_service=semantic)
    index.reindex(source["id"])
    document = database.get_document_by_path(path)
    assert database.document_embeddings_ready(document["id"], semantic.model_id)

    semantic.model_id = "test/multilingual-v2"
    assert index.index_path(source["id"], path)["status"] == "updated"
    assert database.document_embeddings_ready(document["id"], semantic.model_id)


def test_index_jobs_persist_progress_and_failures(tmp_path):
    root = tmp_path / "library"
    root.mkdir()
    path = root / "paper.md"
    path.write_text("first version", encoding="utf-8")
    database = DesktopDatabase(tmp_path / "poppy.db")
    source = database.upsert_library_source(root)
    index = DocumentIndex(database, semantic_service=CrossLanguageEmbeddings())
    watcher = LibraryIndexWatcher(index, debounce_seconds=0.05)
    watcher.start()
    try:
        first = watcher.enqueue(source["id"], "upsert", path, immediate=True)
        wait_until(lambda: database.get_index_job(first["id"])["status"] == "completed")
        assert database.get_document_by_path(path) is not None
        path.write_bytes(b"\x00\x00\x00")
        failed = watcher.enqueue(source["id"], "upsert", path, immediate=True)
        wait_until(lambda: database.get_index_job(failed["id"])["status"] in {"completed", "failed"})
        job = database.get_index_job(failed["id"])
        assert job["progress"] == 100
        assert job["stage"] in {"completed", "failed"}
    finally:
        watcher.stop()


def test_failed_update_keeps_last_successful_chunks(tmp_path, monkeypatch):
    root = tmp_path / "library"
    root.mkdir()
    path = root / "paper.md"
    path.write_text("last successful evidence", encoding="utf-8")
    database = DesktopDatabase(tmp_path / "poppy.db")
    grant = database.add_grant(root)
    source = database.upsert_library_source(root, grant_id=grant["id"])
    index = DocumentIndex(database, semantic_service=CrossLanguageEmbeddings())
    index.reindex(source["id"])
    document = database.get_document_by_path(path)
    old_chunks = database.list_document_chunks(document["id"])

    path.write_text("broken new version", encoding="utf-8")
    monkeypatch.setattr(
        "poppy.features.document_index.extract_document",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(DocumentExtractionError("parser failed")),
    )
    result = index.index_path(source["id"], path)

    assert result["status"] == "failed"
    assert database.list_document_chunks(document["id"]) == old_chunks
