import unittest
import logging
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock

import telebot
from cryptography.fernet import Fernet

from samad_reserver.config import Settings
from samad_reserver.domain import Meal, MealPreference
from samad_reserver.errors import InsufficientBalanceError
from samad_reserver.storage import UserRepository
from samad_reserver.telegram_bot import (
    TelegramBot,
    parse_clear_meal_command,
    parse_meal_preference_command,
)


class TelegramCommandTests(unittest.TestCase):
    def test_manual_run_reports_insufficient_balance_in_persian(self) -> None:
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
                samad_base_url="https://food.sru.ac.ir",
                default_university="دانشگاه شهید رجایی",
                browser_headless=True,
            )
            service = SimpleNamespace(
                run_for_user=Mock(
                    side_effect=InsufficientBalanceError(60_000, 50_000)
                ),
                check_balance_for_user=Mock(),
            )
            controller = TelegramBot(settings, repository, service, api=api)
            message = SimpleNamespace(
                chat=SimpleNamespace(id=50, type="private"),
                from_user=SimpleNamespace(id=123),
            )

            controller._handle_run(message)

            sent_text = api.send_message.call_args.args[1]
            self.assertIn("اعتبار شما کم است", sent_text)
            self.assertIn("60,000", sent_text)
            self.assertIn("50,000", sent_text)

    def test_parses_english_meal_preference_with_multiword_place(self) -> None:
        preference = parse_meal_preference_command(
            "/setmeal saturday lunch Central Brothers Self"
        )

        self.assertEqual(preference, MealPreference(0, Meal.LUNCH, "Central Brothers Self"))

    def test_parses_persian_weekday_and_meal(self) -> None:
        preference = parse_meal_preference_command("/setmeal شنبه ناهار سلف مرکزی برادران")

        self.assertEqual(preference, MealPreference(0, Meal.LUNCH, "سلف مرکزی برادران"))

    def test_parses_multiword_persian_weekday_and_preserves_dining_hall_name(self) -> None:
        preference = parse_meal_preference_command(
            "/setmeal سه شنبه ناهار سلف مرکزی برادران"
        )

        self.assertEqual(preference, MealPreference(3, Meal.LUNCH, "سلف مرکزی برادران"))

    def test_parses_multiword_persian_weekday_for_clear_meal(self) -> None:
        self.assertEqual(
            parse_clear_meal_command("/clearmeal سه شنبه شام"),
            (3, Meal.DINNER),
        )

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
                samad_base_url="https://food.sru.ac.ir",
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
                samad_base_url="https://food.sru.ac.ir",
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
                samad_base_url="https://food.sru.ac.ir",
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
                samad_base_url="https://food.sru.ac.ir",
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

    def test_verbose_command_requests_pin_then_enables_debug_logging(self) -> None:
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
                samad_base_url="https://food.sru.ac.ir",
                default_university="دانشگاه شهید رجایی",
                browser_headless=True,
                verbose_pin="0427",
            )
            controller = TelegramBot(settings, repository, reservation_service=None, api=api)
            private_chat = SimpleNamespace(id=50, type="private")
            user = SimpleNamespace(id=123)
            command = SimpleNamespace(chat=private_chat, from_user=user)

            controller._handle_verbose(command)

            self.assertEqual(controller._get_state(123), "verbose_pin")
            self.assertIn("PIN", api.send_message.call_args.args[1])

            controller._handle_pending_text(
                SimpleNamespace(
                    chat=private_chat,
                    from_user=user,
                    text="0427",
                    message_id=9,
                )
            )

            self.assertEqual(logging.getLogger("samad_reserver").level, logging.DEBUG)
            self.assertIsNone(controller._get_state(123))
            api.delete_message.assert_called_once_with(50, 9)
            self.assertNotIn("0427", api.send_message.call_args.args[1])

            controller._handle_verbose(command)
            controller._handle_pending_text(
                SimpleNamespace(
                    chat=private_chat,
                    from_user=user,
                    text="0427",
                    message_id=11,
                )
            )

            self.assertNotEqual(
                logging.getLogger("samad_reserver").level,
                logging.DEBUG,
            )
            self.assertEqual(api.delete_message.call_count, 2)
            controller._set_verbose_logging(False)

    def test_verbose_command_rejects_wrong_pin_without_enabling_debug(self) -> None:
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
                samad_base_url="https://food.sru.ac.ir",
                default_university="دانشگاه شهید رجایی",
                browser_headless=True,
                verbose_pin="0427",
            )
            controller = TelegramBot(settings, repository, reservation_service=None, api=api)
            private_chat = SimpleNamespace(id=50, type="private")
            user = SimpleNamespace(id=123)

            controller._handle_verbose(
                SimpleNamespace(chat=private_chat, from_user=user)
            )
            controller._handle_pending_text(
                SimpleNamespace(
                    chat=private_chat,
                    from_user=user,
                    text="9999",
                    message_id=10,
                )
            )

            self.assertNotEqual(logging.getLogger("samad_reserver").level, logging.DEBUG)
            self.assertEqual(controller._get_state(123), "verbose_pin")
            api.delete_message.assert_called_once_with(50, 10)
            self.assertNotIn("9999", api.send_message.call_args.args[1])

    def test_verbose_command_is_disabled_when_pin_is_not_configured(self) -> None:
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
                samad_base_url="https://food.sru.ac.ir",
                default_university="دانشگاه شهید رجایی",
                browser_headless=True,
            )
            controller = TelegramBot(settings, repository, reservation_service=None, api=api)

            controller._handle_verbose(
                SimpleNamespace(
                    chat=SimpleNamespace(id=50, type="private"),
                    from_user=SimpleNamespace(id=123),
                )
            )

            self.assertIsNone(controller._get_state(123))
            self.assertIn("not configured", api.send_message.call_args.args[1])

    def test_verbose_pin_locks_after_five_incorrect_attempts(self) -> None:
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
                samad_base_url="https://food.sru.ac.ir",
                default_university="دانشگاه شهید رجایی",
                browser_headless=True,
                verbose_pin="0427",
            )
            controller = TelegramBot(settings, repository, reservation_service=None, api=api)
            private_chat = SimpleNamespace(id=50, type="private")
            user = SimpleNamespace(id=123)

            controller._handle_verbose(
                SimpleNamespace(chat=private_chat, from_user=user)
            )
            for attempt in range(5):
                controller._handle_pending_text(
                    SimpleNamespace(
                        chat=private_chat,
                        from_user=user,
                        text="9999",
                        message_id=20 + attempt,
                    )
                )

            self.assertIsNone(controller._get_state(123))
            self.assertIn(123, controller._verbose_pin_locked_users)
            self.assertIn("locked", api.send_message.call_args.args[1].lower())
            self.assertEqual(api.delete_message.call_count, 5)

    def test_start_polling_registers_verbose_in_the_telegram_command_menu(self) -> None:
        with TemporaryDirectory() as temp_dir:
            repository = UserRepository(
                Path(temp_dir) / "users.sqlite3",
                Fernet.generate_key().decode("ascii"),
            )
            api = telebot.TeleBot("123456:ABCdefFakeTokenForTests")
            api.set_my_commands = Mock()
            api.infinity_polling = Mock()
            settings = Settings(
                telegram_bot_token="123456:ABCdefFakeTokenForTests",
                credential_encryption_key=Fernet.generate_key().decode("ascii"),
                database_path=str(Path(temp_dir) / "unused.sqlite3"),
                samad_base_url="https://food.sru.ac.ir",
                default_university="دانشگاه شهید رجایی",
                browser_headless=True,
            )
            controller = TelegramBot(settings, repository, reservation_service=None, api=api)

            controller.start_polling()

            commands = api.set_my_commands.call_args.args[0]
            self.assertIn("verbose", [command.command for command in commands])


if __name__ == "__main__":
    unittest.main()
