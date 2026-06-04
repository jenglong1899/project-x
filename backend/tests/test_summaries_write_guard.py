import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from src.tools.insert_text import create_insert_text_tool
from src.tools.replace_text import create_replace_text_tool


class _CwdProvider:
    def __init__(self, cwd: Path) -> None:
        self.cwd = cwd


class SummariesWriteGuardTests(unittest.IsolatedAsyncioTestCase):

    async def test_worker_can_only_edit_todo_in_summaries_dir(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            summaries_dir = root / "summaries"
            summaries_dir.mkdir(parents=True, exist_ok=True)
            todo_path = summaries_dir / "TODO.md"
            main_path = summaries_dir / "MAIN.md"
            todo_path.write_text("a\n", encoding="utf-8")
            main_path.write_text("b\n", encoding="utf-8")

            with (
                mock.patch("src.commons.SUMMARIES_DIR", summaries_dir),
                mock.patch("src.commons.TODO_MEMORY_FILEPATH", todo_path),
                mock.patch("src.commons.MAIN_MEMORY_FILEPATH", main_path),
            ):
                tool = create_replace_text_tool(cwd_provider=_CwdProvider(summaries_dir), caller_kind="worker")
                await tool.handler(
                    arguments={
                        "filepath": str(todo_path),
                        "needle": "a",
                        "repl": "a1",
                        "mode": "literal",
                    }
                )

                tool2 = create_replace_text_tool(cwd_provider=_CwdProvider(summaries_dir), caller_kind="worker")
                with self.assertRaises(ValueError) as ctx:
                    await tool2.handler(
                        arguments={
                            "filepath": str(main_path),
                            "needle": "b",
                            "repl": "b1",
                            "mode": "literal",
                        }
                    )
                self.assertIn("你是 worker", str(ctx.exception))

    async def test_summarizer_cannot_edit_todo_in_summaries_dir(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            summaries_dir = root / "summaries"
            summaries_dir.mkdir(parents=True, exist_ok=True)
            todo_path = summaries_dir / "TODO.md"
            main_path = summaries_dir / "MAIN.md"
            todo_path.write_text("a\n", encoding="utf-8")
            main_path.write_text("b\n", encoding="utf-8")

            with (
                mock.patch("src.commons.SUMMARIES_DIR", summaries_dir),
                mock.patch("src.commons.TODO_MEMORY_FILEPATH", todo_path),
                mock.patch("src.commons.MAIN_MEMORY_FILEPATH", main_path),
            ):
                tool = create_insert_text_tool(cwd_provider=_CwdProvider(summaries_dir), caller_kind="summarizer")
                with self.assertRaises(ValueError) as ctx:
                    await tool.handler(
                        arguments={
                            "filepath": str(todo_path),
                            "needle": "a",
                            "direction": "after",
                            "text": "x",
                        }
                    )
                self.assertIn("你是 summarizer", str(ctx.exception))

                tool2 = create_insert_text_tool(cwd_provider=_CwdProvider(summaries_dir), caller_kind="summarizer")
                await tool2.handler(
                    arguments={
                        "filepath": str(main_path),
                        "needle": "b",
                        "direction": "after",
                        "text": "y",
                    }
                )


if __name__ == "__main__":
    unittest.main()
