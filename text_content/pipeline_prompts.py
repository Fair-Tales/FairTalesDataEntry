"""Frozen evidence-rich prompts and request provenance; no service access."""
import hashlib
import json
from pathlib import Path

from conventions import convention_metadata

PACK_VERSION = "pipeline-evidence-v1"
PROMPT_VERSION = "pipeline-prompts-v2"
CHARACTER_TRUNCATED = "Character response reached the output limit; partial evidence retained."
CHARACTER_PARTIAL = "Character response was incomplete; partial evidence retained."
CHARACTER_STRUCTURE_INVALID = "Character response has an invalid structure; raw evidence retained."
RULES = {
    'character_detection': """\
You identify characters and their referring expressions in ONE supplied book.
Use only the supplied main-content pages. The examples below are separate books;
their people, wording and relationships must not be imported into the target.

Find all text-evidenced entities: named or unnamed people, animals, acting or
speaking personified objects, and groups acting collectively. A mention can
identify a character even if it appears once. Ordinary objects, places, food,
and a thing mentioned only as a comparison are not characters on that basis.
For informational books, record generic organism groups separately with
entity_kind="generic_group"; do not silently equate them with an individual.
Record implied narrator/reader roles with entity_kind="discourse_role" and
explicit role evidence. Do not infer a visual-only character from world knowledge.

For every name or alias, copy a referring expression from the supplied text and
give its page and a short exact supporting quote. Do not create nicknames,
expand names, translate Mum to mom, pluralize an individual, or add familiar
names from your knowledge of the book. Keep punctuation/case as printed.
Pronouns provide coreference evidence; do not put he/she/it/they/I/me/you/us in aliases.
A body part such as "long grey trunk" is not an alias for its owner. Preserve it
as coreference evidence when useful, without adding it to lexical aliases.

Merge only when the text supports identity: explicit naming, unambiguous
continuity, apposition, or a clearly identified actor under a disguise. Shared
species, gender, similar names, a simile, possession, or membership of a group
does not establish identity. An individual and a group containing that individual
are separate entities. A place and its inhabitants are separate.

Choose the clearest attested name as name. If an unnamed entity requires a
display label, set name_is_label=true and retain the attested mentions separately;
the display label is not a lexical alias. For unresolved identity, preserve the
mention and alternative identity links without committing either a merge or a
split. Put candidate IDs and evidence in unresolved_mentions; an unresolved
mention is retained even when no human review is available.

Use one canonical display name per supported referent. aliases preserves every
attested referring form, including article, possessive and case variants. Copy
raw forms; the adapter derives a separate canonical field. Do not shorten or
suppress an expression to make this list tidy. Keep exact occurrences in mentions;
matching handles articles and deduplicates repeated hits on the same occurrence.
Retain modifier variants such as "the whale" and "the little whale" when both
occur; shared words do not license merging characters. Under C7 also emit a bare/short contiguous printed referring head alongside the
fuller expression. If it occurs only inside that fuller phrase, label it
surface_scope="embedded_short_form" and retain the full phrase, possessive owner
and exact page/quote evidence. Do not claim a separate standalone occurrence. Named possessives carry
owner evidence and must not be discarded as meaningless wording.

For identity decisions compare explicit naming/apposition, grammatical number,
co-occurring actors, different actions, and attributed dialogue across pages.
Number is evidence, not proof: reindeer has the same singular/plural spelling.
Two mentions in a sentence can be coreferential (apposition); distinct subjects
acting toward one another are stronger evidence of different individuals.
Different quoted turns alone do not prove different speakers. Record the exact
speech/answering cues and do not infer an addressee merely from proximity.

Before returning, check expressions shared across character records. Merge duplicate
records only with explicit identity evidence and retain their former IDs. Keep a
collective and its members separate, linked by member_of. "The reindeer" can name
the herd while "the youngest reindeer" names a member; do not count the herd's
mentions as that individual's. A generic "girl" or "children" is not a globally
unique lookup key. Attach each occurrence using its page and local context. If
identity is unresolved, retain candidate IDs and exclude that occurrence from
exclusive per-character totals. A parenthetical display disambiguator is a label,
not an attested lexical alias. An elided list of names is an editorial summary,
not one printed referring expression.

Use gender only where explicit language supports it; otherwise Non-specific.
Use Transgender only if explicitly stated. Human means a person, not an animal
or personified object. plural identifies a collective; protagonist requires clear
main-character evidence. Do not infer attributes from a stereotyped name or job.

Return JSON only:
{"characters":[{"id":"c1","name":"...","name_is_label":false,
"entity_kind":"individual","aliases":[],"gender":"Non-specific",
"human":false,"plural":false,"protagonist":false,
"mentions":[{"surface":"...","page":1,"quote":"..."}],
"identity_evidence":[],"member_of":[],"editorial_labels":[]}],"unresolved_mentions":[]}

entity_kind is individual, collective, generic_group, or discourse_role.
identity_evidence contains page/quote records supporting nontrivial merges.
unresolved_mentions contains surface/page/quote/candidate_entity_ids.
Return empty lists if no eligible entities/mentions occur.

C7 coverage check: inspect every page again for omitted short/bare forms,
category terms (including hypernyms), and descriptive variants. A category word
must itself be printed and refer to the entity in that context; never invent a
hypernym from knowledge of its species or contents. A collective reference
denotes the collective, even when a reference annotation attaches it to a member.
Preserve membership links and alternative candidate IDs when its local referent
is ambiguous. Never turn group membership into identity or copy all group
occurrences onto one individual. Bare heads need context; retain competing
candidates rather than claiming exclusive attribution. Possessives are never
stripped at match time. Add the shorter surface as evidence-backed data.
Aliases may be structured entries: {"surface":"...","page":1,"quote":"...",
"surface_scope":"full_expression","full_surface":"...",
"candidate_entity_ids":[]}. full_expression and embedded_short_form are the
surface_scope values. Keep every raw emitted alias and uncertainty record.
Preserve speech, narration_about and language_addressed_to relations separately
in language_relations, with page/quote, speaker/addressee candidate IDs and
unknown values when the text does not resolve them. Pronouns and body parts may
be retained in coreference_evidence, never lexical aliases.""",
    'page_extraction': """\
Transcribe the supplied photographed book page or spread. Read the visible book,
not your memory of it. Inspect the whole page, including margins, small isolated
lines, speech bubbles, captions, decorative, angled and curved lettering, and
text printed within illustrations. Include front/back matter and book-authored
signs, labels and sound effects. Ignore the photographed room, hands, library
stickers and later handwritten annotations. Exclude standalone page-number folios;
retain numbers that are part of the content.

Transcribe every visible occurrence once. Preserve spelling, invented words,
punctuation and repeated words/letters as seen. Do not correct the book's wording,
complete a sentence from memory, describe an illustration, or continue beyond
the image. For repeated-letter strings, follow each visible line and stop at its
visible end; do not keep generating the same letter. If uncertain about a span,
use [reading?], or [?] if none of it can be read, retaining readable surroundings.

For facing pages, read the left page fully before the right unless a continuous
text block visibly spans both. Within each page follow its visible reading order.
Before returning, check for a missed final line, second dialogue block, or small
lettering. This check must not add any word you cannot see.

Return JSON only. regions is an ordered list with kind narrative, informational,
illustration_lettering, or paratext and text containing its exact transcription.
text joins those region texts in order with blank lines. has_text is true exactly
when there is transcribable non-folio book text, including an uncertainty marker
for a visibly present unreadable word. With no such text use text="", regions=[]
and has_text=false. A false has_text must never accompany a nonempty text.

Classify page_type independently: cover, title, copyright, dedication, contents,
narrative, informational, wordless_narrative, endpaper, blank, glossary_index,
supplemental_activity, other_back_matter, or uncertain.
is_story_page is true for narrative, informational and wordless_narrative under
the supplied main-content convention. Typography or absence of text alone cannot
decide this. Set story_needs_context=true if sequence context is necessary;
otherwise false. This flag does not suppress transcription.

{"has_text":true,"text":"...","regions":[{"kind":"narrative","text":"..."}],
"is_story_page":true,"page_type":"narrative","story_needs_context":false}""",
    'story_classification': """\
Classify each photographed page of this ONE book using the ordered thumbnails
and supplied OCR. OCR may be incomplete; absence of OCR is not absence of story.
Do not transcribe, correct, or delete text in this task.

The research convention is MAIN CONTENT: narrative scenes and the main
informational/instructional work count; a wordless scene continuing that work
also counts. Covers, title/copyright/dedication/contents pages, decorative
endpapers, genuinely blank pages, glossary/index and supplemental activities
after the main work do not count. An illustrated page can be either main content
or endpaper. Decide from its visual content and sequence, not its page number
alone. Do not fill a continuous story range across blank or bibliographic pages.

A back-cover synopsis remains paratext even when it names the characters and
describes a plot. "THE END" alone is a closing marker, not enough to establish a
narrative scene; an actual scene or concluding dialogue can still be main content.
An in-scene sound effect/sign is transcribed elsewhere, and counts as part of
main content if its illustration is a narrative scene. Lettering on decorative
endpapers does not make them narrative. Do not erase wordless narrative scenes.

For nonfiction distinguish the book's main instructional sequence from a
separately introduced appendix of activities. An introduction explaining how to
use the book and a glossary/index are paratext under this convention.

Return JSON only, with exactly one result for every supplied page_number:
{"pages":[{"page_number":1,"contains_story":false,"page_type":"cover",
"decision":"resolved","evidence":"short visible/sequence basis"}]}
decision is resolved or review_required. If evidence cannot resolve a page,
return contains_story=null, decision=review_required; do not invent certainty.""",
    'locate_key_pages': """\
Locate the internal title page and the copyright/imprint page among these ordered
photographs. Return the supplied image POSITION, not a printed folio. The title
page normally has the full title and credits; prefer it over a half-title with
only part of the title, or a decorative cover. Use the cover only if no internal
title page is visible. The copyright page contains publication/edition statements,
copyright notices, ISBN or publisher details and can occur near either end.
Both roles may occupy the same photograph. Do not assume they are adjacent.
Return JSON only: {"title_page":integer_or_null,"copyright_page":integer_or_null}.""",
    'joint_metadata_extraction': """\
Extract bibliographic evidence from the supplied cover/title/copyright photographs
of ONE physical edition. Use only visible evidence. Multiple statements may refer
to different editions, territories or roles; preserve that distinction.

title: the complete work title, including words distributed across different lines
or artwork. Ignore series advertising. Prefer the full internal title, corroborated
by the cover. Report a conflict if visible sources disagree; do not silently choose
the first nonempty string or supply a famous title from memory.

credits: list every visible person and explicit role: author, illustrator, reteller,
translator, original_author, editor, designer, or unspecified. Exclude credits
in advertisements for another book. "Illustrated by", "pictures by" and "art by" establish
illustrator. Text/illustration copyright statements can establish those roles.
An unexplained name or single ambiguous "by" does not establish both roles. A
reteller is retained as reteller and included in the authors projection. Copy names
exactly; use corroborating print on another supplied page to resolve stylized text.
Never invent a missing first name or infer an illustrator from prior knowledge.

publisher_statements: extract each publication statement with publisher/imprint,
year, scope (original, territorial_first, edition, reprint, copyright, or unknown),
and territory if printed. Distinguish an imprint from a parent, printer,
distributor, rights holder or "by arrangement with" organization. Preserve the raw
publisher string and its role. Do not replace the photographed-edition imprint
with a familiar parent company. Copyright year alone is copyright evidence, not
proof of first publication. The earliest printed date may refer to another work
or component; never use min(all years).

isbns: all visible ISBN strings with format/edition qualifiers if present.
Do not repair digits by guessing. Missing facts are null or empty lists.
Give a source page_number and an exact short quote for each value.

Return JSON only:
{"title":{"value":null,"evidence":[]},
"credits":[{"name":"...","role":"author","page_number":1,"quote":"..."}],
"publisher_statements":[{"publisher":"...","publisher_role":"imprint",
"year":2000,"scope":"edition","territory":null,"page_number":2,"quote":"..."}],
"isbns":[{"raw":"...","qualifier":null,"page_number":2,"quote":"..."}],
"conflicts":[]}

title.evidence is a list of {page_number:integer,quote:string} records.
publisher_role is imprint, publisher, parent, printer, distributor, rights_holder,
or unknown. Publisher and year may be null. conflicts is a list of
{field:string,candidates:[{value:any,page_number:integer,quote:string}],reason:string}.
Keep original_author, editor and designer separate from author/reteller projections.

Under accepted C4, publisher/imprint and its publishing house are separate
scoped values, never synonyms. Preserve an explicit parent relation as evidence;
do not infer an unprinted publishing house. Include publishing_house and
imprint fields on a publication statement only when supported by the supplied
photographs, alongside publisher, publisher_role, year, scope and quote.""",
}


