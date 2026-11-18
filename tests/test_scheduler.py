import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock

from cryptography.fernet import Fernet

from samad_reserver.errors import InsufficientBalanceError
from samad_reserver.scheduler import WeeklyReservationScheduler
from samad_reserver.storage import UserRepository


class WeeklyReservationSchedulerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.repository = UserRepository(
            Path(self.temp_dir.name) / "users.sqlite3",
            Fernet.generate_key().decode("ascii"),
        )
        self.repository.ensure_user(123, "University")
        self.reservation_service = Mock()
        self.send_message = Mock()
        self.scheduler = WeeklyReservationScheduler(
            self.repository,
            self.reservation_service,
            self.send_message,
        )

    def tearDown(self) -> None:
        self.scheduler.shutdown()
        self.temp_dir.cleanup()

    def test_refresh_creates_and_removes_user_schedule_jobs(self) -> None:
        self.repository.update_schedule(123, "07:30", True)

        self.scheduler.refresh()

        jobs = self.scheduler.scheduler.get_jobs()
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].id, "weekly-reservation-123")
        self.assertEqual(jobs[0].args, (123,))
        self.assertEqual(jobs[0].trigger.fields[5].name, "hour")
        self.assertEqual(str(jobs[0].trigger.fields[5]), "7")
        self.assertEqual(str(jobs[0].trigger.fields[6]), "30")
        self.assertEqual(str(jobs[0].trigger.fields[4]), "sat")

        self.repository.update_schedule(123, "07:30", False)
        self.scheduler.refresh()

        self.assertEqual(self.scheduler.scheduler.get_jobs(), [])

    def test_start_loads_persisted_user_schedules(self) -> None:
        self.repository.update_schedule(123, "07:30", True)

        self.scheduler.start()

        self.assertTrue(self.scheduler.scheduler.running)
        self.assertEqual(
            [job.id for job in self.scheduler.scheduler.get_jobs()],
            ["weekly-reservation-123"],
        )

    def test_scheduled_low_balance_run_sends_the_specific_warning(self) -> None:
        self.reservation_service.run_for_user.side_effect = InsufficientBalanceError(
            60_000,
            50_000,
        )

        self.scheduler._run_for_user(123)

        sent_text = self.send_message.call_args.args[1]
        self.assertIn("اعتبار شما کم است", sent_text)
        self.assertIn("60,000", sent_text)
        self.assertIn("50,000", sent_text)


if __name__ == "__main__":
    unittest.main()
