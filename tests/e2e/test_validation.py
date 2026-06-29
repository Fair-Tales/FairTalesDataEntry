"""Data-validation list journeys (#47 / #83 — pages/validation.py).

The validation page is gated to the **team** and **admin** tiers. The page is
only registered in ``st.navigation`` (and only linked in the sidebar) for those
tiers, so an archivist cannot reach it at all — there is no deterministic
archivist-side assertion here beyond 'the link is absent', which lives in
``test_sidebar_roles.py``.

These tests therefore require ``TEST_USER_ROLE`` to be ``team`` or ``admin``;
they skip cleanly otherwise. They are read-only: they reach the awaiting-list,
assert its header and the submitted-only toggle, and toggle it once (a pure UI
filter — it writes nothing). They never open a book for review or approve one.
"""

import pytest

import helpers as h


def _require_team(test_user_role):
    if test_user_role not in ("team", "admin"):
        pytest.skip(
            "Validation is team/admin-gated. Set TEST_USER_ROLE=team (or admin) "
            "with a matching account to exercise it."
        )


def _open_validation(page):
    h.open_sidebar(page)
    link = h.sidebar_link(page, h.SIDEBAR_VALIDATION_LINK)
    link.wait_for(state="visible", timeout=h.RERUN_TIMEOUT)
    link.click()


def test_validation_list_renders_for_team(logged_in_page, test_user_role):
    """Team/admin reach the awaiting-validation list (header + toggle)."""
    _require_team(test_user_role)
    page = logged_in_page
    _open_validation(page)

    # render_list shows the list header, then either the pending selectbox or the
    # 'none pending' info — both are valid; assert the always-present header +
    # the submitted-only toggle.
    page.get_by_text(h.VALIDATION_LIST_HEADER, exact=False).first.wait_for(
        state="visible", timeout=h.RERUN_TIMEOUT
    )
    assert page.locator(h.key(h.VALIDATION_SUBMITTED_TOGGLE)).count() > 0
    # The page must NOT have rejected us with the not-authorised error.
    assert (
        page.get_by_text(h.VALIDATION_NOT_AUTHORISED, exact=False).count() == 0
    )


def test_validation_submitted_only_toggle(logged_in_page, test_user_role):
    """The submitted-only toggle is interactable (pure client-side filter)."""
    _require_team(test_user_role)
    page = logged_in_page
    _open_validation(page)

    toggle = page.locator(f"{h.key(h.VALIDATION_SUBMITTED_TOGGLE)} input")
    toggle.wait_for(state="visible", timeout=h.RERUN_TIMEOUT)
    toggle.click()
    page.wait_for_timeout(800)
    # Still on the validation list after toggling (no crash / no redirect).
    assert page.get_by_text(h.VALIDATION_LIST_HEADER, exact=False).first.is_visible()
