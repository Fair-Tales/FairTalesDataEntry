"""Results dashboard journey: the page renders its title and chart/empty state.

Non-AI and read-only. Chart content depends on dev-DB data, so we assert the
stable scaffolding (title + 'Work in progress' section) and that the dashboard
shows *either* a rendered Vega-Lite chart *or* the documented empty-state info.
"""

import helpers as h


def _open_dashboard(page):
    # Wave B (#75): 'View results' now lands on the collection picker first; the
    # dashboard is reached via its 'View results for all books' shortcut (no
    # collection selected -> the dashboard scopes to ALL books).
    page.locator(f"{h.key(h.LANDING_VIEW_RESULTS)} button").click()
    page.locator(h.key(h.COLLECTION_METHOD_MENU)).wait_for(
        state="visible", timeout=h.RERUN_TIMEOUT
    )
    page.locator(f"{h.key(h.COLLECTION_VIEW_ALL_BUTTON)} button").click()
    page.get_by_text(h.RESULTS_PAGE_TITLE, exact=False).first.wait_for(
        state="visible", timeout=h.RERUN_TIMEOUT
    )


def test_results_dashboard_renders(logged_in_page):
    """The dashboard renders its title and the always-present 'Work in progress'."""
    page = logged_in_page
    _open_dashboard(page)
    assert page.get_by_text(h.RESULTS_PAGE_TITLE, exact=False).first.is_visible()
    page.get_by_text(h.RESULTS_WIP_HEADER, exact=False).first.wait_for(
        state="visible", timeout=h.RERUN_TIMEOUT
    )


def test_results_dashboard_shows_chart_or_empty_state(logged_in_page):
    """Either at least one Vega-Lite chart is drawn, or the empty-state info shows."""
    page = logged_in_page
    _open_dashboard(page)
    chart = page.locator("[data-testid='stVegaLiteChart']")
    empty = page.get_by_text("nothing", exact=False)  # from empty_message text
    # Give the data read + chart render a moment to settle.
    page.wait_for_timeout(1500)
    assert chart.count() > 0 or empty.count() > 0
