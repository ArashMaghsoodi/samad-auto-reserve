import unittest

from samad_reserver.domain import Meal, MealPreference
from samad_reserver.errors import InsufficientBalanceError
from samad_reserver.errors import ReservationError
from samad_reserver.reservation import (
    MealAvailability,
    ReservationOutcome,
    ReservationReport,
    parse_toman_amount,
    parse_wallet_balance,
    plan_reservations,
)


class ReservationPlanTests(unittest.TestCase):
    def test_selects_only_the_explicit_place_for_each_day_and_meal(self) -> None:
        preferences = [
            MealPreference(0, Meal.LUNCH, "Central Brothers"),
            MealPreference(0, Meal.DINNER, "Hafez"),
        ]
        available = [
            MealAvailability(0, Meal.LUNCH, "Central Brothers", 100_000),
            MealAvailability(0, Meal.LUNCH, "Hafez", 10_000),
            MealAvailability(0, Meal.DINNER, "Hafez", 80_000),
        ]

        plan = plan_reservations(preferences, available, balance_toman=180_000)

        self.assertEqual(
            [(item.weekday, item.meal, item.dining_hall) for item in plan.to_reserve],
            [
                (0, Meal.LUNCH, "Central Brothers"),
                (0, Meal.DINNER, "Hafez"),
            ],
        )
        self.assertEqual(plan.required_toman, 180_000)
        self.assertEqual(plan.unavailable, [])

    def test_skips_existing_reservations_and_reports_unavailable_exact_places(self) -> None:
        preferences = [
            MealPreference(1, Meal.LUNCH, "Central Brothers"),
            MealPreference(1, Meal.DINNER, "Hafez"),
        ]
        available = [
            MealAvailability(1, Meal.LUNCH, "Central Brothers", 90_000, already_reserved=True),
            MealAvailability(1, Meal.DINNER, "Central Self", 10_000),
        ]

        plan = plan_reservations(preferences, available, balance_toman=0)

        self.assertEqual(len(plan.to_reserve), 0)
        self.assertEqual(plan.already_reserved, [preferences[0]])
        self.assertEqual(plan.unavailable, [preferences[1]])

    def test_rejects_insufficient_balance_before_returning_any_bookings(self) -> None:
        preferences = [
            MealPreference(0, Meal.LUNCH, "Central Brothers"),
            MealPreference(1, Meal.DINNER, "Hafez"),
        ]
        available = [
            MealAvailability(0, Meal.LUNCH, "Central Brothers", 100_000),
            MealAvailability(1, Meal.DINNER, "Hafez", 50_000),
        ]

        with self.assertRaises(InsufficientBalanceError) as error:
            plan_reservations(preferences, available, balance_toman=149_999)

        self.assertEqual(error.exception.required_toman, 150_000)
        self.assertEqual(error.exception.balance_toman, 149_999)

    def test_parses_persian_wallet_and_price_digits(self) -> None:
        self.assertEqual(parse_wallet_balance("اعتبار شما\n۲۴۰٬۰۰۰\nتومان"), 240_000)
        self.assertEqual(parse_toman_amount("۳۰,۰۰۰ تومان"), 30_000)

    def test_rejects_missing_or_ambiguous_currency_amounts(self) -> None:
        with self.assertRaises(ReservationError):
            parse_wallet_balance("اعتبار نامشخص")
        with self.assertRaises(ReservationError):
            parse_toman_amount("ناهار 30,000 تومان، شام 50,000 تومان")

    def test_formats_per_meal_run_report_without_credentials(self) -> None:
        report = ReservationReport(
            balance_toman=240_000,
            required_toman=30_000,
            outcomes=[
                ReservationOutcome(
                    weekday=0,
                    meal=Meal.LUNCH,
                    dining_hall="سلف مرکزی برادران",
                    status="reserved",
                )
            ],
        )

        message = report.to_message()

        self.assertIn("شنبه ناهار", message)
        self.assertIn("سلف مرکزی برادران", message)
        self.assertIn("رزرو شد", message)
        self.assertNotIn("password", message.casefold())


if __name__ == "__main__":
    unittest.main()
