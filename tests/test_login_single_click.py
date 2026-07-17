"""Regression tests for #174 — login must work with a SINGLE Confirm click.

History: #174 has regressed repeatedly. Two independent causes were found:

1. (fixed in a084d9b) the remember-me cookie was written from INSIDE
   ``st.form('LoginForm')``, where Streamlit suppresses the CookieManager
   component's value-change rerun until the NEXT form submit — so the deferred
   redirect only fired on a second Confirm click.

2. (fixed in this branch, empirically confirmed 2026-07-14 with a Playwright
   repro) browser/password-manager AUTOFILL paints values into the login inputs
   without the input events Streamlit needs to sync widget state, so the first
   Confirm submits EMPTY strings. Streamlit defaults password inputs to
   ``autocomplete="new-password"`` (a registration-form hint); the fix declares
   ``autocomplete="username"`` / ``"current-password"`` so browsers treat the
   form as a login form and fill via real input events. A specific
   ``Login.missing_fields`` warning guards the residual empty-submit case.

``pages/login.py`` executes Streamlit calls at import time, so these are
source-level assertions — they lock the load-bearing properties of the page
text so a refactor cannot silently reintroduce either cause.
"""

from pathlib import Path

from text_content import Login

LOGIN_SRC = (Path(__file__).parent.parent / "pages" / "login.py").read_text()


def _form_block():
    """Return the source of the ``with st.form('LoginForm')`` block only.

    The block ends at the first line indented LESS than the form body (i.e.
    back at the ``with``'s own indentation) after the form line.
    """
    lines = LOGIN_SRC.splitlines()
    start = next(
        i for i, line in enumerate(lines)
        if line.strip().startswith("with st.form('LoginForm')")
    )
    indent = len(lines[start]) - len(lines[start].lstrip())
    block = [lines[start]]
    for line in lines[start + 1:]:
        if line.strip() and (len(line) - len(line.lstrip())) <= indent:
            break
        block.append(line)
    return "\n".join(block)


def test_email_input_declares_username_autocomplete():
    assert 'autocomplete="username"' in _form_block()


def test_password_input_declares_current_password_autocomplete():
    assert 'autocomplete="current-password"' in _form_block()


def test_empty_submit_guard_present_before_authentication():
    """confirm() must reject empty username/password with the specific
    Login.missing_fields warning BEFORE calling authenticate_user."""
    guard = LOGIN_SRC.find("Login.missing_fields")
    auth_call = LOGIN_SRC.find("authenticate_user(username, password)")
    assert guard != -1, "empty-submit guard missing from pages/login.py"
    assert auth_call != -1
    assert guard < auth_call, "guard must run before authenticate_user"


def test_missing_fields_text_defined():
    assert Login.missing_fields
    assert "password" in Login.missing_fields.lower()


def test_cookie_write_not_inside_login_form():
    """Cause 1 (a084d9b): set_remember_cookie must never be called inside the
    LoginForm block — the CookieManager component rerun is suppressed there."""
    assert "set_remember_cookie" not in _form_block()
    # ... and the deferred write must still exist somewhere on the page.
    assert "set_remember_cookie" in LOGIN_SRC
