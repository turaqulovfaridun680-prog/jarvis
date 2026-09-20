"""Muhit sozlamalarini bitta joyda o'qish va tekshirish."""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


ROOT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(ROOT_DIR / ".env")


@dataclass(frozen=True)
class Settings:
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    telegram_bot_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    ai_provider: str = os.getenv("AI_PROVIDER", "openai").strip().lower()
    anthropic_api_key: str = os.getenv("ANTHROPIC_API_KEY", "")
    claude_model: str = os.getenv("CLAUDE_MODEL", "claude-sonnet-5")
    zilola_chat_id: str = os.getenv("ZILOLA_CHAT_ID", "")
    delivery_report_chat_id: str = os.getenv("DASTAVKA_REPORT_CHAT_ID", "")
    debt_group_chat_id: str = os.getenv("DEBT_GROUP_CHAT_ID", "")
    bozor_group_chat_id: str = os.getenv("BOZOR_GROUP_CHAT_ID", "")
    driver_registration_secret: str = os.getenv("DRIVER_REGISTRATION_SECRET", "")
    admin_user_ids: frozenset[str] = frozenset(
        value.strip()
        for value in os.getenv("ADMIN_USER_IDS", "").split(",")
        if value.strip()
    )
    google_credentials_file: Path = Path(
        os.getenv("GOOGLE_CREDENTIALS_FILE") or str(ROOT_DIR / "google_credentials.json")
    )

    def validate(self):
        missing = [
            name
            for name, value in (
                ("OPENAI_API_KEY", self.openai_api_key),
                ("TELEGRAM_BOT_TOKEN", self.telegram_bot_token),
            )
            if not value
        ]
        if missing:
            raise RuntimeError("Majburiy sozlama topilmadi: " + ", ".join(missing))
        for name, value in (
            ("DEBT_GROUP_CHAT_ID", self.debt_group_chat_id),
            ("BOZOR_GROUP_CHAT_ID", self.bozor_group_chat_id),
            ("ZILOLA_CHAT_ID", self.zilola_chat_id),
            ("DASTAVKA_REPORT_CHAT_ID", self.delivery_report_chat_id),
        ):
            if value:
                try:
                    int(value)
                except ValueError as exc:
                    raise RuntimeError(f"{name} butun Telegram ID bo'lishi kerak") from exc


settings = Settings()
