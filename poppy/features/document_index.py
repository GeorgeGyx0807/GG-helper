"""Bounded, grant-aware text indexing for the personal data library.

The index is derived data.  A source is useful only while its path is still
covered by an active read grant, and every returned hit is rechecked against
that boundary before it is exposed to the model or UI.
"""

import hashlib
import mimetypes
import os
import re
import shutil
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path
from math import ceil

from .document_extractors import DOCUMENT_EXTENSIONS, DocumentExtractionError, extract_document
from .semantic_embeddings import SemanticEmbeddingService
from .vector_store import LanceVectorStore
from ..workspace import IGNORED_PATH_NAMES


TEXT_EXTENSIONS = {
    ".md", ".markdown", ".txt", ".rst", ".json", ".yaml", ".yml", ".toml",
    ".csv", ".tsv", ".py", ".js", ".jsx", ".ts", ".tsx", ".rs", ".go",
    ".java", ".c", ".h", ".cpp", ".hpp", ".sh", ".zsh", ".sql", ".html",
    ".css", ".scss", ".xml", ".ini", ".conf", ".log",
}
SUPPORTED_EXTENSIONS = TEXT_EXTENSIONS | DOCUMENT_EXTENSIONS
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_DOCUMENT_FILE_BYTES = 50 * 1024 * 1024
MAX_TOTAL_FILES = 5000
MAX_CHARS = 2_000_000
SELECTION_CONFIDENCE_THRESHOLD = 0.62
RRF_K = 60
SEMANTIC_CANDIDATE_LIMIT = 80
FULL_DOCUMENT_MAX_CHARS = 160_000
FULL_DOCUMENT_MAX_BATCHES = 4
FULL_DOCUMENT_SECTION_COUNT = 16
MIN_FREE_DISK_BYTES = int(os.environ.get("POPPY_MIN_FREE_DISK_GB", "30")) * 1024**3


