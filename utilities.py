from google.cloud import firestore
import streamlit as st
from google.cloud.firestore_v1 import FieldFilter
from google.oauth2 import service_account
import pandas as pd
import base64
import difflib
import json
import re
import urllib.request
import bcrypt
import smtplib
from email.mime.text import MIMEText
from datetime import datetime, timezone

def is_authenticated():
    if 'authentication_status' not in st.session_state:
        st.session_state['authentication_status'] = False

    return st.session_state['authentication_status']


def check_authentication_status():
    if 'authentication_status' not in st.session_state:
        st.session_state['authentication_status'] = False

    if not is_authenticated():
        st.switch_page("./pages/login.py")


_MAX_HISTORY = 10


def navigate_to(page_path):
    """Navigate to a page, pushing the current page onto the back-history stack."""
    current = st.session_state.get('_current_page', None)
    if current:
        history = st.session_state.get('_page_history', [])
        history.append(current)
        st.session_state['_page_history'] = history[-_MAX_HISTORY:]
    st.switch_page(page_path)


def go_back(fallback="./pages/user_home.py"):
    """Navigate to the previous page in the history stack."""
    history = st.session_state.get('_page_history', [])
    if history:
        previous = history.pop()
        st.session_state['_page_history'] = history
        st.switch_page(previous)
    else:
        st.switch_page(fallback)


def clear_page_history():
    """Reset the back-history stack (used at root pages and on logout)."""
    st.session_state['_page_history'] = []


def clear_entity_form_state(prefix):
    """Drop any persisted widget state for a per-entity ``to_form()`` form.

    Entity form widgets are keyed ``<entity>_form_<field>_<document_id>`` (see
    the "Widget key naming" note in CLAUDE.md). A brand-new, unregistered entity
    has an empty/placeholder ``document_id``, so two consecutive new entities of
    the same type would share keys and Streamlit would re-show the first
    entity's values (ignoring the ``value=``/``index=`` seeding) for the second.

    Call this at each "start a new X" choke point with the entity's key prefix
    (e.g. ``"book_form_"``) so the next form re-seeds cleanly.
    """
    for key in [k for k in st.session_state if k.startswith(prefix)]:
        st.session_state.pop(key, None)


def page_layout(current_page=None):
    st.set_page_config(
        initial_sidebar_state="collapsed",
        layout="wide"
    )
    if current_page:
        st.session_state['_current_page'] = current_page
    st.sidebar.page_link("pages/login.py", label="Login")
    st.sidebar.page_link("pages/landing.py", label="Home")
    st.sidebar.page_link("pages/priority_books.py", label="Books We Need")
    st.sidebar.page_link("pages/account_settings.py", label="Settings")
    st.sidebar.page_link("pages/donate.py", label="Donate")
    st.sidebar.page_link("pages/report_feedback.py", label="Report a Bug / Feature")
    if 'admin' in st.session_state and st.session_state['admin']:
        st.sidebar.page_link("pages/admin.py", label="Admin")
    history = st.session_state.get('_page_history', [])
    # Hide Back during the guided book sub-entry flow (add author/illustrator/
    # publisher): returning to add_book.py would just re-forward here. Use Cancel.
    if history and not st.session_state.get('adding_book_entries', False):
        if st.sidebar.button("← Back", key="sidebar_back_button"):
            go_back()



def get_user(username):
    db = FirestoreWrapper().connect_user(auth=False)
    users_ref = db.collection("users")
    query_ref = users_ref.where(filter=firestore.FieldFilter("username", "==", username))
    docs = query_ref.get()
    if len(docs) == 1:
        return docs[0]
    else:
        return None
    
# ---------------------------------------------------------------------------
# Role tiers (issue #83).
#
# Every user has one of three permission tiers, stored as a ``role`` string on
# their Firestore user document:
#   'archivist' (default) — view results; enter single books (manual + photo);
#                           edit ONLY books they uploaded (entered_by == them).
#   'team'                — everything an archivist can do, PLUS edit books
#                           uploaded by others and access the validation
#                           workflow (the validation workflow itself is #47;
#                           this change only gates access to that page).
#   'admin'               — everything above, PLUS delete users/books, export /
#                           download data, and the admin page.
#
# BACK-COMPAT: older user documents predate the ``role`` field. A legacy user
# with ``admin: true`` and no ``role`` resolves to 'admin'; a user with neither
# resolves to 'archivist'. This is resolved at read time (``resolve_role``), so
# NO data migration is required.
#
# NOTE: there is no in-app role-management UI yet — admins set a user's ``role``
# directly on the Firestore user document for now. A management UI is tracked by
# #47 / #69 and is out of scope here.
ROLE_ARCHIVIST = 'archivist'
ROLE_TEAM = 'team'
ROLE_ADMIN = 'admin'
VALID_ROLES = (ROLE_ARCHIVIST, ROLE_TEAM, ROLE_ADMIN)


