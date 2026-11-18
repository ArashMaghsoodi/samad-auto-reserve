from contextlib import closing
import sqlite3
import tempfile
import unittest
from pathlib import Path

from cryptography.fernet import Fernet

from samad_reserver.domain import Meal, MealPreference
from samad_reserver.storage import CredentialDecryptionError, UserRepository


class UserRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp_dir.name) / "users.sqlite3"
        self.repository = UserRepository(
            self.database_path,
            Fernet.generate_key().decode("ascii"),
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_credentials_round_trip_without_plaintext_in_database(self) -> None:
        self.repository.save_credentials(123, "student-id", "samad-password", "University")

        credentials = self.repository.get_credentials(123)
        with closing(sqlite3.connect(self.database_path)) as connection:
            stored_value = connection.execute(
                "SELECT password_ciphertext FROM users WHERE telegram_user_id = ?",
                (123,),
            ).fetchone()[0]

        self.assertEqual(credentials.username, "student-id")
        self.assertEqual(credentials.password, "samad-password")
        self.assertNotIn(b"samad-password", stored_value)

    def test_preferences_are_scoped_to_user_and_replace_atomically(self) -> None:
        self.repository.save_credentials(123, "one", "password-one", "University")
        self.repository.save_credentials(456, "two", "password-two", "University")
        initial = [MealPreference(0, Meal.LUNCH, "Central Brothers")]
        self.repository.replace_preferences(123, initial)

        with self.assertRaisesRegex(ValueError, "duplicate weekday and meal"):
            self.repository.replace_preferences(
                123,
                [
                    MealPreference(1, Meal.DINNER, "Hafez"),
                    MealPreference(1, Meal.DINNER, "Central Brothers"),
                ],
            )

        self.assertEqual(self.repository.get_preferences(123), initial)
        self.assertEqual(self.repository.get_preferences(456), [])

    def test_rejects_invalid_weekday_and_empty_dining_hall(self) -> None:
        with self.assertRaisesRegex(ValueError, "weekday"):
            MealPreference(7, Meal.LUNCH, "Hall")
        with self.assertRaisesRegex(ValueError, "weekday"):
            MealPreference("0", Meal.LUNCH, "Hall")
        with self.assertRaisesRegex(ValueError, "dining hall"):
            MealPreference(0, Meal.LUNCH, "  ")

    def test_wrong_encryption_key_fails_explicitly(self) -> None:
        self.repository.save_credentials(123, "student-id", "samad-password", "University")
        other_repository = UserRepository(
            self.database_path,
            Fernet.generate_key().decode("ascii"),
        )

        with self.assertRaisesRegex(CredentialDecryptionError, "verify the configured encryption key"):
            other_repository.get_credentials(123)

    def test_weekly_schedule_requires_a_valid_time(self) -> None:
        self.repository.save_credentials(123, "student-id", "password", "University")
        with self.assertRaisesRegex(ValueError, "HH:MM"):
            self.repository.update_schedule(123, "25:90", True)

        self.repository.update_schedule(123, "07:30", True)

        profile = self.repository.list_scheduled_profiles()[0]
        self.assertEqual(profile.telegram_user_id, 123)
        self.assertEqual(profile.weekly_run_time, "07:30")
        self.assertTrue(profile.weekly_enabled)


if __name__ == "__main__":
    unittest.main()
