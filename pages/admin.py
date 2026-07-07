import io
import zipfile
import csv
import logging
import streamlit as st
from google.api_core.exceptions import GoogleAPIError
from botocore.exceptions import BotoCoreError, ClientError
from utilities import (
    page_layout,
    check_authentication_status,
    FirestoreWrapper,
    get_s3_filesystem,
    is_admin,
    is_team_or_above,
    resolve_role,
    VALID_ROLES,
    ROLE_ADMIN,
    load_book_dict,
    load_character_dict,
)
from data_structures import Book
from text_content import FeedbackExport, Admin, AdminSettings

logger = logging.getLogger(__name__)

check_authentication_status()

# Admin-only page (#83): user/book deletion, data export/download, role
# management (#47) and other privileged management actions live here.
if not is_admin():
    st.error(Admin.not_admin)
    st.stop()

page_layout()

st.title(Admin.title)

st.page_link("pages/validation.py", label=Admin.validation_link_label)
st.page_link("pages/ai_settings.py", label=AdminSettings.admin_link_label)
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

# ---------------------------------------------------------------------------
# Delete a book (#188). Admin-gated (this whole page requires is_admin) with an
# explicit confirmation step so it can never fire by accident. Deletes the book
# and its OWNED sub-records — its pages, characters and aliases — but NOT the
# shared author/illustrator/publisher, which may belong to other books.


def _remove_book_s3_folder(book):
    """Best-effort removal of a book's S3 image folder (#188).

    Reuses the shared ``get_s3_filesystem`` (#129). Guarded: a missing folder or
    a transient S3 error must never leave the Firestore delete half-done —
    surface a warning and carry on. Returns True only when a folder was removed.
    """
    folder = book.photos_url or f"sawimages/{book.title}"
    fs = get_s3_filesystem()
    try:
        if fs.exists(folder):
            fs.rm(folder, recursive=True)
            return True
    except (FileNotFoundError, OSError, BotoCoreError, ClientError) as exc:
        logger.warning("Delete book: S3 folder removal failed for %s: %s", folder, exc)
        st.warning(Admin.delete_book_s3_warning.format(folder=folder, error=exc))
    return False


def _delete_book_and_children(book):
    """Delete ``book`` and its owned pages/characters/aliases (#188).

    Collects every owned sub-record by its ``book`` reference (plus aliases by
    their ``character`` reference, belt-and-suspenders for legacy aliases with no
    ``book`` field), deletes them in chunked WriteBatches, then the book document
    itself, and finally its S3 image folder. Shared author/illustrator/publisher
    references are never touched.
    """
    firestore = st.session_state.firestore
    book_ref = book.get_ref()

    char_snaps = list(firestore.query_stream('characters', 'book', '==', book_ref))
    char_refs = [snap.reference for snap in char_snaps]
    # Names captured up front (rather than re-querying after delete) so the
    # local ``character_dict`` cache can be pruned below without an extra read.
    char_names = [(snap.to_dict() or {}).get('name') for snap in char_snaps]
    alias_paths = set()
    alias_refs = []
    for snap in firestore.query_stream('aliases', 'book', '==', book_ref):
        if snap.reference.path not in alias_paths:
            alias_paths.add(snap.reference.path)
            alias_refs.append(snap.reference)
    for cref in char_refs:
        for snap in firestore.query_stream('aliases', 'character', '==', cref):
            if snap.reference.path not in alias_paths:
                alias_paths.add(snap.reference.path)
                alias_refs.append(snap.reference)
    page_refs = [
        snap.reference
        for snap in firestore.query_stream('pages', 'book', '==', book_ref)
    ]

    try:
        n_aliases = firestore.batch_delete_references(alias_refs)
        n_chars = firestore.batch_delete_references(char_refs)
        n_pages = firestore.batch_delete_references(page_refs)
        firestore.delete_document(collection='books', doc_id=book.document_id)
    except GoogleAPIError as exc:
        logger.warning("Delete book %s failed: %s", book.document_id, exc)
        st.error(Admin.delete_book_error.format(error=exc))
        return

    _remove_book_s3_folder(book)

    # Invalidate the shared book/character lookup caches (mirrors the
    # register/confirm write-through convention: any collection mutation must
    # clear its ``load_*_dict`` cache) and prune this session's local copies so
    # the deleted book and its characters immediately stop appearing in
    # search (pages/user_home.py book_search/author_search) rather than
    # lingering for up to the cache TTL. Author/illustrator/publisher are
    # intentionally left untouched — they are shared and not deleted here.
    load_book_dict.clear()
    load_character_dict.clear()
    st.session_state.get('book_dict', {}).pop(book.title, None)
    character_dict = st.session_state.get('character_dict')
    if character_dict:
        for _name in char_names:
            character_dict.pop(_name, None)

    # Clear the widget state so the freshly deleted book cannot be re-selected on
    # the rerun, and surface a success summary.
    st.session_state.pop('admin_delete_book_confirm', None)
    st.session_state.pop('admin_delete_book_select', None)
    st.success(
        Admin.delete_book_success.format(
            title=book.title, pages=n_pages, characters=n_chars, aliases=n_aliases
        )
    )
    st.rerun()


st.header(Admin.delete_book_header)
st.write(Admin.delete_book_description)

try:
    _book_docs = list(
        st.session_state.firestore.get_all_documents_stream(collection='books')
    )
except GoogleAPIError as e:
    st.error(Admin.delete_book_load_error.format(error=e))
    _book_docs = None

if _book_docs is not None:
    if not _book_docs:
        st.info(Admin.delete_book_empty)
    else:
        # Map a human label -> the book's stored dict, sorted by title.
        _book_options = {}
        for _doc in sorted(
            _book_docs, key=lambda d: (d.to_dict().get('title') or d.id).lower()
        ):
            _data = _doc.to_dict()
            _title = _data.get('title') or _doc.id
            _book_options[f"{_title}  ({_doc.id})"] = _data

        _placeholder = Admin.delete_book_select_placeholder
        _choice = st.selectbox(
            Admin.delete_book_select_label,
            options=[_placeholder] + list(_book_options.keys()),
            index=0,
            key="admin_delete_book_select",
        )

        if _choice != _placeholder:
            _book = Book(db_object=_book_options[_choice])
            # Confirmation step: the admin must tick a box NAMING the book before
            # the (primary, red) delete button becomes active — a single stray
            # click can never delete a book.
            _confirmed = st.checkbox(
                Admin.delete_book_confirm_label.format(title=_book.title),
                key="admin_delete_book_confirm",
            )
            if st.button(
                Admin.delete_book_button,
                type="primary",
                disabled=not _confirmed,
                key="admin_delete_book_button",
            ):
                _delete_book_and_children(_book)

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
