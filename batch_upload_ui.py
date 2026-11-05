"""Streamlit-only batch controls; durable work is delegated to batch_pipeline."""
import os

import streamlit as st

import batch_pipeline as batches
from data_structures.batch import BatchBook
from image_processing import make_display_copy
from text_content import BatchBookEntry, BookPhotoEntry
from text_content.batch_pipeline import BatchPipeline as Text
from utilities import get_s3_filesystem, get_ai_settings, load_book_dict


STATE_KEY = 'durable_batch_id'


def enabled():
    return os.environ.get('BATCH_UPLOAD_DURABLE_ENABLED', '').lower() in ('1', 'true', 'yes')


def _label(value):
    return Text.state_labels.get(value, Text.not_available)


def _show_isbn_lookup_failures(metadata_rows):
    count = sum(
        (metadata.get('isbn_lookup') or {}).get('status') == 'failed'
        for metadata in metadata_rows
    )
    if count:
        st.warning(Text.isbn_lookup_failures.format(count=count))


def _open_batch(batch_id):
    st.session_state[STATE_KEY] = batch_id
    st.rerun()


def _owner():
    return st.session_state.get('username', '')


def _db():
    return st.session_state['firestore'].connect_book()


def _make_book(metadata, ordinal):
    # Reuse the existing fuzzy matching flow; no new people are auto-committed.
    from pages.add_books_batch import _match_existing
    book = BatchBook()
    book.title = (metadata.get('title') or '').strip() or BatchBookEntry.untitled_title.format(n=ordinal)
    year = metadata.get('published_year')
    if isinstance(year, int):
        book.published = year
    for field, source, dictionary in (
        ('author', 'authors', 'author_dict'), ('illustrator', 'illustrators', 'illustrator_dict')
    ):
        _match_existing(book, field, metadata.get(source) or [], dictionary)
    publisher = metadata.get('publisher')
    _match_existing(book, 'publisher', [publisher] if publisher else [], 'publisher_dict')
    return book


def render_discovery(db):
    saved = batches.discover_batches(db, _owner())
    if saved:
        st.subheader(Text.resume_header)
        by_id = {row['batch_id']: row for row in saved}
        choice = st.selectbox(
            Text.resume_label, list(by_id), key='batch_resume_selectbox',
            format_func=lambda value: Text.resume_option.format(
                batch_id=value[:12], state=_label(by_id.get(value, {}).get('state')),
                count=by_id.get(value, {}).get('source_count', 0)),
        )
        if st.button(Text.resume_button, key='batch_resume_button'):
            _open_batch(choice)
    if st.button(Text.new_batch, key='batch_new_button'):
        batch = batches.create_batch(db, _owner(), get_ai_settings())
        _open_batch(batch.document_id)


def _upload(batch, fs, client):
    from photo_upload import render_batch_upload
    st.write(BatchBookEntry.instructions)
    finished = render_batch_upload(
        fs, batch.document_id,
        on_chunk=lambda _chunk, sources: batches.stage_sources(batch, fs, sources),
    )
    st.button(BatchBookEntry.detect_button, disabled=True, key='add_books_batch_detect_button')
    if finished is not None:
        batches.freeze_sources(batch, finished)
        st.rerun()


def _review(batch, fs, client):
    groups = batches.groups_for(batch._db, batch.document_id)
    source_map = {source.position: source for source in batches.sources_for(batch._db, batch.document_id)}
    st.header(BatchBookEntry.review_header)
    method = {'black_frame': BatchBookEntry.method_black_frame,
              'cover_page': BatchBookEntry.method_cover_page}.get(batch.split_method, BatchBookEntry.method_single)
    st.info(method.format(count=len(groups)))
    _show_isbn_lookup_failures(group.metadata or {} for group in groups)
    for group in groups:
        metadata = group.metadata or {}
        with st.expander(BatchBookEntry.book_summary.format(
                n=group.ordinal, title=metadata.get('title') or BatchBookEntry.untitled_title.format(n=group.ordinal),
                pages=len(group.positions)), expanded=True):
            if group.metadata_state != 'done':
                st.warning(Text.metadata_pending)
            if (metadata.get('isbn_lookup') or {}).get('status') == 'failed':
                st.warning(BookPhotoEntry.isbn_lookup_failed)
            title = st.text_input(Text.review_title, value=metadata.get('title') or '',
                                  key=f'batch_title_{group.document_id}')
            if title != metadata.get('title', ''):
                group.metadata = {**metadata, 'title': title}
            if group.positions:
                first, last = group.positions[0], group.positions[-1]
                st.caption(Text.boundaries.format(first=first, last=last))
                columns = st.columns(2)
                for column, position in zip(columns, (first, last)):
                    source = source_map.get(position)
                    if source:
                        column.image(make_display_copy(batches._read(fs, source.key)), width='stretch')
            for source, label in (('authors', BatchBookEntry.detail_author),
                                  ('illustrators', BatchBookEntry.detail_illustrator)):
                if metadata.get(source):
                    st.write(label.format(value=', '.join(metadata[source])))
            if metadata.get('publisher'):
                st.write(BatchBookEntry.detail_publisher.format(value=metadata['publisher']))
            if metadata.get('published_year'):
                st.write(BatchBookEntry.detail_year.format(value=metadata['published_year']))
    accepted = st.checkbox(Text.review_accept, key=f'batch_boundary_confirmation_{batch.document_id}')
    if st.button(BatchBookEntry.confirm_button.format(count=len(groups)), disabled=not accepted,
                 key='add_books_batch_confirm_button'):
        owner_ref = st.session_state['firestore'].username_to_doc_ref(_owner())
        batches.confirm_batch(batch, fs, client, _make_book, owner_ref)
        load_book_dict.clear()
        st.rerun()