def resolve_role(user_dict):
    """Resolve a user's effective role from their raw user dict (back-compat).

    A valid stored ``role`` wins; otherwise a legacy ``admin: true`` flag maps
    to 'admin'; otherwise the default 'archivist'. Every lookup is guarded with
    a ``.get`` default so a missing field never raises.
    """
    role = user_dict.get('role')
    if role in VALID_ROLES:
        return role
    if user_dict.get('admin', False):
        return ROLE_ADMIN
    return ROLE_ARCHIVIST


def get_role(username):
    """Return the effective role string for ``username`` (back-compat aware).

    Falls back to 'archivist' when the user document cannot be found.
    """
    user = get_user(username)
    if user is None:
        return ROLE_ARCHIVIST
    return resolve_role(user.to_dict())


def get_admin(username):
    """Back-compat shim: True when ``username`` resolves to the admin role."""
    return get_role(username) == ROLE_ADMIN


def is_admin():
    """True when the current session's role is admin (guarded session read).

    Gates admin-only actions: deleting users/books, exporting/downloading data,
    and the admin page.
    """
    return st.session_state.get('role', ROLE_ARCHIVIST) == ROLE_ADMIN


def is_team_or_above():
    """True when the current session's role is team member or admin.

    Gates team-and-above actions: editing books uploaded by others and reaching
    the validation page (the validation workflow itself is #47).
    """
    return st.session_state.get('role', ROLE_ARCHIVIST) in (ROLE_TEAM, ROLE_ADMIN)


def authenticate_user(username, password):
    """Authenticate a user by username and password.

    Returns one of three string statuses:
    - "ok"              — credentials valid and account confirmed.
    - "not_confirmed"   — credentials valid but account not yet confirmed.
    - "bad_credentials" — username not found or password incorrect.

    Security note: the password is always checked before the confirmation flag
    is inspected.  This prevents an attacker from inferring account existence
    via the confirmation state using a wrong password.
    """
    user = get_user(username)
    if user is None:
        return "bad_credentials"

    user_dict = user.to_dict()

    password_ok = bcrypt.checkpw(
        password=password.encode('utf8'),
        hashed_password=user_dict['password'].encode('utf8')
    )
    if not password_ok:
        return "bad_credentials"

    if not user_dict.get('is_confirmed', False):
        return "not_confirmed"

    return "ok"


def hash_password(password):
    hashed_password = bcrypt.hashpw(
        password.encode('utf8'), bcrypt.gensalt()
    ).decode('utf8')
    return hashed_password


def send_confirmation_email(send_to, username, confirmation_token, name):

    smtpserver = smtplib.SMTP_SSL('smtp.gmail.com', 465)
    smtpserver.ehlo()
    smtpserver.login(st.secrets["email_address"], st.secrets["gmail_app_password"])

    subject = "Please confirm your account registration"
    body = """
        Dear %s, 
        
        Thank you for registering for an account on our data entry tool.
        Please click the link below to confirm your registration.
        
        If you did not register, please reply to this email to let us know
        and we will delete your email address.
        
        Thanks,
        The Fair Tales team
        
    """ % name
    confirmation_link = f"{st.secrets['app_url']}confirm?token={confirmation_token}&user={username}"
    body += confirmation_link
    msg = MIMEText(body)
    msg['Subject'] = subject
    msg['From'] = st.secrets["email_address"]
    msg['To'] = send_to

    smtpserver.send_message(msg)
    smtpserver.close()


def send_password_reset_email(send_to, username, reset_token, name):
    """Email a self-service password reset link.

    Mirrors ``send_confirmation_email``'s SMTP path and the ``app_url`` secret
    pattern, but points the recipient at the public ``reset_password`` page with
    ``token`` and ``user`` query params.  The email copy lives in the
    ``text_content`` module (``PasswordReset``).
    """
    from text_content import PasswordReset

    smtpserver = smtplib.SMTP_SSL('smtp.gmail.com', 465)
    smtpserver.ehlo()
    smtpserver.login(st.secrets["email_address"], st.secrets["gmail_app_password"])

    body = PasswordReset.email_body % name
    reset_link = f"{st.secrets['app_url']}reset_password?token={reset_token}&user={username}"
    body += reset_link
    msg = MIMEText(body)
    msg['Subject'] = PasswordReset.email_subject
    msg['From'] = st.secrets["email_address"]
    msg['To'] = send_to

    smtpserver.send_message(msg)
    smtpserver.close()


def author_entry_to_name(entry):
    """
    Helper method converts an author entry from the Firestore database
    to a readable string as 'forename surname'.
    """
    author = entry.to_dict()
    return ' '.join([author['forename'], author['surname']])


