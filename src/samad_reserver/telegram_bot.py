import logging
import sqlite3
import threading
from typing import Callable, Protocol

import telebot
from telebot.apihelper import ApiException

from samad_reserver.config import Settings
from samad_reserver.domain import Meal, MealPreference, Weekday
from samad_reserver.errors import InsufficientBalanceError, ReservationError
from samad_reserver.storage import UserRepository

LOGGER = logging.getLogger(__name__)

_WEEKDAY_ALIASES = {
    "saturday": Weekday.SATURDAY,
    "sat": Weekday.SATURDAY,
    "sunday": Weekday.SUNDAY,
    "sun": Weekday.SUNDAY,
    "monday": Weekday.MONDAY,
    "mon": Weekday.MONDAY,
    "tuesday": Weekday.TUESDAY,
    "tue": Weekday.TUESDAY,
    "tues": Weekday.TUESDAY,
    "wednesday": Weekday.WEDNESDAY,
    "wed": Weekday.WEDNESDAY,
    "thursday": Weekday.THURSDAY,
    "thu": Weekday.THURSDAY,
    "thur": Weekday.THURSDAY,
    "thurs": Weekday.THURSDAY,
    "friday": Weekday.FRIDAY,
    "fri": Weekday.FRIDAY,
}

_MEAL_ALIASES = {
    "breakfast": Meal.BREAKFAST,
    "صبحانه": Meal.BREAKFAST,
    "lunch": Meal.LUNCH,
    "ناهار": Meal.LUNCH,
    "dinner": Meal.DINNER,
    "شام": Meal.DINNER,
}


def _normalize(value: str) -> str:
    return value.strip().casefold().replace("ي", "ی").replace("ك", "ک")


def parse_weekday(value: str) -> Weekday:
    normalized = _normalize(value)
    if normalized.isdecimal():
        try:
            return Weekday(int(normalized))
        except ValueError as error:
            raise ValueError("weekday must be Saturday-Friday or a number from 0 to 6") from error
    for weekday in Weekday:
        if normalized == _normalize(weekday.persian_label):
            return weekday
    weekday = _WEEKDAY_ALIASES.get(normalized)
    if weekday is None:
        raise ValueError("weekday must be Saturday-Friday or a number from 0 to 6")
    return weekday


def parse_meal(value: str) -> Meal:
    meal = _MEAL_ALIASES.get(_normalize(value))
    if meal is None:
        raise ValueError("meal must be breakfast, lunch, or dinner")
    return meal


def parse_meal_preference_command(command: str) -> MealPreference:
    parts = command.strip().split(maxsplit=3)
    if len(parts) != 4 or parts[0].split("@", maxsplit=1)[0] != "/setmeal":
        raise ValueError("usage: /setmeal <weekday> <meal> <exact dining hall name>")
    weekday = parse_weekday(parts[1])
    meal = parse_meal(parts[2])
    return MealPreference(weekday=weekday, meal=meal, dining_hall=parts[3])


def parse_clear_meal_command(command: str) -> tuple[int, Meal]:
    parts = command.strip().split()
    if len(parts) != 3 or parts[0].split("@", maxsplit=1)[0] != "/clearmeal":
        raise ValueError("usage: /clearmeal <weekday> <meal>")
    return int(parse_weekday(parts[1])), parse_meal(parts[2])


class ReservationService(Protocol):
    def run_for_user(self, telegram_user_id: int) -> str: ...

    def check_balance_for_user(self, telegram_user_id: int) -> str: ...


