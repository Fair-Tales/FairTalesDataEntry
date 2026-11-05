"""Direct browser-to-S3 photo upload via presigned PUT URLs (#114).

Streamlit's ``st.file_uploader`` is unreliable on mobile: the native full-screen
photo picker causes the mobile browser to drop the Streamlit websocket, so the
selection is lost on reconnect (streamlit/streamlit#7230). This module lets the
phone upload each photo **straight to S3** using presigned PUT URLs, bypassing
the Streamlit websocket *and* the 1GB server entirely. The win is threefold:
mobile reliability, bounded server memory, and full-resolution archival (no
client-side resize).

Flow (single-book "Add from Photos", Phase 1):
  1. The app mints a stable per-session prefix ``uploads/{session_id}/`` and a
     batch of presigned PUT URLs for ``page_1.jpg`` ... ``page_{MAX}.jpg``.
  2. ``build_uploader_html`` renders a file input + vanilla-JS uploader inside an
     ``st.components.v1.html`` iframe. On selection the JS PUTs each file to its
     presigned URL in selection order via ``XMLHttpRequest`` (per-file progress).
  3. A normal Streamlit "Read the book" button triggers a rerun; the page then
     **lists the S3 prefix** to discover what landed (no bidirectional
     component needed) and feeds the bytes into the existing extraction pipeline.
  4. After the book is registered the temp prefix is cleaned up.

PREREQUISITE (AWS): the S3 bucket needs a CORS policy allowing PUT/GET from the
app origin, or the browser PUT is blocked. See the issue/PR summary for the JSON.
"""

import json
import logging
import re
import time
from io import BytesIO

import boto3
import natsort
import qrcode
import streamlit as st
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from google.api_core.exceptions import GoogleAPIError
from requests.models import PreparedRequest

from text_content import BookPhotoEntry, PhotoUpload, Instructions

logger = logging.getLogger(__name__)

# First path segment used everywhere else in the codebase for s3fs writes
# (e.g. ``sawimages/{title}/page_N.jpg``) is the bucket name.
S3_BUCKET = "sawimages"

# Root prefix for the temporary, per-session upload buffer. Cleaned up once the
# book is registered and its photos have been processed into ``sawimages/``.
UPLOAD_PREFIX_ROOT = "uploads"

# Upper bound on presigned URLs minted per session (one per potential page).
MAX_UPLOAD_PAGES = 60

# Presigned URL lifetime (seconds). One hour comfortably covers a slow mobile
# upload of a full picture book.
PRESIGN_EXPIRY_SECONDS = 3600

# Client-side per-image size cap (50 MB; Chris-approved, #126). The presigned PUT
# URLs sign only Bucket+Key — ``Content-Length`` is unsigned — so the uploader JS
# enforces this bound *before* PUTting each file to catch accidental huge uploads
# (cost/DoS) without changing normal photo uploads. This is a client-side guard
# only; a true server-side hard cap needs presigned POST + a ``content-length-range``
# policy condition (see DECISIONS-007 follow-up). The matching S3 lifecycle rule
# (``scripts/set_uploads_lifecycle.py``) expires abandoned ``uploads/`` objects.
MAX_UPLOAD_BYTES = 50 * 1024 * 1024

# Explicit upload-completion manifest (#199). The uploader JS PUTs a
# ``manifest.json`` into the same temp prefix whenever a selection batch has no
# PUTs left in flight, listing exactly the page files uploaded so far. The app
# then treats the batch as READY only when the manifest exists and its file
# list matches the keys actually present — replacing the old INFERRED
# completion ("key count stable across two polls"), whose premise was false:
# the JS PUTs all files CONCURRENTLY, so a stalled connection made a partial
# batch look settled and an early read renumbered the pages positionally,
# permanently corrupting page order.
MANIFEST_FILENAME = "manifest.json"

#: Firestore ``users`` doc field recording each flow's active upload-session id
#: (#199): ``{flow_key: session_id}``. ``st.session_state`` alone loses the id
#: on a websocket drop / hard reload / re-login, after which the page watches a
#: fresh EMPTY prefix while the photos sit (or continue PUTting for up to the
#: 1h presign expiry) in the old one — "my photos never register".
USER_UPLOAD_SESSIONS_FIELD = "active_upload_sessions"


def _s3_client():
    """Build a boto3 S3 client from the Streamlit AWS secrets.

    The presigned PUT URL must point at the bucket's *regional* endpoint and be
    SigV4-signed: ``eu-north-1`` (and other post-2019 regions) only support SigV4
    and reject the legacy global ``bucket.s3.amazonaws.com`` host. We therefore
    read ``AWS_DEFAULT_REGION``, pin ``s3v4``, force virtual-hosted addressing,
    and set an explicit regional ``endpoint_url`` so the signed host becomes
    ``{bucket}.s3.{region}.amazonaws.com`` (boto3 otherwise emits the global host
    here, which a browser PUT cannot follow when it 400s on a region mismatch).
    """
    region = st.secrets.get("AWS_DEFAULT_REGION")
    endpoint_url = f"https://s3.{region}.amazonaws.com" if region else None
    return boto3.client(
        "s3",
        region_name=region,
        endpoint_url=endpoint_url,
        aws_access_key_id=st.secrets["AWS_ACCESS_KEY_ID"],
        aws_secret_access_key=st.secrets["AWS_SECRET_ACCESS_KEY"],
        config=Config(signature_version="s3v4", s3={"addressing_style": "virtual"}),
    )


def _session_state_key(flow_key):
    """Session-state key holding the active upload-session id for ``flow_key``."""
    return f"upload_session_{flow_key}"


def _counter_key(flow_key):
    """Session-state key holding the per-flow upload counter."""
    return f"upload_counter_{flow_key}"


