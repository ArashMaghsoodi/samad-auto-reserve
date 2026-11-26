from contextlib import closing
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

from cryptography.fernet import Fernet

from samad_reserver.domain import Meal, MealPreference
from samad_reserver.errors import InsufficientBalanceError, ReservationError
from samad_reserver.reservation import ReservationOutcome, ReservationReport
from samad_reserver.service import ReservationService
from samad_reserver.storage import UserRepository


class FakeAutomation:
    def __init__(self) -> None:
        self.report = ReservationReport(
            balance_toman=50_000,
            required_toman=30_000,
            outcomes=[
                ReservationOutcome(
                    weekday=0,
                    meal=Meal.LUNCH,
                    dining_hall="Central Brothers",
                    status="reserved",
                )
            ],
        )
        self.balance = 50_000
        self.block_reservations = False
        self.reservation_started = threading.Event()
        self.release_reservation = threading.Event()

    def reserve_week(self, credentials, preferences):
        self.reservation_started.set()
        if self.block_reservations:
            self.release_reservation.wait(timeout=5)
        return self.report

    def check_balance(self, credentials):
        return self.balance


class ReservationServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp_dir.name) / "users.sqlite3"
        self.repository = UserRepository(
            self.database_path,
            Fernet.generate_key().decode("ascii"),
        )
        self.repository.save_credentials(123, "student-id", "password", "University")
        self.repository.replace_preferences(
            123,
            [MealPreference(0, Meal.LUNCH, "Central Brothers")],
        )
        self.automation = FakeAutomation()
        self.service = ReservationService(self.repository, self.automation)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_runs_configured_preferences_and_records_summary(self) -> None:
        summary = self.service.run_for_user(123)
        with closing(sqlite3.connect(self.database_path)) as connection:
            saved_run = connection.execute(
                "SELECT status, summary FROM reservation_runs WHERE telegram_user_id = ?",
                (123,),
            ).fetchone()

        self.assertIn("Central Brothers", summary)
        self.assertIn("رزرو شد", summary)
        self.assertEqual(saved_run[0], "completed")
        self.assertEqual(saved_run[1], summary)

    def test_verbose_logs_service_progress_without_credential_values(self) -> None:
        with self.assertLogs("samad_reserver.service", level="DEBUG") as logs:
            self.service.run_for_user(123)

        output = "\n".join(logs.output)
        self.assertIn("Starting reservation service run", output)
        self.assertIn("Loaded 1 meal preferences", output)
        self.assertIn("status=completed", output)
        self.assertNotIn("password", output)
        self.assertNotIn("student-id", output)

    def test_low_balance_is_recorded_and_reported_as_a_specific_error(self) -> None:
        self.automation.report = None

        class InsufficientAutomation(FakeAutomation):
            def reserve_week(self, credentials, preferences):
                raise InsufficientBalanceError(60_000, 50_000)

        service = ReservationService(self.repository, InsufficientAutomation())
        with self.assertRaises(InsufficientBalanceError):
            service.run_for_user(123)
        with closing(sqlite3.connect(self.database_path)) as connection:
            status = connection.execute(
                "SELECT status FROM reservation_runs WHERE telegram_user_id = ?",
                (123,),
            ).fetchone()[0]
        self.assertEqual(status, "insufficient_balance")

    def test_concurrent_runs_for_one_user_are_rejected(self) -> None:
        self.automation.block_reservations = True
        background_errors: list[Exception] = []

        def first_run() -> None:
            try:
                self.service.run_for_user(123)
            except Exception as error:
                background_errors.append(error)

        thread = threading.Thread(target=first_run)
        thread.start()
        self.assertTrue(self.automation.reservation_started.wait(timeout=2))

        with self.assertRaisesRegex(ReservationError, "already running"):
            self.service.run_for_user(123)

        self.automation.release_reservation.set()
        thread.join(timeout=3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(background_errors, [])


if __name__ == "__main__":
    unittest.main()
