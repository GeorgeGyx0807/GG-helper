"""Background, debounced incremental indexing for authorized library folders."""

from pathlib import Path
from queue import Empty, Queue
from threading import Event, Lock, Thread
import time

try:
    from watchdog.events import FileSystemEventHandler
    # watchdog's native FSEvents extension can segfault while stopping under
    # the Python 3.13 runtime used by the bundled desktop sidecar.  The polling
    # observer keeps the same recursive event API and is stable in a frozen app.
    from watchdog.observers.polling import PollingObserver as Observer
except ImportError:  # Development environments can still use manual indexing.
    FileSystemEventHandler = object
    Observer = None


class _SourceHandler(FileSystemEventHandler):
    def __init__(self, watcher, source_id):
        super().__init__()
        self.watcher = watcher
        self.source_id = source_id

    def on_created(self, event):
        if not event.is_directory:
            self.watcher.enqueue(self.source_id, "upsert", event.src_path)

    def on_modified(self, event):
        if not event.is_directory:
            self.watcher.enqueue(self.source_id, "upsert", event.src_path)

    def on_deleted(self, event):
        if not event.is_directory:
            self.watcher.enqueue(self.source_id, "delete", event.src_path)

    def on_moved(self, event):
        if not event.is_directory:
            self.watcher.enqueue(self.source_id, "delete", event.src_path)
            self.watcher.enqueue(self.source_id, "upsert", event.dest_path)


class LibraryIndexWatcher:
    """Use macOS FSEvents through watchdog without blocking the chat process."""

    def __init__(self, document_index, debounce_seconds=0.8):
        self.document_index = document_index
        self.debounce_seconds = max(0.1, float(debounce_seconds))
        self.queue = Queue()
        self.stop_event = Event()
        self.observer = None
        self.worker = None
        self.watches = {}
        self.pending = {}
        self.lock = Lock()

    @property
    def running(self):
        return bool(self.worker and self.worker.is_alive())

    @property
    def available(self):
        return Observer is not None

    def start(self):
        if self.running:
            return
        self.stop_event.clear()
        self.document_index.database.recover_interrupted_index_jobs()
        if Observer is not None:
            self.observer = Observer(timeout=0.4)
            self.observer.start()
            for source in self.document_index.list_sources():
                self.add_source(source["id"])
        self.worker = Thread(target=self._run, name="poppy-library-indexer", daemon=True)
        self.worker.start()
        # Catch changes made while Poppy was closed; unchanged files are skipped.
        for source in self.document_index.list_sources():
            self.enqueue(source["id"], "rescan", source["path"], immediate=True)

    def stop(self, timeout=3.0):
        self.stop_event.set()
        self.queue.put(("", "stop", "", 0.0, ""))
        if self.observer is not None:
            self.observer.stop()
            self.observer.join(timeout)
        if self.worker is not None:
            self.worker.join(timeout)
        self.observer = None
        self.worker = None
        self.watches.clear()

    def add_source(self, source_id):
        if self.observer is None:
            return
        source = self.document_index.database.get_library_source(source_id)
        if source is None or source.get("kind") != "folder":
            return
        path = Path(source["path"])
        if not path.is_dir():
            return
        self.remove_source(source_id)
        watch = self.observer.schedule(_SourceHandler(self, source_id), str(path), recursive=True)
        self.watches[source_id] = watch

    def remove_source(self, source_id):
        watch = self.watches.pop(str(source_id), None)
        if watch is not None and self.observer is not None:
            try:
                self.observer.unschedule(watch)
            except KeyError:
                pass

    def enqueue(self, source_id, operation, path, immediate=False):
        due = time.monotonic() if immediate else time.monotonic() + self.debounce_seconds
        key = (str(source_id), str(Path(path).expanduser()), str(operation))
        job = self.document_index.database.create_index_job(
            key[0], key[2], path=key[1]
        )
        with self.lock:
            previous = self.pending.get(key)
            self.pending[key] = (due, job["id"])
        if previous:
            self.document_index.database.update_index_job(
                previous[1], status="cancelled", stage="debounced", progress=0
            )
        self.queue.put((key[0], key[2], key[1], due, job["id"]))
        return job

    def _run(self):
        while not self.stop_event.is_set():
            try:
                source_id, operation, path, due, job_id = self.queue.get(timeout=0.25)
            except Empty:
                continue
            if operation == "stop":
                return
            remaining = due - time.monotonic()
            if remaining > 0 and self.stop_event.wait(min(remaining, 1.0)):
                return
            key = (source_id, path, operation)
            with self.lock:
                latest = self.pending.get(key)
                if latest is None or latest != (due, job_id):
                    continue
                self.pending.pop(key, None)
            try:
                self.document_index.database.update_index_job(
                    job_id, status="running", stage="indexing", progress=10
                )
                if operation == "rescan":
                    self.document_index.reindex(
                        source_id,
                        progress_callback=lambda _source, progress, _indexed, _failed: self.document_index.database.update_index_job(
                            job_id, status="running", stage="embedding", progress=progress
                        ),
                    )
                elif operation == "delete":
                    self.document_index.remove_path(source_id, path)
                else:
                    result = self.document_index.index_path(source_id, path)
                    if result.get("status") == "failed":
                        raise RuntimeError(result.get("error") or "文件索引失败")
                self.document_index.database.update_index_job(
                    job_id, status="completed", stage="completed", progress=100
                )
            except Exception as exc:
                self.document_index.database.update_index_job(
                    job_id, status="failed", stage="failed", progress=100, error=str(exc)
                )
                self.document_index.database.update_library_source_index_state(
                    source_id, "error", 100, last_error=str(exc)
                )
