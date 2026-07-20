"""把旧Qdrant Collection离线重建为原生Sparse/BM25 v2 Collection。

旧库只读；新库使用四个命名向量：

- ``dense``：复用旧Dense向量；
- ``bge_m3_sparse``：把Payload中的BGE-M3 Sparse迁入Qdrant原生Sparse Index；
- ``bm25_word``：英文word分词的BM25 Sparse Vector；
- ``bm25_zh``：中文Jieba分词的BM25 Sparse Vector。

Qdrant Payload Full-text Index只提供匹配/过滤，不返回BM25排序分数，因此BM25
必须使用独立Sparse Vector。中文Full-text优先探测Qdrant ``multilingual``
tokenizer；服务端拒绝时，回退到预分词字段上的``word`` tokenizer。

脚本不删除、不覆盖Collection。中断后只有携带匹配状态文件的 ``--resume`` 才会
继续幂等Upsert，避免把不同来源的数据混入同一目标库。
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

import httpx


try:
    from app.services.query_specificity import (
        chinese_tokens,
        bm25_document_sparse,
        english_tokens,
        sparse_token_id,
    )
except ImportError:  # 从仓库根目录直接执行
    import sys

    REPO_ROOT = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(REPO_ROOT / "backend"))
    from app.services.query_specificity import (  # type: ignore[no-redef]
        chinese_tokens,
        bm25_document_sparse,
        english_tokens,
        sparse_token_id,
    )


FORMAT_VERSION = 1
MIN_NATIVE_SPARSE_VERSION = (1, 7, 0)
MIN_IDF_MODIFIER_VERSION = (1, 10, 0)
VECTOR_NAMES = {
    "dense": "dense",
    "bge_sparse": "bge_m3_sparse",
    "bm25_en": "bm25_word",
    "bm25_zh": "bm25_zh",
}
VERSION_PATTERN = re.compile(r"^(\d+)\.(\d+)\.(\d+)")


class MigrationError(RuntimeError):
    """服务器能力、源数据或迁移状态不满足安全重建条件。"""


@dataclass(frozen=True)
class SourcePoint:
    point_id: str | int
    dense: list[float]
    bge_indices: list[int]
    bge_values: list[float]
    payload: dict[str, Any]


@dataclass(frozen=True)
class Bm25Corpus:
    vectors: list[tuple[list[int], list[float]]]
    average_document_length: float
    vocabulary_size: int
    hash_collisions: int


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _response_result(response: httpx.Response) -> Any:
    response.raise_for_status()
    body = response.json()
    if body.get("status") not in (None, "ok"):
        raise MigrationError(f"Qdrant返回非ok状态：{body.get('status')}")
    return body.get("result")


def qdrant_version(client: httpx.Client, qdrant_url: str) -> str:
    response = client.get(qdrant_url.rstrip("/") + "/")
    response.raise_for_status()
    body = response.json()
    version = str(body.get("version") or "").strip()
    if not VERSION_PATTERN.match(version):
        raise MigrationError(f"无法识别Qdrant版本：{version!r}")
    return version


def _version_tuple(version: str) -> tuple[int, int, int]:
    match = VERSION_PATTERN.match(version)
    if not match:
        raise MigrationError(f"无法解析Qdrant版本：{version}")
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


def collection_names(client: httpx.Client, qdrant_url: str) -> set[str]:
    result = _response_result(client.get(qdrant_url.rstrip("/") + "/collections"))
    return {str(item["name"]) for item in (result or {}).get("collections", [])}


def collection_info(client: httpx.Client, qdrant_url: str, collection: str) -> dict[str, Any]:
    result = _response_result(
        client.get(f"{qdrant_url.rstrip('/')}/collections/{collection}")
    )
    if not isinstance(result, dict):
        raise MigrationError(f"无法读取Collection信息：{collection}")
    return result


def exact_point_count(client: httpx.Client, qdrant_url: str, collection: str) -> int:
    """读取精确点数，避免 collection info 中近似计数造成错误阻断。"""
    result = _response_result(
        client.post(
            f"{qdrant_url.rstrip('/')}/collections/{collection}/points/count",
            json={"exact": True},
        )
    )
    if not isinstance(result, dict) or "count" not in result:
        raise MigrationError(f"无法读取 Collection 精确点数：{collection}")
    return int(result["count"])


def preflight(client: httpx.Client, args: argparse.Namespace) -> dict[str, Any]:
    version = qdrant_version(client, args.qdrant_url)
    names = collection_names(client, args.qdrant_url)
    if args.source not in names:
        raise MigrationError(f"源Collection不存在：{args.source}")
    info = collection_info(client, args.qdrant_url, args.source)
    points = exact_point_count(client, args.qdrant_url, args.source)
    if args.expected_points is not None and points != args.expected_points:
        raise MigrationError(
            f"源Collection points={points}，期望={args.expected_points}"
        )
    version_tuple = _version_tuple(version)
    native_sparse_supported = version_tuple >= MIN_NATIVE_SPARSE_VERSION
    idf_modifier_supported = version_tuple >= MIN_IDF_MODIFIER_VERSION
    report = {
        "status": "compatible" if native_sparse_supported and idf_modifier_supported else "incompatible",
        "qdrant_version": version,
        "minimum_native_sparse_version": ".".join(map(str, MIN_NATIVE_SPARSE_VERSION)),
        "native_sparse_supported": native_sparse_supported,
        "minimum_idf_modifier_version": ".".join(map(str, MIN_IDF_MODIFIER_VERSION)),
        "idf_modifier_supported": idf_modifier_supported,
        "source_collection": args.source,
        "source_points": points,
        "source_vectors": info.get("config", {}).get("params", {}).get("vectors"),
        "multilingual_fulltext": "feature_probe_during_target_build",
        "bm25_ranking": "client_precomputed_named_sparse_vector",
    }
    if not native_sparse_supported or not idf_modifier_supported:
        raise MigrationError(json.dumps(report, ensure_ascii=False))
    return report


def scroll_source(
    client: httpx.Client,
    qdrant_url: str,
    collection: str,
    *,
    batch_size: int,
) -> list[SourcePoint]:
    points: list[SourcePoint] = []
    offset: Any = None
    while True:
        request: dict[str, Any] = {
            "limit": batch_size,
            "with_payload": True,
            "with_vector": True,
        }
        if offset is not None:
            request["offset"] = offset
        response = client.post(
            f"{qdrant_url.rstrip('/')}/collections/{collection}/points/scroll",
            json=request,
        )
        result = _response_result(response) or {}
        for raw in result.get("points", []):
            payload = dict(raw.get("payload") or {})
            vector = raw.get("vector")
            if isinstance(vector, dict):
                dense = vector.get("dense") or vector.get("")
            else:
                dense = vector
            if not isinstance(dense, list) or not dense:
                raise MigrationError(f"Point {raw.get('id')} 缺少Dense向量")
            indices = [int(value) for value in payload.get("sparse_indices") or []]
            values = [float(value) for value in payload.get("sparse_values") or []]
            if len(indices) != len(values) or not indices:
                raise MigrationError(f"Point {raw.get('id')} 的旧Sparse Payload无效")
            if not str(payload.get("chunk_text") or "").strip():
                raise MigrationError(f"Point {raw.get('id')} 缺少chunk_text")
            points.append(
                SourcePoint(
                    point_id=raw["id"],
                    dense=[float(value) for value in dense],
                    bge_indices=indices,
                    bge_values=values,
                    payload=payload,
                )
            )
        offset = result.get("next_page_offset")
        if offset is None:
            break
    if not points:
        raise MigrationError("源Collection没有可迁移Point")
    return points


def build_bm25_corpus(
    texts: list[str],
    tokenizer: Callable[[str], list[str]],
    *,
    average_document_length: float = 256.0,
) -> Bm25Corpus:
    """计算BM25 TF向量；Qdrant IDF modifier负责动态语料频率。"""

    tokenized = [tokenizer(text) for text in texts]
    lengths = [len(tokens) for tokens in tokenized]
    observed_average_length = statistics.fmean(lengths) if lengths else 0.0
    if observed_average_length <= 0:
        raise MigrationError("BM25分词结果全部为空")
    vocabulary: set[str] = set()
    token_by_hash: dict[int, str] = {}
    collisions = 0
    for tokens in tokenized:
        vocabulary.update(tokens)
        for token in set(tokens):
            token_id = sparse_token_id(token)
            previous = token_by_hash.setdefault(token_id, token)
            if previous != token:
                collisions += 1
    if collisions:
        raise MigrationError(
            f"BM25 Token u32哈希发生 {collisions} 次碰撞；拒绝静默降低准确率"
        )

    vectors: list[tuple[list[int], list[float]]] = []
    for tokens in tokenized:
        vectors.append(
            bm25_document_sparse(
                tokens,
                average_document_length=average_document_length,
            )
        )
    return Bm25Corpus(
        vectors=vectors,
        average_document_length=average_document_length,
        vocabulary_size=len(vocabulary),
        hash_collisions=collisions,
    )


def create_target_collection(
    client: httpx.Client,
    args: argparse.Namespace,
    *,
    vector_dim: int,
) -> None:
    body = {
        "vectors": {
            VECTOR_NAMES["dense"]: {
                "size": vector_dim,
                "distance": "Cosine",
            }
        },
        "sparse_vectors": {
            VECTOR_NAMES["bge_sparse"]: {"index": {"on_disk": False}},
            VECTOR_NAMES["bm25_en"]: {
                "index": {"on_disk": False},
                "modifier": "idf",
            },
            VECTOR_NAMES["bm25_zh"]: {
                "index": {"on_disk": False},
                "modifier": "idf",
            },
        },
    }
    _response_result(
        client.put(
            f"{args.qdrant_url.rstrip('/')}/collections/{args.target}",
            json=body,
        )
    )


def create_payload_index(
    client: httpx.Client,
    args: argparse.Namespace,
    field_name: str,
    tokenizer: str,
) -> None:
    body = {
        "field_name": field_name,
        "field_schema": {
            "type": "text",
            "tokenizer": tokenizer,
            "lowercase": True,
            "min_token_len": 1,
            "max_token_len": 80,
        },
    }
    response = client.put(
        f"{args.qdrant_url.rstrip('/')}/collections/{args.target}/index",
        params={"wait": "true"},
        json=body,
    )
    if response.status_code == 409 or (
        response.is_error and "already exists" in response.text.lower()
    ):
        return
    _response_result(response)


def create_fulltext_indexes(client: httpx.Client, args: argparse.Namespace) -> str:
    create_payload_index(client, args, "fulltext_en", "word")
    try:
        create_payload_index(client, args, "fulltext_zh", "multilingual")
        return "qdrant_multilingual"
    except (httpx.HTTPStatusError, MigrationError):
        create_payload_index(client, args, "fulltext_zh_segmented", "word")
        return "jieba_presegmented_word_fallback"


def _batched(items: list[Any], size: int) -> list[list[Any]]:
    return [items[offset : offset + size] for offset in range(0, len(items), size)]


def migrate(client: httpx.Client, args: argparse.Namespace) -> dict[str, Any]:
    preflight_report = preflight(client, args)
    state_path = args.state.resolve()
    names = collection_names(client, args.qdrant_url)
    state: dict[str, Any] = {}
    if state_path.exists():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise MigrationError(f"迁移状态文件损坏：{state_path}") from error
    signature = {
        "format_version": FORMAT_VERSION,
        "qdrant_url": args.qdrant_url.rstrip("/"),
        "source": args.source,
        "target": args.target,
        "expected_points": args.expected_points,
    }
    if args.target in names:
        if not args.resume:
            raise MigrationError(
                f"目标Collection已存在：{args.target}；默认拒绝覆盖，请核对状态后使用--resume"
            )
        if state.get("signature") != signature:
            raise MigrationError("已有目标Collection与迁移状态签名不匹配")
    elif args.resume:
        raise MigrationError("指定了--resume，但目标Collection不存在")

    started = time.monotonic()
    points = scroll_source(
        client,
        args.qdrant_url,
        args.source,
        batch_size=args.scroll_batch_size,
    )
    if args.expected_points is not None and len(points) != args.expected_points:
        raise MigrationError(
            f"实际读取 {len(points)} 个Point，期望 {args.expected_points}"
        )
    vector_dim = len(points[0].dense)
    if any(len(point.dense) != vector_dim for point in points):
        raise MigrationError("源Collection Dense向量维度不一致")
    texts = [str(point.payload["chunk_text"]) for point in points]
    bm25_en = build_bm25_corpus(texts, english_tokens)
    bm25_zh = build_bm25_corpus(texts, chinese_tokens)

    if args.target not in names:
        create_target_collection(client, args, vector_dim=vector_dim)
        state = {
            "signature": signature,
            "created_at": datetime.now(UTC).isoformat(),
            "source_points": len(points),
            "upserted_points": 0,
        }
        _atomic_json(state_path, state)

    batches = _batched(list(range(len(points))), args.upsert_batch_size)
    for batch_number, indexes in enumerate(batches, 1):
        output_points: list[dict[str, Any]] = []
        for index in indexes:
            point = points[index]
            payload = dict(point.payload)
            if not args.keep_legacy_sparse_payload:
                payload.pop("sparse_indices", None)
                payload.pop("sparse_values", None)
            payload.update(
                {
                    "retrieval_schema_version": 2,
                    "fulltext_en": texts[index],
                    "fulltext_zh": texts[index],
                    "fulltext_zh_segmented": " ".join(chinese_tokens(texts[index])),
                }
            )
            en_indices, en_values = bm25_en.vectors[index]
            zh_indices, zh_values = bm25_zh.vectors[index]
            vectors: dict[str, Any] = {
                VECTOR_NAMES["dense"]: point.dense,
                VECTOR_NAMES["bge_sparse"]: {
                    "indices": point.bge_indices,
                    "values": point.bge_values,
                },
            }
            if en_indices:
                vectors[VECTOR_NAMES["bm25_en"]] = {
                    "indices": en_indices,
                    "values": en_values,
                }
            if zh_indices:
                vectors[VECTOR_NAMES["bm25_zh"]] = {
                    "indices": zh_indices,
                    "values": zh_values,
                }
            output_points.append(
                {
                    "id": point.point_id,
                    "vector": vectors,
                    "payload": payload,
                }
            )
        _response_result(
            client.put(
                f"{args.qdrant_url.rstrip('/')}/collections/{args.target}/points",
                params={"wait": "true"},
                json={"points": output_points},
            )
        )
        state["upserted_points"] = min(
            len(points), batch_number * args.upsert_batch_size
        )
        state["updated_at"] = datetime.now(UTC).isoformat()
        _atomic_json(state_path, state)
        print(
            f"[{batch_number}/{len(batches)}] upserted={state['upserted_points']}/{len(points)}",
            flush=True,
        )

    fulltext_mode = create_fulltext_indexes(client, args)
    target_points = exact_point_count(client, args.qdrant_url, args.target)
    if target_points != len(points):
        raise MigrationError(
            f"目标Collection points={target_points}，源数据={len(points)}"
        )
    manifest = {
        "format_version": FORMAT_VERSION,
        "status": "complete",
        "completed_at": datetime.now(UTC).isoformat(),
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "preflight": preflight_report,
        "source_collection": args.source,
        "target_collection": args.target,
        "points": len(points),
        "vector_dim": vector_dim,
        "vector_names": VECTOR_NAMES,
        "fulltext_mode": fulltext_mode,
        "bm25": {
            "k1": 1.2,
            "b": 0.75,
            "idf": "qdrant_dynamic_modifier",
            "english": {
                "tokenizer": "word",
                "average_document_length": bm25_en.average_document_length,
                "vocabulary_size": bm25_en.vocabulary_size,
            },
            "chinese": {
                "tokenizer": "jieba",
                "average_document_length": bm25_zh.average_document_length,
                "vocabulary_size": bm25_zh.vocabulary_size,
            },
        },
        "legacy_sparse_payload_kept": args.keep_legacy_sparse_payload,
        "cutover": {
            "required_env": f"RAG_COLLECTION={args.target}",
            "old_collection_untouched": True,
        },
    }
    _atomic_json(args.manifest.resolve(), manifest)
    state["status"] = "complete"
    state["manifest"] = str(args.manifest.resolve())
    _atomic_json(state_path, state)
    return manifest


def _shared_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--qdrant-url", default="http://db_qdrant:6333")
    parser.add_argument("--source", required=True)
    parser.add_argument("--expected-points", type=int)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Qdrant v2原生Sparse/BM25离线重建")
    subparsers = parser.add_subparsers(dest="command", required=True)
    preflight_parser = subparsers.add_parser("preflight")
    _shared_arguments(preflight_parser)

    migrate_parser = subparsers.add_parser("migrate")
    _shared_arguments(migrate_parser)
    migrate_parser.add_argument("--target", required=True)
    migrate_parser.add_argument("--state", type=Path, required=True)
    migrate_parser.add_argument("--manifest", type=Path, required=True)
    migrate_parser.add_argument("--scroll-batch-size", type=int, default=128)
    migrate_parser.add_argument("--upsert-batch-size", type=int, default=64)
    migrate_parser.add_argument("--resume", action="store_true")
    migrate_parser.add_argument("--keep-legacy-sparse-payload", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        with httpx.Client(timeout=None) as client:
            if args.command == "preflight":
                result = preflight(client, args)
            else:
                if args.scroll_batch_size <= 0 or args.upsert_batch_size <= 0:
                    raise MigrationError("batch size必须大于0")
                result = migrate(client, args)
    except (httpx.HTTPError, MigrationError) as error:
        raise SystemExit(f"[FAIL] {error}") from error
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
