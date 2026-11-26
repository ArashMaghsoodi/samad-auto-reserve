import os
from dataclasses import dataclass
from typing import Mapping
from urllib.parse import urlsplit

LEGACY_PORTAL_HOST = "food.sru.ac.ir"


def validate_samad_base_url(value: str) -> str:
    normalized = value.strip().rstrip("/")
    parsed_url = urlsplit(normalized)
    try:
        port = parsed_url.port
    except ValueError as error:
        raise ValueError(
            f"SAMAD_BASE_URL must be an HTTPS URL on {LEGACY_PORTAL_HOST}"
        ) from error

    if (
        parsed_url.scheme != "https"
        or parsed_url.hostname != LEGACY_PORTAL_HOST
        or port not in (None, 443)
        or parsed_url.username is not None
        or parsed_url.password is not None
    ):
        raise ValueError(
            f"SAMAD_BASE_URL must be an HTTPS URL on {LEGACY_PORTAL_HOST}"
        )
    return normalized


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str
    credential_encryption_key: str
    database_path: str
    samad_base_url: str
    default_university: str
    browser_headless: bool
    verbose_pin: str | None = None

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
        samad_base_url = validate_samad_base_url(
            values.get(
                "SAMAD_BASE_URL",
                "https://food.sru.ac.ir",
            )
        )
        default_university = values.get(
            "DEFAULT_UNIVERSITY",
            "دانشگاه شهید رجایی",
        ).strip()
        if not database_path:
            raise ValueError("DATABASE_PATH must not be empty")
        if not default_university:
            raise ValueError("DEFAULT_UNIVERSITY must not be empty")
        verbose_pin = values.get("VERBOSE_PIN", "").strip() or None
        if verbose_pin is not None and (
            len(verbose_pin) != 4
            or any(character < "0" or character > "9" for character in verbose_pin)
        ):
            raise ValueError("VERBOSE_PIN must be exactly four ASCII digits")

        return cls(
            telegram_bot_token=values["TELEGRAM_BOT_TOKEN"].strip(),
            credential_encryption_key=values["CREDENTIAL_ENCRYPTION_KEY"].strip(),
            database_path=database_path,
            samad_base_url=samad_base_url,
            default_university=default_university,
            browser_headless=headless_value in {"true", "1", "yes"},
            verbose_pin=verbose_pin,
        )
