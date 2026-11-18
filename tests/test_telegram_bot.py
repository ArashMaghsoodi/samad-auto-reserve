import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock

import telebot
from cryptography.fernet import Fernet

from samad_reserver.config import Settings
from samad_reserver.domain import Meal, MealPreference
from samad_reserver.storage import UserRepository
from samad_reserver.telegram_bot import (
    TelegramBot,
    parse_clear_meal_command,
    parse_meal_preference_command,
)


class TelegramCommandTests(unittest.TestCase):
    def test_parses_english_meal_preference_with_multiword_place(self) -> None:
        preference = parse_meal_preference_command(
            "/setmeal saturday lunch Central Brothers Self"
        )

        self.assertEqual(preference, MealPreference(0, Meal.LUNCH, "Central Brothers Self"))

    def test_parses_persian_weekday_and_meal(self) -> None:
        preference = parse_meal_preference_command("/setmeal شنبه ناهار سلف مرکزی برادران")

        self.assertEqual(preference, MealPreference(0, Meal.LUNCH, "سلف مرکزی برادران"))

    def test_rejects_unknown_day_or_missing_place(self) -> None:
        with self.assertRaisesRegex(ValueError, "weekday"):
            parse_meal_preference_command("/setmeal someday lunch Hall")
        with self.assertRaisesRegex(ValueError, "usage"):
            parse_meal_preference_command("/setmeal saturday lunch")

    def test_parses_clear_meal_command(self) -> None:
        self.assertEqual(
            parse_clear_meal_command("/clearmeal friday dinner"),
            (6, Meal.DINNER),
        )

    def test_password_is_saved_and_incoming_message_deleted(self) -> None:
        with TemporaryDirectory() as temp_dir:
            repository = UserRepository(
                Path(temp_dir) / "users.sqlite3",
                Fernet.generate_key().decode("ascii"),
            )
            api = telebot.TeleBot("123456:ABCdefFakeTokenForTests")
            api.send_message = Mock()
            api.delete_message = Mock()
            settings = Settings(
                telegram_bot_token="123456:ABCdefFakeTokenForTests",
                credential_encryption_key=Fernet.generate_key().decode("ascii"),
                database_path=str(Path(temp_dir) / "unused.sqlite3"),
                samad_base_url="https://samad.app",
                default_university="دانشگاه شهید رجایی",
                browser_headless=True,
            )
            controller = TelegramBot(settings, repository, reservation_service=None, api=api)
            private_chat = SimpleNamespace(id=50, type="private")
            user = SimpleNamespace(id=123)

            controller._handle_credentials(
                SimpleNamespace(chat=private_chat, from_user=user)
            )
            controller._handle_pending_text(
                SimpleNamespace(
                    chat=private_chat,
                    from_user=user,
                    text="student-id",
                    message_id=1,
                )
            )
            password_message = SimpleNamespace(
                chat=private_chat,
                from_user=user,
                text="sensitive-password",
                message_id=2,
            )
            controller._handle_pending_text(password_message)

            self.assertEqual(
                repository.get_credentials(123).password,
                "sensitive-password",
            )
            api.delete_message.assert_called_once_with(50, 2)

    def test_credentials_cannot_be_submitted_from_group_chats(self) -> None:
        with TemporaryDirectory() as temp_dir:
            repository = UserRepository(
                Path(temp_dir) / "users.sqlite3",
                Fernet.generate_key().decode("ascii"),
            )
            api = telebot.TeleBot("123456:ABCdefFakeTokenForTests")
            api.send_message = Mock()
            api.delete_message = Mock()
            settings = Settings(
                telegram_bot_token="123456:ABCdefFakeTokenForTests",
                credential_encryption_key=Fernet.generate_key().decode("ascii"),
                database_path=str(Path(temp_dir) / "unused.sqlite3"),
                samad_base_url="https://samad.app",
                default_university="دانشگاه شهید رجایی",
                browser_headless=True,
            )
            controller = TelegramBot(settings, repository, reservation_service=None, api=api)
            group_chat = SimpleNamespace(id=-50, type="group")
            user = SimpleNamespace(id=123)

            controller._handle_credentials(
                SimpleNamespace(chat=group_chat, from_user=user)
            )

            self.assertIsNone(repository.get_credentials(123))
            self.assertNotIn(123, controller._states)
            api.send_message.assert_called_once()

    def test_password_sent_in_group_is_not_stored(self) -> None:
        with TemporaryDirectory() as temp_dir:
            repository = UserRepository(
                Path(temp_dir) / "users.sqlite3",
                Fernet.generate_key().decode("ascii"),
            )
            api = telebot.TeleBot("123456:ABCdefFakeTokenForTests")
            api.send_message = Mock()
            api.delete_message = Mock()
            settings = Settings(
                telegram_bot_token="123456:ABCdefFakeTokenForTests",
                credential_encryption_key=Fernet.generate_key().decode("ascii"),
                database_path=str(Path(temp_dir) / "unused.sqlite3"),
                samad_base_url="https://samad.app",
                default_university="دانشگاه شهید رجایی",
                browser_headless=True,
            )
            controller = TelegramBot(settings, repository, reservation_service=None, api=api)
            private_chat = SimpleNamespace(id=50, type="private")
            group_chat = SimpleNamespace(id=-50, type="group")
            user = SimpleNamespace(id=123)
            controller._handle_credentials(
                SimpleNamespace(chat=private_chat, from_user=user)
            )
            controller._handle_pending_text(
                SimpleNamespace(chat=private_chat, from_user=user, text="student-id")
            )

            group_password = SimpleNamespace(
                chat=group_chat,
                from_user=user,
                text="sensitive-password",
                message_id=3,
            )
            controller._handle_pending_text(group_password)

            self.assertIsNone(repository.get_credentials(123))
            api.delete_message.assert_called_once_with(-50, 3)

    def test_setmeal_handler_keeps_distinct_meal_place_choices(self) -> None:
        with TemporaryDirectory() as temp_dir:
            repository = UserRepository(
                Path(temp_dir) / "users.sqlite3",
                Fernet.generate_key().decode("ascii"),
            )
            api = telebot.TeleBot("123456:ABCdefFakeTokenForTests")
            api.send_message = Mock()
            settings = Settings(
                telegram_bot_token="123456:ABCdefFakeTokenForTests",
                credential_encryption_key=Fernet.generate_key().decode("ascii"),
                database_path=str(Path(temp_dir) / "unused.sqlite3"),
                samad_base_url="https://samad.app",
                default_university="دانشگاه شهید رجایی",
                browser_headless=True,
            )
            controller = TelegramBot(settings, repository, reservation_service=None, api=api)
            private_chat = SimpleNamespace(id=50, type="private")
            user = SimpleNamespace(id=123)

            controller._handle_set_meal(
                SimpleNamespace(
                    chat=private_chat,
                    from_user=user,
                    text="/setmeal saturday lunch سلف مرکزی برادران",
                )
            )
            controller._handle_set_meal(
                SimpleNamespace(
                    chat=private_chat,
                    from_user=user,
                    text="/setmeal saturday dinner حافظ",
                )
            )

            self.assertEqual(
                repository.get_preferences(123),
                [
                    MealPreference(0, Meal.DINNER, "حافظ"),
                    MealPreference(0, Meal.LUNCH, "سلف مرکزی برادران"),
                ],
            )


if __name__ == "__main__":
    unittest.main()
