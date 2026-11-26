import os
import unittest
from unittest.mock import Mock, patch

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Route
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from samad_reserver.domain import Meal, MealPreference
from samad_reserver.errors import ReservationError
from samad_reserver.samad_automation import (
    SamadAutomation,
    _playwright_with_system_ca,
)


class FakeLocator:
    def __init__(self, items: list[object]) -> None:
        self.items = items

    @property
    def first(self) -> object:
        return self.items[0]

    def count(self) -> int:
        return len(self.items)

    def nth(self, index: int) -> object:
        return self.items[index]


class FakeCell:
    def __init__(self, text: str) -> None:
        self.text = text

    def inner_text(self) -> str:
        return self.text


class FakeRow:
    def __init__(self, values: list[str]) -> None:
        self.cells = [FakeCell(value) for value in values]

    def locator(self, selector: str) -> FakeLocator:
        if selector != "th, td":
            raise AssertionError(f"Unexpected selector: {selector}")
        return FakeLocator(self.cells)


class FakeTable:
    def __init__(self, header: list[str], rows: list[list[str]]) -> None:
        self.header = FakeRow(header)
        self.rows = [FakeRow(row) for row in rows]

    def locator(self, selector: str) -> FakeLocator:
        if selector == "thead tr":
            return FakeLocator([self.header])
        if selector == "tbody tr":
            return FakeLocator(self.rows)
        if selector == "tr":
            return FakeLocator([self.header, *self.rows])
        raise AssertionError(f"Unexpected selector: {selector}")


class FakePage:
    def __init__(self, table: FakeTable) -> None:
        self.table = table

    def locator(self, selector: str) -> FakeLocator:
        if selector != "table:visible":
            raise AssertionError(f"Unexpected selector: {selector}")
        return FakeLocator([self.table])


