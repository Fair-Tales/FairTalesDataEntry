from text_content.pipeline_prompts import prompt_for


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

    # Spread-vs-single probe for the refined landscape aspect gate (#217
    # follow-up, validated 2026-07-20). The gate normally refuses to rotate any
    # landscape image on a SIDEWAYS verdict, because an upright double-page
    # spread is landscape and can only need 0/180 — but that also strands a
    # genuinely sideways-shot SINGLE page/cover (landscape raw) unrotated. This
    # probe asks whether the photo actually shows one page (no central fold) or
    # two facing pages meeting at a fold: only a SINGLE verdict un-gates the
    # SIDEWAYS rotation. On real phone photos it is near-perfect (covers 36/36
    # SINGLE incl. all 24 rotated-landscape covers; spreads 200/200 detect the
    # fold). It CANNOT see a fold on whitened photocopy/scan uploads (no fold
    # shadow survives), so the caller keeps rotation_uncertain even when it
    # un-gates, capping that residual risk at "flagged for review", never a
    # silent wrong rotation. Eval assets in scripts/rotation_prompt_eval/.
    rotation_spread_probe = (
        "This is a photo of a book. Decide what the photo shows:\n"
        "- SINGLE: ONE single page, or the front or back cover, on its own — "
        "there is NO central fold or gutter where two facing pages meet.\n"
        "- FOLDVERTICAL: TWO facing pages of an open book, side by side LEFT and "
        "RIGHT, meeting at a central fold/gutter that runs VERTICALLY (top to "
        "bottom) down the middle of the photo.\n"
        "- FOLDHORIZONTAL: TWO facing pages of an open book, stacked TOP and "
        "BOTTOM, meeting at a central fold/gutter that runs HORIZONTALLY (left "
        "to right) across the middle of the photo.\n"
        "Look for the physical fold: a crease or shadow line where the paper "
        "bends at the book's spine, with the page surface usually curving "
        "slightly on each side of it. Ignore the outer edges of the book and the "
        "background.\n"
        "Reply with exactly one word and no explanation: SINGLE, FOLDVERTICAL, "
        "or FOLDHORIZONTAL."
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
    character_detection = prompt_for('character_detection')

    book_metadata_extraction = prompt_for('joint_metadata_extraction')

    # --- Photo-first two-pass metadata (issue #109) -----------------------
    # Pass 1 (locate_key_pages): a single cheap Haiku call over ALL page images
    #   finds the title-page and copyright/imprint-page positions. The copyright
    #   page's position varies (after the title page, or at the back of the book),
    #   so it cannot be assumed.
    # Pass 2 (copyright_page_extraction): Sonnet reads the located copyright page
    #   to pull publisher / first-published year / ISBN, which the title page
    #   usually omits. The title page itself reuses book_metadata_extraction.

    locate_key_pages = prompt_for('locate_key_pages')

    copyright_page_extraction = prompt_for('joint_metadata_extraction')

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

    page_extraction = prompt_for('page_extraction')

    story_classification = prompt_for("story_classification")
    joint_metadata_extraction = prompt_for("joint_metadata_extraction")
