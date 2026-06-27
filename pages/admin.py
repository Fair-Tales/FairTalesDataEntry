import io
import zipfile
import csv
import streamlit as st
from utilities import page_layout, check_authentication_status, FirestoreWrapper

check_authentication_status()

if not st.session_state.get('admin', False):
    st.error("This page is only accessible to admin users.")
    st.stop()

page_layout()

st.title("Admin")

st.page_link("pages/validation.py", label="→ Go to data validation")
st.divider()

st.header("User data")
st.write("Download all available fields for confirmed users (excluding sensitive fields such as password and confirmation token) for analysis.")

if st.button("Prepare user data download"):
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
        label="⬇ Download user list (CSV)",
        data=buf.getvalue().encode('utf-8'),
        file_name="fairtales_users.csv",
        mime="text/csv"
    )

st.divider()

st.header("Book database export")
st.write("Download a ZIP of CSV files — one per collection — for research use. May take a moment for large datasets.")

if st.button("Prepare book data download"):
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
        label="⬇ Download book database (ZIP of CSVs)",
        data=zip_buf.getvalue(),
        file_name="fairtales_book_data.zip",
        mime="application/zip"
    )
