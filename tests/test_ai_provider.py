import sys
import types
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from jarvis_bot import ai_provider


def fake_settings(provider="openai", key="", model="claude-test"):
    return SimpleNamespace(ai_provider=provider, anthropic_api_key=key, claude_model=model)


def fake_openai(text="openai javobi"):
    client = MagicMock()
    client.responses.create.return_value.output_text = f"  {text} "
    return client


def fake_anthropic_module(text="claude javobi", error=None):
    module = types.ModuleType("anthropic")
    client = MagicMock()
    if error:
        client.messages.create.side_effect = error
    else:
        client.messages.create.return_value.content = [SimpleNamespace(type="text", text=f" {text} ")]
    module.Anthropic = MagicMock(return_value=client)
    return module, client


class AiProviderTests(unittest.TestCase):
    def test_default_uses_openai(self):
        openai = fake_openai()
        with patch.object(ai_provider, "settings", fake_settings()):
            self.assertEqual(ai_provider.javob_ol(openai, "salom"), "openai javobi")
        openai.responses.create.assert_called_once()

    def test_claude_used_when_enabled(self):
        openai = fake_openai()
        module, claude = fake_anthropic_module()
        with patch.object(ai_provider, "settings", fake_settings("claude", "kalit")), \
                patch.dict(sys.modules, {"anthropic": module}):
            self.assertEqual(ai_provider.javob_ol(openai, "salom"), "claude javobi")
        openai.responses.create.assert_not_called()
        self.assertEqual(claude.messages.create.call_args.kwargs["model"], "claude-test")

    def test_claude_without_key_uses_openai(self):
        openai = fake_openai()
        with patch.object(ai_provider, "settings", fake_settings("claude", "")):
            self.assertEqual(ai_provider.javob_ol(openai, "salom"), "openai javobi")

    def test_claude_error_falls_back_to_openai(self):
        openai = fake_openai()
        module, _ = fake_anthropic_module(error=RuntimeError("limit"))
        with patch.object(ai_provider, "settings", fake_settings("claude", "kalit")), \
                patch.dict(sys.modules, {"anthropic": module}), \
                self.assertLogs(ai_provider.logger, level="ERROR"):
            self.assertEqual(ai_provider.javob_ol(openai, "salom"), "openai javobi")


if __name__ == "__main__":
    unittest.main()
