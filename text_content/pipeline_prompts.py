"""Frozen evidence-rich prompts and request provenance; no service access."""
from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import json
from pathlib import Path

from conventions import convention_metadata

PACK_VERSION = "pipeline-evidence-v1"
PROMPT_VERSION = "pipeline-prompts-v2"
CHARACTER_TRUNCATED = "Character response reached the output limit; partial evidence retained."
CHARACTER_PARTIAL = "Character response was incomplete; partial evidence retained."
CHARACTER_STRUCTURE_INVALID = "Character response has an invalid structure; raw evidence retained."
PROMPT_VARIANTS = ("legacy-v1", "rules-only", "rules-plus-icl")
_evaluation_book = ContextVar("evaluation_prompt_book", default=None)
_loader_hook = ContextVar("prompt_example_loader_hook", default=None)


def current_loader():
    """The app has no loader; evaluation supplies its own scoped hook."""
    hook = _loader_hook.get()
    return hook() if hook is not None else None


@contextmanager
def example_loader_context(hook):
    """Install evaluation's loader lookup without importing the harness."""
    token = _loader_hook.set(hook)
    try:
        yield
    finally:
        _loader_hook.reset(token)


@contextmanager
def evaluation_prompt_context(book_id):
    """Scope leave-one-book-out rendering and collect exclusions by stage."""
    excluded = {}
    token = _evaluation_book.set((book_id, excluded))
    try:
        yield excluded
    finally:
        _evaluation_book.reset(token)

