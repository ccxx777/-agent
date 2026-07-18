"""Data Worker 命令行入口。

支持三种与旧 Sentinel 相同的模式：``--once`` 单文件、默认批量扫描、
``--watch`` 持续监听。CLI 只解析参数并选择运行模式，入库细节全部委托给
``IngestService``。
"""

from __future__ import annotations

import argparse
import logging
import time
from pathlib import Path

from data_worker.config import WorkerSettings
from data_worker.ingest.chunker import TextChunker
from data_worker.ingest.embedder import DocumentEmbedder
from data_worker.ingest.fingerprint import FingerprintRepository
from data_worker.ingest.service import IngestService
from data_worker.ingest.writer import QdrantWriter
from data_worker.watcher import DirectoryWatcher

logger = logging.getLogger(__name__)


def create_ingest_service(settings: WorkerSettings) -> IngestService:
    """根据统一配置装配一次 Data Worker 入库服务。"""
    return IngestService(
        fingerprints=FingerprintRepository(settings.pg_dsn),
        chunker=TextChunker(
            chunk_size=settings.chunk_size,
            chunk_overlap=settings.chunk_overlap,
        ),
        embedder=DocumentEmbedder(
            endpoint=settings.embed_url,
            batch_size=settings.embedding_batch_size,
        ),
        writer=QdrantWriter(
            url=settings.qdrant_url,
            collection_name=settings.collection_name,
            vector_dim=settings.vector_dim,
        ),
    )


def build_parser(settings: WorkerSettings) -> argparse.ArgumentParser:
    """构建 CLI 参数定义，默认值来自 ``WorkerSettings``。"""
    parser = argparse.ArgumentParser(description="哨兵中枢 — 独立文档入库服务")
    parser.add_argument("--dir", default=str(settings.data_dir), help="监听/扫描目录")
    parser.add_argument("--qdrant-url", default=settings.qdrant_url)
    parser.add_argument("--embed-url", default=settings.embed_url)
    parser.add_argument("--once", default="", help="处理单个文件后退出")
    parser.add_argument("--watch", action="store_true", help="持续监听模式")
    return parser


def main() -> None:
    """解析参数，装配服务并运行所选模式。"""
    base_settings = WorkerSettings()
    args = build_parser(base_settings).parse_args()
    settings = WorkerSettings(
        data_dir=Path(args.dir),
        qdrant_url=args.qdrant_url,
        embed_url=args.embed_url,
        pg_host=base_settings.pg_host,
        pg_port=base_settings.pg_port,
        pg_user=base_settings.pg_user,
        pg_password=base_settings.pg_password,
        pg_database=base_settings.pg_database,
    )
    data_dir = settings.data_dir.resolve()
    ingest_service = create_ingest_service(settings)
    logger.info("Sentinel data directory: %s", data_dir)
    print(f"[INFO] Sentinel is watching ABSOLUTE PATH: {data_dir}", flush=True)

    if args.once:
        file_path = Path(args.once)
        if not file_path.is_absolute():
            file_path = Path("/app") / file_path
        print(ingest_service.ingest(file_path, data_dir=data_dir))
        return

    if args.watch:
        DirectoryWatcher(
            data_dir=data_dir,
            supported_suffixes=settings.supported_suffixes,
            ingest_service=ingest_service,
        ).run()
        return

    supported_files = sorted(
        path
        for path in data_dir.rglob("*")
        if path.is_file() and path.suffix.lower() in settings.supported_suffixes
    )
    if not supported_files:
        logger.warning("No supported files found in %s", data_dir)
        return

    for index, file_path in enumerate(supported_files, 1):
        print(f"  [{index}/{len(supported_files)}] Processing: {file_path.relative_to(data_dir)}", flush=True)
        result = ingest_service.ingest(file_path, data_dir=data_dir)
        print(f"       {'✓' if result['status'] == 'stored' else '⚠'} {result}", flush=True)
        if index < len(supported_files):
            time.sleep(1)


if __name__ == "__main__":
    main()