class LegacyPortalLoginTests(unittest.TestCase):
    def setUp(self) -> None:
        self.automation = SamadAutomation(base_url="https://food.sru.ac.ir")
        self.page = Mock()

    def test_uses_the_secure_legacy_login_page(self) -> None:
        self.assertEqual(
            self.automation.login_url,
            "https://food.sru.ac.ir/index.rose",
        )

    def test_refuses_an_insecure_portal_url(self) -> None:
        with self.assertRaisesRegex(ValueError, "HTTPS"):
            SamadAutomation(base_url="http://food.sru.ac.ir")

    def test_refuses_the_new_samad_portal_host(self) -> None:
        with self.assertRaisesRegex(ValueError, "food.sru.ac.ir"):
            SamadAutomation(base_url="https://samad.app")

    def test_redacts_credentials_query_and_fragment_from_verbose_urls(self) -> None:
        safe_url = self.automation._safe_url_for_log(
            "https://student:secret@food.sru.ac.ir/home.rose?token=private#section"
        )

        self.assertEqual(safe_url, "https://food.sru.ac.ir/home.rose")

    def test_verbose_browser_request_log_redacts_query_strings(self) -> None:
        request = Mock(
            method="POST",
            url="https://food.sru.ac.ir/j_security_check?token=private",
        )

        with self.assertLogs("samad_reserver.samad_automation", level="DEBUG") as logs:
            self.automation._log_browser_request(request)

        self.assertIn("POST https://food.sru.ac.ir/j_security_check", logs.output[0])
        self.assertNotIn("private", logs.output[0])

    def test_reports_portal_navigation_failure_separately_from_login_failure(self) -> None:
        self.page.goto.side_effect = PlaywrightTimeoutError("navigation timeout")

        with self.assertRaisesRegex(ReservationError, "open the legacy portal page"):
            self.automation._login(self.page, Mock())

    def test_playwright_driver_uses_system_ca_and_restores_node_options(self) -> None:
        playwright = Mock()
        manager = Mock()

        def start_driver() -> Mock:
            options = os.environ.get("NODE_OPTIONS", "").split()
            self.assertIn("--use-system-ca", options)
            return playwright

        manager.start.side_effect = start_driver
        with patch.dict(os.environ, {"NODE_OPTIONS": "--trace-warnings"}):
            with patch(
                "samad_reserver.samad_automation.sync_playwright",
                return_value=manager,
            ):
                with _playwright_with_system_ca() as actual_playwright:
                    self.assertIs(actual_playwright, playwright)

            self.assertEqual(os.environ["NODE_OPTIONS"], "--trace-warnings")
        playwright.stop.assert_called_once_with()

    def test_does_not_evaluate_the_old_page_after_login_navigation(self) -> None:
        page = Mock()
        login_button = Mock()
        page.locator.return_value = login_button
        login_button.is_enabled.return_value = True
        page.evaluate.side_effect = PlaywrightError(
            "Execution context was destroyed, most likely because of a navigation"
        )
        self.automation._select_legacy_interface = Mock()
        self.automation._ensure_secure_login_form = Mock()
        self.automation._legacy_login_inputs = Mock(
            return_value=(Mock(), Mock())
        )
        self.automation._visible_count = Mock(return_value=1)
        self.automation._click_unique = Mock()
        self.automation._wait_for_legacy_dashboard = Mock()

        self.automation._login(
            page,
            Mock(username="student", password="not-a-real-password"),
        )

        page.evaluate.assert_not_called()
        self.automation._wait_for_legacy_dashboard.assert_called_once_with(page)

    def test_waits_for_the_dashboard_after_cross_document_login_navigation(self) -> None:
        with self.automation._open_page() as page:
            page.route(
                "https://food.sru.ac.ir/dashboard-test",
                lambda route: route.fulfill(
                    status=200,
                    content_type="text/html",
                    body="<div>&#1585;&#1586;&#1585;&#1608; &#1594;&#1584;&#1575;</div>",
                ),
            )
            page.set_content("<button id='login_btn_submit'>Login</button>")
            page.evaluate(
                "setTimeout(() => { "
                "location.href = 'https://food.sru.ac.ir/dashboard-test'; "
                "}, 50)"
            )

            self.automation._wait_for_legacy_dashboard(page)

            self.assertTrue(page.url.endswith("/dashboard-test"))

    def test_refuses_to_submit_credentials_through_an_http_login_form(self) -> None:
        form = Mock()
        form.is_visible.return_value = True
        form.evaluate.return_value = "http://food.sru.ac.ir/j_security_check"
        forms = Mock()
        forms.count.return_value = 1
        forms.nth.return_value = form
        self.page.url = "https://food.sru.ac.ir/index/index.rose"
        self.page.locator.return_value = forms

        with self.assertRaisesRegex(ReservationError, "does not submit securely"):
            self.automation._ensure_secure_login_form(self.page)

    def test_redirects_insecure_portal_gets_to_https(self) -> None:
        route = Mock()
        route.request.url = "http://food.sru.ac.ir/index/index.rose?next=1"
        route.request.method = "GET"

        self.automation._upgrade_insecure_portal_request(route)

        route.fulfill.assert_called_once_with(
            status=307,
            headers={
                "Location": "https://food.sru.ac.ir/index/index.rose?next=1",
            },
        )

    def test_upgrades_insecure_portal_posts_to_https(self) -> None:
        route = Mock()
        route.request.url = "http://food.sru.ac.ir/j_security_check"
        route.request.method = "POST"

        self.automation._upgrade_insecure_portal_request(route)

        route.fulfill.assert_called_once_with(
            status=307,
            headers={
                "Location": "https://food.sru.ac.ir/j_security_check",
            },
        )
        route.abort.assert_not_called()
        route.continue_.assert_not_called()

    def test_fulfills_secure_portal_post_without_redirect(self) -> None:
        route = Mock()
        route.request.url = "https://food.sru.ac.ir/j_security_check"
        route.request.method = "POST"
        route.request.redirected_from = None
        response = Mock(status=200, headers={})
        route.fetch.return_value = response

        self.automation._protect_portal_request(route)

        route.fetch.assert_called_once_with(max_redirects=0)
        route.fulfill.assert_called_once_with(response=response)
        route.abort.assert_not_called()

    def test_rewrites_http_redirect_from_secure_portal_post_to_https(self) -> None:
        route = Mock()
        route.request.url = "https://food.sru.ac.ir/j_security_check"
        route.request.method = "POST"
        route.request.redirected_from = None
        response = Mock(
            status=307,
            headers={
                "content-type": "text/html",
                "location": "http://food.sru.ac.ir/home.rose?actionName=home",
                "set-cookie": "session=private",
            },
        )
        route.fetch.return_value = response

        self.automation._protect_portal_request(route)

        route.fetch.assert_called_once_with(max_redirects=0)
        route.fulfill.assert_called_once_with(
            response=response,
            headers={
                "content-type": "text/html",
                "location": "https://food.sru.ac.ir/home.rose?actionName=home",
                "set-cookie": "session=private",
            },
        )
        route.abort.assert_not_called()

    def test_blocks_http_redirect_to_unsupported_portal_port(self) -> None:
        route = Mock()
        route.request.url = "https://food.sru.ac.ir/j_security_check"
        route.request.method = "POST"
        route.request.redirected_from = None
        response = Mock(
            status=307,
            headers={"location": "http://food.sru.ac.ir:8080/home.rose"},
        )
        route.fetch.return_value = response

        with self.assertLogs("samad_reserver.samad_automation", level="WARNING"):
            self.automation._protect_portal_request(route)

        route.fetch.assert_called_once_with(max_redirects=0)
        route.abort.assert_called_once_with()
        route.fulfill.assert_not_called()

    def test_redacts_route_fetch_failure_after_aborting_secure_post(self) -> None:
        route = Mock()
        route.request.url = "https://food.sru.ac.ir/j_security_check"
        route.request.method = "POST"
        route.request.redirected_from = None
        route.fetch.side_effect = PlaywrightError(
            "TLS certificate verification failed\n"
            "Call log:\n"
            "  cookie: JSESSIONID=private-session"
        )

        with self.assertRaisesRegex(ReservationError, "securely inspect") as error:
            self.automation._protect_portal_request(route)

        route.abort.assert_called_once_with()
        self.assertNotIn("private-session", str(error.exception))
        self.assertIsNone(error.exception.__cause__)

    def test_allows_same_origin_secure_portal_post_redirects(self) -> None:
        route = Mock()
        route.request.url = "https://food.sru.ac.ir/home.rose?actionName=home"
        route.request.method = "POST"
        route.request.redirected_from.url = "https://food.sru.ac.ir/j_security_check"
        route.request.redirected_from.method = "POST"
        route.request.redirected_from.redirected_from = None
        response = Mock(status=200, headers={})
        route.fetch.return_value = response

        self.automation._protect_portal_request(route)

        route.fetch.assert_called_once_with(max_redirects=0)
        route.fulfill.assert_called_once_with(response=response)
        route.abort.assert_not_called()

    def test_upgrades_http_redirected_portal_posts(self) -> None:
        route = Mock()
        route.request.url = "http://food.sru.ac.ir/home.rose?actionName=home"
        route.request.method = "POST"

        self.automation._protect_portal_request(route)

        route.fulfill.assert_called_once_with(
            status=307,
            headers={
                "Location": "https://food.sru.ac.ir/home.rose?actionName=home",
            },
        )
        route.abort.assert_not_called()
        route.continue_.assert_not_called()

    def test_blocks_off_host_secure_post_redirects(self) -> None:
        route = Mock()
        route.request.url = "https://example.com/submit"
        route.request.method = "POST"
        route.request.redirected_from.url = "https://food.sru.ac.ir/j_security_check"
        route.request.redirected_from.method = "POST"
        route.request.redirected_from.redirected_from = None

        self.automation._protect_portal_request(route)

        route.abort.assert_called_once()
        route.continue_.assert_not_called()

    def test_allows_unrelated_off_host_posts(self) -> None:
        route = Mock()
        route.request.url = "https://api.example.com/submit"
        route.request.method = "POST"
        route.request.redirected_from = None

        self.automation._protect_portal_request(route)

        route.continue_.assert_called_once_with()
        route.abort.assert_not_called()

    def test_allows_non_post_requests_to_other_hosts(self) -> None:
        route = Mock()
        route.request.url = "https://static.example.com/asset.js"
        route.request.method = "GET"

        self.automation._protect_portal_request(route)

        route.continue_.assert_called_once_with()
        route.abort.assert_not_called()

    def test_upgrades_same_host_http_form_submissions_to_https(self) -> None:
        with self.automation._open_page() as page:
            page.goto("about:blank")
            action = page.evaluate(
                """() => {
                    const form = document.createElement('form');
                    form.action = 'http://food.sru.ac.ir/home.rose?actionName=home';
                    form.addEventListener('submit', event => event.preventDefault());
                    document.body.append(form);
                    form.requestSubmit();
                    return form.action;
                }"""
            )

        self.assertEqual(
            action,
            "https://food.sru.ac.ir/home.rose?actionName=home",
        )

    def test_upgrades_same_host_http_submitter_action_to_https(self) -> None:
        with self.automation._open_page() as page:
            page.goto("about:blank")
            action = page.evaluate(
                """() => {
                    const form = document.createElement('form');
                    form.action = 'https://food.sru.ac.ir/j_security_check';
                    const button = document.createElement('button');
                    button.type = 'submit';
                    button.setAttribute(
                        'formaction',
                        'http://food.sru.ac.ir/home.rose?actionName=home'
                    );
                    form.append(button);
                    form.addEventListener('submit', event => event.preventDefault());
                    document.body.append(form);
                    form.requestSubmit(button);
                    return button.formAction;
                }"""
            )

        self.assertEqual(
            action,
            "https://food.sru.ac.ir/home.rose?actionName=home",
        )

    def test_upgrades_native_form_submit_to_https(self) -> None:
        requests: list[tuple[str, str]] = []

        def capture_request(route: Route) -> None:
            requests.append((route.request.url, route.request.method))
            route.fulfill(status=200, content_type="text/html", body="submitted")

        with self.automation._open_page() as page:
            page.goto("about:blank")
            page.route("**/*", capture_request)
            page.evaluate(
                """() => {
                    const form = document.createElement('form');
                    form.method = 'post';
                    form.action = 'http://food.sru.ac.ir/submission-test';
                    document.body.append(form);
                    setTimeout(() => form.submit(), 0);
                }"""
            )
            page.wait_for_url(
                "https://food.sru.ac.ir/submission-test",
                wait_until="domcontentloaded",
                timeout=3_000,
            )

        self.assertEqual(
            requests,
            [("https://food.sru.ac.ir/submission-test", "POST")],
        )

    def test_blocks_native_form_submit_to_another_origin(self) -> None:
        requests: list[str] = []

        def capture_request(route: Route) -> None:
            requests.append(route.request.url)
            route.fulfill(status=200, content_type="text/html", body="blocked")

        with self.automation._open_page() as page:
            page.goto("about:blank")
            page.route("**/*", capture_request)
            blocked = page.evaluate(
                """() => {
                    const form = document.createElement('form');
                    form.method = 'post';
                    form.action = 'https://example.com/submit';
                    document.body.append(form);
                    form.submit();
                    return window.__samadFormSubmissionBlocked === true;
                }"""
            )

        self.assertTrue(blocked)
        self.assertEqual(requests, [])

    def test_blocks_form_submissions_to_another_origin(self) -> None:
        with self.automation._open_page() as page:
            page.goto("about:blank")
            result = page.evaluate(
                """() => {
                    const form = document.createElement('form');
                    form.action = 'https://example.com/submit';
                    form.addEventListener('submit', event => event.preventDefault());
                    document.body.append(form);
                    form.requestSubmit();
                    return {
                        action: form.action,
                        blocked: window.__samadFormSubmissionBlocked === true
                    };
                }"""
            )

            self.assertEqual(result["action"], "https://example.com/submit")
            self.assertTrue(result["blocked"])
            with self.assertRaisesRegex(ReservationError, "approved portal host"):
                self.automation._raise_if_blocked_form_submission(page)

    def test_selects_continue_with_old_version_when_prompt_is_visible(self) -> None:
        button = Mock()
        button.count.return_value = 1
        button.nth.return_value = button
        button.is_visible.return_value = True
        self.page.get_by_role.return_value = button

        self.automation._select_legacy_interface(self.page)

        self.page.get_by_role.assert_called_once_with(
            "button",
            name="ادامه با نسخه قدیمی",
            exact=True,
        )
        button.wait_for.assert_called_once_with(
            state="visible",
            timeout=self.automation.legacy_prompt_timeout_ms,
        )
        button.click.assert_called_once()

    def test_continues_when_the_old_version_prompt_is_not_shown(self) -> None:
        button = Mock()
        button.wait_for.side_effect = PlaywrightTimeoutError("timeout")
        self.page.get_by_role.return_value = button

        self.automation._select_legacy_interface(self.page)

        button.click.assert_not_called()

    def test_targets_the_portals_explicit_login_fields(self) -> None:
        username = Mock()
        password = Mock()
        username.count.return_value = 1
        username.nth.return_value = username
        username.is_visible.return_value = True
        password.count.return_value = 1
        password.nth.return_value = password
        password.is_visible.return_value = True
        self.page.locator.side_effect = {
            "#username:visible": username,
            "#password:visible": password,
        }.__getitem__

        actual_username, actual_password = self.automation._legacy_login_inputs(self.page)

        self.assertIs(actual_username, username)
        self.assertIs(actual_password, password)

    def test_rejects_multiple_password_fields_instead_of_guessing(self) -> None:
        username = Mock()
        password = Mock()
        username.count.return_value = 1
        username.nth.return_value = username
        username.is_visible.return_value = True
        password.count.return_value = 2
        self.page.locator.side_effect = {
            "#username:visible": username,
            "#password:visible": password,
        }.__getitem__

        with self.assertRaisesRegex(ReservationError, "password field"):
            self.automation._legacy_login_inputs(self.page)

    def test_maps_the_requested_weekday_and_meal_to_one_table_cell(self) -> None:
        table = FakeTable(
            ["روز", "صبحانه", "ناهار", "شام"],
            [["شنبه ۱۴۰۵/۰۱/۰۱", "نان", "خوراک", "سوپ"]],
        )
        preference = MealPreference(0, Meal.LUNCH, "Central Brothers")

        cell = self.automation._reservation_cell(FakePage(table), preference)

        self.assertEqual(cell.inner_text(), "خوراک")

    def test_rejects_duplicate_weekday_rows_in_the_reservation_table(self) -> None:
        table = FakeTable(
            ["روز", "صبحانه", "ناهار", "شام"],
            [
                ["شنبه ۱۴۰۵/۰۱/۰۱", "نان", "خوراک", "سوپ"],
                ["شنبه ۱۴۰۵/۰۱/۰۸", "نان", "خوراک", "سوپ"],
            ],
        )

        with self.assertRaisesRegex(ReservationError, "found 2"):
            self.automation._reservation_cell(
                FakePage(table),
                MealPreference(0, Meal.LUNCH, "Central Brothers"),
            )

    def test_reads_an_available_checkbox_and_converts_its_rial_price(self) -> None:
        checkboxes = Mock()
        checkboxes.count.return_value = 1
        checkboxes.nth.return_value.is_visible.return_value = True
        checkboxes.nth.return_value.is_checked.return_value = False
        checkboxes.nth.return_value.is_enabled.return_value = True
        cell = Mock()
        cell.inner_text.return_value = "خوراک ۸۰٬۰۰۰ ریال"
        cell.get_by_role.return_value = checkboxes
        self.automation._reservation_cell = Mock(return_value=cell)

        availability = self.automation._read_meal_availability(
            self.page,
            MealPreference(0, Meal.LUNCH, "Central Brothers"),
        )

        self.assertIsNotNone(availability)
        self.assertEqual(availability.price_toman, 8_000)
        self.assertFalse(availability.already_reserved)

    def test_treats_a_meal_without_a_checkbox_as_unavailable(self) -> None:
        checkboxes = Mock()
        checkboxes.count.return_value = 0
        cell = Mock()
        cell.inner_text.return_value = "غذایی برای این روز ارائه نشده است"
        cell.get_by_role.return_value = checkboxes
        self.automation._reservation_cell = Mock(return_value=cell)

        availability = self.automation._read_meal_availability(
            self.page,
            MealPreference(0, Meal.LUNCH, "Central Brothers"),
        )

        self.assertIsNone(availability)

    def test_clicks_the_only_visible_match_when_hidden_duplicates_exist(self) -> None:
        hidden_button = Mock()
        hidden_button.is_visible.return_value = False
        visible_button = Mock()
        visible_button.is_visible.return_value = True
        buttons = Mock()
        buttons.count.return_value = 2
        buttons.nth.side_effect = lambda index: [hidden_button, visible_button][index]

        self.automation._click_unique(buttons, "test button")

        hidden_button.click.assert_not_called()
        visible_button.click.assert_called_once()

    def test_ignores_food_reservation_text_in_login_page_announcements(self) -> None:
        announcement = Mock()
        announcement.is_visible.return_value = True
        announcement.evaluate.return_value = True
        card = Mock()
        card.is_visible.return_value = True
        card.evaluate.return_value = False
        matches = Mock()
        matches.count.return_value = 2
        matches.nth.side_effect = [announcement, card]
        self.page.get_by_text.return_value = matches

        result = self.automation._food_reservation_card(self.page)

        self.assertIs(result, card)

    def test_rejects_food_reservation_announcement_without_a_dashboard_card(self) -> None:
        announcement = Mock()
        announcement.is_visible.return_value = True
        announcement.evaluate.return_value = True
        matches = Mock()
        matches.count.return_value = 1
        matches.nth.return_value = announcement
        self.page.get_by_text.return_value = matches

        with self.assertRaisesRegex(ReservationError, "food reservation card"):
            self.automation._food_reservation_card(self.page)


if __name__ == "__main__":
    unittest.main()
