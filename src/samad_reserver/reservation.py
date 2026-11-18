import re
from dataclasses import dataclass
from typing import Iterable

from samad_reserver.domain import Meal, MealPreference
from samad_reserver.errors import InsufficientBalanceError, ReservationError

_DIGIT_TRANSLATION = str.maketrans(
    "۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩",
    "01234567890123456789",
)
_AMOUNT_PATTERN = re.compile(r"(?<!\d)([\d۰-۹٠-٩][\d۰-۹٠-٩,٬،]*)\s*(?:تومان|تومن)")


@dataclass(frozen=True)
class MealAvailability:
    weekday: int
    meal: Meal
    dining_hall: str
    price_toman: int
    already_reserved: bool = False

    def __post_init__(self) -> None:
        if isinstance(self.weekday, bool) or not isinstance(self.weekday, int) or not 0 <= self.weekday <= 6:
            raise ValueError("weekday must be an integer from 0 to 6")
        if not isinstance(self.meal, Meal):
            raise ValueError("meal must be a Meal")
        if not self.dining_hall.strip():
            raise ValueError("dining hall must not be empty")
        if (
            not isinstance(self.price_toman, int)
            or isinstance(self.price_toman, bool)
            or self.price_toman < 0
        ):
            raise ValueError("price must be a non-negative toman amount")


@dataclass(frozen=True)
class ReservationPlan:
    to_reserve: list[MealAvailability]
    already_reserved: list[MealPreference]
    unavailable: list[MealPreference]
    required_toman: int
    balance_toman: int


@dataclass(frozen=True)
class ReservationOutcome:
    weekday: int
    meal: Meal
    dining_hall: str
    status: str
    detail: str = ""


@dataclass(frozen=True)
class ReservationReport:
    balance_toman: int
    required_toman: int
    outcomes: list[ReservationOutcome]

    @property
    def run_status(self) -> str:
        if any(outcome.status in {"failed", "unavailable"} for outcome in self.outcomes):
            return "partial_failure"
        return "completed"

    def to_message(self) -> str:
        lines = [
            "نتیجه اجرای رزرو غذا",
            f"اعتبار اولیه: {self.balance_toman:,} تومان",
            f"هزینه رزروهای جدید: {self.required_toman:,} تومان",
        ]
        status_labels = {
            "reserved": "رزرو شد",
            "already_reserved": "از قبل رزرو شده بود",
            "unavailable": "در دسترس نبود",
            "failed": "خطا",
            "skipped": "رد شد",
        }
        for outcome in self.outcomes:
            day_label = {
                0: "شنبه",
                1: "یکشنبه",
                2: "دوشنبه",
                3: "سه‌شنبه",
                4: "چهارشنبه",
                5: "پنجشنبه",
                6: "جمعه",
            }[outcome.weekday]
            line = (
                f"{day_label} {outcome.meal.persian_label} — {outcome.dining_hall}: "
                f"{status_labels.get(outcome.status, outcome.status)}"
            )
            if outcome.detail:
                line += f" ({outcome.detail})"
            lines.append(line)
        return "\n".join(lines)


def parse_toman_amount(text: str) -> int:
    amounts = _AMOUNT_PATTERN.findall(text.translate(_DIGIT_TRANSLATION))
    if len(amounts) != 1:
        raise ReservationError("Could not read one unambiguous toman amount from Samad")
    try:
        return int(re.sub(r"[,٬،]", "", amounts[0]))
    except ValueError as error:
        raise ReservationError("Samad displayed an invalid toman amount") from error


def parse_wallet_balance(page_text: str) -> int:
    wallet_match = re.search(
        r"اعتبار(?:\s*شما)?[\s\S]{0,80}?([\d۰-۹٠-٩][\d۰-۹٠-٩,٬،]*)\s*(?:تومان|تومن)",
        page_text,
    )
    if wallet_match is None:
        raise ReservationError("Could not verify the Samad wallet balance")
    return parse_toman_amount(wallet_match.group(0))


def plan_reservations(
    preferences: Iterable[MealPreference],
    availability: Iterable[MealAvailability],
    balance_toman: int,
) -> ReservationPlan:
    if (
        not isinstance(balance_toman, int)
        or isinstance(balance_toman, bool)
        or balance_toman < 0
    ):
        raise ValueError("wallet balance must be a non-negative integer")

    preference_items = list(preferences)
    preference_slots = [(item.weekday, item.meal) for item in preference_items]
    if len(preference_slots) != len(set(preference_slots)):
        raise ValueError("duplicate weekday and meal preference")

    available_by_selection: dict[tuple[int, Meal, str], MealAvailability] = {}
    for item in availability:
        key = (item.weekday, item.meal, item.dining_hall)
        if key in available_by_selection:
            raise ValueError("duplicate availability for the same weekday, meal, and dining hall")
        available_by_selection[key] = item

    pending: list[MealAvailability] = []
    already_reserved: list[MealPreference] = []
    unavailable: list[MealPreference] = []
    for preference in preference_items:
        key = (preference.weekday, preference.meal, preference.dining_hall)
        meal = available_by_selection.get(key)
        if meal is None:
            unavailable.append(preference)
        elif meal.already_reserved:
            already_reserved.append(preference)
        else:
            pending.append(meal)

    required_toman = sum(item.price_toman for item in pending)
    if required_toman > balance_toman:
        raise InsufficientBalanceError(required_toman, balance_toman)

    return ReservationPlan(
        to_reserve=pending,
        already_reserved=already_reserved,
        unavailable=unavailable,
        required_toman=required_toman,
        balance_toman=balance_toman,
    )