def _recorded_session_id(flow_key):
    """The upload-session id last recorded on the user's Firestore doc for
    ``flow_key`` (#199), or ``None``. Best-effort: any failure (no session
    firestore, missing user doc, transient API error) is logged and treated as
    "nothing recorded" so session recovery can never break the upload page.
    """
    username = st.session_state.get("username")
    if not username or "firestore" not in st.session_state:
        return None
    try:
        doc = st.session_state["firestore"].username_to_doc_ref(username).get()
        data = doc.to_dict() or {}
    except GoogleAPIError as exc:
        logger.warning("Could not read recorded upload session (%s): %s", flow_key, exc)
        return None
    sessions = data.get(USER_UPLOAD_SESSIONS_FIELD)
    if not isinstance(sessions, dict):
        return None
    session_id = sessions.get(flow_key)
    return session_id if isinstance(session_id, str) and session_id else None


def _record_session_id(flow_key, session_id):
    """Record (or with ``None`` clear) the active upload-session id for
    ``flow_key`` on the user's Firestore doc (#199). Best-effort, mirroring
    :func:`_recorded_session_id` — a failed write only costs recoverability.
    """
    username = st.session_state.get("username")
    if not username or "firestore" not in st.session_state:
        return
    firestore_wrapper = st.session_state["firestore"]
    try:
        doc_id = firestore_wrapper.username_to_doc_ref(username).id
        firestore_wrapper.update_field(
            collection="users",
            document=doc_id,
            field=f"{USER_UPLOAD_SESSIONS_FIELD}.{flow_key}",
            value=session_id,
        )
    except GoogleAPIError as exc:
        logger.warning("Could not record upload session (%s): %s", flow_key, exc)


def get_upload_session_id(flow_key):
    """Return a stable per-upload-session id for ``flow_key``, creating one on
    first use.

    ``flow_key`` namespaces independent upload surfaces (e.g. ``"single"``,
    ``"pages"``, ``"batch"``, ``"collection"``) so concurrent flows in one
    browser session never collide on the same temp prefix (#118). Stored in
    ``st.session_state`` so it survives Streamlit reruns — a reload therefore
    reuses the same ``uploads/{flow_key}/{session_id}/`` prefix instead of
    orphaning a fresh one. The id is ``<safe-username>_<counter>_<timestamp>``;
    the counter (bumped by :func:`reset_upload_session`) keeps consecutive
    uploads by the same user in one browser session distinct, and the timestamp
    avoids collisions across separate logins of the same user.

    DURABLE RECOVERY (#199): ``st.session_state`` does NOT survive a websocket
    drop / hard reload / re-login, and the old behaviour then minted a fresh id
    — so the page watched a new, EMPTY prefix while the user's photos sat in
    (or were still PUTting into) the old one, i.e. uploads "never registered".
    The active id is therefore also recorded on the user's Firestore doc when
    minted; a fresh session first tries to RESUME that recorded id. The
    "start a new photo entry" choke points still get a genuinely fresh prefix
    because :func:`reset_upload_session` clears the durable record too.
    """
    ss_key = _session_state_key(flow_key)
    if ss_key not in st.session_state:
        recovered = _recorded_session_id(flow_key)
        if recovered:
            st.session_state[ss_key] = recovered
        else:
            username = st.session_state.get("username") or "anon"
            safe = re.sub(r"[^A-Za-z0-9_-]", "_", username) or "anon"
            counter_key = _counter_key(flow_key)
            counter = st.session_state.get(counter_key, 0) + 1
            st.session_state[counter_key] = counter
            st.session_state[ss_key] = f"{safe}_{counter}_{int(time.time())}"
            _record_session_id(flow_key, st.session_state[ss_key])
    return st.session_state[ss_key]


def reset_upload_session(flow_key):
    """Drop the current upload-session id for ``flow_key`` so the next session
    mints a fresh prefix. Call at each "start a new photo entry" choke point.
    Also clears the durable record (#199) so the abandoned/finished prefix can
    never be resurrected by session recovery.
    """
    st.session_state.pop(_session_state_key(flow_key), None)
    _record_session_id(flow_key, None)


def upload_prefix(flow_key, session_id):
    """Return the temp S3 prefix (no bucket) for a flow/session, e.g.
    ``uploads/<flow_key>/<session_id>``.
    """
    return f"{UPLOAD_PREFIX_ROOT}/{flow_key}/{session_id}"


def generate_put_urls(flow_key, session_id, count=MAX_UPLOAD_PAGES, *, start_index=1):
    """Return ``count`` presigned PUT URLs for
    ``uploads/{flow_key}/{session_id}/page_{i}.jpg``.

    ``Content-Type`` is deliberately *not* signed, so the browser may PUT any
    image bytes (jpeg/png/heic) without having to match a signed header.
    """
    client = _s3_client()
    urls = []
    for i in range(start_index, start_index + count):
        key = f"{upload_prefix(flow_key, session_id)}/page_{i}.jpg"
        urls.append(
            client.generate_presigned_url(
                "put_object",
                Params={"Bucket": S3_BUCKET, "Key": key},
                ExpiresIn=PRESIGN_EXPIRY_SECONDS,
            )
        )
    return urls


def generate_manifest_put_url(flow_key, session_id):
    """Return a presigned PUT URL for the flow/session's ``manifest.json``
    (#199), which the uploader JS writes whenever a selection batch has no PUTs
    left in flight. Same signing rules as :func:`generate_put_urls`.
    """
    client = _s3_client()
    key = f"{upload_prefix(flow_key, session_id)}/{MANIFEST_FILENAME}"
    return client.generate_presigned_url(
        "put_object",
        Params={"Bucket": S3_BUCKET, "Key": key},
        ExpiresIn=PRESIGN_EXPIRY_SECONDS,
    )


def read_upload_manifest(fs, flow_key, session_id):
    """Return the flow/session's upload manifest dict (#199), or ``None`` when
    absent or unreadable. Absence means the uploader JS has not (yet) finished
    a selection batch — or the upload predates the manifest mechanism — so
    callers must treat ``None`` as "completion unknown", not as ready.
    """
    path = f"{S3_BUCKET}/{upload_prefix(flow_key, session_id)}/{MANIFEST_FILENAME}"
    try:
        with fs.open(path, "rb") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        # ValueError covers json.JSONDecodeError (a truncated/in-flight PUT).
        logger.warning("Unreadable upload manifest at %s: %s", path, exc)
        return None
    return data if isinstance(data, dict) else None


