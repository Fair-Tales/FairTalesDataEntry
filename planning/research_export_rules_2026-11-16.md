# Research export missingness rule (C3)

Implemented in `research_export.py`, called by the corpus ZIP download in
`pages/admin.py`. This is the policy reference for the C5 codebook.

## Rule

An imported character's ethnicity, disability and plural are missing unless
there is evidence that a person set that particular current value. Missing is
Python `None` (JSON null), serialized as an empty CSV cell, never the string
"Not specified" or the Boolean False. A human-entered False stays False.

Import identity is `entered_by=pilot_import` (also a `users/pilot_import`
reference) or C1's `source=original_study_import`. The export adds `imported`
to books, characters, illustrators and publishers. It reads raw dictionaries:
it never instantiates write-through entities or rewrites database records.

Evidence for a current human value is C1's per-field
`value_provenance[field].source=human`. Without a known current provenance,
the latest edit-log entry for the same entity type, document id and field also
counts if its editor is a person and its `new_value` matches the current value
and type. An older human edit does not override a later log entry or explicit
machine provenance. Merely reviewing the book, changing another field, or
having a non-default value is not evidence of coding this field. Missing
history therefore stays missing; the export does not guess. The three flat
`*_source` columns say `human` or `missing`; stored provenance is retained.

Imported books have `source=original_study_import`. Their exported `validated`
and `validated_by_person` are true only when `validated` is actually true and
`validated_by` identifies a person, rather than the importer, databot or an
unknown account. Otherwise both are false and `validated_by` is empty. A later
real validation is preserved. Other workflow fields, including `entry_status`,
remain as recorded; completion is not evidence of human validation.

Imported books' illustrator and publisher values are labelled
`automated_lookup` in `illustrator_source` and `publisher_source`, unless the
same field-specific human evidence applies (then `human`), or the value is
null (then `missing`). The existing bibliography projection moves legacy
publisher references to `legacy_publisher`: `legacy_publisher_source` follows
that value. A replacement scoped publisher keeps its own publication evidence
and is labelled `publication_evidence`; a null scoped publisher is `missing`.
Imported illustrator/publisher rows also carry `name_source`. Book-level
sources describe the origin of that book's link; a shared entity's name may
have been edited separately. No lookup model, date or citation is invented.

Non-imported values are preserved, including False and null. If edit history
cannot be loaded, the download stops with an error rather than silently
discarding known human edits.

## Reason and scope

The importer (`scripts/import_pilot_data.py`, builders `build_character_doc`,
`build_book_doc`, `build_named_doc` and `lookup_book_metadata`) supplies
ethnicity/disability/plural defaults absent from the original study, claims
validation under its own account, and fills illustrator/publisher from an
automated web lookup. These are not human codes or human validation. This
code-based finding was recorded in review §5.5; no corpus counts were measured
again for C3. Export-only correction prevents misleading analysis without
changing the source record or losing later human work.

Writer audit: `pages/admin.py` supplies the research corpus CSV ZIP. Its user
and feedback CSVs do not export corpus codes. `pages/results_dashboard.py`
has no corpus download writer. Script writers produce evaluation artifacts,
operational audits, caches or review pages, rather than another research
corpus export.

The evaluation snapshot deliberately retains raw records, including these
defaults (`scripts/extraction_eval/snapshot.py`, `_build_book`). Character
attribute scoring explicitly includes only gender, human and protagonist
(`scripts/extraction_eval/scoring.py`, `score_character_attributes`); ethnicity,
disability and plural never enter its code denominators. Validation flags are
not scoring inputs. Separate metadata agreement does compare illustrator and
publisher, whose snapshot provenance is already `pilot_ai_web_lookup`; that is
agreement with automated metadata, not independent human accuracy. C3 changes
neither snapshots nor scoring. Synthetic regression tests exercise this boundary.
