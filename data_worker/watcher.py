"""文件系统持续监听器。

使用 PollingObserver 兼容 Docker bind mount；文件事件先经过 2 秒线程防抖，
再进入单消费者队列串行入库，防止低配服务器同时执行多个 Embedding 请求。
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers.polling import PollingObserver

from data_worker.ingest.service import IngestService

logger = logging.getLogger(__name__)


class DirectoryWatcher:
    """监听目录中新建/修改的受支持文件并串行提交给 IngestService。"""

    def __init__(
        self,
        *,
        data_dir: Path,
        supported_suffixes: frozenset[str],
        ingest_service: IngestService,
    ) -> None:
        self._data_dir = data_dir
        self._supported_suffixes = supported_suffixes
        self._ingest_service = ingest_service
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._debounce_lock = threading.Lock()
        self._debounce: dict[str, threading.Timer] = {}

    def _consume(self) -> None:
        """在单独线程中串行处理已防抖的文件路径。"""
        while True:
            file_path = self._queue.get()
            if file_path is None:
                self._queue.task_done()
                return
            try:
                path = Path(file_path)
                if path.exists() and path.suffix.lower() in self._supported_suffixes:
                    result = self._ingest_service.ingest(path, data_dir=self._data_dir)
                    logger.info("Processing result: %s", result)
            except Exception as error:
                logger.error("Pipeline failed for %s: %s", file_path, error, exc_info=True)
            finally:
                self._queue.task_done()

    def _schedule(self, path: str) -> None:
        """重置指定路径的 2 秒防抖计时器。"""
        with self._debounce_lock:
            existing = self._debounce.get(path)
            if existing:
                existing.cancel()
            timer = threading.Timer(2.0, self._trigger, args=(path,))
            timer.daemon = True
            self._debounce[path] = timer
            timer.start()

    def _trigger(self, path: str) -> None:
        """防抖结束后校验文件并加入消费队列。"""
        with self._debounce_lock:
            self._debounce.pop(path, None)
        file_path = Path(path)
        if file_path.exists() and file_path.suffix.lower() in self._supported_suffixes:
            logger.info("检测到新文件或文件变更: %s", file_path)
            self._queue.put(path)

    def run(self) -> None:
        """阻塞运行监听器，直到收到 Ctrl+C。"""
        watcher = self

        class Handler(FileSystemEventHandler):
            """将 watchdog 事件转交给外层 DirectoryWatcher。"""

            def on_created(self, event) -> None:
                if not event.is_directory:
                    watcher._schedule(event.src_path)

            def on_modified(self, event) -> None:
                if not event.is_directory:
                    watcher._schedule(event.src_path)

        consumer = threading.Thread(target=self._consume, daemon=True)
        consumer.start()
        observer = PollingObserver()
        observer.schedule(Handler(), str(self._data_dir), recursive=True)
        observer.start()
        logger.info("哨兵已启动，正在监控目录: %s", self._data_dir)

        try:
            while consumer.is_alive():
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        finally:
            observer.stop()
            observer.join()
            self._queue.put(None)
            consumer.join(timeout=5)
            with self._debounce_lock:
                for timer in self._debounce.values():
                    timer.cancel()
