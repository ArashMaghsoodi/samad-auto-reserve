from dataclasses import dataclass
from enum import Enum, IntEnum


class Weekday(IntEnum):
    SATURDAY = 0
    SUNDAY = 1
    MONDAY = 2
    TUESDAY = 3
    WEDNESDAY = 4
    THURSDAY = 5
    FRIDAY = 6

    @property
    def persian_label(self) -> str:
        return (
            "شنبه",
            "یکشنبه",
            "دوشنبه",
            "سه‌شنبه",
            "چهارشنبه",
            "پنجشنبه",
            "جمعه",
        )[self.value]


class Meal(str, Enum):
    BREAKFAST = "breakfast"
    LUNCH = "lunch"
    DINNER = "dinner"

    @property
    def persian_label(self) -> str:
        return {
            Meal.BREAKFAST: "صبحانه",
            Meal.LUNCH: "ناهار",
            Meal.DINNER: "شام",
        }[self]


@dataclass(frozen=True)
class MealPreference:
    weekday: int
    meal: Meal
    dining_hall: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.weekday, int)
            or isinstance(self.weekday, bool)
            or not 0 <= self.weekday <= 6
        ):
            raise ValueError("weekday must be an integer from 0 (Saturday) to 6 (Friday)")
        if not isinstance(self.meal, Meal):
            try:
                object.__setattr__(self, "meal", Meal(self.meal))
            except ValueError as error:
                raise ValueError("meal must be breakfast, lunch, or dinner") from error
        normalized_hall = self.dining_hall.strip()
        if not normalized_hall:
            raise ValueError("dining hall must not be empty")
        object.__setattr__(self, "dining_hall", normalized_hall)


@dataclass(frozen=True)
class Credentials:
    username: str
    password: str


@dataclass(frozen=True)
class UserProfile:
    telegram_user_id: int
    university: str
    username: str | None
    weekly_run_time: str
    weekly_enabled: bool
