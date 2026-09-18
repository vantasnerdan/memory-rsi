"""Tests for BM25 search: scoring, indexing, filtering, snippets, CLI."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_memory.bm25 import BM25
from agent_memory.parser import Frontmatter
from agent_memory.search import (
    _matches_filter,
    _resolve_scope,
    collect_documents,
    search,
)
from agent_memory.snippet import extract_snippet
from agent_memory.tokenizer import tokenize

FIXTURES = Path(__file__).parent / "fixtures"


class TestTokenize:
    def test_basic_tokenization(self) -> None:
        tokens = tokenize("Hello World")
        assert tokens == ["hello", "world"]

    def test_removes_stopwords(self) -> None:
        tokens = tokenize("the cat is on the mat")
        assert "the" not in tokens
        assert "is" not in tokens
        assert "on" not in tokens
        assert "cat" in tokens
        assert "mat" in tokens

    def test_splits_on_non_alphanumeric(self) -> None:
        tokens = tokenize("hello-world foo_bar baz.qux")
        assert tokens == ["hello", "world", "foo", "bar", "baz", "qux"]

    def test_empty_string(self) -> None:
        assert tokenize("") == []

    def test_only_stopwords(self) -> None:
        assert tokenize("the is at which on") == []

    def test_preserves_numbers(self) -> None:
        tokens = tokenize("shift 1 policy")
        assert "shift" in tokens
        assert "1" in tokens
        assert "policy" in tokens


class TestBM25:
    def test_empty_corpus(self) -> None:
        bm25 = BM25()
        bm25.index([])
        assert bm25.score(["hello"]) == []

    def test_single_document_match(self) -> None:
        bm25 = BM25()
        bm25.index([["nan", "policy", "data"]])
        scores = bm25.score(["nan"])
        assert len(scores) == 1
        assert scores[0] > 0

    def test_no_match_zero_score(self) -> None:
        bm25 = BM25()
        bm25.index([["nan", "policy", "data"]])
        scores = bm25.score(["unrelated"])
        assert scores[0] == 0.0

    def test_ranking_order(self) -> None:
        bm25 = BM25()
        bm25.index([
            ["nan", "policy"],           # doc 0: 1 match
            ["nan", "nan", "handling"],   # doc 1: 2 matches (higher tf)
            ["unrelated", "topic"],       # doc 2: 0 matches
        ])
        scores = bm25.score(["nan"])
        assert scores[1] > scores[0]  # Higher tf scores better
        assert scores[2] == 0.0

    def test_idf_effect(self) -> None:
        bm25 = BM25()
        # "rare" appears in 1 doc, "common" in all 3
        bm25.index([
            ["rare", "common", "word"],
            ["common", "other", "word"],
            ["common", "another", "word"],
        ])
        # Query for rare term should give high score to doc 0
        scores_rare = bm25.score(["rare"])
        scores_common = bm25.score(["common"])
        # Doc 0 should score higher for "rare" than for "common"
        # because IDF is higher for "rare"
        assert scores_rare[0] > scores_common[0]

    def test_multi_term_query(self) -> None:
        bm25 = BM25()
        bm25.index([
            ["nan", "data", "missing"],
            ["shift", "window", "policy"],
            ["nan", "shift", "pipeline"],
        ])
        scores = bm25.score(["nan", "shift"])
        # Doc 2 matches both terms, should score highest
        assert scores[2] > scores[0]
        assert scores[2] > scores[1]

    def test_length_normalization(self) -> None:
        bm25 = BM25()
        # Same term count but different doc lengths
        bm25.index([
            ["nan", "policy"],
            ["nan", "policy", "extra", "words", "padding", "length"],
        ])
        scores = bm25.score(["nan"])
        # Shorter doc should score higher (b=0.75 penalizes long docs)
        assert scores[0] > scores[1]

    def test_custom_parameters(self) -> None:
        bm25 = BM25(k1=2.0, b=0.5)
        bm25.index([["test", "document"]])
        scores = bm25.score(["test"])
        assert scores[0] > 0


class TestMatchesFilter:
    def _make_fm(self, **kwargs) -> Frontmatter:
        return Frontmatter(**kwargs)

    def test_no_filters(self) -> None:
        fm = self._make_fm()
        assert _matches_filter(fm) is True

    def test_category_match(self) -> None:
        fm = self._make_fm(category="atlas")
        assert _matches_filter(fm, category="atlas") is True

    def test_category_mismatch(self) -> None:
        fm = self._make_fm(category="atlas")
        assert _matches_filter(fm, category="efforts") is False

    def test_case_insensitive_match(self) -> None:
        fm = self._make_fm(author="Kelvin")
        assert _matches_filter(fm, author="kelvin") is True

    def test_tag_match(self) -> None:
        fm = self._make_fm(tags=["data", "nan", "policy"])
        assert _matches_filter(fm, tags="nan") is True

    def test_tag_mismatch(self) -> None:
        fm = self._make_fm(tags=["data", "nan"])
        assert _matches_filter(fm, tags="missing") is False

    def test_multiple_filters(self) -> None:
        fm = self._make_fm(
            category="atlas", confidence="established", author="kelvin"
        )
        assert _matches_filter(
            fm, category="atlas", confidence="established"
        ) is True
        assert _matches_filter(
            fm, category="atlas", confidence="working"
        ) is False

    def test_none_filters_ignored(self) -> None:
        fm = self._make_fm(category="atlas")
        assert _matches_filter(fm, category=None) is True


class TestResolveScope:
    def test_scope_all(self, tmp_path: Path) -> None:
        dirs = _resolve_scope(tmp_path, "all", "")
        assert dirs == [tmp_path]

    def test_scope_own(self, tmp_path: Path) -> None:
        dirs = _resolve_scope(tmp_path, "own", "kelvin")
        assert dirs == [tmp_path / "kelvin"]

    def test_scope_own_missing_agent_id(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="Agent ID required"):
            _resolve_scope(tmp_path, "own", "")

    def test_scope_shared(self, tmp_path: Path) -> None:
        dirs = _resolve_scope(tmp_path, "shared", "")
        assert dirs == [tmp_path / "shared"]

    def test_scope_agent_prefix(self, tmp_path: Path) -> None:
        dirs = _resolve_scope(tmp_path, "agent:kelvin", "")
        assert dirs == [tmp_path / "kelvin"]

    def test_scope_agent_missing_id(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="Agent ID required"):
            _resolve_scope(tmp_path, "agent:", "")

    def test_invalid_scope(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="Invalid scope"):
            _resolve_scope(tmp_path, "bogus", "")


class TestExtractSnippet:
    def test_best_sentence(self) -> None:
        content = (
            "## Rationale\n"
            "Why missing values matter.\n\n"
            "NaN represents genuine absence of data. "
            "Filling with zeros introduces silent bias."
        )
        snippet = extract_snippet(content, ["nan", "data"])
        assert "NaN" in snippet or "absence" in snippet

    def test_max_length_truncation(self) -> None:
        long_sentence = "word " * 100
        snippet = extract_snippet(long_sentence, ["word"], max_len=50)
        assert len(snippet) <= 50
        assert snippet.endswith("...")

    def test_empty_content(self) -> None:
        assert extract_snippet("", ["test"]) == ""

    def test_no_query_tokens(self) -> None:
        snippet = extract_snippet("Some content here.", [])
        assert snippet == "Some content here."

    def test_skips_header_line(self) -> None:
        content = "## Header Title\nActual description line.\n\nMore content."
        snippet = extract_snippet(content, ["header", "title"])
        # Should prefer content over header lines
        assert not snippet.startswith("## ")


class TestCollectDocuments:
    def _create_memory_tree(self, tmp_path: Path) -> Path:
        """Create a minimal memory tree for testing."""
        base = tmp_path / "memory"
        kelvin_dir = base / "kelvin" / "atlas"
        kelvin_dir.mkdir(parents=True)
        shared_dir = base / "shared"
        shared_dir.mkdir(parents=True)

        (kelvin_dir / "nan-policy.md").write_text(
            "---\n"
            "description: NaN policy for data pipeline\n"
            "author: kelvin\n"
            "created: 2026-02-10T08:00:00Z\n"
            "updated: 2026-02-11T14:30:00Z\n"
            "tags: [data, nan]\n"
            "confidence: established\n"
            "category: atlas\n"
            "status: active\n"
            "---\n"
            "# NaN Policy\n\n"
            "## Rationale\n"
            "Why missing values must not be filled.\n\n"
            "NaN represents genuine absence of data.\n\n"
            "## Implementation\n"
            "How NaN propagation works in the pipeline.\n\n"
            "Every transform checks for NaN and propagates.\n"
        )

        (kelvin_dir / "shift-policy.md").write_text(
            "---\n"
            "description: Shift-one policy for z-score pipeline\n"
            "author: kelvin\n"
            "created: 2026-02-10T08:00:00Z\n"
            "updated: 2026-02-11T14:30:00Z\n"
            "tags: [data, zscore]\n"
            "confidence: working\n"
            "category: atlas\n"
            "status: active\n"
            "---\n"
            "# Shift-One Policy\n\n"
            "## Overview\n"
            "How shift(1) prevents lookahead bias.\n\n"
            "The expanding window z-score uses shift(1) to avoid leakage.\n"
        )

        (shared_dir / "conventions.md").write_text(
            "---\n"
            "description: Team coding conventions\n"
            "author: nexus\n"
            "created: 2026-02-10T08:00:00Z\n"
            "updated: 2026-02-10T08:00:00Z\n"
            "tags: [team, conventions]\n"
            "category: atlas\n"
            "status: active\n"
            "---\n"
            "# Conventions\n\n"
            "## Style Guide\n"
            "Code formatting and naming conventions.\n\n"
            "Use black for formatting.\n"
        )

        return base

    def test_collect_all(self, tmp_path: Path) -> None:
        base = self._create_memory_tree(tmp_path)
        docs = collect_documents(base)
        # 2 sections from nan-policy + 1 from shift-policy + 1 from conventions
        assert len(docs) == 4

    def test_collect_scoped_own(self, tmp_path: Path) -> None:
        base = self._create_memory_tree(tmp_path)
        docs = collect_documents(base, scope="own", agent_id="kelvin")
        # Only kelvin's files: 2 + 1 = 3 sections
        assert len(docs) == 3

    def test_collect_scoped_shared(self, tmp_path: Path) -> None:
        base = self._create_memory_tree(tmp_path)
        docs = collect_documents(base, scope="shared")
        assert len(docs) == 1
        assert docs[0].file_path.startswith("shared/")

    def test_collect_with_category_filter(self, tmp_path: Path) -> None:
        base = self._create_memory_tree(tmp_path)
        docs = collect_documents(base, category="atlas")
        assert len(docs) == 4  # All are atlas

    def test_collect_with_confidence_filter(self, tmp_path: Path) -> None:
        base = self._create_memory_tree(tmp_path)
        docs = collect_documents(base, confidence="established")
        # Only nan-policy is established (2 sections)
        assert len(docs) == 2

    def test_collect_with_author_filter(self, tmp_path: Path) -> None:
        base = self._create_memory_tree(tmp_path)
        docs = collect_documents(base, author="nexus")
        assert len(docs) == 1

    def test_collect_with_tag_filter(self, tmp_path: Path) -> None:
        base = self._create_memory_tree(tmp_path)
        docs = collect_documents(base, tags="nan")
        assert len(docs) == 2  # nan-policy has 2 sections

    def test_collect_nonexistent_scope(self, tmp_path: Path) -> None:
        base = self._create_memory_tree(tmp_path)
        docs = collect_documents(base, scope="agent:nonexistent")
        assert len(docs) == 0

    def test_document_tokens_populated(self, tmp_path: Path) -> None:
        base = self._create_memory_tree(tmp_path)
        docs = collect_documents(base, confidence="established")
        for doc in docs:
            assert len(doc.tokens) > 0

    def test_skips_invalid_files(self, tmp_path: Path) -> None:
        base = self._create_memory_tree(tmp_path)
        # Add a file with invalid frontmatter
        bad_file = base / "kelvin" / "atlas" / "broken.md"
        bad_file.write_text("No frontmatter at all")
        docs = collect_documents(base, scope="own", agent_id="kelvin")
        # Should skip broken.md silently, still have 3 valid sections
        assert len(docs) == 3


class TestSearch:
    def _create_memory_tree(self, tmp_path: Path) -> Path:
        """Create a memory tree for search testing."""
        base = tmp_path / "memory"
        kelvin_dir = base / "kelvin" / "atlas"
        kelvin_dir.mkdir(parents=True)

        (kelvin_dir / "nan-policy.md").write_text(
            "---\n"
            "description: NaN policy for data pipeline\n"
            "author: kelvin\n"
            "created: 2026-02-10T08:00:00Z\n"
            "updated: 2026-02-11T14:30:00Z\n"
            "tags: [data, nan, pipeline]\n"
            "confidence: established\n"
            "category: atlas\n"
            "status: active\n"
            "---\n"
            "# NaN Policy\n\n"
            "## Rationale\n"
            "Why missing values must not be filled with defaults.\n\n"
            "NaN represents genuine absence of data. Filling with zeros "
            "or means introduces silent bias that compounds through "
            "the pipeline.\n\n"
            "## Implementation\n"
            "How the pipeline enforces NaN propagation.\n\n"
            "Every transform checks for NaN and propagates it forward "
            "rather than substituting default values.\n"
        )

        (kelvin_dir / "shift-policy.md").write_text(
            "---\n"
            "description: Shift-one policy for z-score computation\n"
            "author: kelvin\n"
            "created: 2026-02-10T08:00:00Z\n"
            "updated: 2026-02-11T14:30:00Z\n"
            "tags: [data, zscore, lookahead]\n"
            "confidence: working\n"
            "category: atlas\n"
            "status: active\n"
            "---\n"
            "# Shift-One Policy\n\n"
            "## Overview\n"
            "How shift(1) prevents lookahead bias in z-score.\n\n"
            "The expanding window z-score uses shift(1) to prevent "
            "data leakage from future observations.\n\n"
            "## Edge Cases\n"
            "When shift produces NaN at the boundary.\n\n"
            "The first observation after shift(1) is always NaN "
            "because there is no prior value to reference.\n"
        )

        return base

    def test_basic_search(self, tmp_path: Path) -> None:
        base = self._create_memory_tree(tmp_path)
        results = search("NaN missing data", base_path=base)
        assert len(results) > 0
        assert results[0].score > 0

    def test_results_ranked_by_score(self, tmp_path: Path) -> None:
        base = self._create_memory_tree(tmp_path)
        results = search("NaN", base_path=base)
        for i in range(len(results) - 1):
            assert results[i].score >= results[i + 1].score

    def test_result_fields(self, tmp_path: Path) -> None:
        base = self._create_memory_tree(tmp_path)
        results = search("NaN missing", base_path=base)
        r = results[0]
        assert r.rank == 1
        assert r.path.endswith(".md")
        assert r.section != ""
        assert r.score > 0
        assert isinstance(r.file_frontmatter, dict)
        assert r.snippet != ""

    def test_limit_results(self, tmp_path: Path) -> None:
        base = self._create_memory_tree(tmp_path)
        results = search("NaN", base_path=base, limit=1)
        assert len(results) <= 1

    def test_field_description(self, tmp_path: Path) -> None:
        base = self._create_memory_tree(tmp_path)
        results = search("missing values defaults", base_path=base,
                         field="description")
        assert len(results) > 0
        # Rationale section has "missing values" in description
        top = results[0]
        assert "missing" in top.section_description.lower() or \
               "defaults" in top.section_description.lower()

    def test_field_tags(self, tmp_path: Path) -> None:
        base = self._create_memory_tree(tmp_path)
        results = search("lookahead", base_path=base, field="tags")
        assert len(results) > 0
        # shift-policy has "lookahead" tag
        assert "shift" in results[0].path.lower()

    def test_scope_filtering(self, tmp_path: Path) -> None:
        base = self._create_memory_tree(tmp_path)
        results = search("NaN", base_path=base, scope="agent:kelvin")
        assert len(results) > 0
        for r in results:
            assert r.path.startswith("kelvin/")

    def test_confidence_filter(self, tmp_path: Path) -> None:
        base = self._create_memory_tree(tmp_path)
        results = search("NaN", base_path=base, confidence="established")
        for r in results:
            assert r.file_frontmatter.get("confidence") == "established"

    def test_empty_query(self, tmp_path: Path) -> None:
        base = self._create_memory_tree(tmp_path)
        results = search("", base_path=base)
        assert results == []

    def test_stopwords_only_query(self, tmp_path: Path) -> None:
        base = self._create_memory_tree(tmp_path)
        results = search("the is at which", base_path=base)
        assert results == []

    def test_no_matching_results(self, tmp_path: Path) -> None:
        base = self._create_memory_tree(tmp_path)
        results = search("xyzzyzzyxyz", base_path=base)
        assert results == []

    def test_search_rank_numbering(self, tmp_path: Path) -> None:
        base = self._create_memory_tree(tmp_path)
        results = search("NaN", base_path=base)
        for i, r in enumerate(results, start=1):
            assert r.rank == i

    def test_snippet_populated(self, tmp_path: Path) -> None:
        base = self._create_memory_tree(tmp_path)
        results = search("NaN genuine absence", base_path=base)
        assert len(results) > 0
        assert results[0].snippet != ""

    def test_score_rounding(self, tmp_path: Path) -> None:
        base = self._create_memory_tree(tmp_path)
        results = search("NaN", base_path=base)
        for r in results:
            # Score should be rounded to 2 decimal places
            assert r.score == round(r.score, 2)


class TestSearchCLI:
    """Integration tests for the search CLI command."""

    def _create_memory_tree(self, tmp_path: Path) -> Path:
        base = tmp_path / "memory"
        agent_dir = base / "kelvin" / "atlas"
        agent_dir.mkdir(parents=True)

        (agent_dir / "nan-policy.md").write_text(
            "---\n"
            "description: NaN policy for data pipeline\n"
            "author: kelvin\n"
            "created: 2026-02-10T08:00:00Z\n"
            "updated: 2026-02-11T14:30:00Z\n"
            "tags: [data, nan]\n"
            "confidence: established\n"
            "category: atlas\n"
            "status: active\n"
            "---\n"
            "# NaN Policy\n\n"
            "## Rationale\n"
            "Why missing values must not be filled.\n\n"
            "NaN represents genuine absence of data.\n"
        )
        return base

    def test_search_human_output(self, tmp_path: Path) -> None:
        from click.testing import CliRunner

        from agent_memory.cli import cli

        base = self._create_memory_tree(tmp_path)
        runner = CliRunner()
        result = runner.invoke(
            cli, ["search", "NaN", "--base", str(base)]
        )
        assert result.exit_code == 0
        assert "nan-policy.md" in result.output
        assert "Rationale" in result.output
        assert "score:" in result.output

    def test_search_json_output(self, tmp_path: Path) -> None:
        from click.testing import CliRunner

        from agent_memory.cli import cli

        base = self._create_memory_tree(tmp_path)
        runner = CliRunner()
        result = runner.invoke(
            cli, ["--json-output", "search", "NaN", "--base", str(base)]
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data, list)
        assert len(data) > 0
        assert "rank" in data[0]
        assert "path" in data[0]
        assert "section" in data[0]
        assert "score" in data[0]
        assert "file_frontmatter" in data[0]
        assert "snippet" in data[0]

    def test_search_no_results(self, tmp_path: Path) -> None:
        from click.testing import CliRunner

        from agent_memory.cli import cli

        base = self._create_memory_tree(tmp_path)
        runner = CliRunner()
        result = runner.invoke(
            cli, ["search", "xyzzyzzyxyz", "--base", str(base)]
        )
        assert result.exit_code == 0
        assert "No results found" in result.output

    def test_search_json_no_results(self, tmp_path: Path) -> None:
        from click.testing import CliRunner

        from agent_memory.cli import cli

        base = self._create_memory_tree(tmp_path)
        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--json-output", "search", "xyzzyzzyxyz", "--base", str(base)],
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data == []

    def test_search_with_scope(self, tmp_path: Path) -> None:
        from click.testing import CliRunner

        from agent_memory.cli import cli

        base = self._create_memory_tree(tmp_path)
        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["search", "NaN", "--base", str(base),
             "--scope", "agent:kelvin"],
        )
        assert result.exit_code == 0
        assert "nan-policy.md" in result.output

    def test_search_with_filters(self, tmp_path: Path) -> None:
        from click.testing import CliRunner

        from agent_memory.cli import cli

        base = self._create_memory_tree(tmp_path)
        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--json-output", "search", "NaN", "--base", str(base),
             "--confidence", "established", "--category", "atlas"],
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert len(data) > 0
        for item in data:
            assert item["file_frontmatter"]["confidence"] == "established"

    def test_search_invalid_base(self, tmp_path: Path) -> None:
        from click.testing import CliRunner

        from agent_memory.cli import cli

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["search", "test", "--base", str(tmp_path / "nonexistent")],
        )
        assert result.exit_code == 1
        assert "not found" in result.output.lower()

    def test_search_limit_flag(self, tmp_path: Path) -> None:
        from click.testing import CliRunner

        from agent_memory.cli import cli

        base = self._create_memory_tree(tmp_path)
        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--json-output", "search", "NaN", "--base", str(base),
             "--limit", "1"],
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert len(data) <= 1

    def test_search_field_flag(self, tmp_path: Path) -> None:
        from click.testing import CliRunner

        from agent_memory.cli import cli

        base = self._create_memory_tree(tmp_path)
        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--json-output", "search", "missing values", "--base", str(base),
             "--field", "description"],
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data, list)

    def test_search_scope_own_requires_env(self, tmp_path: Path) -> None:
        from click.testing import CliRunner

        from agent_memory.cli import cli

        base = self._create_memory_tree(tmp_path)
        runner = CliRunner(env={"AGENT_ID": ""})
        result = runner.invoke(
            cli,
            ["search", "NaN", "--base", str(base), "--scope", "own"],
        )
        assert result.exit_code == 1
        assert "Agent ID required" in result.output

    def test_search_help(self) -> None:
        from click.testing import CliRunner

        from agent_memory.cli import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["search", "--help"])
        assert result.exit_code == 0
        assert "--scope" in result.output
        assert "--field" in result.output
        assert "--limit" in result.output
        assert "--category" in result.output
        assert "--confidence" in result.output
        assert "--author" in result.output
        assert "--status" in result.output
        assert "--tag" in result.output