def extract_isbn(text):
    """Extract ISBN-13 or ISBN-10 from text. Returns string or None.

    Real-world copyright pages hyphenate ISBNs with varied group sizes, so we
    match a run of digits separated by optional hyphens/spaces and validate the
    cleaned length rather than assuming a fixed grouping.
    """
    if not text:
        return None
    isbn13 = re.search(r'97[89][-\s]?(?:\d[-\s]?){9}\d', text)
    if isbn13:
        return re.sub(r'[-\s]', '', isbn13.group())
    isbn10 = re.search(r'\b\d[-\s]?(?:\d[-\s]?){8}[\dX]\b', text)
    if isbn10:
        return re.sub(r'[-\s]', '', isbn10.group())
    return None


def lookup_person_details(name, role, client):
    """Use Claude + web search to suggest birth year and gender for a named person.

    Returns a dict with 'birth_year' (int or None) and 'gender' (str from
    AuthorForm/IllustratorForm.gender_options), or None on any failure.
    """
    import json as _json
    from text_content import AIPrompts
    try:
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=512,
            tools=[{"type": "web_search_20260209", "name": "web_search"}],
            messages=[{
                "role": "user",
                "content": AIPrompts.person_lookup.format(name=name, role=role)
            }]
        )
        text_block = None
        for block in response.content:
            if hasattr(block, 'type') and block.type == "text":
                text_block = block.text
        if text_block is None:
            return None
        raw = text_block.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        result = _json.loads(raw.strip())
        birth_year = result.get("birth_year")
        if birth_year is not None:
            birth_year = int(birth_year)
        gender = result.get("gender", "Unknown")
        valid_genders = ["Woman", "Man", "Non-binary", "Other", "Unknown"]
        if gender not in valid_genders:
            gender = "Unknown"
        return {"birth_year": birth_year, "gender": gender}
    except Exception:
        return None


def _claude_json(client, prompt, max_tokens=1024):
    """Send a text prompt to Claude Sonnet and parse the JSON response.

    Reuses the model and JSON-fence-stripping convention used by the existing
    Claude helpers (extract_page_info, lookup_person_details, theme detection).
    Raises json.JSONDecodeError if the model does not return valid JSON, or an
    anthropic error if the API call fails — the caller is expected to surface
    these to the user.
    """
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}]
    )
    raw = response.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return json.loads(raw.strip())


def detect_book_characters(pages, client, progress_callback=None):
    """Two-pass character + alias detection across a book's story pages (#52).

    Args:
        pages: iterable of (page_number, page_text) for story pages with text.
        client: an anthropic.Anthropic client.
        progress_callback: optional callable(done, total) for UI progress.

    Returns a list of character suggestion dicts, each with keys:
        name (str), gender (one of CharacterForm.gender_options),
        human (bool), plural (bool), protagonist (bool), aliases (list[str]).

    Pass 1 extracts the character references appearing on each page; pass 2
    consolidates those references across pages so that e.g. "the boy", "Tom"
    and "Tommy" collapse into a single character with the others as aliases.
    Nothing is written to the database — the caller presents the result for the
    user to review, correct and confirm.
    """
    from text_content import AIPrompts

    pages = list(pages)
    total_steps = len(pages) + 1

    # Pass 1 — per-page character mentions.
    per_page_mentions = []
    for index, (page_number, page_text) in enumerate(pages):
        data = _claude_json(
            client,
            AIPrompts.character_extraction.format(page_text=page_text),
            max_tokens=512,
        )
        mentions = data.get("mentions", []) if isinstance(data, dict) else []
        per_page_mentions.append({"page": page_number, "mentions": mentions})
        if progress_callback is not None:
            progress_callback(index + 1, total_steps)

    # Pass 2 — consolidate references into distinct characters.
    mentions_json = json.dumps(per_page_mentions, ensure_ascii=False)
    result = _claude_json(
        client,
        AIPrompts.character_consolidation.format(mentions_json=mentions_json),
        max_tokens=2048,
    )
    raw_characters = result.get("characters", []) if isinstance(result, dict) else []

    valid_genders = ["Female", "Male", "Non-specific", "Transgender"]
    suggestions = []
    for character in raw_characters:
        if not isinstance(character, dict):
            continue
        name = str(character.get("name", "")).strip()
        if not name:
            continue
        gender = character.get("gender", "Non-specific")
        if gender not in valid_genders:
            gender = "Non-specific"
        seen = set()
        aliases = []
        for alias in character.get("aliases", []) or []:
            alias = str(alias).strip()
            if alias and alias.lower() != name.lower() and alias.lower() not in seen:
                seen.add(alias.lower())
                aliases.append(alias)
        suggestions.append({
            "name": name,
            "gender": gender,
            "human": bool(character.get("human", True)),
            "plural": bool(character.get("plural", False)),
            "protagonist": bool(character.get("protagonist", False)),
            "aliases": aliases,
        })

    if progress_callback is not None:
        progress_callback(total_steps, total_steps)
    return suggestions