def manifest_matches(manifest, keys):
    """True when ``manifest`` (see :func:`read_upload_manifest`) records exactly
    the uploaded page files present in ``keys`` (full ``bucket/.../page_N.jpg``
    paths from :func:`list_uploaded_keys`) — the #199 readiness test.

    ``None``/malformed manifests never match ("completion unknown"). A manifest
    listing MORE files than present means PUTs are still landing; FEWER means a
    further selection batch is still uploading. Both are "not ready".
    """
    if not isinstance(manifest, dict):
        return False
    uploaded = manifest.get("uploaded")
    if not isinstance(uploaded, list) or not uploaded:
        return False
    if manifest.get("failed", 0) != 0:
        return False
    if "state" in manifest and manifest.get("state") != "complete":
        return False
    if any(not isinstance(name, str) for name in uploaded):
        return False
    if len(uploaded) != len(set(uploaded)) or missing_upload_slots(uploaded):
        return False
    if "count" in manifest and manifest.get("count") != len(uploaded):
        return False
    names = {key.rsplit("/", 1)[-1] for key in keys}
    return names == set(uploaded)


def upload_batch_ready(fs, flow_key, session_id):
    """One-stop readiness check (#199): returns ``(ready, keys, manifest)``.

    ``ready`` is True only when the manifest exists and lists exactly the page
    files present — the explicit completion signal that replaces inferring
    completion from listing samples. ``keys`` is the current (natural-sorted)
    listing so callers need not re-list.
    """
    keys = list_uploaded_keys(fs, flow_key, session_id)
    manifest = read_upload_manifest(fs, flow_key, session_id)
    return manifest_matches(manifest, keys), keys, manifest


def missing_upload_slots(names):
    """The page-slot numbers missing from ``names`` (file names like
    ``page_3.jpg``): e.g. ``["page_1.jpg", "page_2.jpg", "page_5.jpg"]`` →
    ``[3, 4]``. Non-slot names are ignored. Used to tell the user explicitly
    which photos have not (yet) landed instead of silently renumbering a
    hole-y listing (#199).
    """
    slots = set()
    for name in names:
        match = re.fullmatch(r"page_(\d+)\.jpg", name)
        if match:
            slots.add(int(match.group(1)))
    if not slots:
        return []
    return [n for n in range(1, max(slots) + 1) if n not in slots]


def list_uploaded_keys(fs, flow_key, session_id):
    """Return the uploaded photo keys for a flow/session in page order.

    Uses s3fs to list ``{bucket}/uploads/{flow_key}/{session_id}/`` and
    natural-sorts so ``page_2`` precedes ``page_10``. Missing slots (e.g. a
    skipped page) simply do not appear. Returns full ``bucket/key`` paths.
    """
    prefix = f"{S3_BUCKET}/{upload_prefix(flow_key, session_id)}"
    try:
        # refresh=True bypasses fsspec's directory-listing cache: the photos were
        # just PUT by the browser, so a cached (empty) listing from an earlier
        # render of this prefix would otherwise hide them.
        entries = fs.ls(prefix, detail=False, refresh=True)
    except FileNotFoundError:
        return []
    keys = [e for e in entries if e.lower().endswith(".jpg")]
    return natsort.natsorted(keys)


def fetch_uploaded_photos(fs, flow_key, session_id):
    """Download the flow/session's uploaded photos into memory, in page order.

    Returns a list of ``(name, image_bytes)`` tuples matching the shape the
    extraction pipeline (``extract_photo_first_metadata``) and the downstream
    reuse path (``photo_first_pages``) expect.

    Memory note (#114): Phase 1 downloads the batch into memory here. The
    extraction "locate" pass must see every page anyway, and the existing
    downstream pipeline reuses these bytes (orientation-correct, crop, OCR,
    then write to ``sawimages/{title}/``), so a pure S3 server-side copy would
    bypass that processing and regress behaviour. The phone -> S3 transfer
    itself never touches the server, which is the memory win that matters.
    """
    photos = []
    for key in list_uploaded_keys(fs, flow_key, session_id):
        with fs.open(key, "rb") as handle:
            photos.append((key.rsplit("/", 1)[-1], handle.read()))
    return photos


def cleanup_prefix(fs, flow_key, session_id):
    """Delete the temporary ``uploads/{flow_key}/{session_id}/`` prefix from S3.

    Scoped strictly to the ONE ``flow_key``/``session_id`` prefix passed in, so it
    can never touch another user's or another session's upload buffer (#124).

    Safe to call when nothing was uploaded (a missing prefix is ignored). A
    transient S3 / permission failure is logged and swallowed rather than raised:
    cleanup runs on the success path of a flow that has ALREADY relocated the
    photos into ``sawimages/``, so a cleanup error must never break the user's
    completed entry. The #126 ``uploads/`` lifecycle rule (7-day expiry) is the
    backstop for anything a failed cleanup leaves behind.
    """
    prefix = f"{S3_BUCKET}/{upload_prefix(flow_key, session_id)}"
    try:
        if fs.exists(prefix):
            fs.rm(prefix, recursive=True)
    except FileNotFoundError:
        # Nothing was uploaded, or the prefix is already gone — nothing to do.
        pass
    except (OSError, BotoCoreError, ClientError) as exc:
        logger.warning("cleanup_prefix failed for %s: %s", prefix, exc)


# How long to wait between the two prefix samples in :func:`uploads_settled`.
# Long enough to catch a further photo landing (the browser PUTs sequentially),
# short enough not to make the "read" click feel sluggish.
UPLOAD_SETTLE_SECONDS = 2.0


