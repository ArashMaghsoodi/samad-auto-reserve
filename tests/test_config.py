import unittest

from samad_reserver.config import Settings


class SettingsTests(unittest.TestCase):
    def test_loads_required_secrets_and_uses_safe_defaults(self) -> None:
        settings = Settings.from_env(
            {
                "TELEGRAM_BOT_TOKEN": "bot-token",
                "CREDENTIAL_ENCRYPTION_KEY": "encryption-key",
            }
        )

        self.assertEqual(settings.telegram_bot_token, "bot-token")
        self.assertEqual(settings.credential_encryption_key, "encryption-key")
        self.assertEqual(settings.database_path, "data/reservations.sqlite3")
        self.assertEqual(settings.samad_base_url, "https://samad.app")
        self.assertEqual(settings.default_university, "دانشگاه شهید رجایی")
        self.assertTrue(settings.browser_headless)

    def test_missing_required_secrets_are_reported_together(self) -> None:
        with self.assertRaisesRegex(ValueError, "TELEGRAM_BOT_TOKEN.*CREDENTIAL_ENCRYPTION_KEY"):
            Settings.from_env({})

    def test_rejects_ambiguous_boolean_configuration(self) -> None:
        with self.assertRaisesRegex(ValueError, "BROWSER_HEADLESS"):
            Settings.from_env(
                {
                    "TELEGRAM_BOT_TOKEN": "bot-token",
                    "CREDENTIAL_ENCRYPTION_KEY": "encryption-key",
                    "BROWSER_HEADLESS": "sometimes",
                }
            )

    def test_allows_database_and_university_overrides(self) -> None:
        settings = Settings.from_env(
            {
                "TELEGRAM_BOT_TOKEN": "bot-token",
                "CREDENTIAL_ENCRYPTION_KEY": "encryption-key",
                "DATABASE_PATH": "tmp/test.sqlite3",
                "DEFAULT_UNIVERSITY": "University",
                "BROWSER_HEADLESS": "false",
            }
        )

        self.assertEqual(settings.database_path, "tmp/test.sqlite3")
        self.assertEqual(settings.default_university, "University")
        self.assertFalse(settings.browser_headless)

    def test_rejects_empty_database_path_and_non_http_samad_url(self) -> None:
        base = {
            "TELEGRAM_BOT_TOKEN": "bot-token",
            "CREDENTIAL_ENCRYPTION_KEY": "encryption-key",
        }

        with self.assertRaisesRegex(ValueError, "DATABASE_PATH"):
            Settings.from_env({**base, "DATABASE_PATH": " "})
        with self.assertRaisesRegex(ValueError, "SAMAD_BASE_URL"):
            Settings.from_env({**base, "SAMAD_BASE_URL": "samad.app"})


if __name__ == "__main__":
    unittest.main()
