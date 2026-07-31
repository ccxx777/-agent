"""劳动合同 A 级法律资料的条级 prepared artifact 与服务器入库入口。

示例：

    python -m data_worker.legal_cli prepare --overwrite
    python -m data_worker.legal_cli validate
    python -m data_worker.legal_cli ingest --allow-pending-governance --dry-run

``ingest`` 不会被 Watcher 自动触发，且拒绝写入通用生产语料和评测 Collection。
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from data_worker.ingest.legal_corpus import (
    DEFAULT_COLLECTION,
    DEFAULT_MAX_CHARS,
    DEFAULT_OVERLAP,
    LegalCorpusError,
    ingest_prepared_corpus,
    prepare_legal_corpus,
    validate_prepared_corpus,
)


def _default_base() -> Path:
    return Path("/app/data/legal/labor_contract")


def _default_prepared(base_dir: Path) -> Path:
    return base_dir / "prepared" / "a_level"


def _default_ingestion_state(collection: str) -> Path | None:
    """容器可把可恢复状态写入独立 volume，避免修改只读法律资料。"""

    state_dir = os.getenv("LEGAL_INGEST_STATE_DIR", "").strip()
    if not state_dir:
        return None
    return Path(state_dir) / f"ingestion_state_{collection}.json"


def build_parser() -> argparse.ArgumentParser:
    """构建显式子命令，避免静态法律资料误入通用 Watcher。"""

    parser = argparse.ArgumentParser(description="劳动合同 A 级法律资料条级切片与入库")
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare = subparsers.add_parser("prepare", help="本地生成法条与法条 Chunk artifact")
    prepare.add_argument("--base", type=Path, default=_default_base(), help="法律资料根目录")
    prepare.add_argument("--output", type=Path, help="prepared 输出目录")
    prepare.add_argument("--max-chars", type=int, default=DEFAULT_MAX_CHARS)
    prepare.add_argument("--overlap", type=int, default=DEFAULT_OVERLAP)
    prepare.add_argument("--overwrite", action="store_true", help="显式重建已有 prepared artifact")

    validate = subparsers.add_parser("validate", help="校验已生成的 prepared artifact")
    validate.add_argument("--base", type=Path, default=_default_base(), help="法律资料根目录")
    validate.add_argument("--prepared", type=Path, help="prepared artifact 目录")

    ingest = subparsers.add_parser("ingest", help="在服务器写入独立 Qdrant Collection")
    ingest.add_argument("--base", type=Path, default=_default_base(), help="法律资料根目录")
    ingest.add_argument("--prepared", type=Path, help="prepared artifact 目录")
    ingest.add_argument(
        "--qdrant-url", default=os.getenv("QDRANT_URL", "http://db_qdrant:6333")
    )
    ingest.add_argument(
        "--embed-url", default=os.getenv("EMBED_URL", "http://embedding_service:8001/embed")
    )
    ingest.add_argument("--collection", default=DEFAULT_COLLECTION)
    ingest.add_argument("--upsert-batch-size", type=int, default=32)
    ingest.add_argument("--state", type=Path, help="可恢复入库状态文件")
    ingest.add_argument("--resume", action="store_true", help="继续同一 manifest 的中断入库")
    ingest.add_argument(
        "--allow-pending-governance",
        action="store_true",
        help="仅允许向隔离测试 Collection 写入尚未法律复核激活的资料",
    )
    ingest.add_argument("--dry-run", action="store_true", help="只验证，不连接 Qdrant 或 Embedding")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    base = args.base.resolve()
    try:
        if args.command == "prepare":
            output = args.output.resolve() if args.output else _default_prepared(base)
            result = prepare_legal_corpus(
                base_dir=base,
                output_dir=output,
                max_chars=args.max_chars,
                overlap=args.overlap,
                overwrite=args.overwrite,
            )
        else:
            prepared = (args.prepared or _default_prepared(base)).resolve()
            if args.command == "validate":
                result = validate_prepared_corpus(prepared)
            else:
                result = ingest_prepared_corpus(
                    prepared_dir=prepared,
                    qdrant_url=args.qdrant_url,
                    embed_url=args.embed_url,
                    collection=args.collection,
                    upsert_batch_size=args.upsert_batch_size,
                    resume=args.resume,
                allow_pending_governance=args.allow_pending_governance,
                dry_run=args.dry_run,
                state_path=(
                    args.state.resolve()
                    if args.state
                    else _default_ingestion_state(args.collection)
                ),
                )
    except LegalCorpusError as error:
        print(f"[FAIL] {error}")
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
