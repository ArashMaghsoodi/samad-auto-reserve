import logging

from dotenv import load_dotenv

from samad_reserver.config import Settings
from samad_reserver.samad_automation import SamadAutomation
from samad_reserver.scheduler import WeeklyReservationScheduler
from samad_reserver.service import ReservationService
from samad_reserver.storage import UserRepository
from samad_reserver.telegram_bot import TelegramBot


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    load_dotenv()
    settings = Settings.from_env()
    repository = UserRepository(
        settings.database_path,
        settings.credential_encryption_key,
    )
    automation = SamadAutomation(
        base_url=settings.samad_base_url,
        headless=settings.browser_headless,
    )
    reservation_service = ReservationService(repository, automation)
    bot = TelegramBot(settings, repository, reservation_service)
    scheduler = WeeklyReservationScheduler(
        repository,
        reservation_service,
        lambda user_id, text: bot.api.send_message(user_id, text),
    )
    bot.on_schedule_changed = scheduler.refresh

    try:
        scheduler.start()
        bot.start_polling()
    finally:
        scheduler.shutdown()
        bot.stop_polling()


if __name__ == "__main__":
    main()