def lookup_isbn(isbn):
    """
    Look up book metadata via the Google Books API (free, no auth required).
    Returns dict with keys title, authors, publisher, published_date,
    or None on any failure.
    """
    if not isbn:
        return None
    url = f"https://www.googleapis.com/books/v1/volumes?q=isbn:{isbn}&maxResults=1"
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            data = json.loads(resp.read())
        if data.get('totalItems', 0) == 0:
            return None
        info = data['items'][0]['volumeInfo']
        return {
            'title': info.get('title', ''),
            'authors': info.get('authors', []),
            'publisher': info.get('publisher', ''),
            'published_date': info.get('publishedDate', ''),
        }
    except Exception:
        return None


def extract_book_metadata(image_bytes, client):
    """Extract bibliographic metadata from a title-page image using Claude vision.

    Sends the image to Claude using the same model/integration as
    ``extract_page_info`` and ``lookup_person_details`` and parses the JSON reply.
    Returns a dict with keys:
      - 'title'          (str)
      - 'authors'        (list[str])
      - 'illustrators'   (list[str])
      - 'publisher'      (str or None)
      - 'published_year' (int or None)
      - 'raw'            (the raw model response text, kept for audit/debugging)

    The raw response is always included whenever a reply is received, so the caller
    can store it even when individual fields cannot be parsed. Anthropic API errors
    are deliberately allowed to propagate so the caller can surface them to the user
    (per ``book_edit_home.py``'s pattern); only response-parsing problems are handled
    here, by returning empty fields alongside the raw text.
    """
    from text_content import AIPrompts

    image_data = base64.standard_b64encode(image_bytes).decode('utf-8')
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/jpeg",
                        "data": image_data,
                    },
                },
                {"type": "text", "text": AIPrompts.book_metadata_extraction},
            ],
        }],
    )

    try:
        raw_text = response.content[0].text.strip()
    except (IndexError, AttributeError):
        raw_text = ""
    empty = {
        'title': "",
        'authors': [],
        'illustrators': [],
        'publisher': None,
        'published_year': None,
        'raw': raw_text,
    }

    cleaned = raw_text
    if cleaned.startswith("```"):
        cleaned = cleaned.split("```")[1]
        if cleaned.startswith("json"):
            cleaned = cleaned[4:]
    try:
        result = json.loads(cleaned.strip())
    except (json.JSONDecodeError, ValueError):
        return empty

    def _as_name_list(value):
        if isinstance(value, str):
            return [value.strip()] if value.strip() else []
        if isinstance(value, list):
            return [str(v).strip() for v in value if str(v).strip()]
        return []

    publisher = result.get('publisher')
    if not (isinstance(publisher, str) and publisher.strip()):
        publisher = None
    else:
        publisher = publisher.strip()

    year = result.get('published_year')
    try:
        year = int(year) if year is not None else None
    except (TypeError, ValueError):
        year = None
    if year is not None and not (1900 <= year <= datetime.now(timezone.utc).year):
        year = None

    return {
        'title': (result.get('title') or "").strip(),
        'authors': _as_name_list(result.get('authors')),
        'illustrators': _as_name_list(result.get('illustrators')),
        'publisher': publisher,
        'published_year': year,
        'raw': raw_text,
    }


def locate_key_pages(pages, client):
    """Locate the title-page and copyright-page positions in a set of book photos.

    Pass 1 of the photo-first two-pass flow (#109): a single cheap Claude Haiku
    call is sent ALL page images at once and asked which page is the title page
    and which is the copyright / imprint page (the latter's position varies —
    sometimes just after the title page, sometimes at the back of the book).

    Args:
        pages: ordered list of (name, image_bytes) tuples.
        client: an anthropic.Anthropic client.

    Returns a dict {'title_page': int|None, 'copyright_page': int|None} whose
    values are 1-based positions into ``pages`` (matching the "Page N" labels sent
    to the model), or None when a page could not be identified or the reply could
    not be parsed. Anthropic API errors propagate to the caller.
    """
    from text_content import AIPrompts

    pages = list(pages)
    content = []
    for index, (_name, image_bytes) in enumerate(pages):
        content.append({"type": "text", "text": f"Page {index + 1}:"})
        content.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/jpeg",
                "data": base64.standard_b64encode(image_bytes).decode('utf-8'),
            },
        })
    content.append({"type": "text", "text": AIPrompts.locate_key_pages})

    response = client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=128,
        messages=[{"role": "user", "content": content}],
    )

    none_result = {'title_page': None, 'copyright_page': None}
    try:
        raw = response.content[0].text.strip()
    except (IndexError, AttributeError):
        return none_result
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    try:
        result = json.loads(raw.strip())
    except (json.JSONDecodeError, ValueError):
        return none_result

    def _as_page(value):
        try:
            page = int(value)
        except (TypeError, ValueError):
            return None
        return page if 1 <= page <= len(pages) else None

    return {
        'title_page': _as_page(result.get('title_page')),
        'copyright_page': _as_page(result.get('copyright_page')),
    }


