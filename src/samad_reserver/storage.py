from contextlib import contextmanager
import re
import sqlite3
from pathlib import Path
from typing import Iterable, Iterator

from cryptography.fernet import Fernet, InvalidToken

from samad_reserver.domain import Credentials, Meal, MealPreference, UserProfile


class CredentialDecryptionError(RuntimeError):
    """Raised when stored credentials cannot be decrypted with the configured key."""


class UserRepository:
    def __init__(self, database_path: str | Path, encryption_key: str) -> None:
        self.database_path = str(database_path)
        try:
            self._cipher = Fernet(encryption_key.encode("ascii"))
        except (ValueError, UnicodeEncodeError) as error:
            raise ValueError("CREDENTIAL_ENCRYPTION_KEY must be a valid Fernet key") from error

        if self.database_path != ":memory:":
            Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    telegram_user_id INTEGER PRIMARY KEY,
                    university TEXT NOT NULL,
                    username TEXT,
                    password_ciphertext BLOB,
                    weekly_run_time TEXT NOT NULL DEFAULT '08:00',
                    weekly_enabled INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS meal_preferences (
                    telegram_user_id INTEGER NOT NULL REFERENCES users(telegram_user_id) ON DELETE CASCADE,
                    weekday INTEGER NOT NULL CHECK (weekday BETWEEN 0 AND 6),
                    meal TEXT NOT NULL CHECK (meal IN ('breakfast', 'lunch', 'dinner')),
                    dining_hall TEXT NOT NULL,
                    PRIMARY KEY (telegram_user_id, weekday, meal)
                );
                CREATE TABLE IF NOT EXISTS reservation_runs (
                    run_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    telegram_user_id INTEGER NOT NULL REFERENCES users(telegram_user_id) ON DELETE CASCADE,
                    started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    status TEXT NOT NULL,
                    summary TEXT NOT NULL
                );
                """
            )

    @staticmethod
    def _validate_user_id(telegram_user_id: int) -> None:
        if isinstance(telegram_user_id, bool) or telegram_user_id <= 0:
            raise ValueError("telegram user ID must be a positive integer")

    def save_credentials(
        self,
        telegram_user_id: int,
        username: str,
        password: str,
        university: str,
    ) -> None:
        self._validate_user_id(telegram_user_id)
        username = username.strip()
        university = university.strip()
        if not username:
            raise ValueError("username must not be empty")
        if not password:
            raise ValueError("password must not be empty")
        if not university:
            raise ValueError("university must not be empty")

        ciphertext = self._cipher.encrypt(password.encode("utf-8"))
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO users (telegram_user_id, university, username, password_ciphertext)
                VALUES (?, ?, ?, ?)
                ON CONFLICT (telegram_user_id) DO UPDATE SET
                    university = excluded.university,
                    username = excluded.username,
                    password_ciphertext = excluded.password_ciphertext
                """,
                (telegram_user_id, university, username, ciphertext),
            )

    def get_credentials(self, telegram_user_id: int) -> Credentials | None:
        self._validate_user_id(telegram_user_id)
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT username, password_ciphertext
                FROM users
                WHERE telegram_user_id = ?
                """,
                (telegram_user_id,),
            ).fetchone()
        if row is None or row["username"] is None or row["password_ciphertext"] is None:
            return None

        try:
            password = self._cipher.decrypt(row["password_ciphertext"]).decode("utf-8")
        except (InvalidToken, UnicodeDecodeError) as error:
            raise CredentialDecryptionError(
                "Could not decrypt saved credentials; verify the configured encryption key"
            ) from error
        return Credentials(username=row["username"], password=password)

    def get_profile(self, telegram_user_id: int) -> UserProfile | None:
        self._validate_user_id(telegram_user_id)
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT telegram_user_id, university, username, weekly_run_time, weekly_enabled
                FROM users
                WHERE telegram_user_id = ?
                """,
                (telegram_user_id,),
            ).fetchone()
        if row is None:
            return None
        return UserProfile(
            telegram_user_id=row["telegram_user_id"],
            university=row["university"],
            username=row["username"],
            weekly_run_time=row["weekly_run_time"],
            weekly_enabled=bool(row["weekly_enabled"]),
        )

    def update_university(self, telegram_user_id: int, university: str) -> None:
        self._validate_user_id(telegram_user_id)
        university = university.strip()
        if not university:
            raise ValueError("university must not be empty")
        with self._connect() as connection:
            result = connection.execute(
                "UPDATE users SET university = ? WHERE telegram_user_id = ?",
                (university, telegram_user_id),
            )
        if result.rowcount == 0:
            raise LookupError("user is not configured")

    def replace_preferences(
        self,
        telegram_user_id: int,
        preferences: Iterable[MealPreference],
    ) -> None:
        self._validate_user_id(telegram_user_id)
        items = list(preferences)
        slots = [(item.weekday, item.meal) for item in items]
        if len(slots) != len(set(slots)):
            raise ValueError("duplicate weekday and meal preference")

        with self._connect() as connection:
            user = connection.execute(
                "SELECT 1 FROM users WHERE telegram_user_id = ?",
                (telegram_user_id,),
            ).fetchone()
            if user is None:
                raise LookupError("user is not configured")
            connection.execute(
                "DELETE FROM meal_preferences WHERE telegram_user_id = ?",
                (telegram_user_id,),
            )
            connection.executemany(
                """
                INSERT INTO meal_preferences (telegram_user_id, weekday, meal, dining_hall)
                VALUES (?, ?, ?, ?)
                """,
                [
                    (telegram_user_id, item.weekday, item.meal.value, item.dining_hall)
                    for item in items
                ],
            )

    def get_preferences(self, telegram_user_id: int) -> list[MealPreference]:
        self._validate_user_id(telegram_user_id)
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT weekday, meal, dining_hall
                FROM meal_preferences
                WHERE telegram_user_id = ?
                ORDER BY weekday, meal
                """,
                (telegram_user_id,),
            ).fetchall()
        return [
            MealPreference(
                weekday=row["weekday"],
                meal=Meal(row["meal"]),
                dining_hall=row["dining_hall"],
            )
            for row in rows
        ]

    def update_schedule(
        self,
        telegram_user_id: int,
        weekly_run_time: str,
        weekly_enabled: bool,
    ) -> None:
        self._validate_user_id(telegram_user_id)
        if re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", weekly_run_time) is None:
            raise ValueError("weekly run time must use 24-hour HH:MM format")
        with self._connect() as connection:
            result = connection.execute(
                """
                UPDATE users
                SET weekly_run_time = ?, weekly_enabled = ?
                WHERE telegram_user_id = ?
                """,
                (weekly_run_time, int(weekly_enabled), telegram_user_id),
            )
        if result.rowcount == 0:
            raise LookupError("user is not configured")

    def list_scheduled_profiles(self) -> list[UserProfile]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT telegram_user_id, university, username, weekly_run_time, weekly_enabled
                FROM users
                WHERE weekly_enabled = 1
                ORDER BY telegram_user_id
                """
            ).fetchall()
        return [
            UserProfile(
                telegram_user_id=row["telegram_user_id"],
                university=row["university"],
                username=row["username"],
                weekly_run_time=row["weekly_run_time"],
                weekly_enabled=bool(row["weekly_enabled"]),
            )
            for row in rows
        ]

    def record_run(self, telegram_user_id: int, status: str, summary: str) -> None:
        self._validate_user_id(telegram_user_id)
        if not status.strip() or not summary.strip():
            raise ValueError("run status and summary must not be empty")
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO reservation_runs (telegram_user_id, status, summary)
                VALUES (?, ?, ?)
                """,
                (telegram_user_id, status.strip(), summary.strip()),
            )

    def remove_user(self, telegram_user_id: int) -> None:
        self._validate_user_id(telegram_user_id)
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM users WHERE telegram_user_id = ?",
                (telegram_user_id,),
            )
