import time

from poppy.features.document_index import DocumentIndex
from poppy.features.index_watcher import LibraryIndexWatcher
from poppy.storage import DesktopDatabase


class NoSemanticEmbeddings:
    available = False

    def embed_many(self, texts):
        return [{"language": "", "embedding": ""} for _ in texts]

    def embed_query(self, _text):
        return {"language": "", "embedding": ""}

    @staticmethod
    def similarity(_first, _second):
        return 0.0


def wait_until(predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError("condition did not become true")


def test_filename_match_is_exact_and_ambiguous_stems_do_not_guess(tmp_path):
    root = tmp_path / "papers"
    root.mkdir()
    first = root / "memory-system.pdf.md"
    second = root / "memory-system-notes.pdf.md"
    first.write_text("alpha evidence about checkpoint recovery", encoding="utf-8")
    second.write_text("beta evidence about unrelated cache", encoding="utf-8")
    database = DesktopDatabase(tmp_path / "index.db")
    grant = database.add_grant(root)
    source = database.upsert_library_source(root, grant_id=grant["id"])
    index = DocumentIndex(database, semantic_service=NoSemanticEmbeddings())
    index.reindex(source["id"])

    matched = index.match_document_name("请只看 memory-system.pdf.md 回答", [grant])
    assert matched["path"] == str(first.resolve())
    assert matched["match_confidence"] == 1.0
    assert index.match_document_name("请看 memory-system", [grant]) is None


def test_scoped_search_never_returns_a_similar_other_document(tmp_path):
    root = tmp_path / "papers"
    root.mkdir()
    first = root / "paper-a.md"
    second = root / "paper-b.md"
    first.write_text("shared keyword\nA-only conclusion", encoding="utf-8")
    second.write_text("shared keyword\nB-only conclusion", encoding="utf-8")
    database = DesktopDatabase(tmp_path / "index.db")
    grant = database.add_grant(root)
    source = database.upsert_library_source(root, grant_id=grant["id"])
    index = DocumentIndex(database, semantic_service=NoSemanticEmbeddings())
    index.reindex(source["id"])
    document = database.get_document_by_path(first)

    rows = index.search_document("shared keyword conclusion", document["id"], [grant], limit=10)
    assert rows
    assert {row["path"] for row in rows} == {str(first.resolve())}


def test_full_document_hierarchy_samples_every_section_and_table_evidence(tmp_path):
    root = tmp_path / "papers"
    root.mkdir()
    path = root / "large-paper.md"
    path.write_text("placeholder", encoding="utf-8")
    database = DesktopDatabase(tmp_path / "index.db")
    grant = database.add_grant(root)
    source = database.upsert_library_source(root, grant_id=grant["id"])
    document = database.upsert_document(
        source["id"], path, path.name, "text/markdown", 1, 1, "hash", "large"
    )
    chunks = []
    for index in range(160):
        table = "| metric | value |\n| latency | 12.3 ms |\n" if index == 87 else ""
        chunks.append({
            "line_start": index * 10 + 1,
            "line_end": index * 10 + 10,
            "content": f"SECTION-{index:03d} " + ("x" * 1900) + table,
        })
    database.replace_document_chunks(document["id"], chunks)
    index = DocumentIndex(database, semantic_service=NoSemanticEmbeddings())

    coverage = index.full_document_batches(document["id"], [grant], query="latency metric")
    combined = "\n".join(coverage["batches"])
    assert coverage["coverage_mode"] == "hierarchical"
    assert len(coverage["batches"]) <= 4
    for section_start in range(0, 160, 10):
        assert f"SECTION-{section_start:03d}" in combined
    assert "12.3 ms" in combined
    assert "SECTION-159" in combined


def test_file_watcher_incrementally_adds_updates_and_deletes_documents(tmp_path):
    root = tmp_path / "library"
    root.mkdir()
    database = DesktopDatabase(tmp_path / "index.db")
    database.upsert_library_source(root)
    index = DocumentIndex(database, semantic_service=NoSemanticEmbeddings())
    watcher = LibraryIndexWatcher(index, debounce_seconds=0.1)
    if not watcher.available:
        return
    watcher.start()
    try:
        path = root / "live.md"
        path.write_text("first version", encoding="utf-8")
        wait_until(lambda: database.get_document_by_path(path) is not None)
        wait_until(lambda: "first version" in database.get_document_by_path(path)["content"])

        path.write_text("second version with new fact", encoding="utf-8")
        wait_until(lambda: "second version" in database.get_document_by_path(path)["content"])

        path.unlink()
        wait_until(lambda: database.get_document_by_path(path) is None)
    finally:
        watcher.stop()