def extract_copyright_metadata(image_bytes, client):
    """Extract publisher, first-published year and ISBN from a copyright-page image.

    Pass 2 (copyright page) of the photo-first two-pass flow (#109): Claude Sonnet
    reads the single located copyright / imprint page for the details that the
    title page usually omits. Returns a dict with keys:
      - 'publisher'      (str or None)
      - 'published_year' (int or None)
      - 'isbn'           (str or None — normalised digits via extract_isbn)
      - 'raw'            (the raw model response text, kept for audit/debugging)

    Mirrors ``extract_book_metadata``: the raw text is always retained, parsing
    problems yield empty fields, and Anthropic API errors propagate to the caller.
    """
    from text_content import AIPrompts

    image_data = base64.standard_b64encode(image_bytes).decode('utf-8')
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=512,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/jpeg",
                        "data": image_data,
                    },
                },
                {"type": "text", "text": AIPrompts.copyright_page_extraction},
            ],
        }],
    )

    try:
        raw_text = response.content[0].text.strip()
    except (IndexError, AttributeError):
        raw_text = ""
    empty = {'publisher': None, 'published_year': None, 'isbn': None, 'raw': raw_text}

    cleaned = raw_text
    if cleaned.startswith("```"):
        cleaned = cleaned.split("```")[1]
        if cleaned.startswith("json"):
            cleaned = cleaned[4:]
    try:
        result = json.loads(cleaned.strip())
    except (json.JSONDecodeError, ValueError):
        return empty

    publisher = result.get('publisher')
    if not (isinstance(publisher, str) and publisher.strip()):
        publisher = None
    else:
        publisher = publisher.strip()

    year = result.get('first_published_year')
    try:
        year = int(year) if year is not None else None
    except (TypeError, ValueError):
        year = None
    if year is not None and not (1900 <= year <= datetime.now(timezone.utc).year):
        year = None

    # Reuse the upload pipeline's ISBN parser so hyphenation and stray characters
    # are normalised identically (and ISBN-10/13 both validated).
    raw_isbn = result.get('isbn')
    isbn = extract_isbn(str(raw_isbn)) if raw_isbn else None

    return {'publisher': publisher, 'published_year': year, 'isbn': isbn, 'raw': raw_text}


def extract_photo_first_metadata(pages, client, title_page_hint=None,
                                 progress_callback=None):
    """Two-pass, cost-aware metadata extraction for the photo-first flow
    (#109; completes #63 and makes the #103 form pre-fill reachable).

    Pass 1 (locate): one cheap Claude Haiku call over ALL page images finds the
    title-page and copyright/imprint-page positions (``locate_key_pages``).
    Pass 2 (extract): Claude Sonnet reads ONLY those one or two pages — the title
    page via ``extract_book_metadata`` and the copyright page via
    ``extract_copyright_metadata`` — and any ISBN found is fed into the Google
    Books lookup (``lookup_isbn``), the most reliable metadata source.

    Cost profile: one Haiku call-set + up to two Sonnet calls per book.

    Args:
        pages: ordered list of (name, image_bytes) tuples.
        client: an anthropic.Anthropic client.
        title_page_hint: optional 1-based position the user designated as the
            title page; used in preference to the located title page.
        progress_callback: optional callable(done, total) for UI progress.

    Returns the merged title-page metadata dict (``title``, ``authors``,
    ``illustrators``, ``publisher``, ``published_year``, ``raw``) — with
    publisher / year / authors back-filled from the copyright page and Google
    Books where the title page was silent — plus:
      - 'isbn'          (str or None)
      - 'isbn_metadata' (Google Books dict or None) for the Add-Book form pre-fill
      - 'located'       (the locate-pass result dict)

    Anthropic API errors propagate to the caller (mirrors ``extract_book_metadata``).
    """
    pages = list(pages)
    total_steps = 3
    done = 0

    def _step():
        nonlocal done
        done += 1
        if progress_callback is not None:
            progress_callback(min(done, total_steps), total_steps)

    # Pass 1 — locate the two pages of interest.
    located = locate_key_pages(pages, client)
    _step()

    title_pos = title_page_hint or located.get('title_page')
    copyright_pos = located.get('copyright_page')

    def _bytes_at(position):
        if isinstance(position, int) and 1 <= position <= len(pages):
            return pages[position - 1][1]
        return None

    # Pass 2a — title page (fall back to the first photo if nothing was located).
    title_bytes = _bytes_at(title_pos)
    if title_bytes is None and pages:
        title_bytes = pages[0][1]
    metadata = extract_book_metadata(title_bytes, client)
    _step()

    # Pass 2b — copyright page, only when a distinct one was located.
    copyright_meta = {'publisher': None, 'published_year': None, 'isbn': None, 'raw': None}
    copyright_bytes = _bytes_at(copyright_pos)
    if copyright_bytes is not None and copyright_pos != title_pos:
        copyright_meta = extract_copyright_metadata(copyright_bytes, client)
    _step()

    # Back-fill publisher / year from the copyright page where the title page was
    # silent (these usually live on the copyright page, not the title page).
    if not metadata.get('publisher') and copyright_meta.get('publisher'):
        metadata['publisher'] = copyright_meta['publisher']
    if metadata.get('published_year') is None and copyright_meta.get('published_year') is not None:
        metadata['published_year'] = copyright_meta['published_year']

    # ISBN → Google Books (most reliable source). Use the copyright-page ISBN.
    isbn = copyright_meta.get('isbn')
    isbn_metadata = lookup_isbn(isbn) if isbn else None

    # Google Books back-fills only where vision was silent. Vision stays primary
    # for the printed title and for illustrators (which the API rarely returns).
    if isbn_metadata:
        if not metadata.get('title') and isbn_metadata.get('title'):
            metadata['title'] = isbn_metadata['title']
        if not metadata.get('authors') and isbn_metadata.get('authors'):
            metadata['authors'] = list(isbn_metadata['authors'])
        if not metadata.get('publisher') and isbn_metadata.get('publisher'):
            metadata['publisher'] = isbn_metadata['publisher']

    metadata['isbn'] = isbn
    metadata['isbn_metadata'] = isbn_metadata
    metadata['located'] = located
    return metadata


