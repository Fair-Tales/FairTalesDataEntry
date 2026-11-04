"""Shared, session-local recovery for page text/story persistence.

Missing pages use atomic creation in Page.save_edits. A collision requires a
fresh read and human review; the local draft is never replayed automatically.
"""

import logging

import streamlit as st
from google.api_core.exceptions import Conflict, GoogleAPIError

from data_structures import Page
from text_content import PageRecovery


logger = logging.getLogger(__name__)


class PageSaveRecovery:
    def __init__(self, flow, book_id, page_number):
        self.flow = flow
        self.book_id = book_id
        self.page_number = page_number
        self.scope = (flow, book_id, page_number)

    @property
    def state(self):
        return st.session_state.get('_page_save_recovery', {}).get(self.scope)

    @property
    def blocked(self):
        return self.state is not None or self.scope in st.session_state.get(
            '_page_save_refresh', {}
        )

    def save(self, page, changes, *, source, draft=None, audit=None, on_saved=None):
        """Start one operation, retaining intent before any descriptor writes."""
        if self.blocked or page.pending_edits:
            return False
        if not changes:
            return True
        record = {
            'page': page,
            'draft': dict(draft if draft is not None else {
                'text': changes.get('text', page.text),
                'contains_story': changes.get('contains_story', page.contains_story),
            }),
            'source': source,
            'audit': list(audit or []),
            'creating': not page.is_registered,
            'status': 'saving',
        }
        st.session_state.setdefault('_page_save_recovery', {})[self.scope] = record
        return self._attempt(lambda: page.save_edits(changes), record, on_saved)

    def _attempt(self, write, record, on_saved):
        try:
            write()
        except Conflict:
            # AlreadyExists is a Conflict. Never fall back to set/update after
            # an unsuccessful create, including a lost create acknowledgement.
            record['status'] = 'collision' if record['creating'] else 'failed'
            logger.warning('Page save conflict for %s', self.scope, exc_info=True)
            st.error(PageRecovery.collision if record['creating'] else PageRecovery.save_failed)
            return False
        except GoogleAPIError:
            record['status'] = 'failed'
            logger.warning('Page save failed for %s', self.scope, exc_info=True)
            st.error(PageRecovery.save_failed)
            return False

        st.session_state.get('_page_save_recovery', {}).pop(self.scope, None)
        # Audit and success side effects are outside the persistence boundary:
        # audit failure must not be represented as a failed page creation.
        if on_saved is not None:
            on_saved(record)
        return True

    def retry(self, *, on_saved=None):
        record = self.state
        if record is None or record['status'] == 'collision':
            return False
        if not self._attempt(record['page'].retry_pending_edits, record, on_saved):
            return False
        self.stage_refresh(record['page'])
        return True

    def stage_refresh(self, page):
        """Block autosave until both widgets have consumed the saved values."""
        st.session_state.setdefault('_page_save_refresh', {})[self.scope] = page

    def consume_refresh(self, *, text_key, story_key):
        """Call only before either widget is instantiated on a full rerun."""
        page = st.session_state.get('_page_save_refresh', {}).get(self.scope)
        if page is None:
            return None
        st.session_state[text_key] = page.text
        st.session_state[story_key] = page.contains_story
        st.session_state['_page_save_refresh'].pop(self.scope)
        return page

    def load_saved(self):
        record = self.state
        if record is None or record['status'] != 'collision':
            return None
        try:
            snapshot = record['page'].get_ref().get()
        except GoogleAPIError:
            logger.warning('Page reload failed for %s', self.scope, exc_info=True)
            st.error(PageRecovery.reload_failed)
            return None
        if not snapshot.exists:
            st.error(PageRecovery.page_disappeared)
            return None
        page = Page(db_object=snapshot.to_dict())
        st.session_state.setdefault('_page_retained_drafts', {}).setdefault(
            self.scope, []
        ).append(dict(record['draft']))
        self.stage_refresh(page)
        st.session_state.get('_page_save_recovery', {}).pop(self.scope, None)
        return page

    @staticmethod
    def _show_draft(element, draft):
        element.caption(PageRecovery.draft_label)
        element.code(draft.get('text', ''), language=None)
        element.caption(
            PageRecovery.draft_story if draft.get('contains_story', False)
            else PageRecovery.draft_not_story
        )

    def render_retained_draft(self, element):
        drafts = st.session_state.get('_page_retained_drafts', {}).get(self.scope, [])
        if drafts:
            with element.expander(PageRecovery.retained_draft_label):
                for draft in drafts:
                    self._show_draft(element, draft)

    def render(self, element, *, on_saved=None, on_loaded=None):
        """Return True while normal editing must remain blocked."""
        record = self.state
        if record is None:
            if self.blocked:
                st.rerun()
                return True
            return False
        collision = record['status'] == 'collision'
        element.warning(PageRecovery.collision if collision else PageRecovery.save_failed)
        self._show_draft(element, record['draft'])
        if collision:
            if element.button(
                PageRecovery.load_saved,
                key=f'{self.flow}_page_load_saved_{self.book_id}_{self.page_number}',
            ):
                page = self.load_saved()
                if page is not None:
                    if on_loaded is not None:
                        on_loaded(page)
                    st.rerun()
        elif element.button(
            PageRecovery.retry_save,
            key=f'{self.flow}_page_retry_save_{self.book_id}_{self.page_number}',
        ):
            if self.retry(on_saved=on_saved):
                st.rerun()
        return True
