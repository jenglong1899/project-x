import os
import unittest

from src.tokenizer.token_counter import TokenCounter


class TokenCounterIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_prompt_tokens_match_real_api_usage(self) -> None:
        messages = [
            {"role": "system", "content": "你是一个简洁的助手。"},
            {"role": "user", "content": "嗨，你好吗？"},
        ]

        from litellm import acompletion  # type: ignore
        supported_model_configs: list[tuple[str, str, str]] = [
            (
                "openai/qwen3.5-flash",
                "https://dashscope.aliyuncs.com/compatible-mode/v1",
                os.getenv("DASHSCOPE_API_KEY", "").strip(),
            ),
            (
                "openai/qwen3.5-plus",
                "https://dashscope.aliyuncs.com/compatible-mode/v1",
                os.getenv("DASHSCOPE_API_KEY", "").strip(),
            ),
            (
                "openai/deepseek-v4-flash",
                "https://api.deepseek.com",
                os.getenv("DEEPSEEK_API_KEY", "").strip(),
            ),
            (
                "openai/deepseek-v4-pro",
                "https://api.deepseek.com",
                os.getenv("DEEPSEEK_API_KEY", "").strip(),
            ),
        ]

        counter = TokenCounter()
        for model, api_base, api_key in supported_model_configs:
            if not api_key:
                continue

            local_tokens, is_estimate = counter.count_messages_tokens(model, messages)
            if is_estimate:
                raise unittest.SkipTest("本地无法进行精确 token 统计（可能未安装 transformers 或 tokenizer 初始化失败）")

            resp = await acompletion(
                model=model,
                messages=messages,
                api_base=api_base,
                api_key=api_key,
                stream=False,
                temperature=0,
            )

            usage = getattr(resp, "usage", None) or (resp.get("usage") if isinstance(resp, dict) else None)
            if not usage or "prompt_tokens" not in usage:
                raise unittest.SkipTest("provider 未返回 usage.prompt_tokens，无法做对齐断言")

            self.assertEqual(local_tokens, usage["prompt_tokens"], msg=f"model={model}")

        if not any(api_key for _, _, api_key in supported_model_configs):
            raise unittest.SkipTest("未设置任何受支持模型的 API key，跳过对齐测试")


if __name__ == "__main__":
    unittest.main()