# 5 November baseline: text_content/ai_prompts.py at
# 9277ef723d46d22930ea09ed756208233ead74ae (blob 23f8d8862293191fda85f7ebf905baca087a11be).
# File SHA-256 matches dev20-run/run.json contract.sources:
# b4f278964e017ae8e1592e3954302679f48ae916e27c1dc5c9a1d150218f14a6.
# Preserve literal bytes: no convention suffix or examples. Joint metadata and
# whole-book story classification did not exist in that pipeline.
LEGACY_PROMPTS = {
    'character_detection': """\
You are analysing the FULL text of a SINGLE children's picture book to identify \
its characters. Below is a JSON array; each entry gives one page's number and \
that page's story text.

Identify every distinct character referred to ANYWHERE in the book, grouping all \
the references to the SAME character into one entry. For example "the boy", "Tom" \
and "Tommy" are probably the same character and should be merged — choose ONE as \
the main name and record the others as aliases. Track characters across pages: a \
character introduced as "the boy" on page 1 and named "Tom" on page 3 is one \
character.

Include every kind of reference:
- proper names (e.g. "Tom")
- nicknames (e.g. "Tommy")
- descriptive references (e.g. "the boy", "the little rabbit", "Mum")
- groups of characters (e.g. "the children", "the witches")

Do NOT invent characters that are not referred to in the text.

For each distinct character provide:
- "name": the clearest, most specific name (prefer a proper name over a \
description).
- "aliases": the OTHER references used for this character (exclude the chosen \
name; may be an empty list).
- "gender": exactly one of "Female", "Male", "Non-specific", "Transgender". \
Infer ONLY from gendered pronouns or words in the references; use "Non-specific" \
when unclear. Only use "Transgender" if it is explicit.
- "human": true if the character is a person, false if an animal, object or \
other creature.
- "plural": true if this refers to a group or collection of characters \
(e.g. "the children").
- "protagonist": true only for the clear main character of the story.

Be conservative: only merge references you are confident refer to the same \
character. When in doubt keep them separate — the user will review and can merge \
further.

Respond with valid JSON only — no other text before or after:
{{"characters": [{{"name": "Tom", "aliases": ["the boy", "Tommy"], \
"gender": "Male", "human": true, "plural": false, "protagonist": true}}]}}

Book pages (JSON array of {{"page", "text"}}):
{pages_json}""",
    'page_extraction': """\
Analyse this photo of a children's picture book page.

Instructions:
1. Correct for any rotation or tilt in the image. Focus on the book page itself \
and ignore any background (table, hands, etc.).
2. Decide whether the page carries ANY of the book's OWN printed text at all. \
This includes story/narrative text AND any front- or back-matter text: title, \
copyright notice, ISBN, publisher information, dedication, table of contents, \
about-the-author, back-cover blurb or synopsis, end matter. Many picture-book \
pages are WORDLESS full illustrations — that is normal and expected. Set \
has_text to true if the page carries any of the book's own printed text of any \
kind; set it to false only for a page with no readable book text at all (e.g. a \
wordless full illustration).
3. Transcribe ALL of the book's own printed text visible on the page exactly as \
written — story text AS WELL AS any front/back-matter text (title, copyright \
notice, ISBN, publisher information, dedication, contents, about-the-author, \
back-cover blurb, end matter). Include speech bubbles and captions. Do not \
include page numbers.
   - If the photo shows a double-page spread (two facing pages captured together in \
one image), decide the reading order before transcribing. By default, treat the two \
pages as separate text blocks and transcribe the LEFT-hand page fully before the \
RIGHT-hand page. However, if the text visibly flows as a single continuous block \
across the whole spread — with no separate left/right split — do NOT force a \
left-then-right split; instead follow the natural reading flow of the text as it is \
actually laid out. Use your judgement based on how the text appears in the photo.
   - Text may use a variety of fonts, stylised or decorative lettering, or handwriting — \
transcribe it as accurately as possible regardless of style.
   - Text within the page may appear in different orientations (rotated, vertical, curved, \
or angled) — read and transcribe it even if it is not horizontal.
   - Do NOT transcribe text that is part of the illustration artwork rather than the \
narrative — for example: signposts, shop or street names, labels on objects, posters, \
or other background signage. Only extract text that belongs to the story itself.
   - If a word or passage is difficult to read (blurred, partially obscured, \
unusual lettering), enclose it in square brackets with a question mark: [like this?]
   - If you cannot make out any part of a word at all, write [?] in its place.
   - If the page has no readable book text at all (has_text is false), set text \
to "" (an empty string). NEVER put a description, caption, or note ABOUT the \
illustration into the text field — the text field must contain only the book's \
own transcribed words and nothing else.
4. Classify whether this is a STORY page — meaning it contains narrative text \
that is part of the story itself. The following page types are NOT story pages: \
title page, half-title, copyright, dedication, contents, about the author, \
publisher information, back-cover synopsis, end matter, blank pages.

Respond with valid JSON only — no other text before or after. Example of the \
expected format:
{
  "has_text": true,
  "text": "Once upon a time, a [small?] rabbit lived in the forest.",
  "is_story_page": true,
  "page_type": "story"
}

Example for a copyright page (front/back-matter that has text but is NOT part of \
the story):
{
  "has_text": true,
  "text": "First published in 2019 by Example Press. Text and illustrations \
copyright © Jane Author 2019. ISBN 978-0-00-000000-0. All rights reserved.",
  "is_story_page": false,
  "page_type": "copyright"
}

Example for a wordless illustration:
{
  "has_text": false,
  "text": "",
  "is_story_page": true,
  "page_type": "story"
}

Now analyse the image and return JSON:""",
    'locate_key_pages': """\
You are shown the page images of a children's picture book, in order. Each image \
is preceded by its page number ("Page 1", "Page 2", and so on).

Identify two specific pages and return the page NUMBER shown before each image:
- title_page: the page showing the book's title together with the author and/or \
illustrator (usually the inside title page; use the front cover if no inside \
title page is present).
- copyright_page: the copyright / imprint page — the page bearing the copyright \
notice (©), the ISBN, the publisher's details and/or the first-publication year. \
It is often on the reverse of the title page, but may instead appear at the very \
back of the book.

If either page cannot be found, use null for that field.

Respond with valid JSON only — no other text before or after:
{"title_page": 2, "copyright_page": 3}""",
    'book_metadata_extraction': """\
Analyse this photo of the TITLE PAGE (or front cover) of a children's picture book.

Extract the book's bibliographic details exactly as printed on the page:
- title: the book's title
- authors: a list of the author name(s) — the person(s) who WROTE the book \
(usually shown as "written by", "story by", or simply "by")
- illustrators: a list of the illustrator name(s) — the person(s) who DREW the \
pictures (usually shown as "illustrated by", "pictures by", or "art by"). If the \
same person both wrote and illustrated the book, include their name in both lists.
- publisher: the name of the publishing house, if visible
- published_year: the 4-digit year of first publication, if visible (this is \
often only printed on the copyright page — use null if it is not shown)

Rules:
- Transcribe names exactly as written. Do NOT invent, guess, or look up details \
that are not visible in the image.
- If a field is not present, use an empty list for authors/illustrators, or null \
for title/publisher/published_year.

Respond with valid JSON only — no other text before or after. Example of the \
expected format:
{
  "title": "The Gruffalo",
  "authors": ["Julia Donaldson"],
  "illustrators": ["Axel Scheffler"],
  "publisher": "Macmillan Children's Books",
  "published_year": 1999
}

Now analyse the image and return JSON:""",
    'copyright_page_extraction': """\
Analyse this photo of the COPYRIGHT / IMPRINT page of a children's picture book \
(the page carrying the copyright notice, ISBN and publisher details).

Extract the following, exactly as printed:
- publisher: the name of the publishing house, if shown (else null)
- first_published_year: the 4-digit year the book was FIRST published. Prefer the \
year printed beside "first published" if present; otherwise the earliest \
copyright (©) year shown. Use null if no year is visible.
- isbn: the ISBN (ISBN-13 or ISBN-10), digits and hyphens exactly as printed, or \
null if none is visible.

Rules:
- Transcribe only what is visible. Do NOT invent, guess, or look up details.

Respond with valid JSON only — no other text before or after. Example of the \
expected format:
{
  "publisher": "Macmillan Children's Books",
  "first_published_year": 1999,
  "isbn": "978-0-333-71093-5"
}

Now analyse the image and return JSON:""",
}

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
A body part such as "long grey trunk" is not an alias for its owner.

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
surface_scope values. Keep every raw emitted alias and uncertainty record.""",
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
    path = Path(__file__).with_name("pipeline_examples.json")
    loader = current_loader()
    if loader:
        pack = loader.examples(path)
    else:
        pack = json.loads(path.read_text(encoding="utf-8"))
    development = set(pack.get("development_book_ids", []))
    held_books, held_groups = set(held_out_book_ids), set(held_out_groups)
    for example in pack.get("examples", []):
        if (example.get("book_id") not in development
                or example.get("book_id") in held_books
                or example.get("holdout_group") in held_groups):
            raise ValueError("Example leakage: only frozen iterate sources are permitted")
        source = example.get("source_text", "")
        if hashlib.sha256(source.encode("utf-8")).hexdigest() != example.get("source_text_sha256"):
            raise ValueError("Example source text hash mismatch")
        if not all(quote in source for quote in example.get("quotes", [])):
            raise ValueError("Example quotation is not verbatim source text")
        if not example.get("source_image_sha256"):
            raise ValueError("Example lacks frozen source image provenance")
    return pack


def _stage_examples(stage, variant):
    if variant not in PROMPT_VARIANTS:
        raise ValueError("Unknown prompt variant")
    if variant != "rules-plus-icl":
        return []
    # Keep source admission and validation in the pack loader, independent of
    # where A7a eventually stores the text. Leave-one-out only filters admitted
    # examples; it must not bypass the existing held-out-source refusal.
    examples = load_example_pack().get("examples", [])
    context = _evaluation_book.get() if current_loader() is not None else None
    excluded_ids = set()
    if context is not None:
        book_id, excluded = context
        groups = {example["holdout_group"] for example in examples
                  if example.get("book_id") == book_id and example.get("holdout_group")}
        excluded_ids = {example["id"] for example in examples
                        if example.get("book_id") == book_id
                        or example.get("holdout_group") in groups}
        excluded[stage] = [example["id"] for example in examples
                           if example.get("stage") == stage and example["id"] in excluded_ids]
    return [example for example in examples
            if example.get("stage") == stage and example["id"] not in excluded_ids]


def example_pack_text(stage, variant="rules-plus-icl"):
    blocks = []
    for example in _stage_examples(stage, variant):
        quotes = "\n".join(json.dumps(quote, ensure_ascii=False) for quote in example["quotes"])
        blocks.append(
            f"EXAMPLE {example['id']} — {example['book_id']}, photograph {example['page_number']}\n"
            f"Status: {example['approval_status']}; quoted excerpts unless explicitly stated complete.\n"
            f"Exact source text (JSON strings preserving original line breaks):\n{quotes}\n"
            f"Expected decisions: {example['decision']}")
    return "\n\n".join(blocks)


def prompt_for(stage, variant="rules-plus-icl"):
    if variant not in PROMPT_VARIANTS:
        raise ValueError("Unknown prompt variant")
    if variant == "legacy-v1":
        if stage not in LEGACY_PROMPTS:
            raise ValueError(f"Prompt stage {stage!r} did not exist in legacy-v1")
        return LEGACY_PROMPTS[stage]
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
    pack_examples = _stage_examples(stage, variant)
    return {"prompt_version": "legacy-v1" if variant == "legacy-v1" else PROMPT_VERSION,
            "example_pack_version": PACK_VERSION,
            "prompt_variant": variant, "stage": stage, "encoding": "utf-8",
            "example_pack_utf8": examples,
            "example_pack_sha256": hashlib.sha256(examples.encode("utf-8")).hexdigest(),
            "rendered_prompt_utf8": rendered,
            "rendered_prompt_sha256": hashlib.sha256(rendered.encode("utf-8")).hexdigest(),
            "rendered_prompt_byte_length": len(rendered.encode("utf-8")),
            "examples": [{key: value for key, value in example.items()
                          if key not in {"source_text", "decision"}}
                         for example in pack_examples if example.get("stage") == stage],
            **convention_metadata()}
