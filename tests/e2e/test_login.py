"""Login journey: valid creds reach the landing page; invalid creds show an error."""

import helpers as h


def test_login_page_renders(page, base_url):
    """The app's first load routes to the login page with the email/password form."""
    page.goto(base_url, wait_until="domcontentloaded")
    page.locator(f"{h.key(h.LOGIN_EMAIL)} input").wait_for(
        state="visible", timeout=h.RERUN_TIMEOUT
    )
    assert page.locator(f"{h.key(h.LOGIN_PASSWORD)} input").is_visible()
    assert page.locator(f"{h.key(h.LOGIN_SUBMIT)} button").is_visible()


def test_login_invalid_credentials_shows_error(page, base_url):
    """Submitting bad credentials surfaces the 'Invalid credentials.' alert."""
    page.goto(base_url, wait_until="domcontentloaded")
    page.locator(f"{h.key(h.LOGIN_EMAIL)} input").wait_for(
        state="visible", timeout=h.RERUN_TIMEOUT
    )
    page.locator(f"{h.key(h.LOGIN_EMAIL)} input").fill("not-a-real-user@example.invalid")
    page.locator(f"{h.key(h.LOGIN_PASSWORD)} input").fill("definitely-wrong-password")
    page.locator(f"{h.key(h.LOGIN_SUBMIT)} button").click()
    # Stay on login; the error renders in the main area.
    page.get_by_text(h.INVALID_CREDENTIALS, exact=False).wait_for(
        state="visible", timeout=h.RERUN_TIMEOUT
    )


def test_login_valid_credentials_reaches_landing(logged_in_page):
    """Valid creds (via the logged_in_page fixture) land on the home/landing page."""
    page = logged_in_page
    assert page.locator(h.key(h.LANDING_ENTER_DATA)).is_visible()
    assert page.locator(h.key(h.LANDING_VIEW_RESULTS)).is_visible()
    assert page.get_by_text(h.APP_TITLE, exact=False).first.is_visible()
