import os
from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str
    credential_encryption_key: str
    database_path: str
    samad_base_url: str
    default_university: str
    browser_headless: bool

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Settings":
        values = os.environ if env is None else env
        required = ("TELEGRAM_BOT_TOKEN", "CREDENTIAL_ENCRYPTION_KEY")
        missing = [name for name in required if not values.get(name, "").strip()]
        if missing:
            raise ValueError(f"Missing required configuration: {', '.join(missing)}")

        headless_value = values.get("BROWSER_HEADLESS", "true").strip().lower()
        if headless_value not in {"true", "false", "1", "0", "yes", "no"}:
            raise ValueError("BROWSER_HEADLESS must be true/false, 1/0, or yes/no")

        database_path = values.get("DATABASE_PATH", "data/reservations.sqlite3").strip()
        samad_base_url = values.get("SAMAD_BASE_URL", "https://samad.app").strip().rstrip("/")
        default_university = values.get(
            "DEFAULT_UNIVERSITY",
            "دانشگاه شهید رجایی",
        ).strip()
        if not database_path:
            raise ValueError("DATABASE_PATH must not be empty")
        if not samad_base_url.startswith(("https://", "http://")):
            raise ValueError("SAMAD_BASE_URL must be an HTTP or HTTPS URL")
        if not default_university:
            raise ValueError("DEFAULT_UNIVERSITY must not be empty")

        return cls(
            telegram_bot_token=values["TELEGRAM_BOT_TOKEN"].strip(),
            credential_encryption_key=values["CREDENTIAL_ENCRYPTION_KEY"].strip(),
            database_path=database_path,
            samad_base_url=samad_base_url,
            default_university=default_university,
            browser_headless=headless_value in {"true", "1", "yes"},
        )
