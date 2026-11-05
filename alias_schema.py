"""Pure, versioned raw/canonical alias projection; never performs persistence.

Canonical strings are lexical projections, never identity keys or mention patterns.
"""
import hashlib
import json
from collections.abc import Mapping
from scripts.extraction_eval.normalization import normalize_name

ALIAS_SCHEMA_VERSION = "alias-mentions-v2"
CANONICAL_RULE_VERSION = "alias-canonical-v1"


def raw_surface_hash(surface):
    """Hash exact recorded bytes; do not silently trim archival input."""
    if not isinstance(surface, str):
        surface = ""
    return hashlib.sha256(surface.encode("utf-8")).hexdigest()


def derive_canonical(surface):
    """NFKC/casefold word projection preserving articles, owners and modifiers."""
    if not isinstance(surface, str):
        return ""
    return normalize_name(surface, strip_article=False) or surface


def canonical_bundle(surface, override=None):
    """Return an atomic canonical bundle; an empty override clears manual mode."""
    manual = isinstance(override, str) and bool(override.strip())
    return {
        "canonical_form": override if manual else derive_canonical(surface),
        "canonical_source": "manual" if manual else "derived",
        "canonical_override": manual,
        "canonical_rule_version": CANONICAL_RULE_VERSION,
        "canonical_input_hash": raw_surface_hash(surface),
        "alias_schema_version": ALIAS_SCHEMA_VERSION,
    }


def effective_canonical(record):
    """Read absent, incomplete or stale records safely without writing anything.

    A broken projection derives from the raw surface; punctuation-only surfaces
    retain the original string, so legacy data is never dropped. Unknown metadata
    is *not* repaired here and is preserved by the maintenance migration.
    """
    if not isinstance(record, Mapping):
        record = vars(record)
    surface = record.get("name")
    surface = surface if isinstance(surface, str) else ""
    form = record.get("canonical_form")
    source = record.get("canonical_source")
    override = record.get("canonical_override")
    valid = (
        isinstance(form, str) and bool(form.strip())
        and source in ("derived", "manual")
        and record.get("canonical_rule_version") == CANONICAL_RULE_VERSION
        and record.get("canonical_input_hash") == raw_surface_hash(surface)
        and isinstance(override, bool) and override == (source == "manual")
    )
    if valid:
        return {**{key: record.get(key) for key in canonical_bundle(surface)}, "status": "valid"}
    result = canonical_bundle(surface)
    result.update(canonical_source="legacy_fallback", status="derived_read_fallback")
    return result


def alias_storage_id(book_reference, character_reference, surface):
    """Versioned full tuple hash permits one exact surface under many identities."""
    def path(value):
        return getattr(value, "path", str(value or ""))
    payload = json.dumps([path(book_reference), path(character_reference), surface],
                         ensure_ascii=False, separators=(",", ":"))
    return "alias-v2-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def recorded_aliases(value):
    """Structured input retains every emitted surface including commas and case.

    Legacy comma input remains supported, but cannot reconstruct punctuation
    already lost by an old caller. List input is the lossless entry path.
    """
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    if not isinstance(value, (list, tuple)):
        return []
    result = []
    for entry in value:
        surface = entry.get("surface", entry.get("name")) if isinstance(entry, Mapping) else entry
        if isinstance(surface, str) and surface.strip():
            result.append(surface)
    return result


class RecordedSuggestions(list):
    """Legacy list-compatible suggestions retaining the complete local response."""
    def __init__(self, suggestions, raw_response=None):
        super().__init__(suggestions)
        self.raw_response = raw_response
