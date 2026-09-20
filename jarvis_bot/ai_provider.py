"""Suhbat javoblari uchun AI provayder tanlash (OpenAI yoki Claude)."""

import asyncio
import logging

from .config import settings

logger = logging.getLogger(__name__)

OPENAI_CHAT_MODEL = "gpt-5.6"
CLAUDE_MAX_TOKENS = 1500


def _claude_javob(prompt: str) -> str:
    import anthropic

    claude = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    response = claude.messages.create(
        model=settings.claude_model,
        max_tokens=CLAUDE_MAX_TOKENS,
        messages=[{"role": "user", "content": prompt}],
    )
    return "".join(
        block.text for block in response.content if getattr(block, "type", "") == "text"
    ).strip()


def _openai_javob(openai_client, prompt: str) -> str:
    response = openai_client.responses.create(model=OPENAI_CHAT_MODEL, input=prompt)
    return response.output_text.strip()


def javob_ol(openai_client, prompt: str) -> str:
    """AI_PROVIDER=claude bo'lsa Claude, aks holda OpenAI. Claude xato bersa OpenAI'ga qaytadi."""
    if settings.ai_provider == "claude" and settings.anthropic_api_key:
        try:
            text = _claude_javob(prompt)
            if text:
                return text
        except Exception:
            logger.exception("Claude javob bermadi, OpenAI'ga o'tildi")
    return _openai_javob(openai_client, prompt)


async def javob_ol_async(openai_client, prompt: str) -> str:
    return await asyncio.to_thread(javob_ol, openai_client, prompt)
