from contextlib import contextmanager
import json
import logging
from typing import Iterator
from urllib.parse import urlsplit, urlunsplit

from playwright.sync_api import (
    ConsoleMessage,
    Error as PlaywrightError,
    Frame,
    Locator,
    Page,
    Request,
    Response,
    Route,
    TimeoutError as PlaywrightTimeoutError,
    sync_playwright,
)

from samad_reserver.config import LEGACY_PORTAL_HOST, validate_samad_base_url
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


_RESERVED_MARKERS = ("لغو رزرو", "رزرو شده", "رزرو ثبت شده")
_CONFIRM_LABELS = ("تأیید", "تایید", "بله", "رزرو")
LOGGER = logging.getLogger(__name__)
_SECURE_FORM_SUBMISSION_GUARD = """
const secureFormAction = (form, submitter) => {
    const actionAttribute = submitter && submitter.hasAttribute("formaction")
        ? submitter.getAttribute("formaction")
        : form.getAttribute("action");
    let action;
    try {
        action = new URL(actionAttribute || window.location.href, window.location.href);
    } catch {
        window.__samadFormSubmissionBlocked = true;
        return false;
    }

    if (
        action.hostname !== __PORTAL_HOST__
        || !["http:", "https:"].includes(action.protocol)
        || (action.port !== "" && !(action.protocol === "https:" && action.port === "443"))
        || action.username
        || action.password
    ) {
        window.__samadFormSubmissionBlocked = true;
        return false;
    }

    if (action.protocol === "http:") {
        action.protocol = "https:";
        if (submitter && submitter.hasAttribute("formaction")) {
            submitter.setAttribute("formaction", action.href);
        } else {
            form.action = action.href;
        }
    }
    return true;
};

const nativeSubmit = HTMLFormElement.prototype.submit;
HTMLFormElement.prototype.submit = function() {
    if (!secureFormAction(this, null)) {
        return;
    }
    return nativeSubmit.call(this);
};

document.addEventListener("submit", (event) => {
    const form = event.target;
    if (!(form instanceof HTMLFormElement)) {
        return;
    }
    if (!secureFormAction(form, event.submitter)) {
        event.preventDefault();
        event.stopImmediatePropagation();
    }
}, true);
"""


