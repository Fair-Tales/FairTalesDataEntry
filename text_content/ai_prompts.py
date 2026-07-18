class AIPrompts:

    # Two-step orientation detection (#217). The previous single
    # "how many degrees clockwise?" question scored 64% on a production sample:
    # perfect on 0°/180° but near-chance on 90-vs-270 — VLMs reliably SEE that a
    # page is sideways but cannot reliably tell clockwise from anticlockwise,
    # however the question is worded. The validated replacement (100% on the
    # same sample, see planning/rotation_analysis_2026-07-15.md §4-5) never asks
    # the model a chirality question: step 1 triages upright / upside-down /
    # sideways; for sideways images the code rotates the image 90° clockwise
    # and asks the near-perfect binary question again. Mapping:
    # triage UPRIGHT -> 0, UPSIDEDOWN -> 180; after the +90° code rotation,
    # binary UPRIGHT -> 90, UPSIDEDOWN -> 270.

    # Triage keys the decision on the DIRECTION the lines of text run, not on
    # "how much of a turn" the page needs. On portrait-shot double-page spreads
    # (student holds the phone upright to photograph a landscape spread, so the
    # text lines run vertically) the "half turn vs quarter turn" wording made the
    # model confuse SIDEWAYS (90°) with UPSIDEDOWN (180°) — and non-
    # deterministically: the same page flipped answers between calls, so whether
    # a spread came out landscape or was left sideways was a coin-flip. Framing
    # the three cases as horizontal-upright / horizontal-inverted / vertical
    # fixed that (38/38 vs 29/38 on one production book — see
    # planning/rotation_triage_fix_2026-07-17.md).
    #
    # Validated 2026-07-18 across a RANDOM production sample: 72 raw page
    # images from 18 randomly-chosen books (seed 20260718 + supplement
    # 20260719; 65 scoreable — blanks and no-defined-upright wordless art
    # excluded), hand-labelled by visual inspection; eval assets in
    # scripts/rotation_prompt_eval/. Two load-bearing cues were ADDED on top
    # of the line-direction framing, each fixing a measured failure class:
    #   1. "Never answer UPSIDEDOWN for vertical text" — a sideways spread of
    #      stylised/decorative text (Little Red's author page) was deterministically
    #      called UPSIDEDOWN, leaving the spread sideways at 180°.
    #   2. The spread FOLD cue (horizontal fold / pages stacked top-and-bottom
    #      = SIDEWAYS) — a text- and illustration-independent signal that fixed
    #      WORDLESS sideways spreads (Little Red's wolf-eyes spread), which the
    #      text-only prompt called UPRIGHT. Worded as a spatial description and
    #      scoped to "mostly pictures" spreads: a stronger override-style fold
    #      rule measurably regressed upright pages, and a vertical fold cannot
    #      distinguish 0° from 180°, so it must never outrank the letter cues.
    # Scores (triage+binary pipeline, 2-3 runs/image, claude-sonnet-4-6):
    #   this prompt 195/195 (100%, deterministic) vs the previous line-direction
    #   wording 126/130 (96.9%) — per-class 100% on spreads, singles, covers,
    #   sparse/decorative/WORDLESS text, and 0/90/180/270 true orientations.
    #   Synthetic 4-orientation triage check: 46/48 vs 42/48. The "no
    #   explanation" ending suppresses hedged multi-word replies the strict
    #   parser would reject (observed with wordier variants).
    rotation_triage = (
        "This is a photo of one book page or a two-page spread. Find the lines "
        "of printed text and decide how they are oriented.\n"
        "- UPRIGHT: the lines of text run HORIZONTALLY (left to right) and the "
        "letters are the right way up and readable.\n"
        "- UPSIDEDOWN: the lines of text still run HORIZONTALLY, but every letter "
        "is inverted — you would rotate the whole page a HALF turn (180 degrees) "
        "to read it.\n"
        "- SIDEWAYS: the lines of text run VERTICALLY (up and down the page); you "
        "would rotate the page a QUARTER turn (90 degrees) so the lines become "
        "horizontal and readable.\n"
        "The key cue is the DIRECTION the lines of text run: horizontal means "
        "UPRIGHT or UPSIDEDOWN, vertical means SIDEWAYS. Never answer UPSIDEDOWN "
        "for text whose lines run vertically (top to bottom of the photo) — "
        "vertical lines mean SIDEWAYS even when the letters also look inverted "
        "or strange.\n"
        "Also check for a sideways spread: if the photo shows TWO pages of an "
        "open book with the fold between them running HORIZONTALLY across the "
        "middle — one page filling the top half of the photo and the other page "
        "filling the bottom half — then the spread is SIDEWAYS, even when it is "
        "mostly pictures with little or no text.\n"
        "If there is no text, use the picture (people/objects upright, sky at "
        "the top).\n"
        "Reply with exactly one word and no explanation: UPRIGHT, UPSIDEDOWN, "
        "or SIDEWAYS."
    )

    rotation_binary = (
        "This is a photo of a book page. Is it the RIGHT WAY UP (text reads "
        "normally; if no text, people/objects upright) or UPSIDE DOWN?\n"
        "Answer with exactly one word: UPRIGHT or UPSIDEDOWN."
    )

    crop_quality_check = (
        "Does this image show a properly cropped book page or cover that is the "
        "RIGHT WAY UP? Answer 'yes' only if ALL of these hold: it is upright and "
        "reads the normal way (NOT upside down and NOT sideways); it fills most "
        "of the frame; and no significant content is cut off. Judge orientation "
        "from the letters AND the artwork: upside-down text still lies along "
        "horizontal lines, so check that the letters themselves are the right "
        "way up, not merely that the lines are horizontal; and a cover often has "
        "little text, so also check the illustration — people and animals should "
        "be upright (head above feet) with sky at the top and ground at the "
        "bottom. Answer only: yes or no."
    )

    theme_detection = """\
Below is the full text of a children's picture book. Identify which of the \
following themes are EXPLICITLY represented in the text (not merely imaginable \
— they must be clearly present in the story):

Themes to check:
- disability: characters with physical or cognitive disabilities
- race_ethnicity: race, ethnicity, or cultural identity is part of the story
- sexuality: sexual orientation or LGBTQ+ identity is part of the story
- religion_spirituality: religion, faith, or spirituality is part of the story
- gender: gender identity or gender roles are explicitly addressed
- social_class: socioeconomic status or class difference is part of the story
- age: age-related themes (e.g. ageing, generational difference) are present

Respond with valid JSON only:
{
  "disability": true or false,
  "race_ethnicity": true or false,
  "sexuality": true or false,
  "religion_spirituality": true or false,
  "gender": true or false,
  "social_class": true or false,
  "age": true or false,
  "reasoning": "One sentence explaining which themes you found and why."
}

Book text:
"""

    # Disambiguating clause appended to ``person_lookup`` when the book title is
    # known (#113) — helps resolve common names to the right person.
    person_lookup_book_context = ' known for the children\'s book "{title}"'

    person_lookup = (
        "You are a meticulous children's book archivist. Use web search to "
        "establish the gender of {name}, a children's book {role}{context}.\n\n"
        "The book title is the key piece of context — use it to make sure you "
        "have identified the RIGHT person before reporting anything, especially "
        "for common names.\n\n"
        "GENDER — this person's gender identity, using EXACTLY one of: "
        '"Woman", "Man", "Non-binary", "Other", or "Unknown". Use "Unknown" '
        "whenever the sources do not make it clear — do not guess.\n\n"
        "Do not fabricate details. If web search turns up nothing about this "
        "specific person, return \"Unknown\" for the gender — never invent a "
        "value or borrow details from a different person who merely shares the "
        "name.\n\n"
        "Respond with ONE line of valid JSON and nothing else — no commentary, "
        "no markdown code fence:\n"
        '{{"gender": "<one of the five values above>"}}'
    )

    # --- Character + alias detection (issue #52) ---------------------------
    # Two-pass approach:
    #   Pass 1 (character_extraction): run once per story page to list every
    #           character reference appearing on that page, verbatim.
    #   Pass 2 (character_consolidation): collapse references from all pages
    #           into distinct characters, choosing a main name and recording
    #           the remaining references as aliases.

    character_extraction = """\
You are analysing ONE page of a children's picture book to identify every \
character that is mentioned in the text.

List every distinct reference to a character on this page, exactly as it \
appears in the text. Include:
- proper names (e.g. "Tom")
- nicknames (e.g. "Tommy")
- descriptive references (e.g. "the boy", "the little rabbit", "Mum")
- groups of characters (e.g. "the children", "the witches")

Do NOT invent characters that are not referred to in the text. If the same \
reference appears several times, list it once.

Respond with valid JSON only — no other text before or after:
{{"mentions": ["the boy", "Tom", "his mother"]}}

If there are no characters referred to on this page, respond with \
{{"mentions": []}}.

Page text:
{page_text}"""

    character_consolidation = """\
You are consolidating the character references collected from across ALL pages \
of a SINGLE children's picture book. Below is a JSON array; each entry lists \
the references found on one page.

Your task: group references that refer to the SAME character into one entry. \
For example "the boy", "Tom" and "Tommy" are probably the same character and \
should be merged — choose ONE as the main name and record the others as \
aliases. Track characters across pages: a character introduced as "the boy" on \
page 1 and named "Tom" on page 3 is one character.

For each distinct character provide:
- "name": the clearest, most specific name (prefer a proper name over a \
description).
- "aliases": the OTHER references used for this character (exclude the chosen \
name; may be an empty list).
- "gender": exactly one of "Female", "Male", "Non-specific", "Transgender". \
Infer ONLY from gendered pronouns or words in the references; use \
"Non-specific" when unclear. Only use "Transgender" if it is explicit.
- "human": true if the character is a person, false if an animal, object or \
other creature.
- "plural": true if this refers to a group or collection of characters \
(e.g. "the children").
- "protagonist": true only for the clear main character of the story.

Be conservative: only merge references you are confident refer to the same \
character. When in doubt keep them separate — the user will review and can \
merge further.

Respond with valid JSON only — no other text before or after:
{{"characters": [{{"name": "Tom", "aliases": ["the boy", "Tommy"], \
"gender": "Male", "human": true, "plural": false, "protagonist": true}}]}}

Character references by page:
{mentions_json}"""

    # Single-call character + alias detection (audit item 5). A whole picture
    # book's story text is only ~1-2K tokens, so the two-pass extract/consolidate
    # flow above is now collapsed into ONE call that reads every page's text at
    # once and returns the consolidated characters directly. Output shape matches
    # ``character_consolidation`` exactly. (The two prompts above are retained for
    # reference / possible fallback but are no longer called.)
    character_detection = """\
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
{pages_json}"""

    book_metadata_extraction = """\
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

Now analyse the image and return JSON:"""

    # --- Photo-first two-pass metadata (issue #109) -----------------------
    # Pass 1 (locate_key_pages): a single cheap Haiku call over ALL page images
    #   finds the title-page and copyright/imprint-page positions. The copyright
    #   page's position varies (after the title page, or at the back of the book),
    #   so it cannot be assumed.
    # Pass 2 (copyright_page_extraction): Sonnet reads the located copyright page
    #   to pull publisher / first-published year / ISBN, which the title page
    #   usually omits. The title page itself reuses book_metadata_extraction.

    locate_key_pages = """\
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
{"title_page": 2, "copyright_page": 3}"""

    copyright_page_extraction = """\
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

Now analyse the image and return JSON:"""

    # --- Collection from photos (issue #75) -------------------------------
    # Sent ALL of the user's uploaded photos (front covers and/or spine stacks)
    # at once; Claude lists the visible books (title + author) so they can be
    # fuzzy-matched against the book database and assembled into a collection.
    collection_books_extraction = """\
You are shown one or more photographs of a group of children's picture books — \
either their front covers facing the camera, or a stack/shelf of books with the \
spines facing the camera. Identify EVERY distinct book you can read well enough \
to make out its title.

For each book, read off:
- title: the book's title, exactly as printed on the cover or spine.
- author: the author's name if it is legible, otherwise an empty string.

Rules:
- Only list a book if you can actually read its title. Do NOT guess, invent, or \
look up titles or authors that are not clearly legible in the photo(s).
- List each distinct book once, even if it appears in more than one photo.

Respond with valid JSON only — no other text before or after. Example of the \
expected format:
{"books": [{"title": "The Gruffalo", "author": "Julia Donaldson"}, \
{"title": "Room on the Broom", "author": "Julia Donaldson"}]}

If you cannot read any books at all, respond with {"books": []}.

Now analyse the image(s) and return JSON:"""

    # --- Batch multi-book splitting, Stage 2 fallback (issue #84) ----------
    # Used only when NO black separator frames were found in a batch covering
    # several books. A single cheap Haiku call over ALL page images finds the
    # cover/title page that starts each book, and the batch is split immediately
    # before each detected cover.
    locate_cover_pages = """\
You are shown the page images of SEVERAL children's picture books that were \
photographed one after another, in order, as a single batch. Each image is \
preceded by its page number ("Page 1", "Page 2", and so on).

Identify the FRONT COVER or TITLE PAGE that marks where each NEW book BEGINS — \
the page showing a book's title prominently (with cover artwork, or a title-page \
layout giving the title plus author and/or illustrator) at the START of that \
book. Do NOT list ordinary interior story pages, copyright/imprint pages, \
dedication pages, or back covers.

Return the page numbers that begin a new book, in ascending order. There is \
always at least one (the first book's cover, usually page 1).

Respond with valid JSON only — no other text before or after:
{"cover_pages": [1, 12, 23]}"""

    page_extraction = """\
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

Now analyse the image and return JSON:"""
