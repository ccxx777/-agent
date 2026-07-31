"""劳动合同 A 级法律资料的法条切片、校验与独立 Collection 入库。

通用 ``IngestService`` 面向任意 Markdown/TXT，采用固定长度 Chunk；法律资料则
必须把“第几条”作为稳定证据定位单位。本模块只处理公开法律语料，绝不读取用户合同：

1. 从 ``normalized/a_level`` 的 JSON front matter 和 Markdown 标题解析章节、节、条；
2. 生成可复现的 article 与 article_chunk JSONL，并保存来源、版本和哈希；
3. 以显式 CLI 调用写入新的 Qdrant v2 Collection，不接入目录 Watcher，也不触碰通用
   ``rag_chunks`` 或离线评测 Collection。

生成 prepared artifact 不等于法律资料已经“激活”。来源与文本质量可以被机器校验，
但全国适用范围、授权状态和法律专业复核仍由治理流程决定。
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

FORMAT_VERSION = 1
DEFAULT_COLLECTION = "legal_labor_a_v1"
DEFAULT_MAX_CHARS = 1_200
DEFAULT_OVERLAP = 120
ARTICLE_PREFIX = r"第[一二三四五六七八九十百千万零〇0-9]+条"
ARTICLE_HEADING = re.compile(
    rf"^####\s+(?P<article_no>{ARTICLE_PREFIX})(?P<article_suffix>.*)$"
)
CHAPTER_HEADING = re.compile(r"^##\s+(?P<chapter>第[一二三四五六七八九十百千万零〇0-9]+章.*)$")
SECTION_HEADING = re.compile(r"^###\s+(?P<section>第[一二三四五六七八九十百千万零〇0-9]+节.*)$")
FRONT_MATTER = re.compile(r"\A---json\n(?P<payload>.*?)\n---", re.DOTALL)
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
PENDING_PREFIX = "PENDING_"
PROTECTED_COLLECTION_PREFIXES = ("rag_chunks", "watsonx_docsqa")
SOURCE_ATTESTATION = "MAINTAINER_ATTESTED_OFFICIAL_DOWNLOAD"


class LegalCorpusError(ValueError):
    """法律语料来源、切片或入库前置条件不满足时抛出。"""


@dataclass(frozen=True)
class SourceDocument:
    """通过本地完整性校验的一份 A 级法律资料。"""

    metadata: dict[str, Any]
    markdown_path: Path
    body_lines: tuple[str, ...]
    governance_blockers: tuple[str, ...]


@dataclass(frozen=True)
class ParsedArticle:
    """一条法律条文，或未对应具体条号的前言文字。"""

    article_ordinal: int
    article_key: str
    article_no: str | None
    article_label: str
    chapter: str | None
    section: str | None
    text: str
    citation_eligible: bool


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n"
            for row in rows
        ),
        encoding="utf-8",
    )
    temporary.replace(path)


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise LegalCorpusError(f"JSON 文件不存在：{path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise LegalCorpusError(f"JSON 文件格式无效：{path}") from error
    if not isinstance(value, dict):
        raise LegalCorpusError(f"JSON 根节点必须是对象：{path}")
    return value


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise LegalCorpusError(f"JSONL 文件不存在：{path}")
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as error:
            raise LegalCorpusError(f"{path} 第 {line_number} 行不是有效 JSON") from error
        if not isinstance(value, dict):
            raise LegalCorpusError(f"{path} 第 {line_number} 行必须是 JSON 对象")
        rows.append(value)
    if not rows:
        raise LegalCorpusError(f"JSONL 没有记录：{path}")
    return rows


def _resolve_inside(base_dir: Path, relative_path: str, *, field_name: str) -> Path:
    candidate = (base_dir / relative_path).resolve()
    try:
        candidate.relative_to(base_dir.resolve())
    except ValueError as error:
        raise LegalCorpusError(f"{field_name} 不能越出法律资料目录：{relative_path}") from error
    return candidate


def _trim_blank_lines(lines: list[str]) -> list[str]:
    start = 0
    end = len(lines)
    while start < end and not lines[start].strip():
        start += 1
    while end > start and not lines[end - 1].strip():
        end -= 1
    return lines[start:end]


def _document_body(markdown_path: Path) -> tuple[dict[str, Any], tuple[str, ...]]:
    text = markdown_path.read_text(encoding="utf-8")
    match = FRONT_MATTER.match(text)
    if not match:
        raise LegalCorpusError(f"Markdown 缺少 JSON front matter：{markdown_path}")
    try:
        header = json.loads(match.group("payload"))
    except json.JSONDecodeError as error:
        raise LegalCorpusError(f"Markdown front matter 格式无效：{markdown_path}") from error
    if not isinstance(header, dict):
        raise LegalCorpusError(f"Markdown front matter 必须是对象：{markdown_path}")

    lines = text[match.end() :].splitlines()
    try:
        body_index = lines.index("## 原文正文")
    except ValueError as error:
        raise LegalCorpusError(f"Markdown 缺少“原文正文”边界：{markdown_path}") from error
    return header, tuple(lines[body_index + 1 :])


def _pending_governance_fields(metadata: dict[str, Any]) -> tuple[str, ...]:
    fields = ("jurisdiction", "national_applicability", "license_status")
    return tuple(
        field
        for field in fields
        if str(metadata.get(field, "")).startswith(PENDING_PREFIX)
    )


def _validate_source_metadata(
    *, base_dir: Path, metadata: dict[str, Any]
) -> SourceDocument:
    doc_id = metadata.get("doc_id")
    title = metadata.get("title")
    if not isinstance(doc_id, str) or not doc_id:
        raise LegalCorpusError("source metadata 缺少 doc_id")
    if not isinstance(title, str) or not title:
        raise LegalCorpusError(f"{doc_id} 缺少 title")
    if metadata.get("source_level") != "A":
        raise LegalCorpusError(f"{doc_id} 不是 A 级法律资料")
    if metadata.get("target_collection") != DEFAULT_COLLECTION:
        raise LegalCorpusError(f"{doc_id} target_collection 不是 {DEFAULT_COLLECTION}")
    if metadata.get("amendment_or_repeal_status") != "有效":
        raise LegalCorpusError(f"{doc_id} 未标记为有效资料")
    if not DATE.match(str(metadata.get("effective_date", ""))):
        raise LegalCorpusError(f"{doc_id} 缺少可用的 effective_date")
    official_url = metadata.get("official_url")
    if not isinstance(official_url, str) or not official_url.startswith("https://"):
        raise LegalCorpusError(f"{doc_id} 缺少 HTTPS 官方链接")

    raw_file = metadata.get("raw_file")
    normalized_markdown = metadata.get("normalized_markdown")
    if not isinstance(raw_file, str) or not isinstance(normalized_markdown, str):
        raise LegalCorpusError(f"{doc_id} 缺少 raw_file 或 normalized_markdown")
    raw_path = _resolve_inside(base_dir, raw_file, field_name="raw_file")
    markdown_path = _resolve_inside(
        base_dir, normalized_markdown, field_name="normalized_markdown"
    )
    if _sha256(raw_path) != metadata.get("raw_sha256"):
        raise LegalCorpusError(f"{doc_id} 原始 Word SHA-256 与 metadata 不一致")
    if _sha256(markdown_path) != metadata.get("normalized_markdown_sha256"):
        raise LegalCorpusError(f"{doc_id} Markdown SHA-256 与 metadata 不一致")

    front_matter, body_lines = _document_body(markdown_path)
    for field in ("doc_id", "title", "raw_sha256", "effective_date", "official_url"):
        if front_matter.get(field) != metadata.get(field):
            raise LegalCorpusError(f"{doc_id} Markdown front matter 的 {field} 与 metadata 不一致")
    return SourceDocument(
        metadata=metadata,
        markdown_path=markdown_path,
        body_lines=body_lines,
        governance_blockers=_pending_governance_fields(metadata),
    )


def load_source_documents(base_dir: Path) -> list[SourceDocument]:
    """读取并校验上一步生成的文档级 metadata 与 Markdown。"""

    metadata_path = base_dir / "metadata" / "a_level_documents.jsonl"
    metadata_rows = _read_jsonl(metadata_path)
    source_documents: list[SourceDocument] = []
    seen_doc_ids: set[str] = set()
    for metadata in metadata_rows:
        doc_id = metadata.get("doc_id")
        if doc_id in seen_doc_ids:
            raise LegalCorpusError(f"metadata 存在重复 doc_id：{doc_id}")
        seen_doc_ids.add(doc_id)
        source_documents.append(
            _validate_source_metadata(base_dir=base_dir, metadata=metadata)
        )
    return source_documents


def parse_articles(source: SourceDocument) -> list[ParsedArticle]:
    """从标准化 Markdown 解析章节/节/条，不做模型推断或法律解释。"""

    chapter: str | None = None
    section: str | None = None
    articles: list[ParsedArticle] = []
    current_no: str | None = None
    current_label = ""
    current_chapter: str | None = None
    current_section: str | None = None
    current_lines: list[str] = []
    preamble_lines: list[str] = []
    ordinal = 0

    def flush_current() -> None:
        nonlocal current_no, current_label, current_chapter, current_section
        nonlocal current_lines, ordinal
        lines = _trim_blank_lines(current_lines)
        if current_no is None or not lines:
            current_no = None
            current_label = ""
            current_chapter = None
            current_section = None
            current_lines = []
            return
        ordinal += 1
        articles.append(
            ParsedArticle(
                article_ordinal=ordinal,
                article_key=f"article_{ordinal:03d}",
                article_no=current_no,
                article_label=current_label,
                chapter=current_chapter,
                section=current_section,
                text="\n".join(lines),
                citation_eligible=True,
            )
        )
        current_no = None
        current_label = ""
        current_chapter = None
        current_section = None
        current_lines = []

    for line in source.body_lines:
        chapter_match = CHAPTER_HEADING.match(line)
        if chapter_match:
            flush_current()
            chapter = chapter_match.group("chapter").strip()
            section = None
            continue
        section_match = SECTION_HEADING.match(line)
        if section_match:
            flush_current()
            section = section_match.group("section").strip()
            continue
        article_match = ARTICLE_HEADING.match(line)
        if article_match:
            flush_current()
            current_no = article_match.group("article_no")
            suffix = article_match.group("article_suffix").strip()
            current_label = f"{current_no} {suffix}".rstrip()
            current_chapter = chapter
            current_section = section
            current_lines = [current_label]
            continue

        if current_no is None:
            preamble_lines.append(line)
        else:
            current_lines.append(line)
    flush_current()

    preamble = _trim_blank_lines(preamble_lines)
    if preamble:
        articles.insert(
            0,
            ParsedArticle(
                article_ordinal=0,
                article_key="preamble",
                article_no=None,
                article_label="前言",
                chapter=None,
                section=None,
                text="\n".join(preamble),
                citation_eligible=False,
            ),
        )
    if not any(article.citation_eligible for article in articles):
        doc_id = source.metadata["doc_id"]
        raise LegalCorpusError(f"{doc_id} 未解析到任何“第…条”标题")

    numbers = [article.article_no for article in articles if article.article_no]
    if len(numbers) != len(set(numbers)):
        raise LegalCorpusError(f"{source.metadata['doc_id']} 存在重复条号，拒绝静默切片")
    return articles


def _split_article_text(text: str, *, max_chars: int, overlap: int) -> list[tuple[int, int, str]]:
    if max_chars <= 0:
        raise LegalCorpusError("max_chars 必须大于 0")
    if overlap < 0 or overlap >= max_chars:
        raise LegalCorpusError("overlap 必须大于等于 0 且小于 max_chars")
    if not text:
        raise LegalCorpusError("条文正文不能为空")

    chunks: list[tuple[int, int, str]] = []
    start = 0
    length = len(text)
    preferred = "\n。；："
    while start < length:
        maximum = min(length, start + max_chars)
        if maximum == length:
            end = length
        else:
            floor = start + max(1, max_chars // 3)
            boundary = max((text.rfind(mark, floor, maximum) for mark in preferred), default=-1)
            end = boundary + 1 if boundary >= floor else maximum
        excerpt = text[start:end]
        if not excerpt:
            raise LegalCorpusError("条文切片产生空 Chunk")
        chunks.append((start, end, excerpt))
        if end == length:
            break
        next_start = end - overlap
        if next_start <= start:
            next_start = end
        start = next_start
    return chunks


def _citation_label(*, title: str, article: ParsedArticle) -> str:
    components = [f"《{title}》"]
    if article.chapter:
        components.append(article.chapter)
    if article.section:
        components.append(article.section)
    components.append(article.article_no or article.article_label)
    return " ".join(components)


def _chunk_embedding_text(*, title: str, article: ParsedArticle, excerpt: str) -> str:
    context = [f"法源：{title}"]
    if article.chapter:
        context.append(article.chapter)
    if article.section:
        context.append(article.section)
    if article.article_no:
        context.append(article.article_no)
    return "\n".join(context + [excerpt])


def _build_records(
    source_documents: list[SourceDocument], *, max_chars: int, overlap: int
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    article_rows: list[dict[str, Any]] = []
    chunk_rows: list[dict[str, Any]] = []
    seen_point_ids: set[str] = set()
    for source in source_documents:
        metadata = source.metadata
        for article in parse_articles(source):
            article_sha256 = _sha256_text(article.text)
            chunks = _split_article_text(article.text, max_chars=max_chars, overlap=overlap)
            article_id = f"{metadata['doc_id']}/{article.article_key}"
            citation_label = _citation_label(title=metadata["title"], article=article)
            article_rows.append(
                {
                    "format_version": FORMAT_VERSION,
                    "record_type": "legal_article",
                    "article_id": article_id,
                    "doc_id": metadata["doc_id"],
                    "title": metadata["title"],
                    "source_level": metadata["source_level"],
                    "target_collection": metadata["target_collection"],
                    "document_type": metadata["document_type"],
                    "issuing_authority": metadata["issuing_authority"],
                    "effective_date": metadata["effective_date"],
                    "amendment_or_repeal_status": metadata["amendment_or_repeal_status"],
                    "official_url": metadata["official_url"],
                    "official_source_id": metadata.get("official_source_id"),
                    "raw_file": metadata["raw_file"],
                    "raw_sha256": metadata["raw_sha256"],
                    "normalized_markdown": metadata["normalized_markdown"],
                    "normalized_markdown_sha256": metadata["normalized_markdown_sha256"],
                    "article_ordinal": article.article_ordinal,
                    "article_key": article.article_key,
                    "article_no": article.article_no,
                    "article_label": article.article_label,
                    "chapter": article.chapter,
                    "section": article.section,
                    "citation_label": citation_label,
                    "citation_eligible": article.citation_eligible,
                    "article_text": article.text,
                    "article_text_sha256": article_sha256,
                    "article_character_count": len(article.text),
                    "chunk_count": len(chunks),
                    "source_file_origin": SOURCE_ATTESTATION,
                    "legal_activation_status": "PENDING_LEGAL_REVIEW",
                }
            )
            for chunk_index, (start, end, excerpt) in enumerate(chunks):
                chunk_id = f"{article_id}/chunk_{chunk_index:03d}"
                point_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"legal-corpus/{chunk_id}"))
                if point_id in seen_point_ids:
                    raise LegalCorpusError(f"Point ID 冲突：{point_id}")
                seen_point_ids.add(point_id)
                chunk_text = _chunk_embedding_text(
                    title=metadata["title"], article=article, excerpt=excerpt
                )
                chunk_rows.append(
                    {
                        "format_version": FORMAT_VERSION,
                        "record_type": "legal_article_chunk",
                        "point_id": point_id,
                        "chunk_id": chunk_id,
                        "article_id": article_id,
                        "doc_id": metadata["doc_id"],
                        "title": metadata["title"],
                        "source_level": metadata["source_level"],
                        "target_collection": metadata["target_collection"],
                        "document_type": metadata["document_type"],
                        "issuing_authority": metadata["issuing_authority"],
                        "jurisdiction": metadata["jurisdiction"],
                        "national_applicability": metadata["national_applicability"],
                        "publication_date": metadata["publication_date"],
                        "effective_date": metadata["effective_date"],
                        "amendment_or_repeal_status": metadata["amendment_or_repeal_status"],
                        "official_url": metadata["official_url"],
                        "official_source_id": metadata.get("official_source_id"),
                        "raw_file": metadata["raw_file"],
                        "raw_sha256": metadata["raw_sha256"],
                        "normalized_markdown": metadata["normalized_markdown"],
                        "normalized_markdown_sha256": metadata["normalized_markdown_sha256"],
                        "article_ordinal": article.article_ordinal,
                        "article_key": article.article_key,
                        "article_no": article.article_no,
                        "article_label": article.article_label,
                        "chapter": article.chapter,
                        "section": article.section,
                        "citation_label": citation_label,
                        "citation_eligible": article.citation_eligible,
                        "article_text": article.text,
                        "article_text_sha256": article_sha256,
                        "article_character_count": len(article.text),
                        "chunk_index": chunk_index,
                        "chunk_count": len(chunks),
                        "article_start": start,
                        "article_end": end,
                        "chunk_text": chunk_text,
                        "excerpt_text": excerpt,
                        "excerpt_sha256": _sha256_text(excerpt),
                        "source_file_origin": SOURCE_ATTESTATION,
                        "legal_activation_status": "PENDING_LEGAL_REVIEW",
                    }
                )
    return article_rows, chunk_rows


def _prepared_paths(output_dir: Path) -> dict[str, Path]:
    return {
        "articles": output_dir / "articles.jsonl",
        "article_chunks": output_dir / "article_chunks.jsonl",
        "manifest": output_dir / "manifest.json",
        "validation": output_dir / "validation.json",
    }


def prepare_legal_corpus(
    *,
    base_dir: Path,
    output_dir: Path | None = None,
    max_chars: int = DEFAULT_MAX_CHARS,
    overlap: int = DEFAULT_OVERLAP,
    overwrite: bool = False,
) -> dict[str, Any]:
    """把文档级法律资料转为条级 prepared artifact。"""

    resolved_base = base_dir.resolve()
    resolved_output = (output_dir or resolved_base / "prepared" / "a_level").resolve()
    paths = _prepared_paths(resolved_output)
    if not overwrite:
        existing = [path for path in paths.values() if path.exists()]
        if existing:
            joined = ", ".join(str(path) for path in existing)
            raise LegalCorpusError(f"prepared artifact 已存在；如需重建请添加 --overwrite：{joined}")

    source_documents = load_source_documents(resolved_base)
    article_rows, chunk_rows = _build_records(
        source_documents, max_chars=max_chars, overlap=overlap
    )
    if not article_rows or not chunk_rows:
        raise LegalCorpusError("未生成任何法条记录或入库 Chunk")

    _write_jsonl(paths["articles"], article_rows)
    _write_jsonl(paths["article_chunks"], chunk_rows)
    input_metadata = resolved_base / "metadata" / "a_level_documents.jsonl"
    governance_blockers = sorted(
        {field for source in source_documents for field in source.governance_blockers}
    )
    manifest = {
        "format_version": FORMAT_VERSION,
        "corpus": "labor_contract_a_level",
        "target_collection": DEFAULT_COLLECTION,
        "status": "PREPARED_PENDING_GOVERNANCE_ACTIVATION",
        "prepared_at": datetime.now(UTC).isoformat(),
        "source_attestation": {
            "status": SOURCE_ATTESTATION,
            "note": (
                "维护人确认 raw/a_level 下的 WPS/DOCX 文件从官方页面下载；"
                "该声明不替代法律专业复核或生产激活。"
            ),
        },
        "input": {
            "metadata": str(input_metadata.relative_to(resolved_base).as_posix()),
            "metadata_sha256": _sha256(input_metadata),
            "documents": len(source_documents),
        },
        "chunking": {"max_chars": max_chars, "overlap": overlap, "unit": "article"},
        "counts": {
            "documents": len(source_documents),
            "articles": len(article_rows),
            "article_chunks": len(chunk_rows),
            "citation_eligible_articles": sum(
                1 for article in article_rows if article["citation_eligible"]
            ),
        },
        "governance": {
            "legal_activation_status": "PENDING_LEGAL_REVIEW",
            "pending_document_fields": governance_blockers,
            "blocked_actions": [
                "Do not switch LEGAL_A_COLLECTION to this corpus before legal review.",
                "Do not use citation_eligible=false preamble records as a formal legal basis.",
                "Do not write this corpus into rag_chunks or watsonxDocsQA collections.",
            ],
        },
        "files": {
            "articles": {
                "path": paths["articles"].name,
                "sha256": _sha256(paths["articles"]),
            },
            "article_chunks": {
                "path": paths["article_chunks"].name,
                "sha256": _sha256(paths["article_chunks"]),
            },
        },
    }
    _atomic_json(paths["manifest"], manifest)
    validation = validate_prepared_corpus(resolved_output)
    _atomic_json(paths["validation"], validation)
    manifest["files"]["validation"] = {
        "path": paths["validation"].name,
        "sha256": _sha256(paths["validation"]),
    }
    manifest["validation"] = {
        "status": validation["status"],
        "validated_at": validation["validated_at"],
    }
    _atomic_json(paths["manifest"], manifest)
    return {
        "status": "prepared",
        "base": str(resolved_base),
        "output": str(resolved_output),
        "documents": len(source_documents),
        "articles": len(article_rows),
        "article_chunks": len(chunk_rows),
        "manifest": str(paths["manifest"]),
    }


def validate_prepared_corpus(prepared_dir: Path) -> dict[str, Any]:
    """校验 prepared artifact 的数量、哈希、条级定位与 Chunk 边界。"""

    resolved_prepared = prepared_dir.resolve()
    paths = _prepared_paths(resolved_prepared)
    manifest = _read_json(paths["manifest"])
    files = manifest.get("files")
    if not isinstance(files, dict):
        raise LegalCorpusError("prepared manifest 缺少 files")
    file_names = ["articles", "article_chunks"]
    if "validation" in files:
        file_names.append("validation")
    for name in file_names:
        file_info = files.get(name)
        if not isinstance(file_info, dict):
            raise LegalCorpusError(f"prepared manifest 缺少 {name} 文件信息")
        expected_hash = file_info.get("sha256")
        if expected_hash != _sha256(paths[name]):
            raise LegalCorpusError(f"prepared {name} SHA-256 不一致")

    article_rows = _read_jsonl(paths["articles"])
    chunk_rows = _read_jsonl(paths["article_chunks"])
    counts = manifest.get("counts")
    if not isinstance(counts, dict):
        raise LegalCorpusError("prepared manifest 缺少 counts")
    if counts.get("articles") != len(article_rows):
        raise LegalCorpusError("prepared article 数量与 manifest 不一致")
    if counts.get("article_chunks") != len(chunk_rows):
        raise LegalCorpusError("prepared article_chunk 数量与 manifest 不一致")

    article_by_id: dict[str, dict[str, Any]] = {}
    for article in article_rows:
        article_id = article.get("article_id")
        if not isinstance(article_id, str) or not article_id:
            raise LegalCorpusError("article 记录缺少 article_id")
        if article_id in article_by_id:
            raise LegalCorpusError(f"article_id 重复：{article_id}")
        article_text = article.get("article_text")
        if not isinstance(article_text, str) or not article_text:
            raise LegalCorpusError(f"{article_id} 缺少 article_text")
        if article.get("article_text_sha256") != _sha256_text(article_text):
            raise LegalCorpusError(f"{article_id} article_text SHA-256 不一致")
        article_by_id[article_id] = article

    grouped_chunks: dict[str, list[dict[str, Any]]] = {}
    seen_points: set[str] = set()
    for chunk in chunk_rows:
        point_id = chunk.get("point_id")
        article_id = chunk.get("article_id")
        if not isinstance(point_id, str) or not point_id:
            raise LegalCorpusError("article_chunk 缺少 point_id")
        if point_id in seen_points:
            raise LegalCorpusError(f"point_id 重复：{point_id}")
        seen_points.add(point_id)
        if not isinstance(article_id, str) or article_id not in article_by_id:
            raise LegalCorpusError(f"article_chunk 引用了不存在的 article_id：{article_id}")
        article = article_by_id[article_id]
        if chunk.get("article_text_sha256") != article.get("article_text_sha256"):
            raise LegalCorpusError(f"{chunk.get('chunk_id')} 的 article_text 来源不一致")
        start = chunk.get("article_start")
        end = chunk.get("article_end")
        if not isinstance(start, int) or not isinstance(end, int) or not 0 <= start < end:
            raise LegalCorpusError(f"{chunk.get('chunk_id')} 的条文偏移量无效")
        article_text = str(article["article_text"])
        if end > len(article_text):
            raise LegalCorpusError(f"{chunk.get('chunk_id')} 的条文偏移量越界")
        excerpt = chunk.get("excerpt_text")
        if excerpt != article_text[start:end]:
            raise LegalCorpusError(f"{chunk.get('chunk_id')} 的 excerpt 与条文原文不一致")
        if chunk.get("excerpt_sha256") != _sha256_text(str(excerpt)):
            raise LegalCorpusError(f"{chunk.get('chunk_id')} 的 excerpt SHA-256 不一致")
        if not str(chunk.get("chunk_text") or "").strip():
            raise LegalCorpusError(f"{chunk.get('chunk_id')} 缺少 embedding 文本")
        grouped_chunks.setdefault(article_id, []).append(chunk)

    for article_id, article in article_by_id.items():
        chunks = sorted(grouped_chunks.get(article_id, []), key=lambda item: item["chunk_index"])
        if not chunks:
            raise LegalCorpusError(f"{article_id} 没有任何 article_chunk")
        expected_count = article.get("chunk_count")
        if expected_count != len(chunks):
            raise LegalCorpusError(f"{article_id} 的 chunk_count 不一致")
        indexes = [chunk.get("chunk_index") for chunk in chunks]
        if indexes != list(range(len(chunks))):
            raise LegalCorpusError(f"{article_id} 的 chunk_index 不连续")
        if any(chunk.get("chunk_count") != expected_count for chunk in chunks):
            raise LegalCorpusError(f"{article_id} 的 chunk_count 字段不一致")

    return {
        "format_version": FORMAT_VERSION,
        "status": "valid",
        "validated_at": datetime.now(UTC).isoformat(),
        "prepared_dir": str(resolved_prepared),
        "documents": counts.get("documents"),
        "articles": len(article_rows),
        "article_chunks": len(chunk_rows),
        "citation_eligible_articles": sum(
            1 for article in article_rows if article.get("citation_eligible")
        ),
        "preamble_articles": sum(
            1 for article in article_rows if not article.get("citation_eligible")
        ),
    }


def _require_ingestion_runtime() -> tuple[Any, Any, Any, Any, Any]:
    """延迟导入服务依赖，让本地切片/校验无需安装 Qdrant 客户端。"""

    try:
        import httpx

        from data_worker.ingest.embedder import DocumentEmbedder
        from data_worker.ingest.writer import (
            bm25_document_sparse,
            chinese_tokens,
            english_tokens,
        )
    except ModuleNotFoundError as error:
        raise LegalCorpusError(
            "执行服务器入库需要 data_worker/requirements.txt 中的依赖"
        ) from error
    return httpx, DocumentEmbedder, bm25_document_sparse, chinese_tokens, english_tokens


def _qdrant_result(response: Any) -> Any:
    if response.is_error:
        detail = str(response.text).strip()[:500]
        raise LegalCorpusError(f"Qdrant HTTP {response.status_code}：{detail}")
    try:
        payload = response.json()
    except (TypeError, ValueError) as error:
        raise LegalCorpusError("Qdrant 返回了无效 JSON") from error
    if payload.get("status") not in (None, "ok"):
        raise LegalCorpusError(f"Qdrant 返回非 ok 状态：{payload.get('status')}")
    return payload.get("result")


def _qdrant_version(client: Any, qdrant_url: str) -> tuple[int, int, int]:
    result = client.get(qdrant_url.rstrip("/") + "/")
    result.raise_for_status()
    raw = str(result.json().get("version") or "")
    match = re.match(r"^(\d+)\.(\d+)\.(\d+)", raw)
    if not match:
        raise LegalCorpusError(f"无法识别 Qdrant 版本：{raw!r}")
    version = tuple(int(part) for part in match.groups())
    if version < (1, 10, 0):
        raise LegalCorpusError(f"Qdrant {raw} 不支持本语料要求的 v2 sparse schema")
    return version  # type: ignore[return-value]


def _collection_names(client: Any, qdrant_url: str) -> set[str]:
    result = _qdrant_result(client.get(qdrant_url.rstrip("/") + "/collections"))
    return {str(item["name"]) for item in (result or {}).get("collections", [])}


def _is_protected_collection(collection: str) -> bool:
    return collection.startswith(PROTECTED_COLLECTION_PREFIXES)


def _ensure_legal_collection(
    client: Any,
    *,
    qdrant_url: str,
    collection: str,
    vector_dim: int,
    resume: bool,
) -> bool:
    """返回目标 Collection 是否为本次调用新建；已有集合仅允许显式 resume。"""

    if _is_protected_collection(collection):
        raise LegalCorpusError(f"禁止向受保护 Collection 写入法律资料：{collection}")
    names = _collection_names(client, qdrant_url)
    if collection in names:
        if not resume:
            raise LegalCorpusError(
                f"目标 Collection 已存在：{collection}；如确认是同一批次请使用 --resume"
            )
        info = _qdrant_result(
            client.get(f"{qdrant_url.rstrip('/')}/collections/{collection}")
        )
        params = (info or {}).get("config", {}).get("params", {})
        dense = params.get("vectors") or {}
        sparse = params.get("sparse_vectors") or {}
        required_sparse = {"bge_m3_sparse", "bm25_word", "bm25_zh"}
        if "dense" not in dense or not required_sparse.issubset(sparse):
            raise LegalCorpusError(f"已有 Collection {collection} 不是兼容的 v2 schema")
        return False

    body = {
        "vectors": {"dense": {"size": vector_dim, "distance": "Cosine"}},
        "sparse_vectors": {
            "bge_m3_sparse": {"index": {"on_disk": False}},
            "bm25_word": {"index": {"on_disk": False}, "modifier": "idf"},
            "bm25_zh": {"index": {"on_disk": False}, "modifier": "idf"},
        },
    }
    _qdrant_result(
        client.put(f"{qdrant_url.rstrip('/')}/collections/{collection}", json=body)
    )
    return True


def _create_text_index(client: Any, *, qdrant_url: str, collection: str, field: str, tokenizer: str) -> None:
    response = client.put(
        f"{qdrant_url.rstrip('/')}/collections/{collection}/index",
        params={"wait": "true"},
        json={
            "field_name": field,
            "field_schema": {
                "type": "text",
                "tokenizer": tokenizer,
                "lowercase": True,
                "min_token_len": 1,
                "max_token_len": 80,
            },
        },
    )
    if response.status_code == 409 or (
        response.is_error and "already exists" in response.text.lower()
    ):
        return
    _qdrant_result(response)


def _create_fulltext_indexes(client: Any, *, qdrant_url: str, collection: str) -> str:
    _create_text_index(
        client, qdrant_url=qdrant_url, collection=collection, field="fulltext_en", tokenizer="word"
    )
    try:
        _create_text_index(
            client,
            qdrant_url=qdrant_url,
            collection=collection,
            field="fulltext_zh",
            tokenizer="multilingual",
        )
        return "qdrant_multilingual"
    except LegalCorpusError:
        _create_text_index(
            client,
            qdrant_url=qdrant_url,
            collection=collection,
            field="fulltext_zh_segmented",
            tokenizer="word",
        )
        return "jieba_presegmented_word_fallback"


def _exact_point_count(client: Any, *, qdrant_url: str, collection: str) -> int:
    result = _qdrant_result(
        client.post(
            f"{qdrant_url.rstrip('/')}/collections/{collection}/points/count",
            json={"exact": True},
        )
    )
    if not isinstance(result, dict) or "count" not in result:
        raise LegalCorpusError("无法读取 Qdrant 精确 Point 数")
    return int(result["count"])


def _load_ingestion_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return _read_json(path)


def _build_qdrant_point(
    *,
    record: dict[str, Any],
    dense: list[float],
    sparse: dict[Any, Any],
    bm25_document_sparse: Any,
    chinese_tokens: Any,
    english_tokens: Any,
) -> dict[str, Any]:
    sparse_items = sorted((int(key), float(value)) for key, value in sparse.items())
    if not sparse_items:
        raise LegalCorpusError(f"{record['chunk_id']} 缺少 BGE-M3 sparse embedding")
    chunk_text = str(record["chunk_text"])
    en_indices, en_values = bm25_document_sparse(english_tokens(chunk_text))
    zh_tokens = chinese_tokens(chunk_text)
    zh_indices, zh_values = bm25_document_sparse(zh_tokens)
    payload = {
        "doc_id": record["doc_id"],
        "chunk_id": record["chunk_id"],
        "chunk_text": chunk_text,
        "title": record["title"],
        "source": record["official_url"],
        "source_file": record["raw_file"],
        "sha256": record["raw_sha256"],
        "user_id": "public_legal",
        "source_level": record["source_level"],
        "document_type": record["document_type"],
        "issuing_authority": record["issuing_authority"],
        "jurisdiction": record["jurisdiction"],
        "national_applicability": record["national_applicability"],
        "publication_date": record["publication_date"],
        "effective_date": record["effective_date"],
        "amendment_or_repeal_status": record["amendment_or_repeal_status"],
        "official_source_id": record.get("official_source_id"),
        "article_id": record["article_id"],
        "article_no": record["article_no"],
        "article_label": record["article_label"],
        "article_ordinal": record["article_ordinal"],
        "chapter": record["chapter"],
        "section": record["section"],
        "citation_label": record["citation_label"],
        "citation_eligible": record["citation_eligible"],
        "article_text": record["article_text"],
        "article_text_sha256": record["article_text_sha256"],
        "article_start": record["article_start"],
        "article_end": record["article_end"],
        "excerpt_text": record["excerpt_text"],
        "excerpt_sha256": record["excerpt_sha256"],
        "source_file_origin": record["source_file_origin"],
        "legal_activation_status": record["legal_activation_status"],
        "retrieval_schema_version": 2,
        "fulltext_en": chunk_text,
        "fulltext_zh": chunk_text,
        "fulltext_zh_segmented": " ".join(zh_tokens),
    }
    vectors: dict[str, Any] = {
        "dense": dense,
        "bge_m3_sparse": {
            "indices": [item[0] for item in sparse_items],
            "values": [item[1] for item in sparse_items],
        },
    }
    if en_indices:
        vectors["bm25_word"] = {"indices": en_indices, "values": en_values}
    if zh_indices:
        vectors["bm25_zh"] = {"indices": zh_indices, "values": zh_values}
    return {"id": record["point_id"], "vector": vectors, "payload": payload}


def ingest_prepared_corpus(
    *,
    prepared_dir: Path,
    qdrant_url: str,
    embed_url: str,
    collection: str = DEFAULT_COLLECTION,
    upsert_batch_size: int = 32,
    resume: bool = False,
    allow_pending_governance: bool = False,
    dry_run: bool = False,
    state_path: Path | None = None,
) -> dict[str, Any]:
    """向专用 Collection 写入 prepared legal article chunks，默认拒绝未激活资料。"""

    if upsert_batch_size <= 0:
        raise LegalCorpusError("upsert_batch_size 必须大于 0")
    validation = validate_prepared_corpus(prepared_dir)
    resolved_prepared = prepared_dir.resolve()
    manifest_path = resolved_prepared / "manifest.json"
    manifest = _read_json(manifest_path)
    if collection != manifest.get("target_collection"):
        raise LegalCorpusError(
            f"Collection 不匹配：prepared={manifest.get('target_collection')}，requested={collection}"
        )
    governance = manifest.get("governance") or {}
    if governance.get("legal_activation_status") != "ACTIVE" and not allow_pending_governance:
        raise LegalCorpusError(
            "法律资料尚未激活；只允许使用 --allow-pending-governance 写入隔离测试 Collection"
        )
    if _is_protected_collection(collection):
        raise LegalCorpusError(f"禁止向受保护 Collection 写入法律资料：{collection}")

    chunk_path = resolved_prepared / "article_chunks.jsonl"
    chunks = _read_jsonl(chunk_path)
    if dry_run:
        return {
            "status": "dry_run_valid",
            "collection": collection,
            "points": len(chunks),
            "validation": validation,
            "governance": governance,
        }

    httpx, document_embedder_class, bm25_document_sparse, chinese_tokens, english_tokens = (
        _require_ingestion_runtime()
    )
    resolved_state = (state_path or resolved_prepared / f"ingestion_state_{collection}.json").resolve()
    signature = {
        "format_version": FORMAT_VERSION,
        "collection": collection,
        "prepared_manifest_sha256": _sha256(manifest_path),
        "article_chunks_sha256": _sha256(chunk_path),
        "points": len(chunks),
    }
    state = _load_ingestion_state(resolved_state)
    if state:
        if state.get("signature") != signature:
            raise LegalCorpusError("已有入库状态与当前 prepared artifact 不匹配")
        if not resume:
            raise LegalCorpusError("已存在同批次入库状态；如需继续请使用 --resume")
    elif resume:
        raise LegalCorpusError("指定了 --resume，但找不到对应入库状态")

    started = time.monotonic()
    with httpx.Client(timeout=120.0) as client:
        _qdrant_version(client, qdrant_url)
        created = _ensure_legal_collection(
            client,
            qdrant_url=qdrant_url,
            collection=collection,
            vector_dim=1_024,
            resume=resume,
        )
        if not state:
            state = {
                "format_version": FORMAT_VERSION,
                "status": "in_progress",
                "created_at": datetime.now(UTC).isoformat(),
                "signature": signature,
                "collection_created": created,
                "upserted_points": 0,
            }
            _atomic_json(resolved_state, state)

        start_index = int(state.get("upserted_points", 0))
        if not 0 <= start_index <= len(chunks):
            raise LegalCorpusError("入库状态的 upserted_points 越界")
        remaining = chunks[start_index:]
        if remaining:
            embedder = document_embedder_class(endpoint=embed_url, batch_size=2)
            dense_vectors, sparse_vectors = embedder.embed(
                [str(record["chunk_text"]) for record in remaining]
            )
            if len(dense_vectors) != len(remaining) or len(sparse_vectors) != len(remaining):
                raise LegalCorpusError("Embedding Service 返回的向量数量与条文 Chunk 不一致")
            for batch_start in range(0, len(remaining), upsert_batch_size):
                batch_records = remaining[batch_start : batch_start + upsert_batch_size]
                batch_dense = dense_vectors[batch_start : batch_start + upsert_batch_size]
                batch_sparse = sparse_vectors[batch_start : batch_start + upsert_batch_size]
                points = [
                    _build_qdrant_point(
                        record=record,
                        dense=list(dense),
                        sparse=dict(sparse),
                        bm25_document_sparse=bm25_document_sparse,
                        chinese_tokens=chinese_tokens,
                        english_tokens=english_tokens,
                    )
                    for record, dense, sparse in zip(batch_records, batch_dense, batch_sparse)
                ]
                _qdrant_result(
                    client.put(
                        f"{qdrant_url.rstrip('/')}/collections/{collection}/points",
                        params={"wait": "true"},
                        json={"points": points},
                    )
                )
                state["upserted_points"] = start_index + batch_start + len(batch_records)
                state["updated_at"] = datetime.now(UTC).isoformat()
                _atomic_json(resolved_state, state)

        fulltext_mode = _create_fulltext_indexes(
            client, qdrant_url=qdrant_url, collection=collection
        )
        point_count = _exact_point_count(client, qdrant_url=qdrant_url, collection=collection)
        if point_count != len(chunks):
            raise LegalCorpusError(
                f"Qdrant Point 数不一致：collection={point_count}，prepared={len(chunks)}"
            )

    state.update(
        {
            "status": "complete",
            "completed_at": datetime.now(UTC).isoformat(),
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "fulltext_mode": fulltext_mode,
            "point_count": point_count,
        }
    )
    _atomic_json(resolved_state, state)
    return {
        "status": "complete",
        "collection": collection,
        "points": point_count,
        "fulltext_mode": fulltext_mode,
        "state": str(resolved_state),
        "elapsed_seconds": state["elapsed_seconds"],
        "governance": governance,
    }
