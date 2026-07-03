import io
import zipfile
import csv
import streamlit as st
from google.api_core.exceptions import GoogleAPIError
from utilities import (
    page_layout,
    check_authentication_status,
    FirestoreWrapper,
    is_admin,
    is_team_or_above,
    resolve_role,
    VALID_ROLES,
    ROLE_ADMIN,
)
from text_content import FeedbackExport, Admin

check_authentication_status()

# Admin-only page (#83): user/book deletion, data export/download, role
# management (#47) and other privileged management actions live here.
if not is_admin():
    st.error(Admin.not_admin)
    st.stop()

page_layout()

st.title(Admin.title)

st.page_link("pages/validation.py", label=Admin.validation_link_label)
st.divider()

# ---------------------------------------------------------------------------
# Manage user roles (#47 / #83). Admins can grant or revoke the three-tier
# roles in-app instead of editing the Firestore user document by hand. The
# ``User`` entity is the documented raw-dict exception (#90), so the role is
# written directly to the document rather than via DataStructureBase.
st.header(Admin.manage_roles_header)
st.write(Admin.manage_roles_description)

# A successful save reruns to re-read the list; carry the success message
# across that rerun via session state so the feedback is not lost.
role_flash = st.session_state.pop('admin_role_flash', None)
if role_flash:
    st.success(role_flash)

db = FirestoreWrapper().connect_user(auth=False)
try:
    user_docs = list(db.collection('users').stream())
except GoogleAPIError as e:
    st.error(Admin.roles_load_error.format(error=e))
    user_docs = None

if user_docs is not None:
    if not user_docs:
        st.info(Admin.roles_empty_message)
    else:
        current_username = st.session_state.get('username', '')
        # The user document id is the username (see username_to_doc_ref).
        for doc in sorted(user_docs, key=lambda d: d.id.lower()):
            username = doc.id
            current_role = resolve_role(doc.to_dict())

            # Guard the index lookup: resolve_role always returns a valid role,
            # but never assume a stored value is still a valid option (#91).
            default_index = (
                VALID_ROLES.index(current_role)
                if current_role in VALID_ROLES
                else 0
            )

            col_name, col_select, col_save = st.columns([3, 2, 1])
            with col_name:
                st.write(f"**{username}**")
                st.caption(
                    Admin.role_current_caption.format(
                        role=Admin.role_labels.get(current_role, current_role)
                    )
                )
            with col_select:
                new_role = st.selectbox(
                    Admin.role_select_label,
                    options=VALID_ROLES,
                    index=default_index,
                    format_func=lambda r: Admin.role_labels.get(r, r),
                    key=f"admin_role_select_{username}",
                    label_visibility="collapsed",
                )
            with col_save:
                save = st.button(
                    Admin.role_save_button,
                    key=f"admin_role_save_{username}",
                )

            if save:
                # Self-lockout safeguard: an admin must not demote their own
                # account out of the admin role, or they lose this page.
                if (
                    username == current_username
                    and current_role == ROLE_ADMIN
                    and new_role != ROLE_ADMIN
                ):
                    st.warning(Admin.role_self_demote_blocked)
                elif new_role == current_role:
                    # No change — re-affirm so the admin gets clear feedback.
                    st.info(
                        Admin.role_updated_success.format(
                            username=username,
                            role=Admin.role_labels.get(new_role, new_role),
                        )
                    )
                else:
                    try:
                        # Keep the legacy ``admin`` boolean (#90 raw-dict)
                        # truthful so existing admin-flag reads stay correct:
                        # set it when promoting to admin, clear it otherwise.
                        db.collection('users').document(username).update(
                            {'role': new_role, 'admin': new_role == ROLE_ADMIN}
                        )
                    except GoogleAPIError as e:
                        st.error(
                            Admin.role_update_error.format(
                                username=username, error=e
                            )
                        )
                    else:
                        # Stash feedback, then rerun to re-read the list so it
                        # reflects the new role (the message survives via the
                        # session-state flash popped at the top of the section).
                        st.session_state['admin_role_flash'] = (
                            Admin.role_updated_success.format(
                                username=username,
                                role=Admin.role_labels.get(new_role, new_role),
                            )
                        )
                        st.rerun()

st.divider()

st.header(Admin.user_data_header)
st.write(Admin.user_data_description)