class TelegramBot:
    def __init__(
        self,
        settings: Settings,
        repository: UserRepository,
        reservation_service: ReservationService | None,
        on_schedule_changed: Callable[[], None] | None = None,
        api: telebot.TeleBot | None = None,
    ) -> None:
        self.settings = settings
        self.repository = repository
        self.reservation_service = reservation_service
        self.on_schedule_changed = on_schedule_changed
        self.api = api or telebot.TeleBot(settings.telegram_bot_token)
        self._states: dict[int, str] = {}
        self._usernames: dict[int, str] = {}
        self._state_lock = threading.Lock()
        self._register_handlers()

    def _register_handlers(self) -> None:
        self.api.message_handler(commands=["start"])(self._handle_start)
        self.api.message_handler(commands=["help"])(self._handle_help)
        self.api.message_handler(commands=["credentials"])(self._handle_credentials)
        self.api.message_handler(commands=["cancel"])(self._handle_cancel)
        self.api.message_handler(commands=["university"])(self._handle_university)
        self.api.message_handler(commands=["setmeal"])(self._handle_set_meal)
        self.api.message_handler(commands=["clearmeal"])(self._handle_clear_meal)
        self.api.message_handler(commands=["preferences"])(self._handle_preferences)
        self.api.message_handler(commands=["schedule"])(self._handle_schedule)
        self.api.message_handler(commands=["forget"])(self._handle_forget)
        self.api.message_handler(commands=["balance"])(self._handle_balance)
        self.api.message_handler(commands=["run"])(self._handle_run)
        self.api.message_handler(
            func=lambda message: self._get_state(message.from_user.id) is not None,
            content_types=["text"],
        )(self._handle_pending_text)

    @staticmethod
    def _user_id(message: telebot.types.Message) -> int:
        return message.from_user.id

    def _require_private(self, message: telebot.types.Message) -> bool:
        if message.chat.type == "private":
            return True
        self.api.send_message(
            message.chat.id,
            "For privacy, use this bot only in a private chat.",
        )
        return False

    def _get_state(self, user_id: int) -> str | None:
        with self._state_lock:
            return self._states.get(user_id)

    def _set_state(self, user_id: int, state: str | None) -> None:
        with self._state_lock:
            if state is None:
                self._states.pop(user_id, None)
            else:
                self._states[user_id] = state

    def _ensure_user(self, user_id: int) -> None:
        self.repository.ensure_user(user_id, self.settings.default_university)

    @staticmethod
    def _command_arguments(message: telebot.types.Message) -> str:
        parts = (message.text or "").split(maxsplit=1)
        return parts[1].strip() if len(parts) == 2 else ""

    def _handle_start(self, message: telebot.types.Message) -> None:
        if not self._require_private(message):
            return
        try:
            self._ensure_user(self._user_id(message))
        except sqlite3.Error:
            LOGGER.exception("Could not initialize Telegram user %s", self._user_id(message))
            self.api.send_message(message.chat.id, "Setup failed. Please try again later.")
            return
        self.api.send_message(
            message.chat.id,
            "Welcome to Samad Auto Reserve.\n"
            "Configure your account with /credentials and /university, then use "
            "/setmeal <weekday> <meal> <exact dining hall name>.\n"
            "Use /help to see all commands.",
        )

    def _handle_help(self, message: telebot.types.Message) -> None:
        if not self._require_private(message):
            return
        self.api.send_message(
            message.chat.id,
            "Commands:\n"
            "/credentials - securely replace your Samad username and password\n"
            "/university <name> - set your university\n"
            "/setmeal <weekday> <meal> <dining hall> - set one exact place for a meal\n"
            "/clearmeal <weekday> <meal> - remove one preference\n"
            "/preferences - review your weekly meal settings\n"
            "/schedule HH:MM - enable the Saturday weekly run at that Tehran time\n"
            "/schedule off - disable automatic runs\n"
            "/balance - check your Samad wallet\n"
            "/run - manually run configured reservations\n"
            "/forget - remove saved credentials and disable scheduling\n"
            "/cancel - cancel an active setup prompt\n\n"
            "Weekdays accept Saturday-Friday or Persian weekday names; meals accept "
            "breakfast/lunch/dinner or صبحانه/ناهار/شام.",
        )

    def _handle_credentials(self, message: telebot.types.Message) -> None:
        if not self._require_private(message):
            return
        user_id = self._user_id(message)
        try:
            self._ensure_user(user_id)
        except sqlite3.Error:
            LOGGER.exception("Could not initialize Telegram user %s", user_id)
            self.api.send_message(message.chat.id, "Setup failed. Please try again later.")
            return
        with self._state_lock:
            self._usernames.pop(user_id, None)
            self._states[user_id] = "username"
        self.api.send_message(
            message.chat.id,
            "Security notice: Telegram bot chats are not end-to-end encrypted. "
            "Continue only if you accept Telegram's chat privacy limits. "
            "Your password will be encrypted in local storage, and the password message "
            "will be deleted on a best-effort basis.\n\nSend your Samad username or student ID.",
        )

    def _handle_cancel(self, message: telebot.types.Message) -> None:
        if not self._require_private(message):
            return
        user_id = self._user_id(message)
        with self._state_lock:
            had_state = self._states.pop(user_id, None) is not None
            self._usernames.pop(user_id, None)
        self.api.send_message(
            message.chat.id,
            "Setup cancelled." if had_state else "There is no active setup to cancel.",
        )

    def _handle_pending_text(self, message: telebot.types.Message) -> None:
        user_id = self._user_id(message)
        state = self._get_state(user_id)
        if state is None:
            return
        if not self._require_private(message):
            if state == "password":
                self._delete_password_message(message)
            return

        text = message.text or ""
        if text.startswith("/"):
            self.api.send_message(
                message.chat.id,
                "Send the requested plain-text value or use /cancel.",
            )
            return
        if state == "username":
            username = text.strip()
            if not username:
                self.api.send_message(message.chat.id, "Username cannot be empty. Try again.")
                return
            with self._state_lock:
                self._usernames[user_id] = username
                self._states[user_id] = "password"
            self.api.send_message(message.chat.id, "Now send your Samad password.")
            return
        if state == "password":
            self._save_password(message, text)

    def _save_password(self, message: telebot.types.Message, password: str) -> None:
        user_id = self._user_id(message)
        with self._state_lock:
            username = self._usernames.pop(user_id, None)
            self._states.pop(user_id, None)

        saved = False
        try:
            if not password.strip():
                self.api.send_message(message.chat.id, "Password cannot be empty; start again with /credentials.")
                return
            profile = self.repository.get_profile(user_id)
            university = profile.university if profile else self.settings.default_university
            self.repository.save_credentials(user_id, username or "", password, university)
            saved = True
        except (ValueError, sqlite3.Error):
            LOGGER.exception("Could not save credentials for Telegram user %s", user_id)
            self.api.send_message(message.chat.id, "Could not save credentials. Please try again.")
        finally:
            deleted = self._delete_password_message(message)

        if saved and deleted:
            self.api.send_message(message.chat.id, "Credentials saved securely.")
        elif saved:
            self.api.send_message(
                message.chat.id,
                "Credentials were saved, but Telegram could not delete the password message. "
                "Please delete it manually from this chat.",
            )

    def _delete_password_message(self, message: telebot.types.Message) -> bool:
        try:
            return bool(self.api.delete_message(message.chat.id, message.message_id))
        except ApiException:
            LOGGER.warning(
                "Could not delete password message for Telegram user %s",
                self._user_id(message),
            )
            return False

    def _handle_university(self, message: telebot.types.Message) -> None:
        if not self._require_private(message):
            return
        university = self._command_arguments(message)
        if not university:
            self.api.send_message(message.chat.id, "Usage: /university <university name>")
            return
        user_id = self._user_id(message)
        try:
            self._ensure_user(user_id)
            self.repository.update_university(user_id, university)
        except (ValueError, sqlite3.Error):
            LOGGER.exception("Could not update university for Telegram user %s", user_id)
            self.api.send_message(message.chat.id, "Could not update your university.")
            return
        self.api.send_message(message.chat.id, f"University set to: {university}")

    def _handle_set_meal(self, message: telebot.types.Message) -> None:
        if not self._require_private(message):
            return
        try:
            preference = parse_meal_preference_command(message.text or "")
            user_id = self._user_id(message)
            self._ensure_user(user_id)
            current = {
                (item.weekday, item.meal): item
                for item in self.repository.get_preferences(user_id)
            }
            current[(preference.weekday, preference.meal)] = preference
            self.repository.replace_preferences(user_id, current.values())
        except (ValueError, sqlite3.Error):
            LOGGER.exception("Could not update meal preference for Telegram user %s", self._user_id(message))
            self.api.send_message(
                message.chat.id,
                "Could not update the preference. Usage: /setmeal <weekday> <meal> <exact dining hall name>",
            )
            return
        self.api.send_message(
            message.chat.id,
            f"Set {Weekday(preference.weekday).persian_label} "
            f"{preference.meal.persian_label} to {preference.dining_hall}.",
        )

    def _handle_clear_meal(self, message: telebot.types.Message) -> None:
        if not self._require_private(message):
            return
        try:
            weekday, meal = parse_clear_meal_command(message.text or "")
            user_id = self._user_id(message)
            preferences = [
                item
                for item in self.repository.get_preferences(user_id)
                if (item.weekday, item.meal) != (weekday, meal)
            ]
            self.repository.replace_preferences(user_id, preferences)
        except (ValueError, LookupError, sqlite3.Error):
            self.api.send_message(
                message.chat.id,
                "Could not clear the preference. Usage: /clearmeal <weekday> <meal>",
            )
            return
        self.api.send_message(
            message.chat.id,
            f"Cleared {Weekday(weekday).persian_label} {meal.persian_label}.",
        )

    def _handle_preferences(self, message: telebot.types.Message) -> None:
        if not self._require_private(message):
            return
        preferences = self.repository.get_preferences(self._user_id(message))
        if not preferences:
            self.api.send_message(
                message.chat.id,
                "No meals configured. Use /setmeal <weekday> <meal> <exact dining hall name>.",
            )
            return
        lines = [
            f"{Weekday(item.weekday).persian_label} {item.meal.persian_label}: {item.dining_hall}"
            for item in preferences
        ]
        self.api.send_message(message.chat.id, "Weekly preferences:\n" + "\n".join(lines))

    def _handle_schedule(self, message: telebot.types.Message) -> None:
        if not self._require_private(message):
            return
        user_id = self._user_id(message)
        profile = self.repository.get_profile(user_id)
        if profile is None:
            self.api.send_message(message.chat.id, "Use /start before configuring your schedule.")
            return
        value = self._command_arguments(message)
        enabled = value.casefold() != "off"
        run_time = profile.weekly_run_time if not enabled else value
        if enabled and not value:
            self.api.send_message(message.chat.id, "Usage: /schedule HH:MM or /schedule off")
            return
        try:
            self.repository.update_schedule(user_id, run_time, enabled)
        except (ValueError, LookupError, sqlite3.Error):
            self.api.send_message(message.chat.id, "Use a 24-hour time such as /schedule 07:30.")
            return
        if self.on_schedule_changed is not None:
            self.on_schedule_changed()
        self.api.send_message(
            message.chat.id,
            f"Weekly Saturday run enabled for {run_time} Tehran time."
            if enabled
            else "Automatic weekly runs are disabled.",
        )

    def _handle_forget(self, message: telebot.types.Message) -> None:
        if not self._require_private(message):
            return
        user_id = self._user_id(message)
        with self._state_lock:
            self._states.pop(user_id, None)
            self._usernames.pop(user_id, None)
        try:
            self.repository.remove_credentials(user_id)
        except (LookupError, sqlite3.Error):
            self.api.send_message(message.chat.id, "No saved credentials were found.")
            return
        if self.on_schedule_changed is not None:
            self.on_schedule_changed()
        self.api.send_message(
            message.chat.id,
            "Saved credentials removed and automatic reservations disabled.",
        )

    def _handle_balance(self, message: telebot.types.Message) -> None:
        self._run_service_action(message, check_balance=True)

    def _handle_run(self, message: telebot.types.Message) -> None:
        self._run_service_action(message, check_balance=False)

    def _run_service_action(
        self,
        message: telebot.types.Message,
        check_balance: bool,
    ) -> None:
        if not self._require_private(message):
            return
        if self.reservation_service is None:
            self.api.send_message(message.chat.id, "Reservation automation is not available.")
            return
        try:
            if check_balance:
                result = self.reservation_service.check_balance_for_user(self._user_id(message))
            else:
                result = self.reservation_service.run_for_user(self._user_id(message))
        except InsufficientBalanceError as error:
            LOGGER.warning(
                "Insufficient Samad balance for Telegram user %s",
                self._user_id(message),
            )
            self.api.send_message(
                message.chat.id,
                "اعتبار شما کم است، لطفاً شارژ کنید. "
                f"هزینه لازم: {error.required_toman:,} تومان؛ "
                f"اعتبار فعلی: {error.balance_toman:,} تومان.",
            )
            return
        except ReservationError as error:
            LOGGER.exception("Samad operation failed for Telegram user %s", self._user_id(message))
            self.api.send_message(message.chat.id, str(error))
            return
        except sqlite3.Error:
            LOGGER.exception("Samad operation storage failed for Telegram user %s", self._user_id(message))
            self.api.send_message(message.chat.id, "Could not read or update local data. Please try again.")
            return
        self.api.send_message(message.chat.id, result)

    def start_polling(self) -> None:
        self.api.infinity_polling(skip_pending=True)

    def stop_polling(self) -> None:
        self.api.stop_polling()
