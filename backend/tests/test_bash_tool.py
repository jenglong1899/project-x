import unittest
from pathlib import Path
from unittest import mock

from pydantic import ValidationError

from src.tools.bash import create_bash_tool
from src.tools.cwd_state import CwdState


class _FakeProcess:
    def __init__(self, *, stdout: bytes, stderr: bytes, returncode: int) -> None:
        self._stdout = stdout
        self._stderr = stderr
        self.returncode = returncode

    async def communicate(self) -> tuple[bytes, bytes]:
        return self._stdout, self._stderr


class BashToolTests(unittest.IsolatedAsyncioTestCase):

    @mock.patch("src.tools.bash.asyncio.create_subprocess_exec")
    async def test_bash_tool_runs_command_and_returns_result(self, mock_exec: mock.Mock) -> None:
        mock_exec.return_value = _FakeProcess(stdout=b"/tmp\n", stderr=b"", returncode=0)

        bash_tool = create_bash_tool()
        result = await bash_tool.handler(arguments={"command": "pwd"})

        mock_exec.assert_called_once()
        args = mock_exec.call_args.args
        kwargs = mock_exec.call_args.kwargs
        self.assertEqual(args[:2], ("bash", "-lc"))
        self.assertIn("stdout", kwargs)
        self.assertIn("stderr", kwargs)
        self.assertEqual(
            result,
            {
                "stdout": "/tmp\n",
                "stderr": "",
                "returncode": 0,
            },
        )

    async def test_bash_tool_rejects_invalid_arguments(self) -> None:
        with self.assertRaises(ValidationError):
            await create_bash_tool().handler(arguments={})

    async def test_bash_tool_allows_summarizer_to_run_commands(self) -> None:
        result = await create_bash_tool(caller_kind="summarizer").handler(arguments={"command": "pwd"})
        self.assertEqual(result["returncode"], 0)

    async def test_bash_tool_exposes_pydantic_schema(self) -> None:
        bash_tool = create_bash_tool()
        self.assertEqual(bash_tool.parameters_json_schema["type"], "object")
        self.assertIn("command", bash_tool.parameters_json_schema["required"])
        self.assertEqual(
            bash_tool.parameters_json_schema["properties"]["command"]["type"],
            "string",
        )

    async def test_bash_tool_updates_shared_cwd_state(self) -> None:
        cwd_state = CwdState(initial_cwd="/")
        bash_tool = create_bash_tool(cwd_state=cwd_state)

        with mock.patch("src.tools.bash.persist_worker_cwd") as _mock_persist:
            await bash_tool.handler(arguments={"command": "cd /tmp"})

        self.assertEqual(cwd_state.cwd, Path("/tmp"))

    async def test_bash_tool_persists_cwd_after_run(self) -> None:
        cwd_state = CwdState(initial_cwd="/")
        bash_tool = create_bash_tool(cwd_state=cwd_state)

        with mock.patch("src.tools.bash.persist_worker_cwd") as mock_persist:
            await bash_tool.handler(arguments={"command": "cd /tmp"})

        mock_persist.assert_called()
        self.assertEqual(mock_persist.call_args.kwargs["cwd"], Path("/tmp"))

    @mock.patch("src.tools.bash.asyncio.create_subprocess_exec")
    async def test_bash_tool_expands_user_in_initial_cwd(self, mock_exec: mock.Mock) -> None:
        mock_exec.return_value = _FakeProcess(stdout=b"", stderr=b"", returncode=0)

        bash_tool = create_bash_tool(initial_cwd="~")
        await bash_tool.handler(arguments={"command": "pwd"})

        self.assertEqual(
            mock_exec.call_args.kwargs["cwd"],
            str(Path.home().resolve()),
        )

    async def test_bash_tool_remembers_cwd_between_calls(self) -> None:
        bash_tool = create_bash_tool(initial_cwd="/")

        cd_result = await bash_tool.handler(arguments={"command": "cd /tmp"})
        pwd_result = await bash_tool.handler(arguments={"command": "pwd"})

        self.assertEqual(cd_result["returncode"], 0)
        self.assertEqual(pwd_result["stdout"], "/tmp\n")

    async def test_bash_tool_instances_keep_independent_cwd(self) -> None:
        first_tool = create_bash_tool(initial_cwd="/")
        second_tool = create_bash_tool(initial_cwd="/")

        await first_tool.handler(arguments={"command": "cd /tmp"})
        first_pwd = await first_tool.handler(arguments={"command": "pwd"})
        second_pwd = await second_tool.handler(arguments={"command": "pwd"})

        self.assertEqual(first_pwd["stdout"], "/tmp\n")
        self.assertEqual(second_pwd["stdout"], "/\n")


if __name__ == "__main__":
    unittest.main()
