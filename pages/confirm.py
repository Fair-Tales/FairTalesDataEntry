import streamlit as st
from utilities import FirestoreWrapper, page_layout, normalize_username
from text_content import Confirm

page_layout()

token = st.query_params.token
# Normalize (#129 shared helper): this ``user`` is a raw email typed into the
# confirmation email link built from the (already-normalized) stored username,
# but normalize defensively here too so a manually-edited/differently-cased
# link still resolves to the same account.
user = normalize_username(st.query_params.user)

db = FirestoreWrapper().connect_user(auth=False)
user_ref = db.collection("users").document(user)
user_data = user_ref.get().to_dict()

if user_data['is_confirmed']:
    st.warning(Confirm.already_confirmed)
else:
    try:
        if token == user_data['confirmation_token']:
            user_ref.update({
                'is_confirmed': True
            })
            st.success(
                Confirm.success
            )
        else:
            st.error(Confirm.invalid_link)
    except Exception:
        st.error(Confirm.failed)
