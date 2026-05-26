import unittest
import re
from pathlib import Path
from tempfile import TemporaryDirectory

from pydantic import ValidationError

from src.tools.replace_text import create_replace_text_tool


class _CwdProvider:
    def __init__(self, cwd: Path = Path("/tmp")) -> None:
        self.cwd = cwd


class ReplaceTextToolTests(unittest.IsolatedAsyncioTestCase):

    async def test_replace_text_tool_exposes_pydantic_schema(self) -> None:
        tool = create_replace_text_tool(cwd_provider=_CwdProvider())
        self.assertEqual(tool.parameters_json_schema["type"], "object")
        self.assertIn("filepath", tool.parameters_json_schema["required"])
        self.assertIn("needle", tool.parameters_json_schema["required"])
        self.assertIn("mode", tool.parameters_json_schema["required"])

    async def test_replace_text_tool_rejects_invalid_arguments(self) -> None:
        with self.assertRaises(ValidationError):
            await create_replace_text_tool(cwd_provider=_CwdProvider()).handler(arguments={})

    async def test_replace_text_tool_resolves_relative_path_from_cwd_provider(self) -> None:
        with TemporaryDirectory() as temp_dir:
            cwd = Path(temp_dir)
            path = cwd / "demo.txt"
            path.write_text("hello world\n", encoding="utf-8")

            result = await create_replace_text_tool(cwd_provider=_CwdProvider(cwd)).handler(
                arguments={
                    "filepath": "demo.txt",
                    "needle": "world",
                    "repl": "project-x",
                    "mode": "literal",
                }
            )

            self.assertEqual(result, "ok")
            self.assertEqual(path.read_text(encoding="utf-8"), "hello project-x\n")

    async def test_replace_text_tool_errors_when_multiple_occurrences_not_allowed(self) -> None:
        with TemporaryDirectory() as temp_dir:
            cwd = Path(temp_dir)
            path = cwd / "demo.txt"
            path.write_text("a a a\n", encoding="utf-8")

            with self.assertRaises(ValueError):
                await create_replace_text_tool(cwd_provider=_CwdProvider(cwd)).handler(
                    arguments={
                        "filepath": "demo.txt",
                        "needle": "a",
                        "repl": "b",
                        "mode": "literal",
                        "allow_multiple_occurrences": False,
                    }
                )

            self.assertEqual(path.read_text(encoding="utf-8"), "a a a\n")

    async def test_replace_text_tool_persists_repl_to_tmp_when_edit_fails(self) -> None:
        with TemporaryDirectory() as temp_dir:
            cwd = Path(temp_dir)
            path = cwd / "demo.txt"
            path.write_text("a a a\n", encoding="utf-8")

            try:
                await create_replace_text_tool(cwd_provider=_CwdProvider(cwd)).handler(
                    arguments={
                        "filepath": "demo.txt",
                        "needle": "a",
                        "repl": "b",
                        "mode": "literal",
                        "allow_multiple_occurrences": False,
                    }
                )
                self.fail("期望抛出 ValueError")
            except ValueError as exc:
                message = str(exc)
                match = re.search(r"已将 repl 保存到 (?P<path>/tmp/[^，]+)", message)
                self.assertIsNotNone(match, f"错误信息未包含 repl 保存路径: {message}")
                repl_path = Path(match.group("path"))
                self.assertTrue(repl_path.is_file())
                self.assertEqual(repl_path.read_text(encoding="utf-8"), "b")

    async def test_replace_text_tool_allows_multiple_occurrences(self) -> None:
        with TemporaryDirectory() as temp_dir:
            cwd = Path(temp_dir)
            path = cwd / "demo.txt"
            path.write_text("a a a\n", encoding="utf-8")

            result = await create_replace_text_tool(cwd_provider=_CwdProvider(cwd)).handler(
                arguments={
                    "filepath": "demo.txt",
                    "needle": "a",
                    "repl": "b",
                    "mode": "literal",
                    "allow_multiple_occurrences": True,
                }
            )

            self.assertEqual(result, "ok")
            self.assertEqual(path.read_text(encoding="utf-8"), "b b b\n")

    async def test_replace_text_tool_supports_regex_backreferences(self) -> None:
        with TemporaryDirectory() as temp_dir:
            cwd = Path(temp_dir)
            path = cwd / "demo.txt"
            path.write_text("alpha=beta\n", encoding="utf-8")

            result = await create_replace_text_tool(cwd_provider=_CwdProvider(cwd)).handler(
                arguments={
                    "filepath": "demo.txt",
                    "needle": r"(alpha)=(beta)",
                    "repl": r"$!2=$!1",
                    "mode": "regex",
                }
            )

            self.assertEqual(result, "ok")
            self.assertEqual(path.read_text(encoding="utf-8"), "beta=alpha\n")

    async def test_replace_text_tool_supports_repl_from_file(self) -> None:
        with TemporaryDirectory() as temp_dir:
            cwd = Path(temp_dir)
            path = cwd / "demo.txt"
            repl_file = cwd / "repl.txt"
            path.write_text("hello world\n", encoding="utf-8")
            repl_file.write_text("project-x", encoding="utf-8")

            result = await create_replace_text_tool(cwd_provider=_CwdProvider(cwd)).handler(
                arguments={
                    "filepath": "demo.txt",
                    "needle": "world",
                    "repl_from_file": "repl.txt",
                    "mode": "literal",
                }
            )

            self.assertEqual(result, "ok")
            self.assertEqual(path.read_text(encoding="utf-8"), "hello project-x\n")


if __name__ == "__main__":
    unittest.main()
