import streamlit as st
from google.api_core.exceptions import GoogleAPIError

from utilities import (
    FirestoreWrapper,
    page_layout,
    normalize_username,
    evaluate_confirmation,
)
from text_content import Confirm

page_layout()

# #240/#230: read the query params defensively. ``st.query_params.token`` raises
# AttributeError when the param is absent (raw traceback for the user), so use
# ``.get`` and treat a missing/blank token or user as an invalid link.
token = st.query_params.get("token")
# Normalize (#129 shared helper): this ``user`` is a raw email typed into the
# confirmation email link built from the (already-normalized) stored username,
# but normalize defensively here too so a manually-edited/differently-cased
# link still resolves to the same account.
user = normalize_username(st.query_params.get("user"))

outcome = "invalid"
if token and user:
    db = FirestoreWrapper().connect_user(auth=False)
    user_ref = db.collection("users").document(user)
    try:
        # ``to_dict()`` is ``None`` for a deleted/missing account (#230
        # confirm.py:16); ``evaluate_confirmation`` maps that (and a missing
        # stored token or a mismatch) to the friendly invalid-link message and
        # uses ``hmac.compare_digest`` for the constant-time token compare.
        user_data = user_ref.get().to_dict()
        outcome = evaluate_confirmation(token, user, user_data)
        if outcome == "confirm":
            user_ref.update({"is_confirmed": True})
    except GoogleAPIError as error:
        # A genuine Firestore API error surfaces its message rather than being
        # swallowed by a broad ``except`` (narrow-exception convention).
        st.error(Confirm.failed.format(error=error))
        st.stop()

if outcome == "already_confirmed":
    st.warning(Confirm.already_confirmed)
elif outcome == "confirm":
    st.success(Confirm.success)
else:
    st.error(Confirm.invalid_link)
