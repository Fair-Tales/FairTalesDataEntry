"""Admin/team page: reconstruct orphaned books from their S3 photos (#122).

Some ``sawimages/{folder}/`` folders hold a complete set of page photos but have
NO Firestore book record (the book doc was deleted/lost). Rather than delete
those images, this page rebuilds the book from them: the admin picks an orphaned
folder, its existing S3 photos are fetched (NO re-upload) and fed through the
shared :func:`book_reconstruction.reconstruct_book_from_photos` core — metadata
extraction, per-page OCR and #52 character/alias detection — creating the Book +
Pages + Characters and dropping it into the validation queue (#47) for human
review.

Gated to team members and admins (#83), mirroring the validation page.
"""

import anthropic
import streamlit as st

from utilities import (
    page_layout,
    check_authentication_status,
    is_team_or_above,
    get_s3_filesystem,
    get_anthropic_client,
)
from text_content import ReconstructOrphans, PhotoUpload
from book_reconstruction import (
    list_orphan_folders,
    fetch_folder_photos,
    reconstruct_book_from_photos,
)

check_authentication_status()

# Reconstruction is a team-member-and-above tool (#83), like validation itself.
if not is_team_or_above():
    page_layout()
    st.error(ReconstructOrphans.not_authorised)
    st.stop()

page_layout(current_page="./pages/reconstruct_orphans.py")

st.header(ReconstructOrphans.header)
st.write(ReconstructOrphans.intro)

ai_available = "ANTHROPIC_API_KEY" in st.secrets
if not ai_available:
    st.warning(ReconstructOrphans.no_api_key)

# A successful reconstruction reruns the page; carry its summary across the rerun
# so the feedback survives (and the orphan list is rebuilt fresh below).
result_flash = st.session_state.pop("reconstruct_result_flash", None)
if result_flash:
    st.success(ReconstructOrphans.success_header)
    st.write(
        ReconstructOrphans.success_summary.format(
            title=result_flash["title"],
            pages=result_flash["page_count"],
            characters=result_flash["characters_created"],
            aliases=result_flash["aliases_created"],
        )
    )
    if result_flash.get("moved"):
        st.info(
            ReconstructOrphans.moved_notice.format(
                photos_folder=result_flash["photos_folder"],
                source_folder=result_flash["source_folder"],
            )
        )
    # Pages the AI couldn't read (#132): keep the message simple (count + page
    # numbers); the raw errors are in the extraction_errors debug log.
    failed = result_flash.get("extraction_failures") or []
    if failed:
        st.warning(PhotoUpload.extraction_partial_fail.format(
            failed=len(failed), total=result_flash["page_count"],
            pages=", ".join(str(p) for p in failed),
        ))
    st.page_link("pages/validation.py", label=ReconstructOrphans.validation_link_label)
    st.divider()


def _load_orphans():
    """Scan S3 for orphaned folders and cache the result in session state."""
    with st.spinner(ReconstructOrphans.scanning):
        st.session_state["_orphan_folders"] = list_orphan_folders()


if st.button(ReconstructOrphans.refresh_button, key="reconstruct_refresh_button"):
    _load_orphans()

# Build the list on first visit; subsequent reruns reuse the cached scan until the
# admin refreshes or a reconstruction invalidates it.
if "_orphan_folders" not in st.session_state:
    _load_orphans()

orphans = st.session_state.get("_orphan_folders", [])

if not orphans:
    st.info(ReconstructOrphans.none_found)
    st.stop()

st.write(ReconstructOrphans.found_count.format(count=len(orphans)))

folder_labels = {
    folder: ReconstructOrphans.folder_option.format(folder=folder, count=count)
    for folder, count in orphans
}
selected_folder = st.selectbox(
    ReconstructOrphans.select_label,
    options=[folder for folder, _count in orphans],
    format_func=lambda folder: folder_labels.get(folder, folder),
    key="reconstruct_select_folder",
)

if st.button(
    ReconstructOrphans.reconstruct_button,
    disabled=not ai_available,
    key="reconstruct_run_button",
):
    fs = get_s3_filesystem()
    photos = fetch_folder_photos(fs, selected_folder)
    if not photos:
        st.warning(ReconstructOrphans.no_photos_in_folder)
        st.stop()

    client = get_anthropic_client()
    try:
        with st.status(ReconstructOrphans.status_header, expanded=True) as status:
            # Frequent label updates keep the browser websocket fed during the long
            # synchronous AI pipeline (mirrors the #110 st.status pattern).
            result = reconstruct_book_from_photos(
                photos,
                client,
                fs=fs,
                source_folder=selected_folder,
                progress=lambda message: status.update(label=message),
            )
            status.update(label=ReconstructOrphans.success_header, state="complete")
    except anthropic.AnthropicError as exc:
        st.error(ReconstructOrphans.error.format(error=exc))
    except ValueError as exc:
        st.error(ReconstructOrphans.error.format(error=exc))
    else:
        # Stash a summary and rerun: the reconstructed book is no longer an orphan,
        # so the list must be rebuilt without it.
        st.session_state["reconstruct_result_flash"] = {
            "title": result["title"],
            "page_count": result["page_count"],
            "characters_created": result["characters_created"],
            "aliases_created": result["aliases_created"],
            "moved": result["moved"],
            "photos_folder": result["photos_folder"],
            "source_folder": result["source_folder"],
            "extraction_failures": result["extraction_failures"],
        }
        st.session_state.pop("_orphan_folders", None)
        st.rerun()
