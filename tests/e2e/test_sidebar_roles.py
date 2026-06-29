"""Role-gated sidebar visibility (#47 / #83 — utilities.page_layout).

The sidebar always shows the base links (Login / Home / Books We Need / Settings
/ Donate / Report a Bug). Two extra links are role-gated:

* 'Data validation' — shown for **team** and **admin**.
* 'Admin'           — shown for **admin** only.

Role-independent invariants (asserted for any account):

* the base links are always present, and
* if the 'Admin' link is present then 'Data validation' must also be present
  (admin implies team-visible) — never the reverse.

When ``TEST_USER_ROLE`` is set we additionally assert the *exact* expected
visibility for that tier. The sidebar is collapsed by default, so we open it
first; presence is checked with ``.count()`` (the links stay in the DOM).
"""

import pytest

import helpers as h


def _link_present(page, label) -> bool:
    return h.sidebar_link(page, label).count() > 0


def test_base_sidebar_links_present(logged_in_page):
    """The always-on base links render for every authenticated user."""
    page = logged_in_page
    h.open_sidebar(page)
    page.wait_for_timeout(400)
    assert _link_present(page, h.SIDEBAR_HOME_LINK)
    assert _link_present(page, h.SIDEBAR_SETTINGS_LINK)


def test_admin_link_implies_validation_link(logged_in_page):
    """Role-independent invariant: Admin visible => Data validation visible."""
    page = logged_in_page
    h.open_sidebar(page)
    page.wait_for_timeout(400)
    if _link_present(page, h.SIDEBAR_ADMIN_LINK):
        assert _link_present(page, h.SIDEBAR_VALIDATION_LINK)


def test_role_gated_links_match_role(logged_in_page, test_user_role):
    """With TEST_USER_ROLE known, assert exact gated-link visibility."""
    if test_user_role is None:
        pytest.skip(
            "Set TEST_USER_ROLE (archivist / team / admin) to assert exact "
            "role-gated sidebar visibility."
        )
    page = logged_in_page
    h.open_sidebar(page)
    page.wait_for_timeout(400)

    validation_present = _link_present(page, h.SIDEBAR_VALIDATION_LINK)
    admin_present = _link_present(page, h.SIDEBAR_ADMIN_LINK)

    if test_user_role == "admin":
        assert validation_present
        assert admin_present
    elif test_user_role == "team":
        assert validation_present
        assert not admin_present
    else:  # archivist
        assert not validation_present
        assert not admin_present
