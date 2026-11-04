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

``pages/login.py`` executes Streamlit calls at import time. Source assertions
lock the form properties; AST extraction executes its real authenticated
branch without importing the page or contacting external services.
"""

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

from cookie_auth import RESTORED_FLAG
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


class _Stopped(BaseException):
    pass


class _SwitchedPage(BaseException):
    pass


def _authenticated_branch(state, cookie_result):
    """Compile the real top-level branch with a tiny Streamlit control fake."""
    tree = ast.parse(LOGIN_SRC)
    branch = next(
        node for node in tree.body
        if isinstance(node, ast.If)
        and isinstance(node.test, ast.Call)
        and isinstance(node.test.func, ast.Name)
        and node.test.func.id == "is_authenticated"
    )
    code = compile(ast.Module(body=[branch], type_ignores=[]), "pages/login.py", "exec")
    events = {"cookie_calls": [], "notices": [], "stops": [], "redirects": []}

    def write_cookie(username):
        events["cookie_calls"].append(username)
        return cookie_result

    def stop():
        events["stops"].append(True)
        raise _Stopped()

    def switch_page(path):
        events["redirects"].append(path)
        raise _SwitchedPage()

    namespace = {
        "st": SimpleNamespace(
            session_state=state,
            info=events["notices"].append,
            stop=stop,
            switch_page=switch_page,
        ),
        "is_authenticated": lambda: state.get("authentication_status") is True,
        "set_remember_cookie": write_cookie,
        "Login": Login,
        "RESTORED_FLAG": RESTORED_FLAG,
    }
    return lambda: exec(code, namespace), events


def test_no_cookie_write_redirects_in_same_execution():
    state = {
        "authentication_status": True,
        "username": "alice@example.com",
        "_pending_remember_cookie": "alice@example.com",
        "_post_login_redirect": True,
    }
    execute, events = _authenticated_branch(state, cookie_result=False)

    with pytest.raises(_SwitchedPage):
        execute()

    assert events == {
        "cookie_calls": ["alice@example.com"],
        "notices": [],
        "stops": [],
        "redirects": ["./pages/landing.py"],
    }
    assert state["authentication_status"] is True
    assert state["username"] == "alice@example.com"
    assert "_pending_remember_cookie" not in state
    assert "_post_login_redirect" not in state


def test_issued_cookie_stops_once_then_redirects_without_reissuing():
    state = {
        "authentication_status": True,
        "username": "alice@example.com",
        "_pending_remember_cookie": "alice@example.com",
        "_post_login_redirect": True,
    }
    execute, events = _authenticated_branch(state, cookie_result=True)

    with pytest.raises(_Stopped):
        execute()

    assert events == {
        "cookie_calls": ["alice@example.com"],
        "notices": [Login.signing_in],
        "stops": [True],
        "redirects": [],
    }
    assert state["authentication_status"] is True
    assert state["_post_login_redirect"] is True
    assert "_pending_remember_cookie" not in state

    with pytest.raises(_SwitchedPage):
        execute()

    assert events["cookie_calls"] == ["alice@example.com"]
    assert events["notices"] == [Login.signing_in]
    assert events["stops"] == [True]
    assert events["redirects"] == ["./pages/landing.py"]
    assert state["authentication_status"] is True
    assert state["username"] == "alice@example.com"
    assert "_post_login_redirect" not in state


@pytest.mark.parametrize("redirect_flag", ["_post_login_redirect", RESTORED_FLAG])
def test_no_pending_cookie_preserves_ordinary_redirect(redirect_flag):
    state = {
        "authentication_status": True,
        "username": "alice@example.com",
        redirect_flag: True,
    }
    execute, events = _authenticated_branch(state, cookie_result=True)

    with pytest.raises(_SwitchedPage):
        execute()

    assert events == {
        "cookie_calls": [],
        "notices": [],
        "stops": [],
        "redirects": ["./pages/landing.py"],
    }
    assert state["authentication_status"] is True
    assert state["username"] == "alice@example.com"
