"""Non-destructive, offline validation of emitted referring-expression evidence."""
from copy import deepcopy
import re
import unicodedata

EVIDENCE_VALIDATION_VERSION = "page-quote-surface-v1"


class IncompleteCharacterResponse(ValueError):
    """A retained partial response is audit evidence, never a completed stage."""
    def __init__(self, message, *, raw_response, raw_text, failure_kind):
        super().__init__(message)
        self.raw_response = raw_response
        self.raw_text = raw_text
        self.failure_kind = failure_kind


def validate_character_response_structure(result):
    """Reject a malformed cast instead of silently projecting it to empty success."""
    from text_content.pipeline_prompts import CHARACTER_STRUCTURE_INVALID
    valid = isinstance(result, dict) and isinstance(result.get("characters"), list)
    if valid:
        valid = isinstance(result.get("unresolved_mentions", []), list) and all(
            isinstance(row, dict) for row in result.get("unresolved_mentions", []))
    if valid:
        for character in result["characters"]:
            if not (isinstance(character, dict) and isinstance(character.get("name"), str)
                    and character["name"].strip()):
                valid = False
                break
            for field in ("mentions", "identity_evidence", "member_of", "editorial_labels",
                          "coreference_evidence", "language_relations"):
                if field in character and not isinstance(character[field], list):
                    valid = False
            if "aliases" in character and not isinstance(character["aliases"], (str, list)):
                valid = False
    if not valid:
        raise IncompleteCharacterResponse(
            CHARACTER_STRUCTURE_INVALID, raw_response=deepcopy(result), raw_text=None,
            failure_kind="invalid_character_structure")


def restore_character_suggestions(suggestions, raw_response=None):
    """Rehydrate the response sidecar lost when a list subclass is serialized."""
    from alias_schema import RecordedSuggestions
    return RecordedSuggestions(suggestions if isinstance(suggestions, list) else [],
                               raw_response=deepcopy(raw_response))


def _normalized(value):
    return " ".join(unicodedata.normalize("NFKC", value).split()) if isinstance(value, str) else ""


def _contains(surface, text):
    needle = _normalized(surface)
    return bool(needle) and re.search(r"(?<!\w)" + re.escape(needle) + r"(?!\w)",
                                     _normalized(text)) is not None


def validate_character_evidence(result, pages):
    """Retain the entire response and annotate missing/ambiguous evidence.

    A quote verifies that words occur, never which character they refer to.
    Embedded short forms keep their fuller phrase as evidence and never erase
    possessive owner information from a match-time representation.
    """
    output = deepcopy(result) if isinstance(result, dict) else {"raw_response": result}
    page_text = {number: text for number, text in pages}
    issues = []

    def inspect(record, path, *, surface=None):
        if not isinstance(record, dict):
            issues.append({"path": path, "status": "unsupported_surface", "record": record})
            return
        number = record.get("page", record.get("page_number"))
        text = page_text.get(number) if type(number) in (str, int) else None
        quote = record.get("quote")
        value = surface if surface is not None else record.get("surface", record.get("name"))
        if text is None:
            issues.append({"path": path, "status": "wrong_page", "page": number})
        elif not quote or not _contains(quote, text):
            issues.append({"path": path, "status": "wrong_page", "page": number,
                           "quote": quote})
        if value is not None and (not quote or not _contains(value, quote)):
            issues.append({"path": path, "status": "unsupported_surface", "surface": value})
        candidates = record.get("candidate_entity_ids", [])
        decision = record.get("decision")
        if (isinstance(candidates, list) and len(candidates) > 1) or (
                isinstance(decision, str) and decision in {"unresolved", "review_required"}):
            issues.append({"path": path, "status": "unresolved_attachment",
                           "candidate_entity_ids": candidates})
        if record.get("surface_scope") == "embedded_short_form":
            full = record.get("full_surface")
            if not full or not _contains(value, full) or not _contains(full, quote):
                issues.append({"path": path, "status": "unsupported_surface",
                               "reason": "embedded_form_requires_full_phrase_evidence"})

    characters = output.get("characters", [])
    for index, character in enumerate(characters if isinstance(characters, list) else []):
        if not isinstance(character, dict):
            issues.append({"path": f"characters[{index}]", "status": "malformed_entity"})
            continue
        prefix = f"characters[{index}]"
        mentions = character.get("mentions", [])
        mentions = mentions if isinstance(mentions, list) else []
        for ordinal, mention in enumerate(mentions):
            inspect(mention, f"{prefix}.mentions[{ordinal}]")
        aliases = character.get("aliases", [])
        for ordinal, alias in enumerate(aliases if isinstance(aliases, list) else []):
            if isinstance(alias, dict):
                inspect(alias, f"{prefix}.aliases[{ordinal}]")
            elif not any(isinstance(mention, dict) and mention.get("surface") == alias
                         for mention in mentions):
                issues.append({"path": f"{prefix}.aliases[{ordinal}]",
                               "status": "unsupported_surface", "surface": alias,
                               "reason": "missing_page_quote_evidence"})
        name = character.get("name")
        if name and not character.get("name_is_label") and not any(
                isinstance(mention, dict) and mention.get("surface") == name
                for mention in mentions):
            issues.append({"path": f"{prefix}.name", "status": "unsupported_surface",
                           "surface": name, "reason": "missing_page_quote_evidence"})
        for field in ("identity_evidence", "coreference_evidence", "language_relations"):
            records = character.get(field, [])
            for ordinal, record in enumerate(records if isinstance(records, list) else []):
                inspect(record, f"{prefix}.{field}[{ordinal}]")
        character["evidence_validation"] = [issue for issue in issues
                                             if issue["path"].startswith(prefix + ".")]
    unresolved = output.get("unresolved_mentions", [])
    for ordinal, record in enumerate(unresolved if isinstance(unresolved, list) else []):
        inspect(record, f"unresolved_mentions[{ordinal}]")
    output["evidence_validation"] = {"version": EVIDENCE_VALIDATION_VERSION,
                                     "issues": issues}
    return output
