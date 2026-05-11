import re
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from pydantic import ValidationError

from src.tools.insert_text import create_insert_text_tool


class _CwdProvider:
    def __init__(self, cwd: Path = Path("/tmp")) -> None:
        self.cwd = cwd


class InsertTextToolTests(unittest.IsolatedAsyncioTestCase):

    async def test_insert_text_tool_exposes_pydantic_schema(self) -> None:
        tool = create_insert_text_tool(cwd_provider=_CwdProvider())
        self.assertEqual(tool.parameters_json_schema["type"], "object")
        self.assertIn("filepath", tool.parameters_json_schema["required"])
        self.assertIn("needle", tool.parameters_json_schema["required"])
        self.assertIn("direction", tool.parameters_json_schema["required"])

    async def test_insert_text_tool_rejects_invalid_arguments(self) -> None:
        with self.assertRaises(ValidationError):
            await create_insert_text_tool(cwd_provider=_CwdProvider()).handler(arguments={})

    async def test_insert_text_tool_resolves_relative_path_from_cwd_provider(self) -> None:
        with TemporaryDirectory() as temp_dir:
            cwd = Path(temp_dir)
            path = cwd / "demo.txt"
            path.write_text("hello world\n", encoding="utf-8")

            result = await create_insert_text_tool(cwd_provider=_CwdProvider(cwd)).handler(
                arguments={
                    "filepath": "demo.txt",
                    "needle": "world",
                    "direction": "before",
                    "text": "project-x ",
                }
            )

            self.assertIn("@@ ", result["unified_diff"])
            self.assertEqual(path.read_text(encoding="utf-8"), "hello project-x world\n")

    async def test_insert_text_tool_errors_when_needle_is_not_unique(self) -> None:
        with TemporaryDirectory() as temp_dir:
            cwd = Path(temp_dir)
            path = cwd / "demo.txt"
            path.write_text("a a a\n", encoding="utf-8")

            with self.assertRaises(ValueError):
                await create_insert_text_tool(cwd_provider=_CwdProvider(cwd)).handler(
                    arguments={
                        "filepath": "demo.txt",
                        "needle": "a",
                        "direction": "after",
                        "text": "b",
                    }
                )

            self.assertEqual(path.read_text(encoding="utf-8"), "a a a\n")

    async def test_insert_text_tool_persists_text_to_tmp_when_edit_fails(self) -> None:
        with TemporaryDirectory() as temp_dir:
            cwd = Path(temp_dir)
            path = cwd / "demo.txt"
            path.write_text("a a a\n", encoding="utf-8")

            try:
                await create_insert_text_tool(cwd_provider=_CwdProvider(cwd)).handler(
                    arguments={
                        "filepath": "demo.txt",
                        "needle": "a",
                        "direction": "after",
                        "text": "b",
                    }
                )
                self.fail("期望抛出 ValueError")
            except ValueError as exc:
                message = str(exc)
                match = re.search(r"已将 text 保存到 (?P<path>/tmp/[^，]+)", message)
                self.assertIsNotNone(match, f"错误信息未包含 text 保存路径: {message}")
                text_path = Path(match.group("path"))
                self.assertTrue(text_path.is_file())
                self.assertEqual(text_path.read_text(encoding="utf-8"), "b")

    async def test_insert_text_tool_supports_text_from_file(self) -> None:
        with TemporaryDirectory() as temp_dir:
            cwd = Path(temp_dir)
            path = cwd / "demo.txt"
            insert_file = cwd / "insert.txt"
            path.write_text("hello world\n", encoding="utf-8")
            insert_file.write_text("project-x ", encoding="utf-8")

            await create_insert_text_tool(cwd_provider=_CwdProvider(cwd)).handler(
                arguments={
                    "filepath": "demo.txt",
                    "needle": "world",
                    "direction": "before",
                    "text_from_file": "insert.txt",
                }
            )

            self.assertEqual(path.read_text(encoding="utf-8"), "hello project-x world\n")

    async def test_insert_text_tool_supports_insert_after(self) -> None:
        with TemporaryDirectory() as temp_dir:
            cwd = Path(temp_dir)
            path = cwd / "demo.txt"
            path.write_text("hello world\n", encoding="utf-8")

            await create_insert_text_tool(cwd_provider=_CwdProvider(cwd)).handler(
                arguments={
                    "filepath": "demo.txt",
                    "needle": "hello",
                    "direction": "after",
                    "text": " project-x",
                }
            )

            self.assertEqual(path.read_text(encoding="utf-8"), "hello project-x world\n")


if __name__ == "__main__":
    unittest.main()
