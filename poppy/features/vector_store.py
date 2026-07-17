"""Embedded LanceDB vector store with a no-vector fallback boundary."""

import hashlib
from pathlib import Path
from threading import Lock


class LanceVectorStore:
    """Store chunk vectors outside SQLite while keeping SQLite authoritative."""

    def __init__(self, path):
        self.path = Path(path).expanduser().resolve()
        self._db = None
        self._lock = Lock()
        self.last_error = ""

    @property
    def available(self):
        try:
            import lancedb  # noqa: F401
            return True
        except ImportError:
            return False

    def replace_document(self, document_id, source_id, rows, model_id, dimension):
        vectors = []
        for row in rows:
            vector = row.get("vector")
            if not vector or len(vector) != int(dimension):
                continue
            vectors.append(
                {
                    "chunk_id": int(row["chunk_id"]),
                    "document_id": str(document_id),
                    "source_id": str(source_id),
                    "model_id": str(model_id),
                    "vector": [float(item) for item in vector],
                }
            )
        if not vectors or not self.available:
            return 0
        try:
            with self._lock:
                database = self._connect()
                table_name = self._table_name(model_id, dimension)
                names = set(self._table_names(database))
                if table_name not in names:
                    table = database.create_table(table_name, data=vectors)
                    return len(vectors)
                table = database.open_table(table_name)
                table.delete(f"document_id = {self._literal(document_id)}")
                table.add(vectors)
                return len(vectors)
        except Exception as exc:
            self.last_error = str(exc)[:1000]
            return 0

    def search(self, query_vector, model_id, dimension, limit=80, source_ids=None, document_ids=None):
        if not query_vector or not self.available:
            return []
        try:
            with self._lock:
                database = self._connect()
                table_name = self._table_name(model_id, dimension)
                if table_name not in set(self._table_names(database)):
                    return []
                query = database.open_table(table_name).search(
                    [float(item) for item in query_vector], vector_column_name="vector"
                )
                filters = []
                if document_ids:
                    filters.append(self._in_filter("document_id", document_ids))
                elif source_ids:
                    filters.append(self._in_filter("source_id", source_ids))
                if filters:
                    query = query.where(" AND ".join(filters), prefilter=True)
                rows = query.limit(max(1, min(int(limit), 500))).to_list()
            return [
                {
                    "chunk_id": int(row["chunk_id"]),
                    "document_id": str(row["document_id"]),
                    "source_id": str(row["source_id"]),
                    "semantic_score": max(-1.0, min(1.0, 1.0 - float(row.get("_distance") or 0.0) / 2.0)),
                }
                for row in rows
            ]
        except Exception as exc:
            self.last_error = str(exc)[:1000]
            return []

    def delete_document(self, document_id):
        if not self.available:
            return
        try:
            with self._lock:
                database = self._connect()
                for table_name in self._table_names(database):
                    database.open_table(table_name).delete(
                        f"document_id = {self._literal(document_id)}"
                    )
        except Exception as exc:
            self.last_error = str(exc)[:1000]

    def prune_documents(self, valid_document_ids):
        valid = [str(item) for item in valid_document_ids]
        if not self.available:
            return
        try:
            with self._lock:
                database = self._connect()
                for table_name in self._table_names(database):
                    table = database.open_table(table_name)
                    if valid:
                        table.delete("document_id NOT IN (" + ",".join(self._literal(item) for item in valid) + ")")
                    else:
                        database.drop_table(table_name)
        except Exception as exc:
            self.last_error = str(exc)[:1000]

    def _connect(self):
        if self._db is None:
            import lancedb

            self.path.mkdir(parents=True, exist_ok=True)
            self._db = lancedb.connect(str(self.path))
        return self._db

    @staticmethod
    def _table_names(database):
        response = database.list_tables(limit=1000)
        return list(getattr(response, "tables", response) or [])

    @staticmethod
    def _table_name(model_id, dimension):
        digest = hashlib.sha256(str(model_id).encode("utf-8")).hexdigest()[:12]
        return f"chunks_{int(dimension)}_{digest}"

    @classmethod
    def _in_filter(cls, column, values):
        return f"{column} IN (" + ",".join(cls._literal(item) for item in values) + ")"

    @staticmethod
    def _literal(value):
        return "'" + str(value).replace("'", "''") + "'"
