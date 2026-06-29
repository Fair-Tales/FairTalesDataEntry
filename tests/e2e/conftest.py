"""Pytest fixtures for the FairTalesDataEntry Playwright e2e suite (#82).

This suite assumes the Streamlit app is **already running** at ``base_url``
(default ``http://localhost:8501``). It does NOT start the server itself — start
it yourself with ``streamlit run Home.py`` (see tests/e2e/README.md).

No secrets are hardcoded. Credentials come from the environment:

    APP_BASE_URL        base URL of the running app (default http://localhost:8501)
    TEST_USER_EMAIL     a confirmed test user's email
    TEST_USER_PASSWORD  that user's password

Per DECISIONS.md 004 the suite may run against the real dev Firestore/S3; prefer
read-only / navigation assertions and keep any created data minimal and uniquely
named (see TEST_RUN_TAG below).
"""

from __future__ import annotations

import os
import time

import pytest

from helpers import (
    LANDING_ENTER_DATA,
    LOGIN_EMAIL,
    LOGIN_PASSWORD,
    LOGIN_SUBMIT,
    RERUN_TIMEOUT,
    key,
)

DEFAULT_BASE_URL = "http://localhost:8501"


@pytest.fixture(scope="session")
def base_url() -> str:
    """Base URL of the already-running Streamlit app (env ``APP_BASE_URL``)."""
    return os.environ.get("APP_BASE_URL", DEFAULT_BASE_URL).rstrip("/")


@pytest.fixture(scope="session")
def test_credentials() -> dict:
    """Test user creds from ``TEST_USER_EMAIL`` / ``TEST_USER_PASSWORD``.

    Tests that need a real login skip cleanly (rather than fail) when these are
    not set, so the suite stays runnable without a configured account.
    """
    email = os.environ.get("TEST_USER_EMAIL")
    password = os.environ.get("TEST_USER_PASSWORD")
    if not email or not password:
        pytest.skip(
            "Set TEST_USER_EMAIL and TEST_USER_PASSWORD to run login-dependent "
            "e2e tests."
        )
    return {"email": email, "password": password}


@pytest.fixture(scope="session")
def test_run_tag() -> str:
    """A unique tag for any data a test must create (env override allowed).

    DECISIONS.md 004 lets tests hit the real dev DB but asks that created data be
    uniquely named and noted for cleanup. Default is a real timestamp — fine in
    test code (the app's no-``Date.now`` rule applies only to workflow scripts).
    Override with ``TEST_RUN_TAG`` for a stable, hand-cleanable name.
    """
    return os.environ.get("TEST_RUN_TAG", f"e2e-{int(time.time())}")


def _do_login(page, base_url: str, email: str, password: str) -> None:
    """Drive the login form and wait until the landing page is reached."""
    page.goto(base_url, wait_until="domcontentloaded")
    # First load routes to the login page; wait for the email field to mount.
    email_input = page.locator(f"{key(LOGIN_EMAIL)} input")
    email_input.wait_for(state="visible", timeout=RERUN_TIMEOUT)
    email_input.fill(email)
    page.locator(f"{key(LOGIN_PASSWORD)} input").fill(password)
    page.locator(f"{key(LOGIN_SUBMIT)} button").click()
    # Successful auth switches to the landing page (Enter data / View results).
    page.locator(key(LANDING_ENTER_DATA)).wait_for(
        state="visible", timeout=RERUN_TIMEOUT
    )


@pytest.fixture
def logged_in_page(page, base_url, test_credentials):
    """A Playwright ``page`` already authenticated and on the landing page."""
    _do_login(
        page,
        base_url,
        test_credentials["email"],
        test_credentials["password"],
    )
    return page