class DocumentIndex:
    def __init__(self, database, semantic_service=None, vector_store=None):
        self.database = database
        data_root = Path(getattr(database, "path", Path.cwd())).parent
        self.semantic = semantic_service or SemanticEmbeddingService(cache_dir=data_root / "models")
        self.vector_store = vector_store or LanceVectorStore(data_root / "vectors")

    def list_sources(self):
        return [item for item in self.database.list_library_sources() if item.get("kind") == "folder"]

    def add_source(self, path, grant):
        resolved = self._ensure_authorized_path(path, grant)
        return self.database.upsert_library_source(resolved, grant_id=grant["id"], kind="folder")

    def remove_source(self, source_id):
        for document in self.database.list_documents(source_id):
            self.vector_store.delete_document(document["id"])
        self.database.delete_library_source(source_id)

    def reindex(self, source_id="", progress_callback=None):
        self._ensure_resource_budget()
        sources = [self.database.get_library_source(source_id)] if source_id else self.database.list_library_sources(enabled_only=True)
        results = []
        for source in sources:
            if not source:
                continue
            self.database.update_library_source_index_state(
                source["id"], "indexing", 0, indexed_count=0, failed_count=0
            )
            root = Path(source["path"]).expanduser().resolve()
            kind = str(source.get("kind") or "folder")
            if kind == "attachment-file":
                if not root.is_file():
                    self.database.delete_library_source(source["id"])
                    continue
                try:
                    stat = root.stat()
                    extension = root.suffix.lower()
                    max_bytes = MAX_DOCUMENT_FILE_BYTES if extension in DOCUMENT_EXTENSIONS else MAX_FILE_BYTES
                    if extension not in SUPPORTED_EXTENSIONS or stat.st_size > max_bytes:
                        raise DocumentExtractionError("附件格式不受支持或文件过大")
                    self._index_file(source["id"], root, stat)
                    paths = [root]
                    errors = []
                except (OSError, DocumentExtractionError) as exc:
                    paths = []
                    errors = [{"path": str(root), "error": str(exc)}]
                self.database.delete_documents_not_in(source["id"], paths)
                self.database.replace_index_failures(source["id"], errors)
                self.database.update_library_source_index_state(
                    source["id"],
                    "error" if errors else "idle",
                    100,
                    indexed_count=len(paths),
                    failed_count=len(errors),
                    last_error=errors[0]["error"] if errors else "",
                )
                self.database.mark_library_source_indexed(source["id"])
                results.append({"source_id": source["id"], "path": str(root), "documents": len(paths), "errors": errors})
                continue
            if not root.is_dir():
                self.database.delete_library_source(source["id"])
                continue
            paths = []
            errors = []
            candidates = list(self._iter_files(root))[:MAX_TOTAL_FILES]
            total = max(1, len(candidates))
            for candidate_index, candidate in enumerate(candidates, start=1):
                if len(paths) >= MAX_TOTAL_FILES:
                    break
                try:
                    candidate = candidate.resolve()
                    candidate.relative_to(root)
                    stat = candidate.stat()
                except (OSError, ValueError):
                    continue
                extension = candidate.suffix.lower()
                max_bytes = MAX_DOCUMENT_FILE_BYTES if extension in DOCUMENT_EXTENSIONS else MAX_FILE_BYTES
                if stat.st_size > max_bytes or extension not in SUPPORTED_EXTENSIONS:
                    continue
                try:
                    self._index_file(source["id"], candidate, stat)
                except DocumentExtractionError as exc:
                    errors.append({"path": str(candidate), "error": str(exc)})
                else:
                    paths.append(candidate)
                progress = min(99, int(candidate_index * 100 / total))
                self.database.update_library_source_index_state(
                    source["id"],
                    "indexing",
                    progress,
                    indexed_count=len(paths),
                    failed_count=len(errors),
                    last_error=errors[-1]["error"] if errors else "",
                )
                if progress_callback is not None:
                    progress_callback(source["id"], progress, len(paths), len(errors))
            self.database.delete_documents_not_in(source["id"], paths)
            self.database.replace_index_failures(source["id"], errors)
            self.database.mark_library_source_indexed(source["id"])
            self.database.update_library_source_index_state(
                source["id"],
                "error" if errors else "idle",
                100,
                indexed_count=len(paths),
                failed_count=len(errors),
                last_error=errors[0]["error"] if errors else "",
            )
            results.append({"source_id": source["id"], "path": str(root), "documents": len(paths), "errors": errors})
        if hasattr(self.semantic, "release"):
            self.semantic.release()
        self.vector_store.prune_documents(row["id"] for row in self.database.list_documents())
        return results

    def ingest_attachment(self, path, grant=None):
        """Index an explicitly selected file/folder without broadening grants."""
        resolved = Path(path).expanduser().resolve()
        if not resolved.exists():
            raise ValueError(f"附件不存在: {resolved}")
        if grant is not None:
            grant_root = Path(grant["path"]).expanduser().resolve()
            if not self._within(resolved, grant_root):
                raise PermissionError(f"附件不在授权目录中: {resolved}")
        if resolved.is_dir():
            if grant is not None:
                source = self.add_source(resolved, grant)
            else:
                source = self.database.upsert_library_source(resolved, kind="attachment-folder")
            self.reindex(source["id"])
            return {"path": str(resolved), "kind": "folder", "source_id": source["id"]}
        extension = resolved.suffix.lower()
        if extension not in SUPPORTED_EXTENSIONS:
            raise DocumentExtractionError(f"不支持的附件格式: {extension or 'unknown'}")
        if grant is not None:
            source = self.database.upsert_library_source(grant["path"], grant_id=grant["id"], kind="folder")
        else:
            source = self.database.upsert_library_source(resolved, kind="attachment-file")
        stat = resolved.stat()
        max_bytes = MAX_DOCUMENT_FILE_BYTES if extension in DOCUMENT_EXTENSIONS else MAX_FILE_BYTES
        if stat.st_size > max_bytes:
            raise DocumentExtractionError(f"附件过大，当前上限为 {max_bytes // (1024 * 1024)}MB")
        self._index_file(source["id"], resolved, stat)
        self.database.mark_library_source_indexed(source["id"])
        document = self.database.get_document_by_path(resolved)
        return {"path": str(resolved), "kind": "file", "source_id": source["id"], "document_id": document["id"]}

    def search(self, query, grants, limit=20, attachment_paths=(), scope=None, preserve_duplicate_documents=False):
        source_ids = self._authorized_source_ids(grants, attachment_paths)
        if not source_ids:
            return []
        document_ids = None
        scope = dict(scope or {})
        kind = str(scope.get("kind") or "auto")
        if kind == "notebook" and scope.get("id"):
            notebook = self.database.get_knowledge_space_scope(scope["id"])
            authorized_ids = {
                item["id"] for item in self.database.list_document_summaries(source_ids)
            }
            document_ids = [
                item for item in notebook.get("document_ids", []) if item in authorized_ids
            ]
            if not document_ids:
                return []
        elif kind == "document" and scope.get("id"):
            document_ids = [str(scope["id"])]
        rows = self._hybrid_search(
            query,
            source_ids=None if document_ids else source_ids,
            document_ids=document_ids,
            limit=limit,
            preserve_duplicate_documents=preserve_duplicate_documents,
        )
        return [
            row for row in rows
            if self._document_authorized(row["path"], grants, attachment_paths)
        ][: max(1, int(limit))]

    def search_document(self, query, document_id, grants, attachment_paths=(), limit=6):
        """Search only one already-indexed document after rechecking its authority boundary."""
        document = self.database.get_document(document_id)
        if document is None:
            return []
        path = Path(document["path"]).expanduser().resolve()
        if not self._document_authorized(path, grants, attachment_paths):
            return []
        rows = self._hybrid_search(query, document_ids=[document["id"]], limit=limit)
        return [row for row in rows if Path(row["path"]).expanduser().resolve() == path][: int(limit)]

    def list_authorized_documents(self, grants, attachment_paths=()):
        source_ids = self._authorized_source_ids(grants, attachment_paths)
        return [
            row for row in self.database.list_document_summaries(source_ids)
            if self._document_authorized(row["path"], grants, attachment_paths)
        ]

    def match_document_name(self, query, grants, attachment_paths=()):
        """Resolve an explicitly mentioned filename without fuzzy guessing."""
        raw_query = unicodedata.normalize("NFKC", str(query or "")).casefold()
        if not raw_query.strip():
            return None
        candidates = []
        for document in self.list_authorized_documents(grants, attachment_paths):
            name = str(document.get("display_name") or Path(document["path"]).name)
            folded_name = unicodedata.normalize("NFKC", name).casefold()
            folded_stem = unicodedata.normalize("NFKC", Path(name).stem).casefold()
            if folded_name and folded_name in raw_query:
                score = 1.0
            elif len(folded_stem) >= 4 and folded_stem in raw_query:
                score = 0.98
            else:
                continue
            candidates.append((score, -len(folded_name), document))
        if not candidates:
            return None
        candidates.sort(key=lambda item: (-item[0], item[1], item[2]["path"]))
        best = candidates[0]
        if len(candidates) > 1 and best[:2] == candidates[1][:2]:
            return None
        return {**best[2], "match_confidence": best[0], "match_kind": "filename"}

    def index_path(self, source_id, path):
        self._ensure_resource_budget()
        source = self.database.get_library_source(source_id)
        if source is None:
            return {"status": "missing_source", "path": str(path)}
        candidate = Path(path).expanduser().resolve()
        root = Path(source["path"]).expanduser().resolve()
        if not self._within(candidate, root):
            return {"status": "ignored", "path": str(candidate)}
        if not candidate.is_file():
            self.remove_path(source_id, candidate)
            return {"status": "deleted", "path": str(candidate)}
        extension = candidate.suffix.lower()
        maximum = MAX_DOCUMENT_FILE_BYTES if extension in DOCUMENT_EXTENSIONS else MAX_FILE_BYTES
        if extension not in SUPPORTED_EXTENSIONS or candidate.stat().st_size > maximum:
            self.remove_path(source_id, candidate)
            return {"status": "ignored", "path": str(candidate)}
        self.database.update_library_source_index_state(source_id, "indexing", 0)
        try:
            changed = self._index_file(source_id, candidate, candidate.stat())
            self.database.clear_index_failure(source_id, candidate)
            self.database.mark_library_source_indexed(source_id)
            return {"status": "updated" if changed else "unchanged", "path": str(candidate)}
        except (OSError, DocumentExtractionError) as exc:
            self.database.upsert_index_failure(source_id, candidate, str(exc))
            self.database.update_library_source_index_state(
                source_id, "error", 100, failed_count=1, last_error=str(exc)
            )
            return {"status": "failed", "path": str(candidate), "error": str(exc)}

    def remove_path(self, source_id, path):
        document = self.database.get_document_by_path(path)
        if document is not None:
            self.vector_store.delete_document(document["id"])
        removed = self.database.delete_document_by_path(path, source_id=source_id)
        self.database.clear_index_failure(source_id, path)
        self.database.mark_library_source_indexed(source_id)
        return {"status": "deleted" if removed else "missing", "path": str(Path(path).expanduser().resolve())}

    def full_document_batches(self, document_id, grants, attachment_paths=(), query=""):
        document = self.database.get_document(document_id)
        if document is None or not self._document_authorized(document["path"], grants, attachment_paths):
            return None
        chunks = self.database.list_document_chunks(document_id)
        if not chunks:
            return None
        rendered = [self._render_full_chunk(document, chunk) for chunk in chunks]
        total_chars = sum(len(item) for item in rendered)
        coverage_mode = "raw"
        selected = rendered
        if total_chars > FULL_DOCUMENT_MAX_CHARS:
            coverage_mode = "hierarchical"
            section_size = max(1, ceil(len(chunks) / FULL_DOCUMENT_SECTION_COUNT))
            selected = []
            query_terms = set(self._selection_tokens(query))
            for start in range(0, len(chunks), section_size):
                section = chunks[start:start + section_size]
                ranked = []
                for index, chunk in enumerate(section):
                    content = str(chunk.get("content") or "")
                    tokens = set(self._selection_tokens(content))
                    relevance = len(query_terms & tokens)
                    table_score = sum(
                        1 for line in content.splitlines()
                        if line.count("|") >= 2 or re.search(r"\b\d+(?:\.\d+)?\s*(?:%|ms|GB|MB|x)\b", line)
                    )
                    boundary = 2 if index in {0, len(section) - 1} else 0
                    ranked.append((relevance * 5 + table_score + boundary, index, chunk))
                ranked.sort(key=lambda item: (-item[0], item[1]))
                chosen = sorted(ranked[: min(4, len(ranked))], key=lambda item: item[1])
                selected.extend(self._render_full_chunk(document, item[2]) for item in chosen)
        batch_target = max(1, ceil(sum(len(item) for item in selected) / FULL_DOCUMENT_MAX_BATCHES))
        batches, current, current_size = [], [], 0
        for block in selected:
            if current and current_size + len(block) > batch_target and len(batches) < FULL_DOCUMENT_MAX_BATCHES - 1:
                batches.append("\n\n".join(current))
                current, current_size = [], 0
            current.append(block)
            current_size += len(block)
        if current:
            batches.append("\n\n".join(current))
        return {
            "document": document,
            "batches": batches,
            "coverage_mode": coverage_mode,
            "total_chars": total_chars,
            "included_chars": sum(len(item) for item in selected),
            "total_chunks": len(chunks),
            "included_chunks": len(selected),
        }

    @staticmethod
    def evidence_sufficient(rows):
        if not rows:
            return False
        best = rows[0]
        return bool(
            (
                float(best.get("hybrid_score") or 0) >= 0.016
                and int(best.get("match_score") or 0) >= 1
            )
            or float(best.get("semantic_score") or 0) >= 0.36
            or int(best.get("match_score") or 0) >= 2
        )

    def locate_selection(self, selection, grants, window_title="", attachment_paths=(), limit=30):
        """Locate a cross-application text selection in authorized indexed documents.

        PDF viewers commonly insert line breaks or soft hyphens that make an
        exact FTS query miss.  This method uses a small keyword recall query,
        then reranks normalized candidate chunks and returns neighboring
        chunks for answer context.
        """
        normalized = self._normalize_selection(selection)
        if not normalized:
            return None
        query = self._selection_query(normalized)
        candidates = self.search(
            query,
            grants,
            limit=max(5, min(int(limit), 60)),
            attachment_paths=attachment_paths,
            preserve_duplicate_documents=True,
        )

        title_name = Path(str(window_title or "")).name
        title_hint = self._normalize_selection(title_name)
        title_stem_hint = self._normalize_selection(Path(title_name).stem)
        selection_tokens = set(self._selection_tokens(normalized))
        selection_trigrams = self._trigrams(normalized)
        ranked = []
        for row in candidates:
            content = self._normalize_selection(row.get("content", ""))
            if not content:
                continue
            content_tokens = set(self._selection_tokens(content))
            token_overlap = len(selection_tokens & content_tokens) / max(1, len(selection_tokens))
            phrase_score = 1.0 if normalized in content else 0.0
            if not phrase_score:
                shorter, longer = sorted((normalized, content), key=len)
                probe = shorter[: min(len(shorter), 1200)]
                phrase_score = SequenceMatcher(None, probe, longer[: max(len(probe) * 3, 1200)]).ratio()
            content_trigrams = self._trigrams(content)
            trigram_score = len(selection_trigrams & content_trigrams) / max(1, len(selection_trigrams))
            display_name = str(row.get("display_name", ""))
            display_hint = self._normalize_selection(display_name)
            display_stem_hint = self._normalize_selection(Path(display_name).stem)
            filename_score = 0.0
            if title_hint and (title_hint in display_hint or display_hint in title_hint):
                filename_score = 1.0
            elif title_stem_hint and display_stem_hint:
                if title_stem_hint == display_stem_hint:
                    filename_score = 1.0
                else:
                    stem_similarity = SequenceMatcher(None, title_stem_hint, display_stem_hint).ratio()
                    if stem_similarity >= 0.72:
                        filename_score = stem_similarity
            score = (
                0.45 * phrase_score
                + 0.30 * token_overlap
                + 0.15 * trigram_score
                + 0.10 * filename_score
            )
            ranked.append((score, row))
        if not ranked:
            return None
        ranked.sort(key=lambda item: (-item[0], item[1].get("path", ""), item[1].get("line_start", 0)))
        score, best = ranked[0]
        if score < SELECTION_CONFIDENCE_THRESHOLD:
            return None

        chunks = self.database.list_document_chunks(best["id"])
        selected_index = next(
            (
                index
                for index, chunk in enumerate(chunks)
                if int(chunk.get("id") or -1) == int(best.get("chunk_id") or -2)
            ),
            next(
                (
                    index
                    for index, chunk in enumerate(chunks)
                    if int(chunk.get("line_start") or -1) == int(best.get("line_start") or -2)
                ),
                0,
            ),
        )
        context_rows = []
        for chunk in chunks[max(0, selected_index - 1) : selected_index + 2]:
            context_rows.append({
                "id": best["id"],
                "chunk_id": chunk["id"],
                "path": best["path"],
                "display_name": best["display_name"],
                "line_start": chunk["line_start"],
                "line_end": chunk["line_end"],
                "location": chunk.get("location") or {},
                "content": chunk["content"],
                "rank": best.get("rank", 0),
                "match_score": best.get("match_score", 0),
            })
        return {
            "document_id": best["id"],
            "path": best["path"],
            "display_name": best["display_name"],
            "location": best.get("location") or {},
            "line_start": best.get("line_start"),
            "line_end": best.get("line_end"),
            "confidence": round(min(1.0, score), 4),
            "context_rows": context_rows,
        }

    def preview(self, grants, attachment_paths=(), limit=6):
        """Return the first useful chunks for explicitly scoped documents."""
        allowed_roots = [Path(item["path"]).expanduser().resolve() for item in grants if item.get("can_read")]
        attachment_roots = [Path(item).expanduser().resolve() for item in attachment_paths]
        rows = []
        for document in self.database.list_documents():
            path = Path(document["path"]).expanduser().resolve()
            if not (
                any(self._within(path, root) for root in allowed_roots)
                or any(self._within(path, root) or path == root for root in attachment_roots)
            ):
                continue
            for chunk in self.database.list_document_chunks(document["id"])[:2]:
                rows.append({
                    "id": document["id"],
                    "source_id": document["source_id"],
                    "chunk_id": chunk["id"],
                    "path": document["path"],
                    "display_name": document["display_name"],
                    "line_start": chunk["line_start"],
                    "line_end": chunk["line_end"],
                    "location": chunk.get("location") or {},
                    "content": chunk["content"],
                    "rank": 0,
                    "match_score": 0,
                })
                if len(rows) >= int(limit):
                    return rows
        return rows

    def preview_document(self, document_id, grants, attachment_paths=(), limit=6):
        """Return bounded opening chunks from exactly one authorized document."""
        document = self.database.get_document(document_id)
        if document is None:
            return []
        path = Path(document["path"]).expanduser().resolve()
        allowed_roots = [Path(item["path"]).expanduser().resolve() for item in grants if item.get("can_read")]
        attachment_roots = [Path(item).expanduser().resolve() for item in attachment_paths]
        authorized = any(self._within(path, root) for root in allowed_roots) or any(
            self._within(path, root) or path == root for root in attachment_roots
        )
        if not authorized:
            return []
        rows = []
        for chunk in self.database.list_document_chunks(document["id"])[: max(1, int(limit))]:
            rows.append({
                "id": document["id"],
                "source_id": document["source_id"],
                "chunk_id": chunk["id"],
                "path": document["path"],
                "display_name": document["display_name"],
                "line_start": chunk["line_start"],
                "line_end": chunk["line_end"],
                "location": chunk.get("location") or {},
                "content": chunk["content"],
                "rank": 0,
                "match_score": 0,
            })
        return rows

    def read_document(self, document_id, grants):
        row = self.database.get_document(document_id)
        if row is None:
            raise KeyError(f"unknown document: {document_id}")
        path = Path(row["path"]).expanduser().resolve()
        roots = [Path(item["path"]).expanduser().resolve() for item in grants if item.get("can_read")]
        if not any(self._within(path, root) for root in roots):
            raise PermissionError(f"document is outside authorized folders: {path}")
        return row

    def _index_file(self, source_id, path, stat):
        existing = self.database.get_document_by_path(path)
        if (
            existing is not None
            and int(existing.get("size") or -1) == int(stat.st_size)
            and int(existing.get("mtime_ns") or -1) == int(stat.st_mtime_ns)
            and existing.get("source_id") == str(source_id)
            and (
                not self.semantic.available
                or self.database.document_embeddings_ready(
                    existing["id"], getattr(self.semantic, "model_id", "")
                )
            )
        ):
            return False
        extracted = extract_document(path, TEXT_EXTENSIONS)
        content = extracted.content[:MAX_CHARS]
        if not content.strip() or not extracted.chunks:
            raise DocumentExtractionError("文件没有可索引的文字")
        title = path.name
        for line in content.splitlines()[:80]:
            match = re.match(r"^#{1,3}\s+(.{2,200})$", line.strip())
            if match:
                title = match.group(1).strip()
                break
        chunks = [dict(item) for item in extracted.chunks]
        for chunk in chunks:
            section = str(chunk.get("section_title") or "").strip()
            prefix = [f"文档：{title}", f"文件：{path.name}"]
            if section:
                prefix.append(f"章节：{section}")
            chunk["context_text"] = "\n".join(prefix) + "\n正文：" + str(chunk.get("content") or "")
            chunk["content_hash"] = hashlib.sha256(
                chunk["context_text"].encode("utf-8", errors="replace")
            ).hexdigest()
        embeddings = []
        for start in range(0, len(chunks), 16):
            embeddings.extend(
                self.semantic.embed_many(
                    [item["context_text"] for item in chunks[start:start + 16]]
                )
            )
        for chunk, embedding in zip(chunks, embeddings):
            chunk["embedding_language"] = embedding.get("language", "")
            chunk["embedding_base64"] = embedding.get("embedding", "")
            chunk["embedding_model"] = embedding.get("model", "")
            chunk["embedding_dimension"] = int(embedding.get("dimension") or 0)
            chunk["vector"] = embedding.get("vector")
        document = self.database.upsert_document(
            source_id,
            path,
            path.name,
            mimetypes.guess_type(path.name)[0] or "text/plain",
            stat.st_size,
            stat.st_mtime_ns,
            hashlib.sha256(content.encode("utf-8", errors="replace")).hexdigest(),
            content,
            metadata={"title": title, "extractor": extracted.extractor},
        )
        inserted = self.database.replace_document_chunks(document["id"], chunks)
        self.vector_store.delete_document(document["id"])
        vector_rows = [item for item in inserted if item.get("vector")]
        if vector_rows:
            model_id = str(vector_rows[0].get("embedding_model") or "")
            dimension = int(vector_rows[0].get("embedding_dimension") or 0)
            if model_id and dimension:
                self.vector_store.replace_document(
                    document["id"], source_id, vector_rows, model_id, dimension
                )
        return True

    def _hybrid_search(self, query, source_ids=None, document_ids=None, limit=20, preserve_duplicate_documents=False):
        limit = max(1, int(limit))
        lexical = self.database.search_documents(
            query,
            limit=max(SEMANTIC_CANDIDATE_LIMIT, limit * 5),
            source_ids=source_ids,
            document_ids=document_ids,
        )
        semantic = self._semantic_candidates(query, source_ids=source_ids, document_ids=document_ids)
        if not document_ids:
            document_scores = {}
            for rank, row in enumerate(lexical, start=1):
                key = str(row["id"])
                document_scores[key] = document_scores.get(key, 0.0) + 1.0 / (RRF_K + rank)
            for rank, row in enumerate(semantic, start=1):
                key = str(row["id"])
                document_scores[key] = document_scores.get(key, 0.0) + 1.0 / (RRF_K + rank)
            candidate_documents = {
                item[0]
                for item in sorted(document_scores.items(), key=lambda item: (-item[1], item[0]))[:20]
            }
            if candidate_documents:
                lexical = [row for row in lexical if str(row["id"]) in candidate_documents]
                semantic = [row for row in semantic if str(row["id"]) in candidate_documents]
        fused = {}
        for rank, row in enumerate(lexical, start=1):
            item = fused.setdefault(int(row["chunk_id"]), dict(row))
            item["lexical_rank"] = rank
            item["hybrid_score"] = float(item.get("hybrid_score") or 0) + 1.0 / (RRF_K + rank)
        for rank, row in enumerate(semantic, start=1):
            item = fused.setdefault(int(row["chunk_id"]), dict(row))
            item["semantic_rank"] = rank
            item["semantic_score"] = row["semantic_score"]
            item["hybrid_score"] = float(item.get("hybrid_score") or 0) + 1.0 / (RRF_K + rank)
        rows = list(fused.values())
        query_folded = unicodedata.normalize("NFKC", str(query or "")).casefold()
        for row in rows:
            name = unicodedata.normalize("NFKC", str(row.get("display_name") or "")).casefold()
            stem = unicodedata.normalize("NFKC", Path(name).stem).casefold()
            if name and name in query_folded:
                row["hybrid_score"] += 0.05
            elif len(stem) >= 4 and stem in query_folded:
                row["hybrid_score"] += 0.04
        rows.sort(
            key=lambda item: (
                -float(item.get("hybrid_score") or 0),
                -float(item.get("semantic_score") or -1),
                -int(item.get("match_score") or 0),
                item["path"],
                int(item.get("line_start") or 0),
            )
        )
        selected = []
        per_document = {}
        seen_content = set()
        for row in rows:
            normalized = re.sub(r"\s+", " ", str(row.get("content") or "")).strip().casefold()
            fingerprint_text = (
                f"{row.get('id', '')}\n{normalized}"
                if preserve_duplicate_documents else normalized
            )
            fingerprint = hashlib.sha256(fingerprint_text.encode("utf-8", errors="replace")).hexdigest()
            if fingerprint in seen_content:
                continue
            document_id = str(row.get("id") or "")
            if not document_ids and per_document.get(document_id, 0) >= max(2, min(4, limit // 2)):
                continue
            seen_content.add(fingerprint)
            per_document[document_id] = per_document.get(document_id, 0) + 1
            selected.append(row)
            if len(selected) >= limit:
                break
        return selected

    def _semantic_candidates(self, query, source_ids=None, document_ids=None):
        query_embedding = self.semantic.embed_query(str(query or ""))
        language = str(query_embedding.get("language") or "")
        vector = str(query_embedding.get("embedding") or "")
        if not language or not vector:
            return []
        model_id = str(query_embedding.get("model") or "")
        dimension = int(query_embedding.get("dimension") or 0)
        raw_vector = query_embedding.get("vector")
        if raw_vector is None and hasattr(self.semantic, "decode"):
            raw_vector = self.semantic.decode(vector)
        if model_id and dimension and raw_vector:
            matches = self.vector_store.search(
                raw_vector,
                model_id,
                dimension,
                limit=SEMANTIC_CANDIDATE_LIMIT,
                source_ids=source_ids,
                document_ids=document_ids,
            )
            if matches:
                scores = {
                    int(item["chunk_id"]): float(item["semantic_score"])
                    for item in matches
                }
                matched_rows = self.database.get_chunks_by_ids(
                    [item["chunk_id"] for item in matches]
                )
                for row in matched_rows:
                    row["semantic_score"] = scores.get(int(row["chunk_id"]), 0.0)
                    row["rank"] = 0
                    row["match_score"] = 0
                return matched_rows
        rows = []
        for row in self.database.list_chunk_embeddings(source_ids=source_ids, document_ids=document_ids):
            row_language = str(row.get("embedding_language") or "")
            if language != "multilingual" and row_language not in {language, "multilingual"}:
                continue
            score = self.semantic.similarity(vector, row.get("embedding_base64"))
            if score < 0.18:
                continue
            item = dict(row)
            try:
                import json
                item["location"] = json.loads(item.pop("location_json") or "{}")
            except (TypeError, ValueError):
                item["location"] = {}
            item["semantic_score"] = score
            item["rank"] = 0
            item["match_score"] = 0
            rows.append(item)
        rows.sort(key=lambda item: (-item["semantic_score"], item["path"], item["line_start"]))
        return rows[:SEMANTIC_CANDIDATE_LIMIT]

    def _authorized_source_ids(self, grants, attachment_paths=()):
        allowed_roots = [Path(item["path"]).expanduser().resolve() for item in grants if item and item.get("can_read")]
        attachment_roots = [Path(item).expanduser().resolve() for item in attachment_paths]
        source_ids = []
        for source in self.database.list_library_sources(enabled_only=True):
            source_path = Path(source["path"]).expanduser().resolve()
            if any(self._within(source_path, root) for root in allowed_roots) or any(
                self._within(source_path, root) or self._within(root, source_path)
                for root in attachment_roots
            ):
                source_ids.append(source["id"])
        return source_ids

    def resource_status(self):
        usage = shutil.disk_usage(Path(getattr(self.database, "path", Path.cwd())).parent)
        return {
            "free_disk_bytes": int(usage.free),
            "minimum_free_disk_bytes": int(MIN_FREE_DISK_BYTES),
            "indexing_allowed": int(usage.free) >= int(MIN_FREE_DISK_BYTES),
            "embedding": self.semantic.status() if hasattr(self.semantic, "status") else {},
            "vector_backend": "lancedb" if self.vector_store.available else "sqlite-fallback",
            "vector_error": str(self.vector_store.last_error or ""),
        }

    def _ensure_resource_budget(self):
        status = self.resource_status()
        if not status["indexing_allowed"]:
            free_gb = status["free_disk_bytes"] / 1024**3
            raise RuntimeError(
                f"可用磁盘仅 {free_gb:.1f}GB，低于 30GB 安全阈值；已暂停索引以保护本机。"
            )

    def _document_authorized(self, path, grants, attachment_paths=()):
        path = Path(path).expanduser().resolve()
        allowed_roots = [Path(item["path"]).expanduser().resolve() for item in grants if item and item.get("can_read")]
        attachment_roots = [Path(item).expanduser().resolve() for item in attachment_paths]
        return any(self._within(path, root) for root in allowed_roots) or any(
            self._within(path, root) or path == root for root in attachment_roots
        )

    @staticmethod
    def _render_full_chunk(document, chunk):
        location = chunk.get("location") or {}
        if location.get("kind") == "pdf_page":
            locator = f"第 {location.get('page', '?')} 页"
        elif location.get("kind") == "spreadsheet":
            locator = f"工作表 {location.get('sheet', '?')}，第 {location.get('row_start', '?')}-{location.get('row_end', '?')} 行"
        else:
            locator = f"文本行 {chunk.get('line_start', '?')}-{chunk.get('line_end', '?')}"
        return f"[{document['display_name']} · {locator}]\n{chunk.get('content', '')}"

    @staticmethod
    def _iter_files(root):
        """Yield supported candidates without descending into build/cache trees."""
        candidates = []
        for current, directory_names, file_names in os.walk(root, followlinks=False):
            directory_names[:] = sorted(
                name for name in directory_names
                if name not in IGNORED_PATH_NAMES and not (Path(current) / name).is_symlink()
            )
            for name in file_names:
                candidate = Path(current) / name
                if candidate.is_symlink():
                    continue
                candidates.append(candidate)
        return sorted(candidates)

    @staticmethod
    def _chunks(content, lines_per_chunk=80):
        lines = content.splitlines()
        chunks = []
        for start in range(0, len(lines), lines_per_chunk):
            body = "\n".join(lines[start:start + lines_per_chunk]).strip()
            if body:
                chunks.append({"line_start": start + 1, "line_end": min(len(lines), start + lines_per_chunk), "content": body})
        return chunks

    @staticmethod
    def _within(path, root):
        try:
            path.relative_to(root)
            return True
        except ValueError:
            return False

    @staticmethod
    def _normalize_selection(value):
        text = unicodedata.normalize("NFKC", str(value or ""))
        text = text.replace("\u00ad", "").replace("‐", "-").replace("‑", "-")
        text = re.sub(r"(?<=[A-Za-z])-\s*\n\s*(?=[A-Za-z])", "", text)
        return re.sub(r"\s+", " ", text).strip().casefold()

    @classmethod
    def _selection_tokens(cls, value):
        normalized = cls._normalize_selection(value)
        tokens = re.findall(r"[a-z0-9_]{2,}|[\u3400-\u9fff]{2,}", normalized)
        expanded = []
        for token in tokens:
            if re.fullmatch(r"[\u3400-\u9fff]+", token) and len(token) > 6:
                expanded.extend(token[index : index + 4] for index in range(0, len(token) - 3, 3))
            else:
                expanded.append(token)
        return expanded

    @classmethod
    def _selection_query(cls, normalized):
        seen = set()
        terms = []
        for token in cls._selection_tokens(normalized):
            if token in seen:
                continue
            seen.add(token)
            terms.append(token)
            if len(terms) >= 18:
                break
        return " ".join(terms) or normalized[:160]

    @staticmethod
    def _trigrams(value, limit=4000):
        compact = re.sub(r"\s+", " ", str(value or ""))[:limit]
        if len(compact) < 3:
            return {compact} if compact else set()
        return {compact[index : index + 3] for index in range(len(compact) - 2)}

    @classmethod
    def _ensure_authorized_path(cls, path, grant):
        resolved = Path(path).expanduser().resolve()
        root = Path(grant["path"]).expanduser().resolve()
        if not resolved.is_dir():
            raise ValueError("library source must be an existing directory")
        if not cls._within(resolved, root):
            raise PermissionError(f"library source is outside authorized folder: {resolved}")
        return resolved
