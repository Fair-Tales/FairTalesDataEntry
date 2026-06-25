# Brief: Issue #54 — Email validation and account management improvements

**Branch:** `feat/54-email-validation`  
**Model:** Sonnet  
**Read first:** `planning/agent_briefs/00_shared_context.md`

## What to implement

Three targeted improvements. Read each relevant file before changing it.

---

## Fix 1 — Improve email validation in `pages/register_user.py`

Current implementation:
```python
def is_valid_email(email):
    return ('@' in email) and ('.' in email)
```

This accepts obviously invalid strings like `@.` or `a@b`.

Replace with a more robust check using Python's `email.utils` module (standard library, no extra dependency):

```python
import email.utils

def is_valid_email(email):
    """Basic RFC 5322 email validation using the standard library."""
    if not email or len(email) > 254:
        return False
    try:
        parsed = email.utils.parseaddr(email)
        if not parsed[1] or '@' not in parsed[1]:
            return False
        local, domain = parsed[1].rsplit('@', 1)
        return bool(local) and bool(domain) and '.' in domain
    except Exception:
        return False
```

This correctly rejects `@.`, `a@b` (no dot in domain), empty strings, and strings without `@`.

---

## Fix 2 — Whitespace stripping in registration

In `pages/register_user.py`, in the registration form, ensure name and email inputs are stripped of leading/trailing whitespace before validation and saving:

```python
username = st.text_input("Email", value="", key='register_email').lower().strip()
name = st.text_input("Name", value="", key='name_of_user').strip()
```

(email is already `.lower()` — add `.strip()` after it)

---

## Fix 3 — Password reset stub

This is a stub implementation that captures the email and tells the user to contact support, pending a full implementation. The full flow (token generation, email sending) is tracked in the issue but is out of scope for this brief.

In `pages/login.py`, below the login form and above the register option, add a collapsible expander:

```python
with st.expander("Forgot your password?"):
    reset_email = st.text_input("Enter your email address", key='reset_email')
    if st.button("Request password reset"):
        if reset_email.strip():
            # TODO: implement full token-based reset flow
            st.info(
                "Password reset is not yet automated. Please email "
                "dataentry.kidsbooks@gmail.com with your username and we will "
                "reset your password manually."
            )
        else:
            st.warning("Please enter your email address.")
```

This gives users a path forward without requiring the full token/email infrastructure to be built now.

---

## Verification

```
.venv/bin/python -m py_compile pages/register_user.py pages/login.py
```
