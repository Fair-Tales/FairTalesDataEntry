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

    page_extraction = """\
Analyse this photo of a children's picture book page.

Instructions:
1. Correct for any rotation or tilt in the image. Focus on the book page itself \
and ignore any background (table, hands, etc.).
2. Transcribe ALL text visible on the page exactly as written. Include speech \
bubbles and captions. Do not include page numbers.
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
