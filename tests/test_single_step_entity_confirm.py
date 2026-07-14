"""Regression lock for #168 — adding an author/illustrator/publisher must be a
SINGLE-step confirmation.

The entity form itself is the review step: its submit branch registers inline
via ``utilities.register_and_link_book_entity`` and returns straight to the
book form. Routing through the separate ``confirm_entry.py`` re-confirmation
page (the old two-stage flow) must not come back. Book and character keep their
genuinely separate multi-field review step.
"""

import inspect

from utilities import FormConfirmation
from data_structures import Author, Illustrator, Publisher


def test_confirm_entry_routes_only_book_and_character():
    assert set(FormConfirmation.forms) == {"new_book", "new_character"}


def test_no_orphaned_entity_confirm_methods():
    for orphan in (
        "confirm_new_author",
        "confirm_new_illustrator",
        "confirm_new_publisher",
    ):
        assert not hasattr(FormConfirmation, orphan)


def test_entity_forms_register_inline():
    for cls in (Author, Illustrator, Publisher):
        src = inspect.getsource(cls.to_form)
        assert "register_and_link_book_entity" in src, cls.__name__
        assert "active_form_to_confirm" not in src, (
            f"{cls.__name__}.to_form must not route to confirm_entry.py (#168)"
        )
