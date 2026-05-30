import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from src.tools.apply_patch import create_apply_patch_tool


class _CwdProvider:
    def __init__(self, cwd: Path) -> None:
        self.cwd = cwd


class ApplyPatchToolTests(unittest.IsolatedAsyncioTestCase):

    async def test_worker_cwd_relative_paths_are_checked_against_summaries_guard(self) -> None:
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
                tool = create_apply_patch_tool(caller_kind="worker", cwd_provider=_CwdProvider(summaries_dir))
                with self.assertRaises(ValueError) as ctx:
                    await tool.handler(
                        arguments={
                            "patch": (
                                "*** Begin Patch\n"
                                "*** Update File: MAIN.md\n"
                                "@@\n"
                                "-b\n"
                                "+b1\n"
                                "*** End Patch\n"
                            )
                        }
                    )
                self.assertIn("你是 worker", str(ctx.exception))

    async def test_apply_patch_runs_under_cwd_provider(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            summaries_dir = root / "summaries"
            summaries_dir.mkdir(parents=True, exist_ok=True)
            todo_path = summaries_dir / "TODO.md"
            main_path = summaries_dir / "MAIN.md"
            todo_path.write_text("a\n", encoding="utf-8")
            main_path.write_text("b\n", encoding="utf-8")

            captured: dict[str, str] = {}

            def _fake_apply_patch(patch: str) -> str:
                captured["cwd"] = os.getcwd()
                captured["patch"] = patch
                return "ok"

            with (
                mock.patch("src.commons.SUMMARIES_DIR", summaries_dir),
                mock.patch("src.commons.TODO_MEMORY_FILEPATH", todo_path),
                mock.patch("src.commons.MAIN_MEMORY_FILEPATH", main_path),
                mock.patch("src.tools.apply_patch.cap.apply_patch", side_effect=_fake_apply_patch),
            ):
                tool = create_apply_patch_tool(caller_kind="worker", cwd_provider=_CwdProvider(summaries_dir))
                result = await tool.handler(
                    arguments={
                        "patch": (
                            "*** Begin Patch\n"
                            "*** Update File: TODO.md\n"
                            "@@\n"
                            "-a\n"
                            "+a1\n"
                            "*** End Patch\n"
                        )
                    }
                )
                self.assertTrue(result["ok"])
                self.assertEqual(Path(captured["cwd"]).resolve(), summaries_dir.resolve())
                self.assertIn("*** Update File: TODO.md", captured["patch"])


if __name__ == "__main__":
    unittest.main()