def fuzzy_match_name(name, options, cutoff=0.8):
    """Return the closest matching key in ``options`` for ``name``, or None.

    Case-insensitive fuzzy match using the standard-library ``difflib``. ``cutoff``
    is the minimum similarity ratio (0-1). Used to reconcile names extracted from a
    title page against the existing author/illustrator/publisher session lookup
    dicts before creating a new record.
    """
    if not name or not options:
        return None
    name_l = name.strip().lower()
    lower_to_original = {}
    for opt in options:
        lower_to_original.setdefault(opt.lower(), opt)
    matches = difflib.get_close_matches(
        name_l, list(lower_to_original.keys()), n=1, cutoff=cutoff
    )
    if not matches:
        return None
    return lower_to_original[matches[0]]


def split_name(full_name):
    """Split a full name into ``(forename, surname)``.

    The final whitespace-separated token is treated as the surname and the rest as
    the forename(s). A single-token name is returned as the forename with an empty
    surname. Used to seed the new-author/illustrator sub-forms from an extracted
    name that did not fuzzy-match an existing record.
    """
    parts = (full_name or "").strip().split()
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return " ".join(parts[:-1]), parts[-1]


class FirestoreWrapper:
    """
    Wrapper class to handle interacting with
    Firestore database (searching, querying, entering new data).
    """

    def __init__(self, auth=True):
        self.auth = auth
        self.firestore_key = json.loads(st.secrets["firestore_key"])

    def _connect(self, auth=None):
        auth = self.auth if auth is None else auth
        if is_authenticated() or not auth:
            creds = service_account.Credentials.from_service_account_info(self.firestore_key)
            return firestore.Client(credentials=creds, project="sawdataentry")
        else:
            return None

    # connect_book and connect_user are kept as separate methods in anticipation
    # of issue #48, which will split the single Firestore database into two:
    # one for book/content data and one for user credentials. When that work is
    # done, each method will connect to its own named database. For now both
    # route to the same default database.
    def connect_book(self, auth=None):
        return self._connect(auth)

    def connect_user(self, auth=None):
        return self._connect(auth)

    def single_field_search(self, collection, field, contains_string):
        """ Search for string withing field. """
        db = self.connect_book()

        results = (
            db.collection(collection)
                .where(filter=FieldFilter(field, ">=", contains_string))
                .where(filter=FieldFilter(field, "<=", contains_string + 'z'))
                .stream()
        )

        results_dict = list(map(lambda x: x.to_dict(), results))
        return pd.DataFrame(results_dict)

    def get_by_field(self, collection, field, match):
        """ Get exact match in field"""
        db = self.connect_book()
        results = db.collection(collection).where(
            filter=FieldFilter(field, "==", match)
        ).stream()
        # return doc_ref.get()

        results_dict = list(map(lambda x: x.to_dict(), results))
        return pd.DataFrame(results_dict)

    def get_by_reference(self, collection, document_ref):
        db = self.connect_book()
        doc_ref = db.collection(collection).document(document_ref)
        return doc_ref.get()

    def get_all_documents_stream(self, collection):
        db = self.connect_book()
        return db.collection(collection).stream()

    def query_stream(self, collection, field, op, value):
        """Stream documents from ``collection`` matching a single field filter.

        Unlike ``get_by_field`` (which returns a DataFrame of values), this
        yields the raw document snapshots so callers can access ``.id`` and
        ``.reference`` — needed for deletion and reference look-ups.
        """
        db = self.connect_book()
        return (
            db.collection(collection)
            .where(filter=FieldFilter(field, op, value))
            .stream()
        )

    def delete_document(self, collection, doc_id):
        db = self.connect_book()
        db.collection(collection).document(doc_id).delete()

    def username_to_doc_ref(self, username):
        return self.connect_user().collection('users').document(username)

    def document_exists(self, collection, doc_id):
        db = self.connect_book()
        doc = db.collection(collection).document(doc_id).get()
        return doc.exists

    def update_field(self, collection, document, field, value):
        db = self.connect_book()
        doc_ref = db.collection(collection).document(document)
        doc_ref.update({field: value})