if st.button(Admin.prepare_user_download_button, key="admin_prepare_user_download_button"):
    db = FirestoreWrapper().connect_user(auth=False)
    users = db.collection('users').where('is_confirmed', '==', True).stream()

    # Export every available field for analysis, except sensitive ones.
    sensitive_fields = {'password', 'confirmation_token'}
    rows = []
    all_fields = set()
    for user in users:
        d = {k: v for k, v in user.to_dict().items() if k not in sensitive_fields}
        rows.append(d)
        all_fields.update(d.keys())

    buf = io.StringIO()
    fieldnames = sorted(all_fields)
    writer = csv.DictWriter(buf, fieldnames=fieldnames, restval='', extrasaction='ignore')
    writer.writeheader()
    for d in rows:
        writer.writerow({
            k: (v.id if hasattr(v, 'id') else ('' if v is None else str(v)))
            for k, v in d.items()
        })

    st.download_button(
        label=Admin.download_user_button,
        data=buf.getvalue().encode('utf-8'),
        file_name=Admin.user_file_name,
        mime="text/csv",
        key="admin_download_user_button"
    )

st.divider()

st.header(Admin.book_export_header)
st.write(Admin.book_export_description)

if st.button(Admin.prepare_book_download_button, key="admin_prepare_book_download_button"):
    collections = ['books', 'authors', 'illustrators', 'publishers', 'characters', 'pages', 'aliases']

    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        for collection_name in collections:
            try:
                docs = st.session_state.firestore.get_all_documents_stream(collection=collection_name)
                rows = []
                for doc in docs:
                    d = doc.to_dict()
                    clean = {}
                    for k, v in d.items():
                        try:
                            clean[k] = v.id if hasattr(v, 'id') else str(v) if v is not None else ''
                        except Exception:
                            clean[k] = str(v) if v is not None else ''
                    rows.append(clean)

                if rows:
                    csv_buf = io.StringIO()
                    fieldnames = list(rows[0].keys())
                    writer = csv.DictWriter(csv_buf, fieldnames=fieldnames, extrasaction='ignore')
                    writer.writeheader()
                    writer.writerows(rows)
                    zf.writestr(f"{collection_name}.csv", csv_buf.getvalue())
            except Exception as e:
                zf.writestr(f"{collection_name}_error.txt", str(e))

    st.download_button(
        label=Admin.download_book_button,
        data=zip_buf.getvalue(),
        file_name=Admin.book_file_name,
        mime="application/zip",
        key="admin_download_book_button"
    )

st.divider()

st.header(FeedbackExport.header)
st.write(FeedbackExport.description)

if st.button(FeedbackExport.prepare_button, key="admin_prepare_feedback_button"):
    db = FirestoreWrapper().connect_book()
    try:
        feedback_docs = list(db.collection("feedback").stream())
    except GoogleAPIError as e:
        st.error(FeedbackExport.error_message.format(error=e))
        feedback_docs = None

    if feedback_docs is not None:
        if not feedback_docs:
            st.info(FeedbackExport.empty_message)
        else:
            rows = []
            for doc in feedback_docs:
                d = doc.to_dict()

                # Resolve the stored user DocumentReference to a username and,
                # where available, an email address. The reference id is the
                # username (see report_feedback.py / username_to_doc_ref).
                user_ref = d.get("user")
                username = ""
                email = ""
                if user_ref is not None and hasattr(user_ref, "id"):
                    username = user_ref.id
                    try:
                        user_doc = user_ref.get()
                        if user_doc.exists:
                            email = user_doc.to_dict().get("email", "")
                    except GoogleAPIError:
                        # Keep the username we already have; leave email blank
                        # rather than failing the whole export for one lookup.
                        email = ""

                timestamp = d.get("timestamp")
                rows.append({
                    "timestamp": timestamp.isoformat() if hasattr(timestamp, "isoformat") else ("" if timestamp is None else str(timestamp)),
                    "type": d.get("type", ""),
                    "username": username,
                    "email": email,
                    "text": d.get("text", ""),
                })

            buf = io.StringIO()
            fieldnames = ["timestamp", "type", "username", "email", "text"]
            writer = csv.DictWriter(buf, fieldnames=fieldnames, restval="", extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)

            st.download_button(
                label=FeedbackExport.download_button,
                data=buf.getvalue().encode("utf-8"),
                file_name=FeedbackExport.file_name,
                mime="text/csv",
                key="admin_download_feedback_button"
            )

# ---------------------------------------------------------------------------
# Reconstruct orphaned books (#141). Moved off the sidebar to the bottom of the
# Admin page. The link is shown to team members and admins; the target page keeps
# its own is_team_or_above gating. (This page already requires admin to render,
# so the guard is always true here, but it documents the intended access tier and
# stays correct if the page's gating is ever relaxed.)
if is_team_or_above():
    st.divider()
    st.header(Admin.reconstruct_section_header)
    st.write(Admin.reconstruct_section_description)
    st.page_link(
        "pages/reconstruct_orphans.py",
        label=Admin.reconstruct_link_label,
    )