class SamadAutomation:
    def __init__(
        self,
        base_url: str = "https://food.sru.ac.ir",
        headless: bool = True,
        timeout_ms: int = 15_000,
        login_timeout_ms: int = 30_000,
    ) -> None:
        self.base_url = validate_samad_base_url(base_url)
        parsed_base_url = urlsplit(self.base_url)
        self.login_url = (
            self.base_url
            if parsed_base_url.path
            else f"{self.base_url}/index.rose"
        )
        self.headless = headless
        self.timeout_ms = timeout_ms
        self.login_timeout_ms = login_timeout_ms
        self.legacy_prompt_timeout_ms = 2_000

    def check_balance(self, credentials: Credentials) -> int:
        LOGGER.debug("Starting a Samad wallet balance check")
        with self._open_page() as page:
            self._login(page, credentials)
            return parse_wallet_balance(page.locator("body").inner_text())

    def reserve_week(
        self,
        credentials: Credentials,
        preferences: list[MealPreference],
    ) -> ReservationReport:
        if not preferences:
            raise ReservationError("Configure at least one weekday and meal before running reservations")

        LOGGER.debug(
            "Starting a Samad reservation run for %d configured meals",
            len(preferences),
        )
        selected_preferences = sorted(
            preferences,
            key=lambda item: (item.weekday, item.meal.value),
        )
        halls = sorted({item.dining_hall for item in selected_preferences})
        with self._open_page() as page:
            self._login(page, credentials)
            self._open_reservation_page(page, selected_preferences[0].dining_hall, halls)
            balance_toman = parse_wallet_balance(page.locator("body").inner_text())

            availability = []
            for preference in selected_preferences:
                meal = self._inspect_preference(page, preference, halls)
                if meal is not None:
                    availability.append(meal)
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
        LOGGER.debug("Launching Chromium (headless=%s)", self.headless)
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
                context.add_init_script(
                    _SECURE_FORM_SUBMISSION_GUARD.replace(
                        "__PORTAL_HOST__",
                        json.dumps(LEGACY_PORTAL_HOST),
                    )
                )
                context.route("**/*", self._protect_portal_request)
                page = context.new_page()
                page.set_default_timeout(self.timeout_ms)
                if LOGGER.isEnabledFor(logging.DEBUG):
                    page.on("request", self._log_browser_request)
                    page.on("response", self._log_browser_response)
                    page.on("requestfailed", self._log_browser_request_failure)
                    page.on("framenavigated", self._log_browser_navigation)
                    page.on("console", self._log_browser_console)
                    page.on("pageerror", self._log_browser_page_error)
                yield page
            except PlaywrightError as error:
                raise ReservationError(
                    "Legacy portal interaction failed or timed out; the page may have changed"
                ) from error
            finally:
                context.close()
                browser.close()

    def _login(self, page: Page, credentials: Credentials) -> None:
        LOGGER.debug("Opening the legacy portal login page: %s", self._safe_url_for_log(self.login_url))
        try:
            page.goto(self.login_url, wait_until="domcontentloaded")
        except PlaywrightError as error:
            raise ReservationError(
                "Could not open the legacy portal page; check SAMAD_BASE_URL and "
                "network connectivity"
            ) from error

        try:
            LOGGER.debug("Checking for the legacy-interface selection prompt")
            self._select_legacy_interface(page)
            self._ensure_secure_login_form(page)
            username_input, password_input = self._legacy_login_inputs(page)
            username_input.fill(credentials.username)
            password_input.fill(credentials.password)
            LOGGER.debug("Filled login fields; credential values are redacted")
            login_button = page.locator("#login_btn_submit:visible")
            if self._visible_count(login_button) != 1:
                raise ReservationError("Could not identify one legacy portal login button")
            if not login_button.is_enabled():
                try:
                    page.wait_for_function(
                        "() => { const button = document.querySelector('#login_btn_submit'); "
                        "return Boolean(button) && !button.disabled; }",
                        timeout=self.timeout_ms,
                    )
                except PlaywrightTimeoutError as error:
                    raise ReservationError(
                        "The legacy portal login button remained disabled; check the login "
                        "form manually. Automated verification will not be attempted"
                    ) from error
            LOGGER.debug("Submitting the login form")
            self._click_unique(login_button, "legacy portal login button")
            self._handle_send_anyway_prompt(page)
        except PlaywrightError as error:
            raise ReservationError("Could not complete the legacy portal login form") from error

        LOGGER.debug("Login form submitted; waiting for the student dashboard")
        self._wait_for_legacy_dashboard(page)
        LOGGER.debug("Student dashboard detected")

    def _handle_send_anyway_prompt(self, page: Page) -> None:
        page_url = urlsplit(page.url)
        if page_url.scheme != "https" or page_url.hostname != LEGACY_PORTAL_HOST:
            return

        send_anyway_button = page.get_by_role(
            "button",
            name="Send anyway",
            exact=True,
        )
        try:
            send_anyway_button.wait_for(
                state="visible",
                timeout=self.legacy_prompt_timeout_ms,
            )
        except PlaywrightTimeoutError:
            return

        self._click_unique(send_anyway_button, "Send anyway confirmation button")

    @staticmethod
    def _raise_if_blocked_form_submission(page: Page) -> None:
        if page.evaluate(
            "() => window.__samadFormSubmissionBlocked === true"
        ):
            raise ReservationError(
                "The legacy portal attempted to submit a form outside the approved "
                "portal host; the form was blocked"
            )

    def _ensure_secure_login_form(self, page: Page) -> None:
        page_url = urlsplit(page.url)
        if page_url.scheme != "https" or page_url.hostname != LEGACY_PORTAL_HOST:
            raise ReservationError(
                "The legacy portal login page is not secure; credentials were not submitted"
            )

        forms = page.locator("form:visible")
        if self._visible_count(forms) != 1:
            raise ReservationError(
                "Could not identify one visible legacy login form; credentials were not submitted"
            )

        action_url = urlsplit(
            forms.nth(0).evaluate("(form) => form.action")
        )
        try:
            action_port = action_url.port
        except ValueError:
            action_port = -1
        if (
            action_url.scheme != "https"
            or action_url.hostname != LEGACY_PORTAL_HOST
            or action_port not in (None, 443)
        ):
            LOGGER.warning(
                "Blocked insecure or unsupported login form action: %s",
                self._safe_url_for_log(forms.nth(0).evaluate("(form) => form.action")),
            )
            raise ReservationError(
                "The legacy portal login form does not submit securely; "
                "credentials were not submitted"
            )

    @staticmethod
    def _upgrade_insecure_portal_request(route: Route) -> None:
        request_url = urlsplit(route.request.url)
        try:
            request_port = request_url.port
        except ValueError:
            request_port = -1

        if (
            request_url.scheme != "http"
            or request_url.hostname != LEGACY_PORTAL_HOST
            or request_port not in (None, 80)
            or route.request.method.upper() not in {"GET", "HEAD", "POST"}
        ):
            route.abort()
            return

        secure_url = urlunsplit(
            (
                "https",
                LEGACY_PORTAL_HOST,
                request_url.path,
                request_url.query,
                "",
            )
        )
        LOGGER.debug(
            "Upgrading legacy portal %s request from %s to %s",
            route.request.method.upper(),
            SamadAutomation._safe_url_for_log(route.request.url),
            SamadAutomation._safe_url_for_log(secure_url),
        )
        route.fulfill(status=307, headers={"Location": secure_url})

    @staticmethod
    def _protect_portal_request(route: Route) -> None:
        request_url = urlsplit(route.request.url)
        method = route.request.method.upper()
        if request_url.scheme == "http" and request_url.hostname == LEGACY_PORTAL_HOST:
            SamadAutomation._upgrade_insecure_portal_request(route)
            return

        if method == "POST":
            try:
                request_port = request_url.port
            except ValueError:
                request_port = -1
            is_secure_portal_post = (
                request_url.scheme == "https"
                and request_url.hostname == LEGACY_PORTAL_HOST
                and request_port in (None, 443)
            )
            is_unsupported_portal_post = (
                request_url.hostname == LEGACY_PORTAL_HOST
                and not is_secure_portal_post
            )
            is_redirected_portal_post = (
                SamadAutomation._was_redirected_from_secure_portal_post(
                    route.request
                )
            )
            if is_unsupported_portal_post or is_redirected_portal_post:
                LOGGER.warning(
                    "Blocked insecure or off-origin POST after portal login: %s",
                    SamadAutomation._safe_url_for_log(route.request.url),
                )
                route.abort()
                return

        route.continue_()

    @staticmethod
    def _was_redirected_from_secure_portal_post(request: Request) -> bool:
        previous_request = request.redirected_from
        while previous_request is not None:
            previous_url = urlsplit(previous_request.url)
            try:
                previous_port = previous_url.port
            except ValueError:
                previous_port = -1
            if (
                previous_request.method.upper() == "POST"
                and previous_url.scheme == "https"
                and previous_url.hostname == LEGACY_PORTAL_HOST
                and previous_port in (None, 443)
            ):
                return True
            previous_request = previous_request.redirected_from
        return False

    @staticmethod
    def _log_browser_request(request: Request) -> None:
        LOGGER.debug(
            "Browser request %s %s",
            request.method,
            SamadAutomation._safe_url_for_log(request.url),
        )

    @staticmethod
    def _log_browser_response(response: Response) -> None:
        LOGGER.debug(
            "Browser response %s %s",
            response.status,
            SamadAutomation._safe_url_for_log(response.url),
        )

    @staticmethod
    def _log_browser_request_failure(request: Request) -> None:
        LOGGER.debug(
            "Browser request failed: %s %s",
            request.method,
            SamadAutomation._safe_url_for_log(request.url),
        )

    @staticmethod
    def _log_browser_navigation(frame: Frame) -> None:
        LOGGER.debug(
            "Browser frame navigated to %s",
            SamadAutomation._safe_url_for_log(frame.url),
        )

    @staticmethod
    def _log_browser_console(message: ConsoleMessage) -> None:
        if message.type in {"error", "warning"}:
            LOGGER.debug("Browser console event type=%s (message omitted)", message.type)

    @staticmethod
    def _log_browser_page_error(error: Exception) -> None:
        LOGGER.debug(
            "Browser page JavaScript exception type=%s (message omitted)",
            type(error).__name__,
        )

    @staticmethod
    def _safe_url_for_log(value: str) -> str:
        parsed_url = urlsplit(value)
        hostname = parsed_url.hostname or ""
        if ":" in hostname and not hostname.startswith("["):
            hostname = f"[{hostname}]"
        try:
            port = parsed_url.port
        except ValueError:
            port = None
        netloc = f"{hostname}:{port}" if port is not None else hostname
        return urlunsplit(
            (
                parsed_url.scheme,
                netloc,
                parsed_url.path,
                "",
                "",
            )
        )

    def _select_legacy_interface(self, page: Page) -> None:
        old_version_button = page.get_by_role(
            "button",
            name="ادامه با نسخه قدیمی",
            exact=True,
        )
        try:
            old_version_button.wait_for(
                state="visible",
                timeout=self.legacy_prompt_timeout_ms,
            )
        except PlaywrightTimeoutError:
            return
        self._click_unique(old_version_button, "continue with old version button")

    def _legacy_login_inputs(self, page: Page) -> tuple[Locator, Locator]:
        username_inputs = page.locator("#username:visible")
        password_inputs = page.locator("#password:visible")
        if self._visible_count(username_inputs) != 1:
            raise ReservationError(
                "Could not identify exactly one legacy portal username field"
            )
        if self._visible_count(password_inputs) != 1:
            raise ReservationError(
                "Could not identify exactly one legacy portal password field"
            )
        return username_inputs, password_inputs

    def _wait_for_legacy_dashboard(self, page: Page) -> None:
        LOGGER.debug(
            "Waiting up to %.1f seconds for login navigation to finish",
            self.login_timeout_ms / 1000,
        )
        try:
            page.wait_for_function(
                "() => { const button = document.querySelector('#login_btn_submit'); "
                "return window.__samadFormSubmissionBlocked === true || "
                "!button || button.offsetParent === null; }",
                timeout=self.login_timeout_ms,
            )
        except PlaywrightTimeoutError as error:
            timeout_seconds = self.login_timeout_ms / 1000
            raise ReservationError(
                f"The legacy portal did not leave its login page within "
                f"{timeout_seconds:g} seconds. Login may be pending, rejected, or "
                "waiting for manual verification"
            ) from error

        self._raise_if_blocked_form_submission(page)

        LOGGER.debug(
            "Login navigation finished at %s; checking for the reservation dashboard",
            self._safe_url_for_log(page.url),
        )
        try:
            page.wait_for_function(
                "() => Array.from(document.querySelectorAll('body *')).some(element => "
                "element.textContent.trim() === 'رزرو غذا' && "
                "!element.closest('.app-collapsible-item') && "
                "element.getClientRects().length > 0)",
                timeout=self.timeout_ms,
            )
        except PlaywrightTimeoutError as error:
            raise ReservationError("The legacy portal student dashboard did not load") from error

    def _open_food_menu(self, page: Page) -> None:
        self._click_unique(
            self._food_reservation_card(page),
            "food reservation card",
        )

    def _food_reservation_card(self, page: Page) -> Locator:
        text_matches = page.get_by_text("رزرو غذا", exact=True)
        cards = []
        for index in range(text_matches.count()):
            candidate = text_matches.nth(index)
            if candidate.is_visible() and not candidate.evaluate(
                "element => Boolean(element.closest('.app-collapsible-item'))"
            ):
                cards.append(candidate)
        if len(cards) != 1:
            raise ReservationError(
                f"Expected one visible food reservation card, found {len(cards)}"
            )
        return cards[0]

    def _open_reservation_page(
        self,
        page: Page,
        first_hall: str,
        configured_halls: list[str],
    ) -> None:
        self._open_food_menu(page)
        self._select_dining_hall(page, first_hall, configured_halls)
        self._continue_hall_selection(page, required=True)

    def _select_dining_hall(
        self,
        page: Page,
        target_hall: str,
        configured_halls: list[str],
    ) -> None:
        selects = page.locator("select:visible")
        matching_selects: list[tuple[Locator, str]] = []
        for index in range(selects.count()):
            select = selects.nth(index)
            for label in select.locator("option").all_inner_texts():
                if self._normalize(label) == self._normalize(target_hall):
                    matching_selects.append((select, label.strip()))
                    break
        if len(matching_selects) > 1:
            raise ReservationError("The legacy portal dining-hall selector is ambiguous")
        if matching_selects:
            select, option_label = matching_selects[0]
            try:
                current_label = select.locator("option:checked").inner_text().strip()
                if self._normalize(current_label) == self._normalize(target_hall):
                    return
                select.select_option(label=option_label)
            except PlaywrightError as error:
                raise ReservationError(
                    f"Could not select legacy portal dining hall {target_hall}"
                ) from error
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
                matching_combos[0].click()
                self._choose_visible_option(page, target_hall, f"dining hall {target_hall}")
            except PlaywrightError as error:
                raise ReservationError(
                    f"Could not select legacy portal dining hall {target_hall}"
                ) from error
            return
        if len(matching_combos) > 1:
            raise ReservationError("The legacy portal dining-hall selector is ambiguous")

        buttons = page.get_by_role("button")
        matching_buttons: list[tuple[Locator, str]] = []
        for index in range(buttons.count()):
            button = buttons.nth(index)
            if not button.is_visible():
                continue
            label = button.inner_text().strip()
            if any(self._normalize(hall) in self._normalize(label) for hall in configured_halls):
                matching_buttons.append((button, label))
        if len(matching_buttons) != 1:
            raise ReservationError("Could not identify the legacy portal dining-hall selector")
        button, current_label = matching_buttons[0]
        if self._normalize(target_hall) in self._normalize(current_label):
            return
        try:
            button.click()
            self._choose_visible_option(page, target_hall, f"dining hall {target_hall}")
        except PlaywrightError as error:
            raise ReservationError(
                f"Could not select legacy portal dining hall {target_hall}"
            ) from error

    def _continue_hall_selection(self, page: Page, required: bool = False) -> None:
        continue_button = page.get_by_role(
            "button",
            name="تایید و ادامه",
            exact=True,
        )
        visible_count = self._visible_count(continue_button)
        if visible_count > 1:
            raise ReservationError("The legacy portal hall confirmation button is ambiguous")
        if visible_count == 0:
            if required:
                self._reservation_table(page)
            return
        self._click_unique(continue_button, "legacy portal hall confirmation button")
        self._wait_for_reservation_table(page)

    def _wait_for_reservation_table(self, page: Page) -> None:
        try:
            page.locator("table:visible").first.wait_for(
                state="visible",
                timeout=self.timeout_ms,
            )
        except PlaywrightTimeoutError as error:
            raise ReservationError(
                "The legacy portal weekly reservation table did not load"
            ) from error
        self._reservation_table(page)

    def _inspect_preference(
        self,
        page: Page,
        preference: MealPreference,
        configured_halls: list[str],
    ) -> MealAvailability | None:
        self._select_dining_hall(page, preference.dining_hall, configured_halls)
        self._continue_hall_selection(page)
        return self._read_meal_availability(page, preference)

    def _read_meal_availability(
        self,
        page: Page,
        preference: MealPreference,
    ) -> MealAvailability | None:
        cell = self._reservation_cell(page, preference)
        cell_text = cell.inner_text()
        normalized_text = self._normalize(cell_text)
        already_reserved = any(
            self._normalize(marker) in normalized_text for marker in _RESERVED_MARKERS
        )
        checkboxes = cell.get_by_role("checkbox")
        visible_checkboxes = []
        for index in range(checkboxes.count()):
            checkbox = checkboxes.nth(index)
            if checkbox.is_visible():
                visible_checkboxes.append(checkbox)
        if len(visible_checkboxes) > 1:
            raise ReservationError(
                f"The {preference.meal.persian_label} cell has multiple reservation checkboxes"
            )
        if not visible_checkboxes:
            if not already_reserved:
                return None
            return MealAvailability(
                weekday=preference.weekday,
                meal=preference.meal,
                dining_hall=preference.dining_hall,
                price_toman=0,
                already_reserved=True,
            )
        checkbox = visible_checkboxes[0]
        if already_reserved or checkbox.is_checked():
            return MealAvailability(
                weekday=preference.weekday,
                meal=preference.meal,
                dining_hall=preference.dining_hall,
                price_toman=0,
                already_reserved=True,
            )
        if not checkbox.is_enabled():
            return None
        price_toman = parse_toman_amount(cell_text)
        return MealAvailability(
            weekday=preference.weekday,
            meal=preference.meal,
            dining_hall=preference.dining_hall,
            price_toman=price_toman,
            already_reserved=already_reserved,
        )

    def _reservation_table(self, page: Page) -> Locator:
        tables = page.locator("table:visible")
        matching_tables: list[Locator] = []
        for index in range(tables.count()):
            table = tables.nth(index)
            header_cells = self._table_header_cells(table)
            if header_cells is None:
                continue
            labels = [
                self._normalize(header_cells.nth(cell_index).inner_text())
                for cell_index in range(header_cells.count())
            ]
            has_day_column = any(
                "روز" in label or "تاریخ" in label
                for label in labels
            )
            required_meals = ("صبحانه", "ناهار", "شام")
            has_meal_columns = any(
                self._normalize(meal_label) in label
                for meal_label in required_meals
                for label in labels
            )
            if has_day_column and has_meal_columns:
                matching_tables.append(table)
        if len(matching_tables) != 1:
            raise ReservationError(
                f"Could not identify exactly one legacy weekly reservation table; "
                f"found {len(matching_tables)}"
            )
        return matching_tables[0]

    @staticmethod
    def _table_header_cells(table: Locator) -> Locator | None:
        header_rows = table.locator("thead tr")
        if header_rows.count() > 0:
            return header_rows.first.locator("th, td")
        rows = table.locator("tr")
        for index in range(rows.count()):
            row = rows.nth(index)
            if row.locator("th").count() > 0:
                return row.locator("th, td")
        return None

    def _reservation_cell(
        self,
        page: Page,
        preference: MealPreference,
    ) -> Locator:
        table = self._reservation_table(page)
        header_cells = self._table_header_cells(table)
        if header_cells is None:
            raise ReservationError("The legacy reservation table has no identifiable header")
        headers = [
            self._normalize(header_cells.nth(index).inner_text())
            for index in range(header_cells.count())
        ]
        day_columns = [
            index
            for index, label in enumerate(headers)
            if "روز" in label or "تاریخ" in label
        ]
        meal_label = self._normalize(preference.meal.persian_label)
        meal_columns = [
            index
            for index, label in enumerate(headers)
            if meal_label in label
        ]
        if len(day_columns) != 1 or len(meal_columns) != 1:
            raise ReservationError(
                f"Could not identify one weekday and {preference.meal.persian_label} column"
            )

        rows = table.locator("tr")
        matching_rows: list[Locator] = []
        day_label = self._normalize_day(Weekday(preference.weekday).persian_label)
        for row_index in range(rows.count()):
            row = rows.nth(row_index)
            cells = row.locator("th, td")
            if cells.count() <= max(day_columns[0], meal_columns[0]):
                continue
            row_day = self._normalize_day(cells.nth(day_columns[0]).inner_text())
            if day_label in row_day:
                matching_rows.append(row)
        if len(matching_rows) != 1:
            raise ReservationError(
                f"Could not identify one {Weekday(preference.weekday).persian_label} row; "
                f"found {len(matching_rows)}"
            )
        return matching_rows[0].locator("th, td").nth(meal_columns[0])

    def _reserve_meal(
        self,
        page: Page,
        planned: MealAvailability,
        configured_halls: list[str],
    ) -> ReservationOutcome:
        self._select_dining_hall(page, planned.dining_hall, configured_halls)
        self._continue_hall_selection(page)
        preference = MealPreference(
            weekday=planned.weekday,
            meal=planned.meal,
            dining_hall=planned.dining_hall,
        )
        current = self._read_meal_availability(page, preference)
        if current is None:
            raise ReservationError(
                f"The {preference.meal.persian_label} is no longer available"
            )
        if current.already_reserved:
            return ReservationOutcome(
                planned.weekday,
                planned.meal,
                planned.dining_hall,
                "already_reserved",
            )
        if current.price_toman != planned.price_toman:
            raise ReservationError(
                "Meal price changed after the balance preflight; stopping the run"
            )

        cell = self._reservation_cell(page, preference)
        checkbox = cell.get_by_role("checkbox")
        self._click_unique(
            checkbox,
            f"reservation checkbox for {preference.meal.persian_label}",
        )
        confirm_button = page.get_by_role("button", name="تایید", exact=True)
        if self._visible_count(confirm_button) == 0:
            confirm_button = page.get_by_role("button", name="تأیید", exact=True)
        self._click_unique(confirm_button, "legacy portal reservation confirmation button")
        self._confirm_reservation_if_requested(page)

        refreshed = self._read_meal_availability(page, preference)
        if refreshed is None or not refreshed.already_reserved:
            raise ReservationError(
                f"The legacy portal did not confirm the {preference.meal.persian_label} reservation"
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
        raise ReservationError(
            "The legacy portal displayed an unrecognized reservation confirmation dialog"
        )

    def _choose_visible_option(self, page: Page, label: str, description: str) -> None:
        options = page.get_by_role("option", name=label, exact=True)
        if self._visible_count(options) == 1:
            self._click_unique(options, description)
            return
        self._click_unique(page.get_by_text(label, exact=True), description)

    def _click_unique(self, locator: Locator, description: str) -> None:
        visible_matches = []
        for index in range(locator.count()):
            candidate = locator.nth(index)
            if candidate.is_visible():
                visible_matches.append(candidate)
        if len(visible_matches) != 1:
            raise ReservationError(
                f"Expected one visible {description}, found {len(visible_matches)}; "
                "refusing an ambiguous action"
            )
        try:
            visible_matches[0].click()
        except PlaywrightError as error:
            raise ReservationError(f"Could not click {description}") from error

    @staticmethod
    def _visible_count(locator: Locator) -> int:
        return sum(1 for index in range(locator.count()) if locator.nth(index).is_visible())

    @staticmethod
    def _normalize(value: str) -> str:
        return value.strip().casefold().replace("ي", "ی").replace("ك", "ک")

    @classmethod
    def _normalize_day(cls, value: str) -> str:
        return "".join(
            character
            for character in cls._normalize(value)
            if not character.isspace() and character != "\u200c"
        )
