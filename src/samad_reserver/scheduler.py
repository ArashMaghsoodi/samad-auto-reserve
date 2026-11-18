import logging
import sqlite3
from collections.abc import Callable

from apscheduler.schedulers.background import BackgroundScheduler
from telebot.apihelper import ApiException

from samad_reserver.errors import InsufficientBalanceError, ReservationError
from samad_reserver.service import ReservationService
from samad_reserver.storage import UserRepository

LOGGER = logging.getLogger(__name__)
_JOB_PREFIX = "weekly-reservation-"


class WeeklyReservationScheduler:
    def __init__(
        self,
        repository: UserRepository,
        reservation_service: ReservationService,
        send_message: Callable[[int, str], object],
    ) -> None:
        self.repository = repository
        self.reservation_service = reservation_service
        self.send_message = send_message
        self.scheduler = BackgroundScheduler(timezone="Asia/Tehran")

    def refresh(self) -> None:
        profiles = self.repository.list_scheduled_profiles()
        configured_ids = {profile.telegram_user_id for profile in profiles}

        for job in self.scheduler.get_jobs():
            if job.id.startswith(_JOB_PREFIX):
                user_id = int(job.id.removeprefix(_JOB_PREFIX))
                if user_id not in configured_ids:
                    self.scheduler.remove_job(job.id)

        for profile in profiles:
            hour, minute = (int(part) for part in profile.weekly_run_time.split(":"))
            self.scheduler.add_job(
                self._run_for_user,
                trigger="cron",
                day_of_week="sat",
                hour=hour,
                minute=minute,
                timezone="Asia/Tehran",
                id=f"{_JOB_PREFIX}{profile.telegram_user_id}",
                args=[profile.telegram_user_id],
                replace_existing=True,
                max_instances=1,
                coalesce=True,
            )

    def start(self) -> None:
        self.refresh()
        self.scheduler.start()

    def shutdown(self) -> None:
        if self.scheduler.running:
            self.scheduler.shutdown(wait=True)

    def _run_for_user(self, telegram_user_id: int) -> None:
        try:
            summary = self.reservation_service.run_for_user(telegram_user_id)
        except InsufficientBalanceError as error:
            summary = (
                "اعتبار شما کم است، لطفاً شارژ کنید. "
                f"هزینه لازم: {error.required_toman:,} تومان؛ "
                f"اعتبار فعلی: {error.balance_toman:,} تومان."
            )
        except ReservationError as error:
            LOGGER.exception(
                "Scheduled Samad reservation failed for Telegram user %s",
                telegram_user_id,
            )
            summary = f"اجرای خودکار رزرو انجام نشد: {error}"
        except sqlite3.Error:
            LOGGER.exception(
                "Scheduled Samad reservation storage failed for Telegram user %s",
                telegram_user_id,
            )
            summary = "خطای ذخیره‌سازی محلی هنگام اجرای رزرو خودکار."

        try:
            self.send_message(telegram_user_id, summary)
        except ApiException:
            LOGGER.exception(
                "Could not send scheduled reservation report to Telegram user %s",
                telegram_user_id,
            )