# ---------------------------------------------------------------------------
# Cached lookup-dict loaders (issue #53 — reduce Firestore read traffic).
#
# Previously Home.initialise() streamed the whole of every lookup collection
# (authors, publishers, illustrators, books, characters) on *every* session
# init. These functions move that work behind Streamlit's cache so the data is
# fetched once and shared across sessions/reruns instead of re-read each load.
#
# Why @st.cache_resource and NOT @st.cache_data:
#   The dict *values* are Firestore ``DocumentReference`` objects bound to a
#   live ``firestore.Client``. ``@st.cache_data`` pickles its return value, and
#   a client-bound DocumentReference is explicitly unpicklable
#   ("Pickling client objects is explicitly not supported"), so cache_data
#   would raise at runtime. ``@st.cache_resource`` stores the object by
#   reference without serialising, which both works and keeps the underlying
#   client alive for as long as the refs are cached. The cached dict is
#   shallow-copied into session_state by the caller so in-session mutations
#   (adding a freshly registered author/book/etc.) never poison the shared
#   cache.
#
# FRESHNESS / INVALIDATION — IMPORTANT:
#   The TTL is a safety net only. Whenever a write *adds* an entry to one of
#   these collections (the FormConfirmation.confirm_new_* methods and
#   Character.register), the caller MUST call the matching ``load_*_dict.clear()``
#   so the next session re-reads from Firestore. The current session continues
#   to see its own newly added entry because the entry is also written into the
#   session_state copy in place (unchanged existing behaviour). This preserves
#   the write-through freshness guarantee while removing the per-session full
#   re-read.
_LOOKUP_CACHE_TTL_SECONDS = 600  # 10 minutes; bounds staleness from external edits.


@st.cache_resource(ttl=_LOOKUP_CACHE_TTL_SECONDS, show_spinner=False)
def load_author_dict():
    firestore_wrapper = FirestoreWrapper(auth=False)
    return {
        author_entry_to_name(author): author.reference
        for author in firestore_wrapper.get_all_documents_stream(collection='authors')
    }


@st.cache_resource(ttl=_LOOKUP_CACHE_TTL_SECONDS, show_spinner=False)
def load_publisher_dict():
    firestore_wrapper = FirestoreWrapper(auth=False)
    return {
        publisher.to_dict()['name'].replace('_', ' '): publisher.reference
        for publisher in firestore_wrapper.get_all_documents_stream(collection='publishers')
    }


@st.cache_resource(ttl=_LOOKUP_CACHE_TTL_SECONDS, show_spinner=False)
def load_illustrator_dict():
    firestore_wrapper = FirestoreWrapper(auth=False)
    return {
        author_entry_to_name(illustrator): illustrator.reference
        for illustrator in firestore_wrapper.get_all_documents_stream(collection='illustrators')
    }


@st.cache_resource(ttl=_LOOKUP_CACHE_TTL_SECONDS, show_spinner=False)
def load_book_dict():
    firestore_wrapper = FirestoreWrapper(auth=False)
    return {
        book.to_dict()['title']: book.reference
        for book in firestore_wrapper.get_all_documents_stream(collection='books')
    }


@st.cache_resource(ttl=_LOOKUP_CACHE_TTL_SECONDS, show_spinner=False)
def load_character_dict():
    firestore_wrapper = FirestoreWrapper(auth=False)
    return {
        character.to_dict()['name']: character.reference
        for character in firestore_wrapper.get_all_documents_stream(collection='characters')
    }