def uploads_settled(fs, flow_key, session_id, settle_seconds=UPLOAD_SETTLE_SECONDS):
    """Check that no more photos are still arriving in the temp prefix.

    The direct-to-S3 uploader iframe is intentionally ONE-WAY: it cannot signal
    "all uploads finished" back to Streamlit. The PRIMARY completion signal is
    now the explicit upload manifest (#199): when one exists, ``settled`` is
    exactly :func:`manifest_matches` — the manifest lists every page file
    present, so nothing is still in flight and nothing extra is expected.

    FALLBACK (no manifest — the upload predates the manifest JS, or its PUT
    failed): the legacy two-sample heuristic, kept so the manual read button
    cannot dead-end. Sample the prefix twice ``settle_seconds`` apart and treat
    a non-growing count as settled. NOTE its known weakness (the reason for the
    manifest): the JS PUTs files CONCURRENTLY, so a stall can make a partial
    batch look settled — callers should treat a fallback "settled" as weaker
    (see the force-proceed affordances on the read buttons).

    Returns ``(settled, keys)``; ``keys`` is the latest listing so the caller
    need not re-list. ``settled`` is ``True`` for an empty prefix (the caller
    distinguishes "nothing uploaded" from "still uploading" via ``keys``).
    """
    keys = list_uploaded_keys(fs, flow_key, session_id)
    manifest = read_upload_manifest(fs, flow_key, session_id)
    if manifest is not None:
        return manifest_matches(manifest, keys), keys
    if not keys:
        return True, keys
    time.sleep(settle_seconds)
    second = list_uploaded_keys(fs, flow_key, session_id)
    return len(second) <= len(keys), second


def _app_base_url():
    """Public base URL (trailing slash) for building the phone/QR deep-link.

    The link must point at the PUBLIC deployment, not the server's internal
    address. Prefer the live request's Origin/Host (correct on any deployment and
    robust to a mis-set ``app_url`` secret — e.g. a leftover ``localhost`` value
    copied into production); fall back to the configured ``st.secrets.app_url``.
    """
    try:
        headers = getattr(st.context, "headers", None) or {}
        origin = headers.get("Origin") or headers.get("origin")
        if origin:
            return origin.rstrip("/") + "/"
        host = (
            headers.get("X-Forwarded-Host")
            or headers.get("Host")
            or headers.get("host")
        )
        if host:
            proto = headers.get("X-Forwarded-Proto") or "https"
            return f"{proto}://{host.rstrip('/')}/"
    except Exception:  # noqa: BLE001 - best-effort; degrade to the configured app_url
        pass
    return str(st.secrets.get("app_url", "") or "")


def build_phone_upload_url(flow_key, session_id):
    """Build the ``qr_landing`` deep-link that points a phone at THIS surface's
    temp prefix ``uploads/{flow_key}/{session_id}/`` (#143).

    Carries the ``flow``/``session`` so the phone's uploads land in the SAME
    prefix the computer surface reads, plus the ``user`` + confirmation ``token``
    for auth (mirroring ``page_photo_upload``'s QR). No ``book`` param: the phone
    only PUTs the photos; the computer surface does the processing when the user
    returns and taps its read button.
    """
    url = f"{_app_base_url()}qr_landing"
    user = st.session_state.username
    token = (
        st.session_state.firestore.username_to_doc_ref(user)
        .get()
        .to_dict()["confirmation_token"]
    )
    params = {
        "user": user,
        "token": token,
        "flow": flow_key,
        "session": session_id,
    }
    req = PreparedRequest()
    req.prepare_url(url, params)
    return req.url


