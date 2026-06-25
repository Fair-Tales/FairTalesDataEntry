# Brief: Issue #68 — Newsletter opt-in during registration

**Branch:** `feat/68-newsletter-optin`  
**Model:** Sonnet  
**Read first:** `planning/agent_briefs/00_shared_context.md`

## What to implement

Add an optional newsletter opt-in checkbox to the user registration form. The preference is stored in Firestore on the user record.

## Changes required

### `pages/register_user.py`

Read this file in full first.

1. Inside `with st.form('registration_form'):`, after the `user_birth_year` selectbox and before `registered = st.form_submit_button("Register")`, add:

```python
newsletter_opt_in = st.checkbox(
    "Keep me updated with research findings and project news from Fair Tales "
    "(max. one email per month). You can opt out at any time.",
    value=False
)
```

The checkbox must default to **unchecked** (`value=False`) — pre-checked opt-ins are not GDPR-compliant.

2. In the `if registered:` block, pass `newsletter_opt_in` to `validate_user_details()`:

```python
if registered:
    if validate_user_details(username, name, password, gender, gender_custom, user_birth_year):
        register_user(username, name, password, gender, user_birth_year, newsletter_opt_in)
```

3. Update `register_user()` to accept and store the new parameter:

```python
def register_user(_username, _name, _password, _gender, _birth_year, _newsletter_opt_in=False):
```

Add `"newsletter_opt_in": _newsletter_opt_in` to the `user_data` dict.

## What NOT to change

- Do not change the login flow in `pages/login.py`
- Do not add `newsletter_opt_in` to session state — it is stored in Firestore only
- Do not modify `validate_user_details()` — no extra validation needed for a boolean

## Verification

```
.venv/bin/python -m py_compile pages/register_user.py
```
