import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from click.testing import CliRunner

from agent_memory.cli import cli
from agent_memory.validator import category_values, validate_file
from agent_memory.writer import create_entry


def _write_memory_entry(path: Path, category: str) -> None:
    path.write_text(
        "\n".join(
            [
                "---",
                "description: Category validation repro",
                "author: axis",
                "created: '2026-05-20T00:00:00+00:00'",
                "updated: '2026-05-20T00:00:00+00:00'",
                f"category: {category}",
                "confidence: working",
                "status: active",
                "---",
                "",
                "## Summary",
                "This section has the required prose description.",
                "",
            ]
        ),
        encoding="utf-8",
    )


class ConfigurableCategoriesTest(unittest.TestCase):
    def test_unknown_category_fails_without_configuration(self) -> None:
        with TemporaryDirectory() as tmp:
            entry = Path(tmp) / "entry.md"
            _write_memory_entry(entry, "scientific_papers")

            with patch("agent_memory.validator.load_config", return_value={}):
                with patch.dict(os.environ, {}, clear=False):
                    os.environ.pop("AGENT_MEMORY_CATEGORIES", None)
                    result = validate_file(entry)

        self.assertFalse(result.is_valid)
        self.assertIn(
            "Invalid category 'scientific_papers'",
            "\n".join(result.errors),
        )

    def test_repo_local_config_allows_custom_category(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".agent-memory.yaml").write_text(
                "categories:\n  - scientific_papers\n",
                encoding="utf-8",
            )
            entry_dir = root / "axis" / "scientific_papers"
            entry_dir.mkdir(parents=True)
            entry = entry_dir / "paper.md"
            _write_memory_entry(entry, "scientific_papers")

            with patch("agent_memory.validator.load_config", return_value={}):
                result = validate_file(entry)

        self.assertTrue(result.is_valid, result.errors)
        self.assertIn("Valid category: scientific_papers", result.checks_passed)

    def test_create_entry_uses_repo_local_categories(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".agent-memory.yaml").write_text(
                "categories:\n  - scientific_papers\n",
                encoding="utf-8",
            )

            with patch("agent_memory.validator.load_config", return_value={}):
                path = create_entry(
                    base_path=root,
                    agent_id="axis",
                    name="Paper One",
                    description="Paper category repro",
                    body="## Summary\nThis entry uses a custom category.",
                    category="scientific_papers",
                    confidence="working",
                )

        self.assertEqual(
            path.relative_to(root).as_posix(),
            "axis/scientific_papers/paper-one.md",
        )

    def test_environment_categories_are_added(self) -> None:
        with patch("agent_memory.validator.load_config", return_value={}):
            with patch.dict(
                os.environ,
                {"AGENT_MEMORY_CATEGORIES": "scientific_papers,experiment_notes"},
            ):
                values = category_values()

        self.assertIn("scientific_papers", values)
        self.assertIn("experiment_notes", values)

    def test_unsafe_category_name_is_rejected(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".agent-memory.yaml").write_text(
                "categories:\n  - scientific_papers\n",
                encoding="utf-8",
            )

            with patch("agent_memory.validator.load_config", return_value={}):
                with self.assertRaisesRegex(ValueError, "must match"):
                    create_entry(
                        base_path=root,
                        agent_id="axis",
                        name="Bad",
                        description="Unsafe category repro",
                        category="../bad",
                    )

    def test_cli_new_accepts_configured_custom_category(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".agent-memory.yaml").write_text(
                "categories:\n  - scientific_papers\n",
                encoding="utf-8",
            )
            runner = CliRunner()

            with patch("agent_memory.validator.load_config", return_value={}):
                result = runner.invoke(
                    cli,
                    [
                        "new",
                        "Paper Two",
                        "--description",
                        "Paper category CLI repro",
                        "--author",
                        "axis",
                        "--category",
                        "scientific_papers",
                        "--confidence",
                        "working",
                        "--base",
                        str(root),
                        "--no-git",
                    ],
                )

            self.assertEqual(result.exit_code, 0, result.output)
            self.assertTrue(
                (root / "axis" / "scientific_papers" / "paper-two.md").is_file()
            )


if __name__ == "__main__":
    unittest.main()
