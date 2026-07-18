"""文档入库业务编排服务。

该服务是 Loader、Chunker、Embedder、Writer 与 Fingerprint Repository 的唯一
编排入口。返回字典保持原 Sentinel CLI 的 ``status/chunks/sha256`` 契约。
"""

from __future__ import annotations

import logging
from pathlib import Path

from data_worker.ingest.chunker import TextChunker
from data_worker.ingest.embedder import DocumentEmbedder
from data_worker.ingest.fingerprint import FingerprintRepository, compute_sha256
from data_worker.ingest.loader import load_document
from data_worker.ingest.writer import QdrantWriter

logger = logging.getLogger(__name__)


class IngestService:
    """按固定顺序执行一次完整文档入库。"""

    def __init__(
        self,
        *,
        fingerprints: FingerprintRepository,
        chunker: TextChunker,
        embedder: DocumentEmbedder,
        writer: QdrantWriter,
    ) -> None:
        self._fingerprints = fingerprints
        self._chunker = chunker
        self._embedder = embedder
        self._writer = writer

    def ingest(self, file_path: Path, *, data_dir: Path | None = None) -> dict:
        """处理单个文件，并把可诊断的阶段结果返回给 CLI/Watcher。"""
        relative_path = str(file_path.relative_to(data_dir)) if data_dir else str(file_path)
        try:
            logger.info("开始解析文件: %s", file_path)
            sha256 = compute_sha256(file_path)
            exists, old_path = self._fingerprints.find(sha256)

            if exists and old_path == str(file_path):
                logger.info("指纹匹配，路径未变: %s → SKIP", relative_path)
                return {
                    "status": "skipped",
                    "chunks": 0,
                    "sha256": sha256,
                    "reason": "fingerprint match, path unchanged",
                }

            if exists:
                self._fingerprints.update_source(sha256, str(file_path))
                count = self._writer.sync_source(sha256, str(file_path))
                return {
                    "status": "synced",
                    "chunks": count,
                    "sha256": sha256,
                    "reason": f"metadata synced: {old_path} → {file_path}",
                }

            document = load_document(file_path)
            chunks = self._chunker.split(document.content)
            if not chunks:
                return {"status": "error", "step": "chunker", "message": "分块结果为空"}

            dense_vectors, sparse_vectors = self._embedder.embed(chunks)
            doc_id, title, point_count = self._writer.write(
                source=document.source,
                sha256=sha256,
                chunks=chunks,
                dense_vectors=dense_vectors,
                sparse_vectors=sparse_vectors,
            )
            self._fingerprints.record_document(
                doc_id=doc_id,
                title=title,
                source=document.source,
                sha256=sha256,
                chunk_count=point_count,
            )
            logger.info("文件 %s 入库完成 (%d points)", file_path, point_count)
            return {"status": "stored", "chunks": len(chunks), "sha256": sha256}
        except FileNotFoundError as error:
            return {"status": "error", "step": "loader", "message": str(error)}
        except Exception as error:
            logger.error("处理文件 %s 时发生错误: %s", file_path, error, exc_info=True)
            return {"status": "error", "step": "pipeline", "message": str(error)}