def render_go_to_phone(flow_key, session_id):
    """Render the "scan a QR and upload from your phone" option for a surface.

    Shared by every direct-to-S3 upload surface (single / batch / collection, #143)
    so a user on a computer can photograph the book on their phone and have those
    photos land in the exact prefix this surface reads. All strings come from
    :class:`PhotoUpload`.
    """
    st.write(PhotoUpload.qr_instruction)
    target_url = build_phone_upload_url(flow_key, session_id)
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=10,
        border=4,
    )
    qr.add_data(target_url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    temp = BytesIO()
    img.save(temp)
    # Fixed, phone-scannable size — width="stretch" blew the QR up so large it
    # ran off small/zoomed screens and couldn't be scanned (hotfix).
    st.image(temp.getvalue(), width=260)
    st.write(PhotoUpload.link_line % (target_url, target_url))
    st.write(PhotoUpload.qr_return_instruction)


def render_photo_instructions(container=None, expanded=True):
    """Render the canonical "How to photograph a book" guidance (#186).

    Shared across EVERY upload surface (desktop upload pages, add_book_photos, and
    the phone ``qr_landing`` page) so the same photos-first framing/lighting/order
    advice is always shown — previously the QR flow surfaced none of it. Rendered
    inside a ``st.expander`` (open by default) so it is prominent but collapsible.
    ``container`` may be any Streamlit container (defaults to the page).
    """
    target = container if container is not None else st
    with target.expander(
        Instructions.photo_instructions_expander_title, expanded=expanded
    ):
        st.write(Instructions.photo_instructions_canonical)


def render_uploaded_photos_list(fs, flow_key, session_id, container=None):
    """Render a live "uploaded so far" list for a flow/session prefix (#186).

    Lists the photos already in the temp prefix (via :func:`list_uploaded_keys`)
    with a count and page numbering ("These will become pages 1-N, in this
    order") so a user can verify order and completeness before processing, and
    warns when a file name appears more than once (the duplicate guard). Returns
    the list of keys so the caller can avoid re-listing.
    """
    target = container if container is not None else st
    keys = list_uploaded_keys(fs, flow_key, session_id)
    if not keys:
        return keys
    names = [k.rsplit("/", 1)[-1] for k in keys]
    target.caption(BookPhotoEntry.uploaded_so_far_header.format(count=len(names)))
    target.write(BookPhotoEntry.uploaded_page_range.format(n=len(names)))
    # Duplicate guard: warn if any file name shows up more than once so the user
    # can clear and start again rather than silently uploading a page twice.
    seen = set()
    duplicates = sorted({n for n in names if n in seen or seen.add(n)})
    if duplicates:
        target.warning(
            BookPhotoEntry.uploaded_duplicates_warning.format(
                names=", ".join(duplicates)
            )
        )
    # Gap guard (#199): name the page slots that have not landed (still
    # uploading, or their PUT failed) instead of silently renumbering around
    # the holes — a hole-y listing shown renumbered 1..N is what read as
    # "wrong page order" mid-upload.
    gaps = missing_upload_slots(names)
    if gaps:
        target.warning(
            BookPhotoEntry.uploaded_gaps_warning.format(
                slots=", ".join(str(n) for n in gaps)
            )
        )
    target.write(
        "\n".join(f"{i}. {name}" for i, name in enumerate(names, start=1))
    )
    return keys


# The component markup/JS lives here as a template; only the *strings* shown to
# the user come from text_content (per the project convention). Placeholders are
# substituted via str.replace so the literal CSS/JS braces need no escaping.
_UPLOADER_TEMPLATE = """
<div id="ftu">
  <style>
    #ftu { font-family: "Source Sans Pro", sans-serif; color: inherit; }
    #ftu .ftu-select {
      display: inline-block; padding: 0.6rem 1.1rem; border-radius: 0.5rem;
      background: #2f6fed; color: #fff; font-weight: 600; cursor: pointer;
      user-select: none;
    }
    #ftu .ftu-select:hover { background: #2257c4; }
    #ftu .ftu-hint { font-size: 0.85rem; opacity: 0.75; margin: 0.5rem 0 0.75rem; }
    #ftu .ftu-row {
      display: flex; align-items: center; gap: 0.6rem; padding: 0.35rem 0;
      border-bottom: 1px solid rgba(128,128,128,0.2);
    }
    #ftu .ftu-name { flex: 1 1 auto; overflow: hidden; text-overflow: ellipsis;
      white-space: nowrap; font-size: 0.9rem; }
    #ftu progress { width: 38%; height: 0.9rem; }
    #ftu .ftu-status { width: 1.4rem; text-align: center; font-weight: 700; }
    #ftu .ftu-status.ok { color: #1f9d55; }
    #ftu .ftu-status.err { color: #d1453b; }
    /* Bound the per-photo list so a long book scrolls internally (st.iframe has no
       `scrolling` param like the old components.html). */
    #ftu #ftu-list { max-height: 300px; overflow-y: auto; }
    #ftu #ftu-summary { margin-top: 0.75rem; font-weight: 600; }
  </style>

  <label class="ftu-select" for="ftu-input">__SELECT_LABEL__</label>
  <div class="ftu-hint">__HINT__</div>
  <input id="ftu-input" type="file" accept="image/*" multiple style="display:none">
  <div id="ftu-list"></div>
  <div id="ftu-summary"></div>
</div>

<script>
(function () {
  var URLS = __URLS__;
  var TEXT = __TEXT__;
  var MAX_BYTES = __MAX_BYTES__;
  var MANIFEST_URL = __MANIFEST_URL__;
  var MAX_MB = Math.round(MAX_BYTES / (1024 * 1024));
  var input = document.getElementById("ftu-input");
  var list = document.getElementById("ftu-list");
  var summary = document.getElementById("ftu-summary");
  var nextIndex = 0, total = 0, done = 0, failed = 0;
  // Explicit completion manifest (#199): the names of every slot successfully
  // PUT so far, plus how many PUTs are still in flight. Whenever the in-flight
  // count drains to zero the manifest is (re)written, so the app can require
  // "manifest lists exactly the keys present" instead of inferring completion
  // from listing samples. A further selection batch makes the manifest stale
  // (fewer names than keys) until it finishes — which correctly reads as
  // "not ready".
  var uploadedNames = [];
  var inFlight = 0;

  function writeManifest() {
    if (!MANIFEST_URL || inFlight !== 0) { return; }
    var xhr = new XMLHttpRequest();
    xhr.open("PUT", MANIFEST_URL, true);
    // Best-effort: a failed manifest PUT only means the app falls back to the
    // manual read path; the photo PUTs themselves are unaffected.
    xhr.send(JSON.stringify({
      uploaded: uploadedNames.slice(),
      count: uploadedNames.length,
      failed: failed
    }));
  }

  function showError(label) {
    // Render a per-file error row (no progress bar) in the existing list UI.
    var row = document.createElement("div"); row.className = "ftu-row";
    var name = document.createElement("span"); name.className = "ftu-name";
    name.textContent = label;
    var status = document.createElement("span");
    status.className = "ftu-status err"; status.textContent = "✗";
    row.appendChild(name); row.appendChild(status);
    list.appendChild(row);
  }

  function refresh() {
    if (total === 0) { summary.textContent = ""; return; }
    var msg = TEXT.uploaded.replace("{done}", done).replace("{total}", total);
    if (failed > 0) { msg += " " + TEXT.failed.replace("{failed}", failed); }
    summary.textContent = msg;
  }

  function uploadFile(file, url, slotName) {
    var row = document.createElement("div"); row.className = "ftu-row";
    var name = document.createElement("span"); name.className = "ftu-name";
    name.textContent = file.name;
    var bar = document.createElement("progress"); bar.max = 100; bar.value = 0;
    var status = document.createElement("span"); status.className = "ftu-status";
    row.appendChild(name); row.appendChild(bar); row.appendChild(status);
    list.appendChild(row);

    inFlight += 1;
    var xhr = new XMLHttpRequest();
    xhr.open("PUT", url, true);
    xhr.upload.onprogress = function (ev) {
      if (ev.lengthComputable) { bar.value = Math.round((ev.loaded / ev.total) * 100); }
    };
    xhr.onload = function () {
      if (xhr.status >= 200 && xhr.status < 300) {
        bar.value = 100; status.textContent = "✓"; status.className = "ftu-status ok";
        done += 1;
        uploadedNames.push(slotName);
      } else {
        status.textContent = "✗"; status.className = "ftu-status err"; failed += 1;
      }
      inFlight -= 1;
      writeManifest();
      refresh();
    };
    xhr.onerror = function () {
      status.textContent = "✗"; status.className = "ftu-status err"; failed += 1;
      inFlight -= 1;
      writeManifest();
      refresh();
    };
    xhr.send(file);
  }

  input.addEventListener("change", function () {
    var files = Array.prototype.slice.call(input.files);
    input.value = "";  // allow re-opening the picker to add more photos
    // Order by file name (natural/numeric) so page order follows the photo file
    // names (e.g. IMG_1, IMG_2 ... or date-time names), NOT the order the OS
    // picker happened to return them in — the archivist can select in any order.
    // NOTE: applies per selection batch; selecting all pages in one go gives full
    // file-name ordering. Assumes the camera names photos sequentially in capture
    // order (typical for Android + iPhone) — verify on real devices.
    files.sort(function (a, b) {
      return a.name.localeCompare(b.name, undefined, { numeric: true, sensitivity: "base" });
    });
    files.forEach(function (file) {
      if (file.size > MAX_BYTES) {
        // Skip oversize files without consuming a presigned URL slot, and show a
        // clear per-file error; the rest of the batch continues uploading (#126).
        var mb = (file.size / (1024 * 1024)).toFixed(1);
        showError(TEXT.too_large
          .replace("{name}", file.name)
          .replace("{size}", mb)
          .replace("{max}", MAX_MB));
        failed += 1;
        refresh();
        return;
      }
      if (nextIndex >= URLS.length) {
        var note = document.createElement("div");
        note.className = "ftu-hint"; note.textContent = TEXT.max_reached;
        list.appendChild(note);
        return;
      }
      total += 1;
      nextIndex += 1;
      uploadFile(file, URLS[nextIndex - 1], "page_" + nextIndex + ".jpg");
    });
    refresh();
  });
})();
</script>
"""


def build_uploader_html(put_urls, manifest_url=None):
    """Return the HTML/JS string for the direct-to-S3 uploader component.

    ``put_urls`` is the presigned-PUT URL list from :func:`generate_put_urls`;
    ``manifest_url`` the presigned manifest PUT URL from
    :func:`generate_manifest_put_url` (#199 — ``None`` disables the manifest
    write, leaving callers on the legacy inferred-completion fallback).
    All user-facing strings are sourced from :class:`BookPhotoEntry`.
    """
    text = {
        "uploaded": BookPhotoEntry.upload_progress,
        "failed": BookPhotoEntry.upload_failed_count,
        "max_reached": BookPhotoEntry.upload_max_reached,
        "too_large": BookPhotoEntry.upload_too_large,
    }
    return (
        _UPLOADER_TEMPLATE
        .replace("__URLS__", json.dumps(put_urls))
        .replace("__TEXT__", json.dumps(text))
        .replace("__MAX_BYTES__", str(MAX_UPLOAD_BYTES))
        .replace("__MANIFEST_URL__", json.dumps(manifest_url))
        .replace("__SELECT_LABEL__", BookPhotoEntry.upload_select_button)
        .replace("__HINT__", BookPhotoEntry.upload_component_hint)
    )


# Strict, immutable upload generations used by unattended multi-book batches.
# The legacy uploader above remains available to existing single-book sessions.
BATCH_STATE_FILENAME = "upload_state.json"
MAX_BATCH_CHUNKS = 100
MAX_BATCH_UPLOAD_PHOTOS = 1200


def _batch_state_path(session_id):
    return f"{S3_BUCKET}/batch_sources/{session_id}/{BATCH_STATE_FILENAME}"


def _write_upload_json(fs, path, value):
    with fs.open(path, "wb") as handle:
        handle.write(json.dumps(value).encode("utf-8"))


def get_batch_upload_state(fs, session_id):
    """Recover chunk allocation from storage, independently of browser state."""
    from text_content.batch_upload import BatchUpload

    try:
        with fs.open(_batch_state_path(session_id), "rb") as handle:
            state = json.load(handle)
    except FileNotFoundError:
        state = {"version": 1, "chunks": [{"index": 0, "offset": 0}], "finished": False}
        _write_upload_json(fs, _batch_state_path(session_id), state)
    except (ValueError, OSError) as exc:
        raise ValueError(BatchUpload.invalid_state) from exc
    chunks = state.get("chunks") if isinstance(state, dict) else None
    if (not isinstance(chunks, list) or not chunks or len(chunks) > MAX_BATCH_CHUNKS
            or any(not isinstance(chunk, dict) or chunk.get("index") != i
                   or type(chunk.get("offset")) is not int or chunk.get("offset", -1) < 0
                   for i, chunk in enumerate(chunks))):
        raise ValueError(BatchUpload.invalid_state)
    return state


def _chunk_session(session_id, index):
    return f"{session_id}/chunks/{index:04d}"


def strict_manifest_matches(manifest, keys, *, generation, start_index=1):
    """Require a frozen generation, zero failures and every expected slot.

    A completed manifest cannot describe just successful PUTs: the browser saves
    every selected slot before beginning uploads. A generation allows only one
    immutable selection, preventing a previous completion from matching a new
    selection before that new selection's first PUT reaches storage.
    """
    if not isinstance(manifest, dict):
        return False
    expected = manifest.get("expected")
    uploaded = manifest.get("uploaded")
    if (manifest.get("generation") != generation or manifest.get("state") != "complete"
            or manifest.get("failed") != 0 or not isinstance(expected, list)
            or not expected or len(expected) > MAX_UPLOAD_PAGES
            or not isinstance(uploaded, list)
            or any(not isinstance(name, str) for name in expected + uploaded)):
        return False
    names = [f"page_{n}.jpg" for n in range(start_index, start_index + len(expected))]
    return (expected == names and len(uploaded) == len(set(uploaded))
            and len(keys) == len(expected) and set(uploaded) == set(expected)
            and {key.rsplit("/", 1)[-1] for key in keys} == set(expected))


def batch_chunk_snapshot(fs, session_id, chunk):
    """Return readiness and globally ordered source descriptors for one chunk."""
    accepted = chunk.get("accepted_sources")
    if accepted is not None:
        valid = (isinstance(accepted, list) and 0 < len(accepted) <= MAX_UPLOAD_PAGES
                 and all(isinstance(source, dict)
                         and source.get("position") == chunk.get("offset", 0) + index
                         and isinstance(source.get("key"), str)
                         for index, source in enumerate(accepted, 1)))
        return bool(valid), accepted if valid else [], None
    chunk_id = _chunk_session(session_id, chunk["index"])
    keys = list_uploaded_keys(fs, "batch", chunk_id)
    manifest = read_upload_manifest(fs, "batch", chunk_id)
    ready = strict_manifest_matches(
        manifest, keys, generation=chunk_id, start_index=chunk["offset"] + 1,
    )
    sources = [
        {"name": key.rsplit("/", 1)[-1], "key": key, "position": chunk["offset"] + i}
        for i, key in enumerate(keys, 1)
    ] if ready else []
    return ready, sources, manifest


def _completed_batch_sources(fs, session_id, state, on_chunk=None):
    from text_content.batch_upload import BatchUpload

    sources = []
    for chunk in state.get("chunks", []):
        if chunk.get("offset") != len(sources):
            raise ValueError(BatchUpload.invalid_state)
        accepted = chunk.get("accepted_sources")
        if accepted is not None:
            if (not isinstance(accepted, list) or not accepted
                    or any(not isinstance(source, dict)
                           or source.get("position") != len(sources) + index
                           or not isinstance(source.get("key"), str)
                           for index, source in enumerate(accepted, 1))):
                raise ValueError(BatchUpload.invalid_state)
            chunk_sources = accepted
        else:
            ready, chunk_sources, _manifest = batch_chunk_snapshot(fs, session_id, chunk)
            if not ready:
                raise ValueError(BatchUpload.incomplete)
            if on_chunk is not None:
                on_chunk(chunk["index"], chunk_sources)
                # Only successful durable staging permits recovery without the
                # temporary objects/manifests, which may expire after seven days.
                chunk["accepted_sources"] = chunk_sources
                _write_upload_json(fs, _batch_state_path(session_id), state)
        sources.extend(chunk_sources)
    return sources


def append_batch_upload_chunk(fs, session_id, *, on_chunk=None):
    """Freeze and hand off completed sources before allocating fresh PUT slots."""
    from text_content.batch_upload import BatchUpload

    state = get_batch_upload_state(fs, session_id)
    if state.get("finished"):
        raise ValueError(BatchUpload.invalid_state)
    sources = _completed_batch_sources(fs, session_id, state, on_chunk)
    if len(state["chunks"]) >= MAX_BATCH_CHUNKS or len(sources) >= MAX_BATCH_UPLOAD_PHOTOS:
        raise ValueError(BatchUpload.chunk_limit)
    state["chunks"].append({"index": len(state["chunks"]), "offset": len(sources)})
    _write_upload_json(fs, _batch_state_path(session_id), state)
    return state


def finish_batch_upload(fs, session_id, *, on_chunk=None):
    """Freeze the full ordered source manifest, only after every chunk finished."""
    state = get_batch_upload_state(fs, session_id)
    sources = _completed_batch_sources(fs, session_id, state, on_chunk)
    state["finished"] = True
    _write_upload_json(fs, _batch_state_path(session_id), state)
    return sources


_STRICT_UPLOADER_TEMPLATE = """
<div id="ftu-strict">
  <input id="batch-upload-input" type="file" accept="image/*" multiple>
  <button id="batch-upload-retry" type="button">__RETRY__</button>
  <button id="batch-upload-finish" type="button" disabled>__FINISH__</button>
  <p id="batch-upload-status"></p>
  <ul id="batch-upload-list"></ul>
</div>
<script>
(function () {
  var URLS = __URLS__, MANIFEST_URL = __MANIFEST_URL__;
  var GENERATION = __GENERATION__, START = __START__, MAX_BYTES = __MAX_BYTES__;
  var TEXT = __TEXT__, saved = __INITIAL__;
  var input = document.getElementById("batch-upload-input");
  var finish = document.getElementById("batch-upload-finish");
  var retry = document.getElementById("batch-upload-retry");
  var status = document.getElementById("batch-upload-status");
  var list = document.getElementById("batch-upload-list");
  var descriptors = saved && saved.files || [];
  var uploaded = saved && saved.uploaded || [];
  var expected = saved && saved.expected || [];
  var files = [], rows = [], busy = false, frozen = saved && saved.state === "complete";
  var sequence = Promise.resolve();
  input.disabled = frozen;
  status.textContent = frozen ? TEXT.finished : (expected.length ? TEXT.resume : "");

  function put(url, data) {
    return new Promise(function(resolve, reject) {
      var xhr = new XMLHttpRequest();
      xhr.open("PUT", url, true);
      xhr.timeout = 180000;
      xhr.onload = function() { if (xhr.status >= 200 && xhr.status < 300) resolve(); else reject(new Error("PUT " + xhr.status)); };
      xhr.onerror = function() { reject(new Error("network")); };
      xhr.ontimeout = function() { reject(new Error("timeout")); };
      xhr.send(data);
    });
  }
  function save(state) {
    var body = JSON.stringify({version: 2, generation: GENERATION, state: state,
      expected: expected.slice(), uploaded: uploaded.slice(), files: descriptors.slice(),
      count: uploaded.length, failed: expected.length - uploaded.length});
    // Serialize manifest PUTs: an earlier pending checkpoint cannot overwrite
    // a later completed generation after a slow network response.
    sequence = sequence.catch(function() {}).then(function() { return put(MANIFEST_URL, body); });
    return sequence;
  }
  function showRows() {
    list.textContent = "";
    rows = descriptors.map(function(d, i) {
      var row = document.createElement("li");
      row.textContent = d.name + (uploaded.indexOf(expected[i]) >= 0 ? " ✓" : " …");
      list.appendChild(row); return row;
    });
  }
  async function uploadMissing() {
    if (busy || frozen || !files.length) return;
    busy = true; input.disabled = true; finish.disabled = true; retry.disabled = true;
    try {
      // No photo PUT starts until the exact selected slots are durably recorded.
      await save("uploading");
      for (var i = 0; i < files.length; i++) {
        if (uploaded.indexOf(expected[i]) >= 0) continue;
        try {
          await put(URLS[i], files[i]);
          uploaded.push(expected[i]);
          rows[i].textContent = descriptors[i].name + " ✓";
        } catch (error) {
          rows[i].textContent = descriptors[i].name + " ✗";
        }
        await save("uploading");
      }
      status.textContent = TEXT.uploaded.replace("{done}", uploaded.length).replace("{total}", expected.length);
      finish.disabled = uploaded.length !== expected.length;
    } catch (error) { status.textContent = TEXT.manifest_failed; }
    busy = false; input.disabled = false; retry.disabled = false;
  }
  input.addEventListener("change", function() {
    if (frozen || busy) return;
    var selected = Array.prototype.slice.call(input.files);
    input.value = "";
    selected.sort(function(a,b) { return a.name.localeCompare(b.name, undefined, {numeric:true, sensitivity:"base"}); });
    if (!selected.length) return;
    if (selected.length > URLS.length) { status.textContent = TEXT.too_many; return; }
    if (selected.some(function(f) { return f.size > MAX_BYTES; })) { status.textContent = TEXT.too_large; return; }
    var next = selected.map(function(f) { return {name:f.name, size:f.size}; });
    if (descriptors.length && JSON.stringify(next) !== JSON.stringify(descriptors)) { status.textContent = TEXT.mismatch; return; }
    descriptors = next;
    expected = selected.map(function(_, i) { return "page_" + (START+i) + ".jpg"; });
    files = selected; showRows(); uploadMissing();
  });
  retry.addEventListener("click", uploadMissing);
  finish.addEventListener("click", async function() {
    if (busy || frozen || !expected.length || uploaded.length !== expected.length) return;
    busy = true; input.disabled = true; finish.disabled = true; retry.disabled = true;
    try { await save("complete"); frozen = true; status.textContent = TEXT.finished; }
    catch (error) { status.textContent = TEXT.manifest_failed; input.disabled = false; finish.disabled = false; retry.disabled = false; }
    busy = false;
  });
  showRows();
  if (!frozen && expected.length && uploaded.length === expected.length) finish.disabled = false;
})();
</script>
"""


def build_batch_uploader_html(put_urls, manifest_url, *, generation, start_index=1, initial=None):
    """A single immutable generation with resumable failed-slot retries."""
    from text_content.batch_upload import BatchUpload

    texts = {
        "resume": BatchUpload.resume, "finished": BatchUpload.chunk_finished,
        "manifest_failed": BatchUpload.manifest_failed, "mismatch": BatchUpload.mismatch,
        "too_many": BatchUpload.too_many, "too_large": BatchUpload.too_large,
        "uploaded": BookPhotoEntry.upload_progress,
    }
    if not isinstance(initial, dict) or initial.get("generation") != generation or initial.get("version") != 2:
        initial = None

    def script_json(value):
        return json.dumps(value).replace("<", "\\u003c").replace(">", "\\u003e")

    replacements = {
        "__URLS__": script_json(put_urls), "__MANIFEST_URL__": script_json(manifest_url),
        "__GENERATION__": script_json(generation), "__START__": str(start_index),
        "__MAX_BYTES__": str(MAX_UPLOAD_BYTES), "__INITIAL__": script_json(initial),
        "__TEXT__": script_json(texts), "__RETRY__": BatchUpload.retry,
        "__FINISH__": BatchUpload.finish_chunk,
    }
    html = _STRICT_UPLOADER_TEMPLATE
    for key, value in replacements.items():
        html = html.replace(key, value)
    return html


def render_batch_upload(fs, session_id, *, on_chunk=None):
    """Render bounded chunks; return frozen source descriptors after Finish batch.

    ``on_chunk(index, sources)`` is an idempotent durable staging callback. It
    executes on the script thread before advancing the upload or finishing it.
    No extraction runs here and no source object is deleted.
    """
    from text_content.batch_upload import BatchUpload

    state = get_batch_upload_state(fs, session_id)
    if state.get("finished"):
        sources = _completed_batch_sources(fs, session_id, state, on_chunk)
        st.success(BatchUpload.frozen.format(count=len(sources)))
        return sources
    st.write(BatchUpload.instructions)
    chunk = state["chunks"][-1]
    chunk_id = _chunk_session(session_id, chunk["index"])
    ready, _sources, manifest = batch_chunk_snapshot(fs, session_id, chunk)
    st.caption(BatchUpload.chunk_caption.format(number=chunk["index"] + 1, start=chunk["offset"] + 1))
    if not ready:
        urls = generate_put_urls(
            "batch", chunk_id, count=min(MAX_UPLOAD_PAGES, MAX_BATCH_UPLOAD_PHOTOS - chunk["offset"]),
            start_index=chunk["offset"] + 1,
        )
        url = generate_manifest_put_url("batch", chunk_id)
        st.iframe(build_batch_uploader_html(
            urls, url, generation=chunk_id, start_index=chunk["offset"] + 1, initial=manifest,
        ), height=460)
        st.info(BatchUpload.waiting)
        st.button(BatchUpload.refresh, key="batch_upload_refresh_button")
        return None
    st.success(BatchUpload.ready)
    if st.button(BatchUpload.next_chunk, key="batch_upload_next_chunk_button"):
        append_batch_upload_chunk(fs, session_id, on_chunk=on_chunk)
        st.rerun()
    if st.button(BatchUpload.finish_batch, key="batch_upload_finish_button"):
        return finish_batch_upload(fs, session_id, on_chunk=on_chunk)
    return None
