import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from pydantic import ValidationError

from src.tools.read_file import create_read_file_tool


class _CwdProvider:
    def __init__(self, cwd: Path = Path("/tmp")) -> None:
        self.cwd = cwd


class ReadFileToolTests(unittest.IsolatedAsyncioTestCase):

    async def test_read_file_tool_exposes_pydantic_schema(self) -> None:
        read_file_tool = create_read_file_tool(cwd_provider=_CwdProvider())

        self.assertEqual(read_file_tool.parameters_json_schema["type"], "object")
        self.assertIn("filepath", read_file_tool.parameters_json_schema["required"])

    async def test_read_file_tool_rejects_invalid_line_range(self) -> None:
        with self.assertRaises(ValidationError):
            await create_read_file_tool(cwd_provider=_CwdProvider()).handler(
                arguments={
                    "filepath": "demo.txt",
                    "line_range_start": 3,
                    "line_range_end": 2,
                }
            )

    async def test_read_file_tool_resolves_relative_path_from_cwd_provider(self) -> None:
        with TemporaryDirectory() as temp_dir:
            cwd = Path(temp_dir)
            (cwd / "demo.txt").write_text("第一行\n第二行\n", encoding="utf-8")

            result = await create_read_file_tool(cwd_provider=_CwdProvider(cwd)).handler(
                arguments={"filepath": "demo.txt"}
            )

        self.assertEqual(result["filepath"], str(cwd / "demo.txt"))
        self.assertEqual(result["start"], 1)
        self.assertEqual(result["end"], 2)
        self.assertEqual(result["content"], "     1\t第一行\n     2\t第二行\n")
        self.assertFalse(result["truncated"])
        self.assertIsNone(result["error"])

    async def test_read_file_tool_reads_closed_line_range_without_line_numbers(self) -> None:
        with TemporaryDirectory() as temp_dir:
            cwd = Path(temp_dir)
            (cwd / "demo.txt").write_text("a\nb\nc\n", encoding="utf-8")

            result = await create_read_file_tool(cwd_provider=_CwdProvider(cwd)).handler(
                arguments={
                    "filepath": "demo.txt",
                    "line_range_start": 2,
                    "line_range_end": 3,
                    "show_line_numbers": False,
                }
            )

        self.assertEqual(result["start"], 2)
        self.assertEqual(result["end"], 3)
        self.assertEqual(result["content"], "b\nc\n")

    async def test_read_file_tool_truncates_on_complete_line(self) -> None:
        with TemporaryDirectory() as temp_dir:
            cwd = Path(temp_dir)
            (cwd / "demo.txt").write_text("alpha\nbeta\ngamma\n", encoding="utf-8")

            result = await create_read_file_tool(cwd_provider=_CwdProvider(cwd)).handler(
                arguments={"filepath": "demo.txt", "show_line_numbers": False, "max_chars": 11}
            )

        self.assertEqual(result["content"], "alpha\nbeta\n")
        self.assertEqual(result["end"], 2)
        self.assertTrue(result["truncated"])

    async def test_read_file_tool_returns_no_end_when_first_line_exceeds_max_chars(self) -> None:
        with TemporaryDirectory() as temp_dir:
            cwd = Path(temp_dir)
            (cwd / "demo.txt").write_text("alpha\nbeta\n", encoding="utf-8")

            result = await create_read_file_tool(cwd_provider=_CwdProvider(cwd)).handler(
                arguments={"filepath": "demo.txt", "show_line_numbers": False, "max_chars": 3}
            )

        self.assertEqual(result["content"], "")
        self.assertIsNone(result["end"])
        self.assertTrue(result["truncated"])


if __name__ == "__main__":
    unittest.main()
