import re
from contextlib import contextmanager
from typing import Iterator
from urllib.parse import urlsplit

from playwright.sync_api import (
    Error as PlaywrightError,
    Locator,
    Page,
    TimeoutError as PlaywrightTimeoutError,
    sync_playwright,
)

from samad_reserver.domain import Credentials, MealPreference, Weekday
from samad_reserver.errors import ReservationError
from samad_reserver.reservation import (
    MealAvailability,
    ReservationOutcome,
    ReservationReport,
    parse_toman_amount,
    parse_wallet_balance,
    plan_reservations,
)


_UNIVERSITY_MARKERS = ("دانشگاه", "university")
_USERNAME_MARKERS = (
    "username",
    "student",
    "user",
    "دانشجویی",
    "دانشجو",
    "شناسه",
    "شماره",
    "نام کاربری",
    "کد",
)
_RESERVED_MARKERS = ("لغو رزرو", "رزرو شده", "رزرو ثبت شده")
_CONFIRM_LABELS = ("تأیید", "تایید", "بله", "رزرو")


class SamadAutomation:
    def __init__(
        self,
        base_url: str = "https://samad.app",
        headless: bool = True,
        timeout_ms: int = 15_000,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.headless = headless
        self.timeout_ms = timeout_ms

    def check_balance(self, credentials: Credentials, university: str) -> int:
        with self._open_page() as page:
            self._login(page, credentials, university)
            self._open_food_menu(page)
            return parse_wallet_balance(page.locator("body").inner_text())

    def reserve_week(
        self,
        credentials: Credentials,
        university: str,
        preferences: list[MealPreference],
    ) -> ReservationReport:
        if not preferences:
            raise ReservationError("Configure at least one weekday and meal before running reservations")

        selected_preferences = sorted(
            preferences,
            key=lambda item: (item.weekday, item.meal.value),
        )
        halls = sorted({item.dining_hall for item in selected_preferences})
        with self._open_page() as page:
            self._login(page, credentials, university)
            self._open_food_menu(page)
            balance_toman = parse_wallet_balance(page.locator("body").inner_text())
            self._open_reservation_page(page, selected_preferences[0].dining_hall, halls)

            availability = [
                self._inspect_preference(page, preference, halls)
                for preference in selected_preferences
            ]
            plan = plan_reservations(selected_preferences, availability, balance_toman)

            outcomes = [
                ReservationOutcome(
                    weekday=item.weekday,
                    meal=item.meal,
                    dining_hall=item.dining_hall,
                    status="already_reserved",
                )
                for item in plan.already_reserved
            ]
            outcomes.extend(
                ReservationOutcome(
                    weekday=item.weekday,
                    meal=item.meal,
                    dining_hall=item.dining_hall,
                    status="unavailable",
                    detail="The exact dining hall and meal were not available in the bookable week",
                )
                for item in plan.unavailable
            )

            for index, meal in enumerate(plan.to_reserve):
                try:
                    outcome = self._reserve_meal(page, meal, halls)
                except ReservationError as error:
                    outcomes.append(
                        ReservationOutcome(
                            weekday=meal.weekday,
                            meal=meal.meal,
                            dining_hall=meal.dining_hall,
                            status="failed",
                            detail=str(error),
                        )
                    )
                    outcomes.extend(
                        ReservationOutcome(
                            weekday=remaining.weekday,
                            meal=remaining.meal,
                            dining_hall=remaining.dining_hall,
                            status="skipped",
                            detail="Run stopped after an earlier reservation failure",
                        )
                        for remaining in plan.to_reserve[index + 1 :]
                    )
                    break
                outcomes.append(outcome)

            outcome_order = {
                (item.weekday, item.meal): index
                for index, item in enumerate(selected_preferences)
            }
            outcomes.sort(key=lambda item: outcome_order[(item.weekday, item.meal)])
            return ReservationReport(
                balance_toman=balance_toman,
                required_toman=plan.required_toman,
                outcomes=outcomes,
            )

    @contextmanager
    def _open_page(self) -> Iterator[Page]:
        with sync_playwright() as playwright:
            try:
                browser = playwright.chromium.launch(headless=self.headless)
            except PlaywrightError as error:
                raise ReservationError(
                    "Could not start Chromium; install the Playwright browser with "
                    "`python -m playwright install chromium`"
                ) from error
            context = browser.new_context()
            try:
                page = context.new_page()
                page.set_default_timeout(self.timeout_ms)
                yield page
            except PlaywrightError as error:
                raise ReservationError(
                    "Samad page interaction failed or timed out; the page may have changed"
                ) from error
            finally:
                context.close()
                browser.close()

    def _login(self, page: Page, credentials: Credentials, university: str) -> None:
        try:
            page.goto(f"{self.base_url}/login", wait_until="domcontentloaded")
            page.locator("input[name='username']:visible").wait_for(state="visible")
            self._select_university(page, university)
            username_input, password_input = self._login_inputs(page, university)
            username_input.fill(credentials.username)
            password_input.fill(credentials.password)
            login_button = page.get_by_role("button", name="ورود", exact=True)
            if self._visible_count(login_button) != 1:
                raise ReservationError("Could not identify one Samad login button")
            if not login_button.is_enabled():
                raise ReservationError(
                    "Samad login is disabled, possibly because it requires additional verification; "
                    "automated verification will not be attempted"
                )
            self._click_unique(login_button, "Samad login button")
        except PlaywrightError as error:
            raise ReservationError("Could not complete the Samad login form") from error

        student_entry = page.get_by_role(
            "button",
            name="ورود به رابط کاربری دانشجویی",
            exact=True,
        )
        try:
            student_entry.wait_for(state="visible", timeout=self.timeout_ms)
        except PlaywrightTimeoutError as error:
            if self._route(page).rstrip("/") != "/food":
                raise ReservationError(
                    "Samad login did not reach the student interface; check credentials and university"
                ) from error
        else:
            self._click_unique(student_entry, "student-interface button")

        food_card = page.get_by_text("رزرو غذا", exact=True)
        try:
            food_card.wait_for(state="visible", timeout=self.timeout_ms)
        except PlaywrightTimeoutError as error:
            raise ReservationError("Samad student food dashboard did not load") from error

    def _select_university(self, page: Page, university: str) -> None:
        selected_university = page.locator("input:visible")
        for index in range(selected_university.count()):
            field = selected_university.nth(index)
            if self._normalize(field.get_attribute("value") or "") == self._normalize(university):
                return

        search = page.get_by_placeholder("نام دانشگاه را جستجو کنید...", exact=True)
        if self._visible_count(search) == 1:
            try:
                search.fill(university)
                self._click_unique(
                    page.get_by_text(university, exact=True),
                    f"university {university}",
                )
            except PlaywrightError as error:
                raise ReservationError("Could not select the configured university") from error
            return

        university_picker = page.get_by_placeholder("دانشگاه را انتخاب کنید", exact=True)
        if self._visible_count(university_picker) == 1:
            try:
                if university_picker.is_disabled():
                    edit_control = page.locator(".input-btn-edit:visible")
                    self._click_unique(edit_control, "university edit control")
                else:
                    university_picker.click()
                search = page.get_by_placeholder("نام دانشگاه را جستجو کنید...", exact=True)
                search.wait_for(state="visible")
                search.fill(university)
                self._click_unique(
                    page.get_by_text(university, exact=True),
                    f"university {university}",
                )
            except PlaywrightError as error:
                raise ReservationError("Could not select the configured university") from error
            return

        native_selects = page.locator("select:visible")
        matching_selects: list[tuple[Locator, str]] = []
        for index in range(native_selects.count()):
            select = native_selects.nth(index)
            for option in select.locator("option").all_inner_texts():
                if self._normalize(option) == self._normalize(university):
                    matching_selects.append((select, option.strip()))
                    break
        if len(matching_selects) > 1:
            raise ReservationError("Samad university selector is ambiguous")
        if matching_selects:
            select, option_label = matching_selects[0]
            try:
                select.select_option(label=option_label)
            except PlaywrightError as error:
                raise ReservationError("Could not select the configured university") from error
            return

        inputs = page.locator("input:visible")
        for index in range(inputs.count()):
            field = inputs.nth(index)
            value = (field.get_attribute("value") or "").strip()
            if self._normalize(value) == self._normalize(university):
                return

        exact_text = page.get_by_text(university, exact=True)
        if self._visible_count(exact_text) == 1:
            return

        combos = page.get_by_role("combobox")
        university_combos: list[Locator] = []
        for index in range(combos.count()):
            combo = combos.nth(index)
            metadata = " ".join(
                filter(
                    None,
                    (
                        combo.get_attribute("aria-label"),
                        combo.get_attribute("name"),
                        combo.get_attribute("placeholder"),
                    ),
                )
            )
            if any(marker in self._normalize(metadata) for marker in _UNIVERSITY_MARKERS):
                university_combos.append(combo)
        if len(university_combos) == 1:
            combo = university_combos[0]
            try:
                combo.click()
                self._choose_visible_option(page, university, "configured university")
            except PlaywrightError as error:
                raise ReservationError("Could not select the configured university") from error
            return

        raise ReservationError(
            "Could not identify the Samad university selector; refusing to log in with an uncertain university"
        )

    def _login_inputs(self, page: Page, university: str) -> tuple[Locator, Locator]:
        password_inputs = page.locator("input[type='password']:visible")
        if password_inputs.count() != 1:
            raise ReservationError("Could not identify exactly one Samad password field")
        password_input = password_inputs.first
        named_usernames = page.locator("input[name='username']:visible")
        if named_usernames.count() == 1:
            return named_usernames.first, password_input

        visible_inputs = page.locator("input:visible")
        username_candidates: list[Locator] = []
        fallback_candidates: list[Locator] = []
        for index in range(visible_inputs.count()):
            field = visible_inputs.nth(index)
            input_type = (field.get_attribute("type") or "text").casefold()
            if input_type not in {"text", "email", "tel", "number"}:
                continue
            metadata = " ".join(
                filter(
                    None,
                    (
                        field.get_attribute("name"),
                        field.get_attribute("id"),
                        field.get_attribute("placeholder"),
                        field.get_attribute("aria-label"),
                        field.get_attribute("autocomplete"),
                    ),
                )
            )
            value = field.get_attribute("value") or ""
            if self._normalize(value) == self._normalize(university):
                continue
            normalized_metadata = self._normalize(metadata)
            if any(marker in normalized_metadata for marker in _UNIVERSITY_MARKERS):
                continue
            fallback_candidates.append(field)
            if any(marker in normalized_metadata for marker in _USERNAME_MARKERS):
                username_candidates.append(field)

        candidates = username_candidates or fallback_candidates
        if len(candidates) != 1:
            raise ReservationError("Could not identify exactly one Samad username/student-ID field")
        return candidates[0], password_input

    def _open_food_menu(self, page: Page) -> None:
        if self._route(page).rstrip("/") == "/food":
            return
        self._click_unique(page.get_by_text("رزرو غذا", exact=True), "food reservation card")
        if self._route(page).rstrip("/") not in {"/food", "/food/selfs", "/food/reserve"}:
            raise ReservationError("The Samad food reservation page did not open")

    def _open_reservation_page(
        self,
        page: Page,
        first_hall: str,
        configured_halls: list[str],
    ) -> None:
        route = self._route(page).rstrip("/")
        if route == "/food/reserve":
            return
        if route == "/food":
            self._click_unique(page.get_by_text("رزرو غذا", exact=True), "food reservation card")
            route = self._route(page).rstrip("/")
        if route == "/food/selfs":
            self._select_dining_hall(page, first_hall, configured_halls)
            route = self._route(page).rstrip("/")
        if route != "/food/reserve":
            raise ReservationError("Could not reach Samad's food reservation page")

    def _select_dining_hall(
        self,
        page: Page,
        target_hall: str,
        configured_halls: list[str],
    ) -> None:
        if self._route(page).rstrip("/") == "/food/selfs":
            self._click_unique(
                page.get_by_text(target_hall, exact=True),
                f"dining hall {target_hall}",
            )
            if self._route(page).rstrip("/") != "/food/reserve":
                raise ReservationError(f"Selecting {target_hall} did not open its reservation page")
            return

        combos = page.get_by_role("combobox")
        matching_combos: list[Locator] = []
        for index in range(combos.count()):
            combo = combos.nth(index)
            option_labels = combo.locator("option").all_inner_texts()
            if any(
                self._normalize(option) == self._normalize(target_hall)
                for option in option_labels
            ):
                matching_combos.append(combo)
        if len(matching_combos) == 1:
            try:
                matching_combos[0].select_option(label=target_hall)
            except PlaywrightError as error:
                raise ReservationError(f"Could not select dining hall {target_hall}") from error
            return
        if len(matching_combos) > 1:
            raise ReservationError("Samad dining-hall selector is ambiguous")

        buttons = page.get_by_role("button")
        matching_buttons: list[tuple[Locator, str]] = []
        for index in range(buttons.count()):
            button = buttons.nth(index)
            label = button.inner_text().strip()
            if any(self._normalize(hall) in self._normalize(label) for hall in configured_halls):
                matching_buttons.append((button, label))
        if len(matching_buttons) != 1:
            raise ReservationError("Could not identify the Samad dining-hall dropdown")
        button, current_label = matching_buttons[0]
        if self._normalize(target_hall) in self._normalize(current_label):
            return
        try:
            button.click()
            self._choose_visible_option(page, target_hall, f"dining hall {target_hall}")
        except PlaywrightError as error:
            raise ReservationError(f"Could not select dining hall {target_hall}") from error

    def _select_day(self, page: Page, weekday: int) -> None:
        label = Weekday(weekday).persian_label
        buttons = page.get_by_role("button", name=label, exact=True)
        if self._visible_count(buttons) == 1:
            self._click_unique(buttons, f"weekday {label}")
            return
        self._click_unique(page.get_by_text(label, exact=True), f"weekday {label}")

    def _inspect_preference(
        self,
        page: Page,
        preference: MealPreference,
        configured_halls: list[str],
    ) -> MealAvailability:
        self._select_dining_hall(page, preference.dining_hall, configured_halls)
        self._select_day(page, preference.weekday)
        return self._read_meal_availability(page, preference)

    def _read_meal_availability(
        self,
        page: Page,
        preference: MealPreference,
    ) -> MealAvailability:
        card = self._meal_card(page, preference)
        card_text = card.inner_text()
        price_toman = parse_toman_amount(card_text)
        button_labels = [
            label.strip()
            for label in card.get_by_role("button").all_inner_texts()
            if label.strip()
        ]
        normalized_text = self._normalize(card_text)
        already_reserved = any(
            self._normalize(marker) in normalized_text for marker in _RESERVED_MARKERS
        )
        if not already_reserved and not any(
            self._normalize(label) == "رزرو" for label in button_labels
        ):
            raise ReservationError(
                f"Could not determine whether {preference.meal.persian_label} is already reserved"
            )
        return MealAvailability(
            weekday=preference.weekday,
            meal=preference.meal,
            dining_hall=preference.dining_hall,
            price_toman=price_toman,
            already_reserved=already_reserved,
        )

    def _meal_card(self, page: Page, preference: MealPreference) -> Locator:
        label = preference.meal.persian_label
        matches = page.get_by_text(label, exact=True)
        visible_matches = [
            matches.nth(index)
            for index in range(matches.count())
            if matches.nth(index).is_visible()
        ]
        if len(visible_matches) != 1:
            raise ReservationError(f"Could not identify one {label} card in Samad")

        container = visible_matches[0]
        for _ in range(7):
            container = container.locator("xpath=..")
            text = container.inner_text()
            if "تومان" not in text and "تومن" not in text:
                continue
            if container.get_by_role("button").count() > 0:
                try:
                    parse_toman_amount(text)
                except ReservationError:
                    continue
                return container
        raise ReservationError(f"Could not identify the price and reserve button for {label}")

    def _reserve_meal(
        self,
        page: Page,
        planned: MealAvailability,
        configured_halls: list[str],
    ) -> ReservationOutcome:
        self._select_dining_hall(page, planned.dining_hall, configured_halls)
        self._select_day(page, planned.weekday)
        preference = MealPreference(
            weekday=planned.weekday,
            meal=planned.meal,
            dining_hall=planned.dining_hall,
        )
        current = self._read_meal_availability(page, preference)
        if current.already_reserved:
            return ReservationOutcome(
                planned.weekday,
                planned.meal,
                planned.dining_hall,
                "already_reserved",
            )
        if current.price_toman != planned.price_toman:
            raise ReservationError("Meal price changed after the balance preflight; stopping the run")

        card = self._meal_card(page, preference)
        quantity_inputs = card.locator("input[type='number']")
        if quantity_inputs.count() > 1:
            raise ReservationError("Samad displayed an ambiguous reservation quantity control")
        if quantity_inputs.count() == 1:
            quantity_inputs.fill("1")
        reserve_button = card.get_by_role("button", name="رزرو", exact=True)
        self._click_unique(reserve_button, f"reserve button for {preference.meal.persian_label}")
        self._confirm_reservation_if_requested(page)

        refreshed = self._read_meal_availability(page, preference)
        if not refreshed.already_reserved:
            raise ReservationError(
                f"Samad did not confirm the {preference.meal.persian_label} reservation"
            )
        return ReservationOutcome(
            planned.weekday,
            planned.meal,
            planned.dining_hall,
            "reserved",
        )

    def _confirm_reservation_if_requested(self, page: Page) -> None:
        dialog = page.get_by_role("dialog")
        try:
            dialog.wait_for(state="visible", timeout=1_000)
        except PlaywrightTimeoutError:
            return
        for label in _CONFIRM_LABELS:
            button = dialog.get_by_role("button", name=label, exact=True)
            if self._visible_count(button) == 1:
                self._click_unique(button, "reservation confirmation button")
                return
        raise ReservationError("Samad displayed an unrecognized reservation confirmation dialog")

    def _choose_visible_option(self, page: Page, label: str, description: str) -> None:
        options = page.get_by_role("option", name=label, exact=True)
        if self._visible_count(options) == 1:
            self._click_unique(options, description)
            return
        self._click_unique(page.get_by_text(label, exact=True), description)

    def _click_unique(self, locator: Locator, description: str) -> None:
        visible_count = self._visible_count(locator)
        if visible_count != 1:
            raise ReservationError(
                f"Expected one visible {description}, found {visible_count}; refusing an ambiguous action"
            )
        try:
            locator.click()
        except PlaywrightError as error:
            raise ReservationError(f"Could not click {description}") from error

    @staticmethod
    def _visible_count(locator: Locator) -> int:
        return sum(1 for index in range(locator.count()) if locator.nth(index).is_visible())

    @staticmethod
    def _normalize(value: str) -> str:
        return value.strip().casefold().replace("ي", "ی").replace("ك", "ک")

    @staticmethod
    def _route(page: Page) -> str:
        return urlsplit(page.url).path
