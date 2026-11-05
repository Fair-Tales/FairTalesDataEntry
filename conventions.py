"""Versioned, pure research projections over retained extraction evidence.

Changing a projection does not require inference: archival regions, page kinds,
explicit contributor roles and referring-expression evidence remain available.
"""
from copy import deepcopy

CONVENTION_VERSION = "pipeline-conventions-v1"
DEFAULT_CONVENTIONS = {
    "C1": {"version": "all-print-regions-v1", "status": "provisional",
           "rule": "Archive all book-authored lettering; analyse narrative, informational and in-scene illustration lettering separately from paratext."},
    "C2": {"version": "main-content-v1", "status": "provisional",
           "rule": "Main narrative, informational and wordless narrative pages participate; bibliographic matter, decorative endpapers, glossary/index and supplemental activities do not."},
    "C3": {"version": "names-descriptions-v1", "status": "accepted",
           "rule": "Names and descriptive referring expressions are lexical aliases; pronouns, body parts and editorial labels are separately retained evidence."},
    "C3_population": {"version": "categorized-entities-v1", "status": "provisional",
                      "rule": "Retain individuals, collectives, generic groups and discourse roles in separate strata with unresolved identity."},
    "C4": {"version": "scoped-publication-v1", "status": "accepted",
           "rule": "Keep photographed-edition imprint and publishing house distinct; preserve first-publication and edition events and source evidence."},
    "C5": {"version": "pilot-claims-v1", "status": "provisional",
           "rule": "Existing positives and consistency metrics support exploratory pilot-only claims; dev20 aliases-only recall has ten positives and is a smoke test."},
    "C6": {"version": "credited-retellers-v1", "status": "provisional",
           "rule": "Analytical authors include explicitly credited authors and retellers; retain all original contributor roles."},
    "C7": {"version": "evidenced-short-category-collective-v1", "status": "provisional",
           "rule": "Also emit bare/short contiguous printed referring heads with full phrase and owner evidence; label embedded_short_form when no independent occurrence exists. Category words must be printed and refer in context. Collective occurrences stay with the collective; membership alone never attaches them to an individual. Ambiguous heads retain alternative candidates."},
}


def convention_metadata():
    return {"convention_version": CONVENTION_VERSION,
            "conventions": deepcopy(DEFAULT_CONVENTIONS)}


def project_regions(text, regions, *, include_illustration_lettering=True):
    """Project retained tagged regions; legacy text remains explicitly unclassified."""
    allowed = {"narrative", "informational"}
    if include_illustration_lettering:
        allowed.add("illustration_lettering")
    valid = isinstance(regions, list) and all(
        isinstance(region, dict) and isinstance(region.get("text"), str)
        and isinstance(region.get("kind"), str)
        and region.get("kind") in allowed | {"illustration_lettering", "paratext"}
        for region in regions)
    archive = text if isinstance(text, str) else ""
    # Missing/malformed regions never erase old archival text or pretend to have
    # separated lettering that the old parser did not retain.
    if not valid or "\n\n".join(region["text"] for region in regions) != archive:
        projected, status = archive, "legacy_unclassified"
    else:
        projected = "\n\n".join(region["text"] for region in regions
                                if region.get("kind") in allowed)
        status = "projected"
    return {"analysis_text": projected, "projection_status": status,
            "analysis_projection_version": ("main-regions-with-lettering-v1"
                if include_illustration_lettering else "main-regions-without-lettering-v1"),
            "convention_version": CONVENTION_VERSION,
            "transcription_convention_version": DEFAULT_CONVENTIONS["C1"]["version"]}


def project_analysis_text(page, *, include_illustration_lettering=True):
    return project_regions(page.get("text", ""), page.get("regions"),
                           include_illustration_lettering=include_illustration_lettering)


def region_revision_payload(evidence):
    """Region edits invalidate derived cast inputs; legacy hashes stay unchanged."""
    if not isinstance(evidence, dict) or not evidence.get("regions"):
        return None
    return {"regions": deepcopy(evidence.get("regions")),
            "analysis_projection_version": "main-regions-with-lettering-v1"}


def project_authors(credits, *, include_retellers=True):
    roles = {"author", "reteller"} if include_retellers else {"author"}
    names = []
    for credit in credits or []:
        if not isinstance(credit, dict):
            continue
        name = credit.get("name")
        if (isinstance(credit.get("role"), str) and credit.get("role") in roles
                and isinstance(name, str) and name and name not in names):
            names.append(name)
    return names


def project_story(page_type, *, include_supplemental=False):
    """Revisit C2 from preserved page kinds; uncertain remains an abstention."""
    excluded = {"cover", "title", "copyright", "dedication", "contents", "endpaper",
                "blank", "glossary_index", "supplemental_activity", "other_back_matter"}
    included = {"narrative", "informational", "wordless_narrative", "story"}
    if not isinstance(page_type, str) or page_type not in excluded | included:
        return None
    return page_type in included or (
        include_supplemental and page_type == "supplemental_activity")
