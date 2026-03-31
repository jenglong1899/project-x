import unittest
from unittest import mock

from pydantic import ValidationError

from src.tools.bash import BASH_TOOL


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

        result = await BASH_TOOL.handler(arguments={"command": "pwd"})

        mock_exec.assert_called_once()
        args = mock_exec.call_args.args
        kwargs = mock_exec.call_args.kwargs
        self.assertEqual(args[:3], ("bash", "-lc", "pwd"))
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
            await BASH_TOOL.handler(arguments={})

    async def test_bash_tool_exposes_pydantic_schema(self) -> None:
        self.assertEqual(BASH_TOOL.parameters_json_schema["type"], "object")
        self.assertIn("command", BASH_TOOL.parameters_json_schema["required"])
        self.assertEqual(
            BASH_TOOL.parameters_json_schema["properties"]["command"]["type"],
            "string",
        )


if __name__ == "__main__":
    unittest.main()