def load_example_pack(*, held_out_book_ids=(), held_out_groups=()):
    pack = json.loads(Path(__file__).with_name("pipeline_examples.json").read_text())
    development = set(pack.get("development_book_ids", []))
    development_groups = set(pack.get("development_holdout_groups", []))
    held_books, held_groups = set(held_out_book_ids), set(held_out_groups)
    for example in pack.get("examples", []):
        if (example.get("book_id") not in development
                or example.get("holdout_group") not in development_groups
                or example.get("book_id") in held_books
                or example.get("holdout_group") in held_groups):
            raise ValueError("Example leakage: only frozen development groups are permitted")
        source = example.get("source_text", "")
        if hashlib.sha256(source.encode("utf-8")).hexdigest() != example.get("source_text_sha256"):
            raise ValueError("Example source text hash mismatch")
        if not all(quote in source for quote in example.get("quotes", [])):
            raise ValueError("Example quotation is not verbatim source text")
        if not example.get("source_image_sha256"):
            raise ValueError("Example lacks frozen source image provenance")
    return pack


def example_pack_text(stage, variant="rules-plus-icl"):
    if variant not in {"rules-only", "rules-plus-icl"}:
        raise ValueError("Unknown prompt variant")
    if variant == "rules-only":
        return ""
    blocks = []
    for example in load_example_pack().get("examples", []):
        if example.get("stage") != stage:
            continue
        quotes = "\n".join(json.dumps(quote, ensure_ascii=False) for quote in example["quotes"])
        blocks.append(
            f"EXAMPLE {example['id']} — {example['book_id']}, photograph {example['page_number']}\n"
            f"Status: {example['approval_status']}; quoted excerpts unless explicitly stated complete.\n"
            f"Exact source text (JSON strings preserving original line breaks):\n{quotes}\n"
            f"Expected decisions: {example['decision']}")
    return "\n\n".join(blocks)


