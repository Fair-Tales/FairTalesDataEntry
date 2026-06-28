class AIPrompts:

    rotation_angle = (
        "By how many degrees clockwise should this image be rotated so that "
        "the book page text reads horizontally? "
        "Reply with a single integer between -180 and 180. "
        "Reply with 0 if the text is already horizontal or there is no clear text."
    )

    crop_quality_check = (
        "Does this image show a properly cropped book page — "
        "text roughly horizontal, the page filling most of the frame, "
        "and no significant content cut off? "
        "Answer only: yes or no."
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

    person_lookup = (
        "You are helping a children's book archivist. Using web search, look up "
        "biographical information about {name}, a children's book {role}.\n\n"
        "Find:\n"
        "1. Birth year (4-digit integer, or null if not found or uncertain)\n"
        "2. Gender identity, using exactly one of: "
        '"Woman", "Man", "Non-binary", "Other", or "Unknown"\n\n'
        "Respond with valid JSON only — no other text:\n"
        '{{"birth_year": <integer or null>, "gender": "<string>"}}'
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

    page_extraction = """\
Analyse this photo of a children's picture book page.

Instructions:
1. Correct for any rotation or tilt in the image. Focus on the book page itself \
and ignore any background (table, hands, etc.).
2. Transcribe ALL story text visible on the page exactly as written. Include speech \
bubbles and captions. Do not include page numbers.
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
3. Classify whether this is a STORY page — meaning it contains narrative text \
that is part of the story itself. The following page types are NOT story pages: \
title page, half-title, copyright, dedication, contents, about the author, \
publisher information, back-cover synopsis, end matter, blank pages.

Respond with valid JSON only — no other text before or after. Example of the \
expected format:
{
  "text": "Once upon a time, a [small?] rabbit lived in the forest.",
  "is_story_page": true,
  "page_type": "story"
}

Now analyse the image and return JSON:"""
