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
        self.assertEqual(settings.samad_base_url, "https://food.sru.ac.ir")
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

    def test_accepts_a_four_digit_verbose_pin(self) -> None:
        settings = Settings.from_env(
            {
                "TELEGRAM_BOT_TOKEN": "bot-token",
                "CREDENTIAL_ENCRYPTION_KEY": "encryption-key",
                "VERBOSE_PIN": "0427",
            }
        )

        self.assertEqual(settings.verbose_pin, "0427")

    def test_rejects_a_verbose_pin_that_is_not_four_ascii_digits(self) -> None:
        for invalid_pin in ("123", "12345", "12a4", "۱۲۳۴"):
            with self.subTest(invalid_pin=invalid_pin):
                with self.assertRaisesRegex(ValueError, "VERBOSE_PIN"):
                    Settings.from_env(
                        {
                            "TELEGRAM_BOT_TOKEN": "bot-token",
                            "CREDENTIAL_ENCRYPTION_KEY": "encryption-key",
                            "VERBOSE_PIN": invalid_pin,
                        }
                    )

    def test_rejects_empty_database_path_and_non_https_samad_url(self) -> None:
        base = {
            "TELEGRAM_BOT_TOKEN": "bot-token",
            "CREDENTIAL_ENCRYPTION_KEY": "encryption-key",
        }

        with self.assertRaisesRegex(ValueError, "DATABASE_PATH"):
            Settings.from_env({**base, "DATABASE_PATH": " "})
        with self.assertRaisesRegex(ValueError, "SAMAD_BASE_URL"):
            Settings.from_env({**base, "SAMAD_BASE_URL": "samad.app"})
        with self.assertRaisesRegex(ValueError, "HTTPS"):
            Settings.from_env({**base, "SAMAD_BASE_URL": "http://food.sru.ac.ir"})
        with self.assertRaisesRegex(ValueError, "food.sru.ac.ir"):
            Settings.from_env({**base, "SAMAD_BASE_URL": "https://samad.app"})


if __name__ == "__main__":
    unittest.main()