def prompt_for(stage, variant="rules-plus-icl"):
    if stage not in RULES:
        raise ValueError("Unknown pipeline prompt stage")
    prompt = RULES[stage] + "\n\nConvention version: " + convention_metadata()["convention_version"]
    examples = example_pack_text(stage, variant)
    if examples:
        prompt += "\n\n" + examples
    if stage == "character_detection":
        # The existing caller uses .format; JSON schema braces stay literal.
        prompt = prompt.replace("{", "{{").replace("}", "}}")
        prompt += "\n\nTarget book pages:\n{pages_json}"
    return prompt


def prompt_request_manifest(stage, variant="rules-plus-icl", rendered_prompt=None):
    examples = example_pack_text(stage, variant)
    rendered = prompt_for(stage, variant) if rendered_prompt is None else rendered_prompt
    return {"prompt_version": PROMPT_VERSION, "example_pack_version": PACK_VERSION,
            "prompt_variant": variant, "stage": stage, "encoding": "utf-8",
            "example_pack_utf8": examples,
            "example_pack_sha256": hashlib.sha256(examples.encode("utf-8")).hexdigest(),
            "rendered_prompt_utf8": rendered,
            "rendered_prompt_sha256": hashlib.sha256(rendered.encode("utf-8")).hexdigest(),
            "rendered_prompt_byte_length": len(rendered.encode("utf-8")),
            "examples": [{key: value for key, value in example.items()
                          if key not in {"source_text", "decision"}}
                         for example in load_example_pack()["examples"]
                         if variant == "rules-plus-icl" and example.get("stage") == stage],
            **convention_metadata()}
