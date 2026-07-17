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


def test_login_inputs_declare_login_autocomplete(page, base_url):
    """#174 regression: the login inputs must carry autocomplete="username" /
    "current-password" so browser/password-manager autofill dispatches real
    input events (Streamlit's default "new-password" marks the form as a
    REGISTRATION form and autofilled values never sync — first Confirm then
    submits empty strings, i.e. the recurring two-click login)."""
    page.goto(base_url, wait_until="domcontentloaded")
    email = page.locator(f"{h.key(h.LOGIN_EMAIL)} input")
    email.wait_for(state="visible", timeout=h.RERUN_TIMEOUT)
    assert email.get_attribute("autocomplete") == "username"
    assert (
        page.locator(f"{h.key(h.LOGIN_PASSWORD)} input").get_attribute("autocomplete")
        == "current-password"
    )


def test_login_empty_submit_shows_missing_fields_warning(page, base_url):
    """#174: an empty Confirm click (the autofill-desync symptom) must show the
    specific missing-fields hint, not the misleading 'Invalid credentials.'"""
    page.goto(base_url, wait_until="domcontentloaded")
    page.locator(f"{h.key(h.LOGIN_SUBMIT)} button").wait_for(
        state="visible", timeout=h.RERUN_TIMEOUT
    )
    page.locator(f"{h.key(h.LOGIN_SUBMIT)} button").click()
    page.get_by_text("Please enter your email and password", exact=False).wait_for(
        state="visible", timeout=h.RERUN_TIMEOUT
    )


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
