import unittest

from src.tokenizer.token_counter import DEFAULT_CONTEXT_WINDOW, TokenCounter, TokenCounterError, TokenizerRegistry


class _FailingRegistry(TokenizerRegistry):
    def get_tokenizer(self, tokenizer_id: str) -> object:  # noqa: ARG002
        raise TokenCounterError("模拟：tokenizer 初始化失败")


class TokenCounterTests(unittest.TestCase):
    def test_unknown_message_key_fail_fast(self) -> None:
        counter = TokenCounter(registry=_FailingRegistry())

        with self.assertRaises(TokenCounterError):
            counter.count_messages_tokens(
                "openai/deepseek-v4-flash",
                [{"role": "user", "content": "hi", "unknown_field": 1}],
            )

    def test_count_text_tokens_falls_back_to_estimate(self) -> None:
        counter = TokenCounter(registry=_FailingRegistry())

        text = "你好，world"
        tokens, is_estimate = counter.count_text_tokens("openai/deepseek-v4-flash", text)

        self.assertTrue(is_estimate)
        expected = (len(text.encode("utf-8")) + 3) // 4
        self.assertEqual(tokens, expected)

    def test_count_messages_tokens_falls_back_to_estimate(self) -> None:
        counter = TokenCounter(registry=_FailingRegistry())

        messages = [{"role": "user", "content": "你好，world"}]
        tokens, is_estimate = counter.count_messages_tokens("openai/deepseek-v4-flash", messages)

        self.assertTrue(is_estimate)
        expected = counter.estimate_messages_tokens_by_chars("openai/deepseek-v4-flash", messages)
        self.assertEqual(tokens, expected)

    def test_reasoning_content_is_accepted_and_counted_by_folding_into_content(self) -> None:
        counter = TokenCounter(registry=_FailingRegistry())

        messages = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "ok", "reasoning_content": "（隐藏推理）"},
        ]
        tokens, is_estimate = counter.count_messages_tokens("openai/deepseek-v4-flash", messages)

        self.assertTrue(is_estimate)
        expected = counter.estimate_messages_tokens_by_chars("openai/deepseek-v4-flash", messages)
        self.assertEqual(tokens, expected)

        base_messages = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "ok"},
        ]
        base_tokens = counter.estimate_messages_tokens_by_chars("openai/deepseek-v4-flash", base_messages)
        self.assertGreater(tokens, base_tokens)

    def test_token_percentage_propagates_estimate_flag(self) -> None:
        counter = TokenCounter(registry=_FailingRegistry())

        messages = [{"role": "user", "content": "你好，world"}]
        percentage, is_estimate = counter.token_percentage("openai/deepseek-v4-flash", messages)

        self.assertTrue(is_estimate)
        self.assertGreaterEqual(percentage, 1)

    def test_context_window_falls_back_for_unknown_model(self) -> None:
        counter = TokenCounter(registry=_FailingRegistry())

        self.assertEqual(counter.context_window("mock"), DEFAULT_CONTEXT_WINDOW)


if __name__ == "__main__":
    unittest.main()
