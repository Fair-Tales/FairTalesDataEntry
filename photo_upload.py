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
from requests.models import PreparedRequest

from text_content import BookPhotoEntry, PhotoUpload

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
    """
    ss_key = _session_state_key(flow_key)
    if ss_key not in st.session_state:
        username = st.session_state.get("username") or "anon"
        safe = re.sub(r"[^A-Za-z0-9_-]", "_", username) or "anon"
        counter_key = _counter_key(flow_key)
        counter = st.session_state.get(counter_key, 0) + 1
        st.session_state[counter_key] = counter
        st.session_state[ss_key] = f"{safe}_{counter}_{int(time.time())}"
    return st.session_state[ss_key]


def reset_upload_session(flow_key):
    """Drop the current upload-session id for ``flow_key`` so the next session
    mints a fresh prefix. Call at each "start a new photo entry" choke point.
    """
    st.session_state.pop(_session_state_key(flow_key), None)


def upload_prefix(flow_key, session_id):
    """Return the temp S3 prefix (no bucket) for a flow/session, e.g.
    ``uploads/<flow_key>/<session_id>``.
    """
    return f"{UPLOAD_PREFIX_ROOT}/{flow_key}/{session_id}"


def generate_put_urls(flow_key, session_id, count=MAX_UPLOAD_PAGES):
    """Return ``count`` presigned PUT URLs for
    ``uploads/{flow_key}/{session_id}/page_{i}.jpg``.

    ``Content-Type`` is deliberately *not* signed, so the browser may PUT any
    image bytes (jpeg/png/heic) without having to match a signed header.
    """
    client = _s3_client()
    urls = []
    for i in range(1, count + 1):
        key = f"{upload_prefix(flow_key, session_id)}/page_{i}.jpg"
        urls.append(
            client.generate_presigned_url(
                "put_object",
                Params={"Bucket": S3_BUCKET, "Key": key},
                ExpiresIn=PRESIGN_EXPIRY_SECONDS,
            )
        )
    return urls


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
    """Best-effort check that no more photos are still arriving in the temp prefix.

    The direct-to-S3 uploader iframe is intentionally ONE-WAY: it cannot signal
    "all uploads finished" back to Streamlit. Streamlit 1.58's
    ``st.context.cookies`` are frozen at the websocket handshake (so a JS-set
    completion cookie is not readable without a full page reload), and a bespoke
    bidirectional component is out of scope before this release. So to stop the
    user reading a PARTIAL batch mid-upload (#142) we sample the prefix twice,
    ``settle_seconds`` apart: because the browser PUTs the selected photos
    sequentially, a still-running upload shows up as a growing key count.

    Returns ``(settled, keys)`` where ``settled`` is ``True`` when the count did
    not grow across the interval, and ``keys`` is the second (latest) listing so
    the caller need not re-list. ``settled`` is ``True`` for an empty prefix (the
    caller distinguishes "nothing uploaded" from "still uploading" via ``keys``).
    """
    first = list_uploaded_keys(fs, flow_key, session_id)
    time.sleep(settle_seconds)
    keys = list_uploaded_keys(fs, flow_key, session_id)
    return len(keys) <= len(first), keys


def build_phone_upload_url(flow_key, session_id):
    """Build the ``qr_landing`` deep-link that points a phone at THIS surface's
    temp prefix ``uploads/{flow_key}/{session_id}/`` (#143).

    Carries the ``flow``/``session`` so the phone's uploads land in the SAME
    prefix the computer surface reads, plus the ``user`` + confirmation ``token``
    for auth (mirroring ``page_photo_upload``'s QR). No ``book`` param: the phone
    only PUTs the photos; the computer surface does the processing when the user
    returns and taps its read button.
    """
    url = f"{st.secrets.app_url}qr_landing"
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
    st.image(temp.getvalue(), width="stretch")
    st.write(PhotoUpload.link_line % (target_url, target_url))
    st.write(PhotoUpload.qr_return_instruction)


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
  var MAX_MB = Math.round(MAX_BYTES / (1024 * 1024));
  var input = document.getElementById("ftu-input");
  var list = document.getElementById("ftu-list");
  var summary = document.getElementById("ftu-summary");
  var nextIndex = 0, total = 0, done = 0, failed = 0;

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

  function uploadFile(file, url) {
    var row = document.createElement("div"); row.className = "ftu-row";
    var name = document.createElement("span"); name.className = "ftu-name";
    name.textContent = file.name;
    var bar = document.createElement("progress"); bar.max = 100; bar.value = 0;
    var status = document.createElement("span"); status.className = "ftu-status";
    row.appendChild(name); row.appendChild(bar); row.appendChild(status);
    list.appendChild(row);

    var xhr = new XMLHttpRequest();
    xhr.open("PUT", url, true);
    xhr.upload.onprogress = function (ev) {
      if (ev.lengthComputable) { bar.value = Math.round((ev.loaded / ev.total) * 100); }
    };
    xhr.onload = function () {
      if (xhr.status >= 200 && xhr.status < 300) {
        bar.value = 100; status.textContent = "✓"; status.className = "ftu-status ok";
        done += 1;
      } else {
        status.textContent = "✗"; status.className = "ftu-status err"; failed += 1;
      }
      refresh();
    };
    xhr.onerror = function () {
      status.textContent = "✗"; status.className = "ftu-status err"; failed += 1;
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
      uploadFile(file, URLS[nextIndex++]);
    });
    refresh();
  });
})();
</script>
"""


def build_uploader_html(put_urls):
    """Return the HTML/JS string for the direct-to-S3 uploader component.

    ``put_urls`` is the presigned-PUT URL list from :func:`generate_put_urls`.
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
        .replace("__SELECT_LABEL__", BookPhotoEntry.upload_select_button)
        .replace("__HINT__", BookPhotoEntry.upload_component_hint)
    )
