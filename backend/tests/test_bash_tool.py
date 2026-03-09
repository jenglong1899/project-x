import subprocess
import unittest
from unittest import mock

from pydantic import ValidationError

from src.tools.bash import BASH_TOOL


class BashToolTests(unittest.TestCase):

    @mock.patch("src.tools.bash.subprocess.run")
    def test_bash_tool_runs_command_and_returns_result(self, mock_run: mock.Mock) -> None:
        mock_run.return_value = subprocess.CompletedProcess(
            args=["bash", "-lc", "pwd"],
            returncode=0,
            stdout="/tmp\n",
            stderr="",
        )

        result = BASH_TOOL.handler(arguments={"command": "pwd"})

        mock_run.assert_called_once_with(
            ["bash", "-lc", "pwd"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(
            result,
            {
                "stdout": "/tmp\n",
                "stderr": "",
                "returncode": 0,
            },
        )

    def test_bash_tool_rejects_invalid_arguments(self) -> None:
        with self.assertRaises(ValidationError):
            BASH_TOOL.handler(arguments={})

    def test_bash_tool_exposes_pydantic_schema(self) -> None:
        self.assertEqual(BASH_TOOL.parameters_json_schema["type"], "object")
        self.assertIn("command", BASH_TOOL.parameters_json_schema["required"])
        self.assertEqual(
            BASH_TOOL.parameters_json_schema["properties"]["command"]["type"],
            "string",
        )


if __name__ == "__main__":
    unittest.main()
