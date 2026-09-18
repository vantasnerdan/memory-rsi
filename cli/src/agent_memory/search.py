"""BM25 search over memory entry sections.

Section-level indexing with file-level frontmatter filtering.
Each ## section is a BM25 document; frontmatter fields are used
for pre-ranking filters (category, confidence, author, etc.).

Default search uses BM25F field-weighted scoring:
  - title (## heading): weight x4
  - description (frontmatter + section first-line): weight x3
  - first_line (section description): weight x2
  - body (remaining content): weight x1

Supports optional SQLite index cache for performance at scale.
When cache is available, documents are loaded from cache instead
of re-reading all files from disk.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from agent_memory.bm25 import BM25, FIELD_WEIGHTS
from agent_memory.cache import CachedSection, IndexCache, resolve_cache_path
from agent_memory.parser import Frontmatter, parse_frontmatter, parse_sections
from agent_memory.snippet import extract_snippet
from agent_memory.sources import SourceEntry, collect_all_source_files
from agent_memory.tokenizer import tokenize

logger = logging.getLogger(__name__)


@dataclass
class SectionDocument:
    """A searchable section with its file context."""

    file_path: str
    section_title: str
    section_description: str
    section_content: str
    frontmatter: Frontmatter
    tokens: list[str] = field(default_factory=list)
    source: str = "memory"


@dataclass
class SearchResult:
    """A single search result with score and snippet."""

    rank: int
    path: str
    section: str
    section_description: str
    score: float
    file_frontmatter: dict
    snippet: str
    source: str = "memory"


def _matches_filter(fm: Frontmatter, **filters: str | None) -> bool:
    """Check if frontmatter matches all provided filters."""
    for key, value in filters.items():
        if value is None:
            continue
        if key == "tags":
            if value not in (fm.tags or []):
                return False
        else:
            fm_value = getattr(fm, key, "")
            if isinstance(fm_value, str):
                if fm_value.lower() != value.lower():
                    return False
            else:
                return False
    return True


def _resolve_scope(base_path: Path, scope: str, agent_id: str) -> list[Path]:
    """Resolve scope string to list of directories to search."""
    if scope == "own":
        if not agent_id:
            raise ValueError(
                "Agent ID required for --scope own. Set AGENT_ID environment variable."
            )
        return [base_path / agent_id]
    elif scope == "shared":
        return [base_path / "shared"]
    elif scope.startswith("agent:"):
        target_id = scope[6:]
        if not target_id:
            raise ValueError(
                "Agent ID required after 'agent:' prefix. Example: --scope agent:other-agent"
            )
        return [base_path / target_id]
    elif scope == "all":
        return [base_path]
    else:
        raise ValueError(
            f"Invalid scope '{scope}'. Use: all, own, shared, or agent:<id>"
        )


def collect_documents(
    base_path: Path,
    scope: str = "all",
    agent_id: str = "",
    **filters: str | None,
) -> list[SectionDocument]:
    """Collect section documents from memory entries with filtering.

    Reads files directly from disk. For cached loading, use
    collect_documents_cached() instead.
    """
    search_dirs = _resolve_scope(base_path, scope, agent_id)

    documents: list[SectionDocument] = []
    for search_dir in search_dirs:
        if not search_dir.exists():
            continue
        for md_file in sorted(search_dir.rglob("*.md")):
            if any(p.startswith("_") for p in md_file.relative_to(base_path).parts):
                continue
            try:
                text = md_file.read_text(encoding="utf-8")
                fm, body = parse_frontmatter(text)
            except (ValueError, OSError):
                continue

            if not _matches_filter(fm, **filters):
                continue

            rel_path = str(md_file.relative_to(base_path))
            for section in parse_sections(body):
                combined = f"{section.title} {section.description} {section.content}"
                documents.append(
                    SectionDocument(
                        file_path=rel_path,
                        section_title=section.title,
                        section_description=section.description,
                        section_content=section.content,
                        frontmatter=fm,
                        tokens=tokenize(combined),
                    )
                )

    return documents


def _scope_prefix(scope: str, agent_id: str) -> str | None:
    """Convert scope to a path prefix for cache queries."""
    if scope == "own":
        if not agent_id:
            raise ValueError(
                "Agent ID required for --scope own. Set AGENT_ID environment variable."
            )
        return agent_id + "/"
    elif scope == "shared":
        return "shared/"
    elif scope.startswith("agent:"):
        target_id = scope[6:]
        if not target_id:
            raise ValueError(
                "Agent ID required after 'agent:' prefix. Example: --scope agent:other-agent"
            )
        return target_id + "/"
    elif scope == "all":
        return None
    else:
        raise ValueError(
            f"Invalid scope '{scope}'. Use: all, own, shared, or agent:<id>"
        )


def _cached_to_document(cached: CachedSection) -> SectionDocument:
    """Convert a CachedSection to a SectionDocument."""
    raw = json.loads(cached.frontmatter_json)
    fm = Frontmatter(
        description=str(raw.get("description", "")),
        author=str(raw.get("author", "")),
        created=str(raw.get("created", "")),
        updated=str(raw.get("updated", "")),
        tags=raw.get("tags", []) or [],
        related=raw.get("related", []) or [],
        confidence=str(raw.get("confidence", "")),
        category=str(raw.get("category", "")),
        status=str(raw.get("status", "")),
        supersedes=str(raw.get("supersedes", "")),
        raw=raw,
    )
    return SectionDocument(
        file_path=cached.file_path,
        section_title=cached.section_title,
        section_description=cached.section_description,
        section_content=cached.section_content,
        frontmatter=fm,
        tokens=cached.tokens,
    )


def collect_documents_cached(
    cache: IndexCache,
    scope: str = "all",
    agent_id: str = "",
    **filters: str | None,
) -> list[SectionDocument]:
    """Collect section documents from cache with filtering."""
    prefix = _scope_prefix(scope, agent_id)
    cached_sections = cache.get_documents(scope_prefix=prefix)

    documents: list[SectionDocument] = []
    for cached in cached_sections:
        doc = _cached_to_document(cached)
        if not _matches_filter(doc.frontmatter, **filters):
            continue
        documents.append(doc)

    return documents


def _source_entries_to_documents(
    entries: list[SourceEntry],
) -> list[SectionDocument]:
    """Convert parsed source entries into searchable section documents.

    Each ## section in a source file becomes a separate document.
    Files without ## sections are indexed as a single document
    using the file's description and full body.
    """
    documents: list[SectionDocument] = []
    for entry in entries:
        label = entry.file.source_label
        display_path = f"{label}/{entry.file.rel_path}"
        sections = parse_sections(entry.body)
        if sections:
            for section in sections:
                combined = (
                    f"{section.title} {section.description} "
                    f"{section.content}"
                )
                documents.append(SectionDocument(
                    file_path=display_path,
                    section_title=section.title,
                    section_description=section.description,
                    section_content=section.content,
                    frontmatter=entry.frontmatter,
                    tokens=tokenize(combined),
                    source=label,
                ))
        else:
            combined = (
                f"{entry.frontmatter.description} {entry.body}"
            )
            documents.append(SectionDocument(
                file_path=display_path,
                section_title=entry.frontmatter.description or entry.file.rel_path,
                section_description="",
                section_content=entry.body,
                frontmatter=entry.frontmatter,
                tokens=tokenize(combined),
                source=label,
            ))
    return documents


def _build_field_corpora(
    documents: list[SectionDocument],
) -> dict[str, list[list[str]]]:
    """Build per-field token lists for BM25F scoring.

    Fields extracted per document:
      - title: section heading (## text)
      - description: frontmatter description (file-level)
      - first_line: section description (first line after ##)
      - body: remaining section content
    """
    titles: list[list[str]] = []
    descriptions: list[list[str]] = []
    first_lines: list[list[str]] = []
    bodies: list[list[str]] = []

    for doc in documents:
        titles.append(tokenize(doc.section_title))
        descriptions.append(tokenize(doc.frontmatter.description))
        first_lines.append(tokenize(doc.section_description))
        bodies.append(tokenize(doc.section_content))

    return {
        "title": titles,
        "description": descriptions,
        "first_line": first_lines,
        "body": bodies,
    }


def search(
    query: str,
    base_path: Path,
    scope: str = "all",
    agent_id: str = "",
    field: str = "content",
    limit: int = 10,
    no_cache: bool = False,
    include_sources: bool = False,
    **filters: str | None,
) -> list[SearchResult]:
    """Run a BM25 search over memory entry sections.

    When field is "content" (default), uses BM25F field-weighted
    scoring: title x4, description x3, first_line x2, body x1.
    When field is "description" or "tags", scores that single
    field only (no field boosting).

    When no_cache is False (default), attempts to use the SQLite
    index cache for faster loading. Falls back to direct file
    reading if the cache is unavailable or errors occur.

    When include_sources is True, also indexes files from additional
    configured sources (rules, skills, etc.) and merges results.
    Source attribution is preserved in the ``source`` field.
    """
    if not query.strip():
        return []

    documents: list[SectionDocument] = []

    if not no_cache:
        try:
            cache_path = resolve_cache_path(base_path)
            if cache_path.exists():
                cache = IndexCache(cache_path)
                cache.open()
                try:
                    cache.refresh(base_path)
                    documents = collect_documents_cached(
                        cache,
                        scope=scope,
                        agent_id=agent_id,
                        **filters,
                    )
                finally:
                    cache.close()
        except Exception as exc:
            logger.debug("Cache unavailable, falling back to disk: %s", exc)
            documents = []

    if not documents:
        documents = collect_documents(
            base_path, scope=scope, agent_id=agent_id, **filters
        )

    if include_sources:
        source_entries = collect_all_source_files()
        source_docs = _source_entries_to_documents(source_entries)
        documents.extend(source_docs)

    if not documents:
        return []

    query_tokens = tokenize(query)
    if not query_tokens:
        return []

    if field == "description":
        corpus = [tokenize(d.section_description) for d in documents]
        bm25 = BM25()
        bm25.index(corpus)
        scores = bm25.score(query_tokens)
    elif field == "tags":
        corpus = [tokenize(" ".join(d.frontmatter.tags or [])) for d in documents]
        bm25 = BM25()
        bm25.index(corpus)
        scores = bm25.score(query_tokens)
    else:
        field_corpora = _build_field_corpora(documents)
        union_corpus = [d.tokens for d in documents]
        bm25 = BM25()
        bm25.index(union_corpus)
        scores = bm25.score_fields(query_tokens, field_corpora, FIELD_WEIGHTS)

    scored = [(doc, score) for doc, score in zip(documents, scores) if score > 0]
    scored.sort(key=lambda x: x[1], reverse=True)
    scored = scored[:limit]

    return [
        SearchResult(
            rank=rank,
            path=doc.file_path,
            section=doc.section_title,
            section_description=doc.section_description,
            score=round(score, 2),
            file_frontmatter=doc.frontmatter.raw,
            snippet=extract_snippet(doc.section_content, query_tokens),
            source=doc.source,
        )
        for rank, (doc, score) in enumerate(scored, start=1)
    ]