# TODO: check that required fields (e.g. book title) are not blank
# TODO: fix warnings in table display (arrows?)
class FormConfirmation:
    """
    Class with helper methods to handle form confirmation and routing
    based on form type.
    """

    forms = {
        'new_book': 'confirm_new_book',
        'new_author': 'confirm_new_author',
        'new_illustrator': 'confirm_new_illustrator',
        'new_publisher': 'confirm_new_publisher',
        'new_character': 'confirm_new_character'
    }

    @classmethod
    def display_confirmation(cls, data):

        # Compact, borderless key/value summary in a constrained-width column,
        # rather than a full-width bordered table.
        summary_col, _ = st.columns([2, 1])
        for field, value in data.items():
            label = field.replace('_', ' ').capitalize()
            display_value = "" if value is None else value
            summary_col.markdown(f"**{label}:** {display_value}")
        col1, col2 = st.columns(2)
        confirm_button = col1.button("Confirm", key="confirm_display_confirm_button")
        edit_button = col2.button("Edit", key="confirm_display_edit_button")

        return confirm_button, edit_button

    @classmethod
    def confirm_new_book(cls):
        confirm_button, edit_button = cls.display_confirmation(
            st.session_state['current_book'].to_dict(
                form_fields_only=True,
                convert_ref_fields_to_ids=True
            )
        )

        if confirm_button:
            if st.session_state['current_book'].author is None:
                navigate_to("./pages/add_author.py")

            else:
                st.session_state['current_book'].register()
                st.session_state['book_dict'][
                    st.session_state['current_book'].title
                ] = st.session_state['current_book'].get_ref()
                # Invalidate the shared cache so other/new sessions re-read the
                # newly registered book (this session already sees it via the
                # in-place session_state update above).
                load_book_dict.clear()
                st.session_state.pop('isbn_metadata', None)

                if st.session_state.current_book.photos_uploaded:
                    navigate_to("./pages/enter_text.py")
                else:
                    navigate_to("./pages/page_photo_upload.py")

        if edit_button:
            st.switch_page("./pages/add_book.py")

    @classmethod
    def confirm_new_author(cls):
        confirm_button, edit_button = cls.display_confirmation(
            st.session_state['current_author'].to_dict(
                form_fields_only=True,
                convert_ref_fields_to_ids=True
            )
        )

        if confirm_button:
            st.session_state['current_author'].register()
            st.session_state['author_dict'][
                st.session_state['current_author'].name
            ] = st.session_state['current_author'].get_ref()
            # Invalidate shared cache so new/other sessions re-read this author.
            load_author_dict.clear()

            st.session_state['current_book'].author = (
                st.session_state['current_author'].name
            )
            st.switch_page("./pages/add_book.py")

        if edit_button:
            st.switch_page("./pages/add_author.py")

    @classmethod
    def confirm_new_illustrator(cls):
        confirm_button, edit_button = cls.display_confirmation(
            st.session_state['current_illustrator'].to_dict(
                form_fields_only=True,
                convert_ref_fields_to_ids=True
            )
        )

        if confirm_button:
            st.session_state['current_illustrator'].register()
            st.session_state['illustrator_dict'][
                st.session_state['current_illustrator'].name
            ] = st.session_state['current_illustrator'].get_ref()
            # Invalidate shared cache so new/other sessions re-read this illustrator.
            load_illustrator_dict.clear()

            st.session_state['current_book'].illustrator = (
                st.session_state['current_illustrator'].name
            )
            st.switch_page("./pages/add_book.py")

        if edit_button:
            st.switch_page("./pages/add_illustrator.py")

    @classmethod
    def confirm_new_publisher(cls):
        confirm_button, edit_button = cls.display_confirmation(
            st.session_state['current_publisher'].to_dict(
                form_fields_only=True,
                convert_ref_fields_to_ids=True
            )
        )

        if confirm_button:
            st.session_state['current_publisher'].register()
            st.session_state['publisher_dict'][
                st.session_state['current_publisher'].name
            ] = st.session_state['current_publisher'].get_ref()
            # Invalidate shared cache so new/other sessions re-read this publisher.
            load_publisher_dict.clear()

            st.session_state['current_book'].publisher = (
                st.session_state['current_publisher'].name
            )
            st.switch_page("./pages/add_book.py")

        if edit_button:
            st.switch_page("./pages/add_publisher.py")

    @classmethod
    def confirm_new_character(cls):
        confirm_button, edit_button = cls.display_confirmation('character_details')

        if confirm_button:
            navigate_to("./pages/book_data_entry.py")

        if edit_button:
            st.switch_page("./pages/add_character.py")


@st.dialog("Are you sure?")
def confirm_submit():
    st.write(
        """
        Are you sure you want to submit this book? You will not be able to edit it again after submission,
        so please only submit once you are confident that everything is correct and complete.
        """
    )
    if st.button("Confirm", key="confirm_submit_confirm_button"):
        st.session_state.current_book.entry_status = 'completed'
        st.session_state.current_book.datetime_submitted = datetime.now(timezone.utc)
        clear_page_history()
        st.switch_page("./pages/user_home.py")
    if st.button("Cancel", key="confirm_submit_cancel_button"):
        st.rerun()
