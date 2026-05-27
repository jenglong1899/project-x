import asyncio


class _FakeSseResponse:
    def __init__(self, *, chunks: list[str]) -> None:
        self._chunks = chunks

    async def aiter_text(self):
        for chunk in self._chunks:
            yield chunk


class TestCodexClientSseEvents:
    def test_parses_crlf_delimited_events(self) -> None:
        """
        回归测试：SSE 服务端常用 CRLF（\\r\\n）。如果只按 \\n\\n 切分，会永远解析不出事件，
        表现为“模型一直没响应”。
        """
        from src.core.codex_client import CodexClient

        async def _run() -> list[dict]:
            client = object.__new__(CodexClient)
            response = _FakeSseResponse(
                chunks=[
                    'data: {"type":"response.output_text.delta","delta":"H"}\r\n',
                    "\r\n",
                    'data: {"type":"response.output_text.delta","delta":"i"}\r\n\r\n',
                    "data: [DONE]\r\n\r\n",
                ]
            )
            return [event async for event in client._sse_events(response=response)]

        events = asyncio.run(_run())
        assert [e.get("delta") for e in events] == ["H", "i"]
