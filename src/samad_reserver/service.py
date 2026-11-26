import logging
import threading
from typing import Protocol

from samad_reserver.domain import Credentials, MealPreference
from samad_reserver.errors import InsufficientBalanceError, ReservationError
from samad_reserver.reservation import ReservationReport
from samad_reserver.storage import UserRepository

LOGGER = logging.getLogger(__name__)


class ReservationAutomation(Protocol):
    def reserve_week(
        self,
        credentials: Credentials,
        preferences: list[MealPreference],
    ) -> ReservationReport: ...

    def check_balance(self, credentials: Credentials) -> int: ...


class ReservationService:
    def __init__(
        self,
        repository: UserRepository,
        automation: ReservationAutomation,
    ) -> None:
        self.repository = repository
        self.automation = automation
        self._locks: dict[int, threading.Lock] = {}
        self._locks_guard = threading.Lock()

    def _user_lock(self, telegram_user_id: int) -> threading.Lock:
        with self._locks_guard:
            return self._locks.setdefault(telegram_user_id, threading.Lock())

    def _user_context(
        self,
        telegram_user_id: int,
    ) -> tuple[Credentials, list[MealPreference]]:
        profile = self.repository.get_profile(telegram_user_id)
        if profile is None:
            raise ReservationError("Use /start to configure your account first")
        credentials = self.repository.get_credentials(telegram_user_id)
        if credentials is None:
            raise ReservationError("Set your Samad credentials first with /credentials")
        return (
            credentials,
            self.repository.get_preferences(telegram_user_id),
        )

    def run_for_user(self, telegram_user_id: int) -> str:
        LOGGER.debug("Starting reservation service run for Telegram user %s", telegram_user_id)
        lock = self._user_lock(telegram_user_id)
        if not lock.acquire(blocking=False):
            raise ReservationError("A Samad operation is already running for this account")
        try:
            credentials, preferences = self._user_context(telegram_user_id)
            LOGGER.debug(
                "Loaded %d meal preferences for Telegram user %s",
                len(preferences),
                telegram_user_id,
            )
            try:
                report = self.automation.reserve_week(credentials, preferences)
            except InsufficientBalanceError as error:
                summary = (
                    f"اعتبار شما کم است، لطفاً شارژ کنید. "
                    f"هزینه لازم: {error.required_toman:,} تومان؛ "
                    f"اعتبار فعلی: {error.balance_toman:,} تومان."
                )
                self.repository.record_run(
                    telegram_user_id,
                    "insufficient_balance",
                    summary,
                )
                raise
            except ReservationError as error:
                self.repository.record_run(
                    telegram_user_id,
                    "failed",
                    f"Reservation run failed: {error}",
                )
                raise

            summary = report.to_message()
            self.repository.record_run(telegram_user_id, report.run_status, summary)
            LOGGER.debug(
                "Reservation service run completed for Telegram user %s status=%s",
                telegram_user_id,
                report.run_status,
            )
            return summary
        finally:
            lock.release()

    def check_balance_for_user(self, telegram_user_id: int) -> str:
        LOGGER.debug("Starting wallet balance service check for Telegram user %s", telegram_user_id)
        lock = self._user_lock(telegram_user_id)
        if not lock.acquire(blocking=False):
            raise ReservationError("A Samad operation is already running for this account")
        try:
            profile = self.repository.get_profile(telegram_user_id)
            if profile is None:
                raise ReservationError("Use /start to configure your account first")
            credentials = self.repository.get_credentials(telegram_user_id)
            if credentials is None:
                raise ReservationError("Set your Samad credentials first with /credentials")
            balance = self.automation.check_balance(credentials)
            if not isinstance(balance, int) or isinstance(balance, bool) or balance < 0:
                raise ReservationError("Samad returned an invalid wallet balance")
            LOGGER.debug("Wallet balance service check completed for Telegram user %s", telegram_user_id)
            return f"اعتبار فعلی شما: {balance:,} تومان"
        finally:
            lock.release()