def _show_progress(batch, fs, client):
    import background_pipeline as pipeline
    batches.finalize_ready(batch, fs)
    rows = batches.progress_snapshot(batch)
    _show_isbn_lookup_failures(row.get('metadata') or {} for row in rows)
    for row in rows:
        progress = row.get('progress') or {}
        title = (row.get('metadata') or {}).get('title') or BatchBookEntry.untitled_title.format(n=row['ordinal'])
        st.write(Text.progress_row.format(
            title=title, state=_label(progress.get('status') or row['state']),
            done=progress.get('done', progress.get('pages_done', 0)), total=row['pages'],
            failed=progress.get('failed', progress.get('pages_failed', 0)),
            characters=_label(progress.get('character_outcome') or progress.get('character_status')),
            finalization=_label(row['state']),
        ))
        if progress.get('character_coverage') == 'incomplete':
            from text_content import EnterText
            st.warning(EnterText.batch_detection_incomplete)
        if row.get('error'):
            st.warning(Text.save_failed.format(error=row['error']))
        job_id = row.get('job_id')
        if job_id and progress.get('status') in ('partial', 'failed'):
            if st.button(Text.retry_book, key=f'batch_retry_{row["group_id"]}'):
                if pipeline.retry_failed_pages(batch._db, job_id):
                    from data_structures.batch import BatchGroup
                    group = BatchGroup.load(batch._db, row['group_id'])
                    group.state = 'queued'
                    pipeline.resume_job(fs, batch._db, client, job_id)
                st.rerun()
        for failure in ('worker_error', 'staging_error', 'character_error'):
            if progress.get(failure):
                st.warning(Text.save_failed.format(error=progress[failure]))
        if job_id and progress.get('status') not in ('complete', 'cancelled'):
            if st.button(Text.cancel_book, key=f'batch_cancel_{row["group_id"]}'):
                pipeline.cancel_job(batch._db, job_id)
                st.rerun()
        if row['state'] == 'finalized':
            if st.button(Text.review_book, key=f'batch_review_{row["group_id"]}'):
                from utilities import clear_entity_form_state
                clear_entity_form_state('book_form_')
                for key in ('book_pages_dict', '_pages_dict_book_id', '_detected_characters',
                            '_batch_character_result', '_batch_character_review'):
                    st.session_state.pop(key, None)
                snap = batch._db.collection('books').document(row['book_id']).get()
                st.session_state['current_book'] = BatchBook(snap.to_dict())
                st.switch_page('./pages/enter_text.py')


def _progress(batch, fs, client):
    st.header(Text.progress_header)
    st.info(Text.progress_intro)
    st.caption(Text.unknown_response)
    if st.button(Text.resume_work, key=f'batch_resume_work_{batch.document_id}'):
        owner_ref = st.session_state['firestore'].username_to_doc_ref(_owner())
        batches.confirm_batch(batch, fs, client, _make_book, owner_ref)
    if st.button(Text.refresh, key='batch_progress_refresh_button'):
        st.rerun()

    @st.fragment(run_every='5s')
    def dashboard():
        _show_progress(batch, fs, client)
    dashboard()


def render(client):
    if not enabled():
        st.info(Text.rollout_disabled)
        st.button(BatchBookEntry.detect_button, disabled=True, key='add_books_batch_detect_button')
        return
    batch_id = st.session_state.get(STATE_KEY)
    try:
        db = _db()
        if not batch_id:
            render_discovery(db)
            return
        batch = batches.load_batch(db, batch_id, _owner())
        fs = get_s3_filesystem()
        st.caption(Text.saved.format(batch_id=batch.document_id[:12]))
        if batch.state == 'uploading':
            _upload(batch, fs, client)
        elif batch.state == 'detecting':
            st.info(Text.detecting_resume)
            st.caption(Text.unknown_response)
            if st.button(Text.continue_detection, key='batch_continue_detection_button'):
                batches.detect_groups(batch, fs, client)
                st.rerun()
        elif batch.state == 'review':
            _review(batch, fs, client)
        else:
            _progress(batch, fs, client)
        if st.button(BatchBookEntry.done_home_button, key='batch_home_button'):
            st.session_state.pop(STATE_KEY, None)
            st.switch_page('./pages/user_home.py')
    except batches.SERVICE_ERRORS as exc:
        st.error(Text.save_failed.format(error=str(exc)))
