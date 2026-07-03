class GenderRegistration:

    options = ["Woman", "Man", "Non-binary", "Let me type...", "Prefer not to say"]
    manual_input_option = "Let me type..."
    question = "What is your gender identity? Please select an option"
    manual_input_prompt = "Or describe your gender identify here."
    help = """
        As we are researching gender in children's literature, we are keen to also record the gender identities of
        our archivists. We may use this information for analysis purposes in our research, but only to
        calculate aggregate statistics. Your personal information will be kept private.   
    """

class BookForm:
    header = "Please enter or review the details of this book."
    cancel_text = "Cancel"
    cancel_help = "Cancel entering or editing the data for this book."
    author_help = """
        Please select author from the list. If the author is not listed, please select `None of these`
        and you will be guided to create a new author on the next step.
    """
    illustrator_help = """
        Please select illustrator from the list. If the illustrator is not listed, please select `None of these`
        and you will be guided to create a new illustrator on the next step.
    """
    publisher_help = """
        Please select publisher from the list. If the publisher is not listed, please select `None of these`
        and you will be guided to create a new publisher on the next step.
    """
    book_exists = """
        This book already exists in the database. You can use the `Book search` function on your 
        homepage to check before entering a book.
    """
    theme_options = {
        'disability': 'Disability',
        'race_ethnicity': 'Race/Ethnicity',
        'sexuality': 'Sexuality',
        'religion_spirituality': 'Religion/Spirituality',
        'gender': 'Gender',
        'social_class': 'Social class',
        'age': 'Age'
    }
    themes_help = """
        Please select all themes that you feel are explicitly represented in this book. You may want to 
        return to revise your selection after you have entered the book text. You can do this at any time
        by selection `Edit metadata` from the options menu. 
        
        Note: you may not like or agree with the way that a theme is represented. For our analysis, 
        we just need to know if it is represented or not. You are also able to add a comment in the 
        box below.   
    """
    comment_help = """
        You can add a comment with any thoughts you have about this book.
    """

    # --- to_form() widget labels (data_structures/book.py) ---
    title_label = "Title"
    published_label = "Date first published"
    author_select_label = "Select from existing authors"
    publisher_select_label = "Select from existing publishers"
    illustrator_select_label = "Select from existing illustrators"
    new_author_option = "None of these (create a new author now)."
    new_publisher_option = "None of these (create a new publisher now)."
    new_illustrator_option = "None of these (create a new illustrator now)."
    themes_label = "Select themes"
    comment_label = "Comment"
    isbn_prefill_caption = "ℹ Metadata pre-filled from ISBN lookup — please verify."
    submit_button = "Submit"
    title_required = "Book title is required."


class AuthorForm:
    header = "Please enter author details."
    gender_options = ["Woman", "Man", "Non-binary", "Other", "Unknown"]
    gender_prompt = """
        Click "Look up birth year and gender" to auto-fill these fields using web search,
        or select manually. If it is not clear, please select `Unknown`.
    """
    lookup_help = (
        "Use AI web search to suggest birth year and gender based on the name entered above."
    )
    author_exists = """
        This author already exists in the database. Please either select them from the
        dropdown menu above, or enter a unique name for your new author.
    """

    # --- to_form() widget labels (data_structures/author.py) ---
    forename_label = "First name"
    surname_label = "Surname"
    birth_year_label = "What is the author's birth year?"
    birth_year_placeholder = "Select year of birth"
    birth_year_unknown = "I don't know"
    birth_year_earlier = "Earlier year"
    gender_label = "Gender"
    submit_button = "Submit"
    lookup_button = "Look up birth year and gender"
    lookup_spinner = "Looking up birth year and gender…"
    lookup_failed = (
        "Couldn't find reliable birth year / gender details for this name — "
        "please enter them manually."
    )
    lookup_no_name = "Please enter a first name or surname before looking up."
    name_required = "Author first name and surname are required."
    cancel_text = "Cancel entering new author."

class IllustratorForm:
    header = "Please enter illustrator details."
    gender_options = ["Woman", "Man", "Non-binary", "Other", "Unknown"]
    gender_prompt = """
        Click "Look up birth year and gender" to auto-fill these fields using web search,
        or select manually. If it is not clear, please select `Unknown`.
    """
    lookup_help = (
        "Use AI web search to suggest birth year and gender based on the name entered above."
    )
    illustrator_exists = """
        This illustrator already exists in the database. Please either select them from the
        dropdown menu above, or enter a unique name for your new illustrator.
    """

    # --- to_form() widget labels (data_structures/illustrator.py) ---
    forename_label = "First name"
    surname_label = "Surname"
    birth_year_label = "What is the illustrator's birth year?"
    birth_year_placeholder = "Select year of birth"
    birth_year_unknown = "I don't know"
    birth_year_earlier = "Earlier year"
    gender_label = "Gender"
    submit_button = "Submit"
    lookup_button = "Look up birth year and gender"
    lookup_spinner = "Looking up birth year and gender…"
    lookup_failed = (
        "Couldn't find reliable birth year / gender details for this name — "
        "please enter them manually."
    )
    lookup_no_name = "Please enter a first name or surname before looking up."
    name_required = "Illustrator first name and surname are required."
    cancel_text = "Cancel entering new illustrator."

class PublisherForm:
    header = "Please enter publisher details."
    publisher_exists = """
        This publisher already exists in the database. Please either select them from the 
        dropdown menu above, or enter a unique name for your new publisher.
    """

    # --- to_form() widget labels (data_structures/publisher.py) ---
    name_label = "Name"
    founding_year_label = "Which year was the publisher founded?"
    founding_year_placeholder = "Select year of founding"
    founding_year_unknown = "I don't know"
    founding_year_earlier = "Earlier year"
    submit_button = "Submit"
    name_required = "Publisher name is required."
    cancel_text = "Cancel entering new publisher."


class BookPhotoEntry:
    """Strings for the photo-initiated ("photos first") book entry flow (#59)."""

    menu_label = "Add from Photos"
    header = "Add a book from photos"
    instructions = (
        "Upload photos of the book to get started. We'll read the details and use "
        "them to pre-fill the book information on the next step, where you can "
        "review and correct everything before saving.\n\n"
        "Please include a clear photo of all pages in the book, including front "
        "and back covers."
    )
    upload_label = "Select the book's page photos to upload"
    title_page_label = "Which photo is the title page?"
    title_page_help = (
        "Select the photo that shows the book's title, author and illustrator. "
        "We read this page for the title and creators, and automatically find the "
        "copyright page for the publisher, year and ISBN."
    )
    extract_button = "Read the book and pre-fill details"
    extracting = "Reading the title and copyright pages..."
    extract_failed = (
        "Could not read the book automatically ({error}). "
        "You can still enter the details manually."
    )
    extract_empty = (
        "We couldn't extract any details from those photos. You can still add the "
        "book — tap below to enter the details manually (your photos are kept), or "
        "re-take a clearer shot of the title page and click Go again."
    )
    enter_manually_button = "Enter the book's details manually →"
    # Surfaced under an expander when extraction comes back empty, to help diagnose
    # whether the title page was located and what the model actually returned.
    extract_diag_header = "Details (for troubleshooting)"
    extract_diag_pages = "Photos processed: {n}"
    extract_diag_located = "Title / copyright page the AI located: {located}"
    extract_diag_raw = "What the AI returned for the title page:"
    no_api_key = (
        "Automatic detail extraction requires an Anthropic API key. "
        "You can still enter the details manually."
    )
    extract_success = "Book details read. Please review and correct them below."
    reuse_notice = "Using the {count} photo(s) you already uploaded. Processing them now..."
    cancel_text = "Cancel"

    # Direct-to-S3 browser upload (#114). Each photo PUTs straight to S3 via
    # presigned URLs, bypassing the Streamlit websocket that drops on mobile while
    # the native photo picker is open. Device-agnostic wording (#143): we don't
    # assume the user is on a phone here — that's the "Go to phone" option.
    direct_upload_instructions = (
        "Choose **Select book photos** and pick every page of the book, including "
        "the front and back covers. **The order doesn't matter** — your photos are "
        "sorted by file name. Each photo uploads directly to secure storage; watch "
        "the progress bars, then click **Go** once they have all finished uploading."
    )
    upload_select_button = "Select book photos"
    upload_component_hint = (
        "Full resolution, uploaded directly to secure storage. You can tap again "
        "to add more photos."
    )
    upload_progress = "Uploaded {done} of {total} photo(s)."
    upload_failed_count = "({failed} failed — please tap Select book photos to retry.)"
    upload_max_reached = "Maximum number of photos reached."
    # Shown per-file when a selected photo exceeds the client-side size cap; the
    # oversize file is skipped (not uploaded) and the rest of the batch proceeds.
    upload_too_large = "{name} is too large ({size} MB). The maximum is {max} MB — it was skipped."
    read_button = "Go"
    no_photos_uploaded = (
        "We couldn't find any uploaded photos yet. Please select your book photos "
        "above and wait for every progress bar to finish, then try again."
    )
    reading_photos = "Fetching your uploaded photos..."
    # Shown while the "Read the book" click checks the temp prefix has stopped
    # growing (uploads_settled), so we don't read a partial batch (#142).
    checking_uploads = "Checking your photos have finished uploading..."
    uploads_in_progress = (
        "Your photos are still uploading. Please wait for every progress bar to "
        "finish, then click **Go** again."
    )


class BatchBookEntry:
    """Strings for the batch multi-book photo upload flow (#84).

    The user uploads ONE batch of photos covering SEVERAL books (taken in
    sequence); the app splits them into per-book groups, reads each book's
    details, lets the user review the split, then creates a separate book
    record per group.
    """

    menu_label = "Batch Upload"
    header = "Batch upload — add several books at once"
    instructions = """
        Upload a single batch of photos that covers **several books**, taken in
        order. We'll split the photos into separate books and create a record for
        each one.

        **To mark where one book ends and the next begins, cover the camera lens
        and take a fully black photo between books.** We use those black photos as
        separators and discard them. If you didn't take separator photos, we'll try
        to detect each book's cover page automatically instead.

        Upload all the photos in order, then review the detected books before we
        create them.
    """
    upload_label = "Select all the page photos for the batch (in order)"
    # Direct-to-S3 browser upload (#118). Each photo PUTs straight from the device
    # to S3 at full resolution, bypassing the websocket that drops on mobile.
    direct_upload_instructions = (
        "Tap **Select book photos** and choose every page across all the books, in "
        "order (remember the black separator photos between books). Each photo "
        "uploads straight from your device — watch the progress bars, then tap "
        "**Detect books** once they have all finished."
    )
    no_api_key = (
        "Without an Anthropic API key we can still split books on the black "
        "separator photos, but we can't auto-detect covers, read titles, or "
        "extract page text — those steps will be skipped."
    )
    detect_button = "Detect books"
    detecting = "Splitting the batch and reading each book..."
    reading_book = "Reading book {n} of {total}..."
    no_photos = "Please upload the batch photos first."
    no_books_detected = (
        "We couldn't split this batch into any books. Please check your photos "
        "and try again."
    )

    # --- Review step ---
    review_header = "Review the detected books"
    method_black_frame = (
        "Split into **{count}** book(s) using the black separator photos "
        "(the separators have been discarded)."
    )
    method_cover_page = (
        "No black separator photos were found, so we split into **{count}** "
        "book(s) by detecting each book's cover page. Please check the split "
        "carefully before continuing."
    )
    method_single = (
        "We found no separators or distinct covers, so we've treated the whole "
        "batch as a **single** book. Please check this is correct."
    )
    book_summary = "Book {n}: {title} — {pages} page(s)"
    untitled_title = "Untitled book {n}"
    detail_author = "Author: {value}"
    detail_illustrator = "Illustrator: {value}"
    detail_publisher = "Publisher: {value}"
    detail_year = "First published: {value}"
    review_metadata_warning = (
        "We couldn't read the details for **{count}** of these book(s). They'll "
        "still be created, but you'll need to add the title and details yourself "
        "via **Edit my books**. They're flagged below."
    )
    detail_metadata_error = (
        "⚠️ The details for this book couldn't be read — it will be created with "
        "a placeholder title. Please add the title and details via **Edit my "
        "books** after creating."
    )
    confirm_button = "Create {count} book(s)"
    start_over_button = "Start over"
    cancel_button = "Cancel"

    # --- Processing step ---
    creating = "Creating books and processing pages..."
    creating_complete = "All books created."
    processing_page = "{title}: processing page {page} of {total}..."
    # Per-book prefix for the shared per-page sub-step messages (Uploader.substep_*),
    # so the batch flow shows the same fine-grained progress as the single-book
    # upload and keeps the websocket alive across many books (#110).
    page_prefix = "{title} — "

    # --- Done step ---
    done_header = "Books created"
    done_summary = "Created **{count}** book(s) from the batch:"
    done_needs_details = (
        "**{count}** of these book(s) need manual details: their title and "
        "information couldn't be read, so they were saved with a placeholder "
        "title. Please add the missing details via **Edit my books**."
    )
    done_book_line = "- {title} ({pages} page(s))"
    done_book_line_unread = (
        "- {title} ({pages} page(s)) — ⚠️ metadata couldn't be read; please add "
        "the title/details via **Edit my books**"
    )
    done_note = (
        "Each book has been saved with its pages and any details we could read. "
        "Open **Edit my books** to review, add authors / illustrators / "
        "publishers, and finish each one."
    )
    done_home_button = "Back to home"
    done_another_button = "Upload another batch"


class EnterText:

    header = "Please enter text and add characters"
    instruction = """
        - You only need to enter text for the pages that contain the story (or main content of the book).
        - Please enter with spelling verbatim and use 'enter' for newlines. 
        - When a linebreak appears to mark the end of a sentence please add a full stop. **This is very
        important for our language analysis.**
        - Please add all characters and their aliases (other names used to refer to them). You only need to 
        enter each character once and it doesn't matter when you enter them or which of their names you 
        use for their 'main' name, as long as you record all the other names as aliases.
    """

    character_help = """
        As you work through the pages, please enter all of the characters (or groups of characters) 
        that you encounter. You can enter a character at any time but only need to do it once.
        When different names refer to the same character, please just add the 
        character once and then use `Add alias` to record all of the other names that are used to 
        refer to them. This is essential for our language analysis, so that we can reliably identify 
        each time the same character appears in the book.
    """

    alias_help = """
        Please record all of the alternative names that are used to refer to the same character
        or group of characters. You can add an alias at any time after you have created the character.  
    """

    save_help = """
        The data you enter will save automatically as you work. This button finishes entering this book.
        You will not be able to edit it again after this.
    """

    # --- AI character + alias detection (issue #52) ---
    detect_help = """
        Use AI to read the text you have entered so far and suggest the characters and their aliases
        across the whole book. Nothing is saved until you review and confirm the suggestions.
    """
    detect_intro = """
        This will read the story text you have entered for every page and suggest a list of
        characters and their aliases for you to review. Make sure you have entered (or auto-extracted)
        the page text first. Nothing is saved until you confirm.
    """
    detect_spinner = "Reading the book text and detecting characters..."
    detect_progress = "Detecting characters — step {done} of {total}..."
    detect_no_api_key = "AI character detection requires an Anthropic API key."
    detect_no_text = (
        "No story text found. Please enter or extract text for the book pages first, "
        "and tick 'Does this page contain story text?' on the relevant pages."
    )
    detect_failed = "Character detection failed: {error}"
    detect_none_found = "The AI did not find any characters in the text. You can add characters manually."
    review_instruction = """
        Review the suggested characters below. Correct any names, genders or other details,
        edit the comma-separated aliases, and choose an action for each one. To merge a duplicate,
        choose 'Merge into' on the duplicate and select the character to keep — its name and aliases
        will be added to that character. Nothing is saved until you press the button at the bottom.
    """
    review_action_create = "Create as a new character"
    review_action_skip = "Skip (do not add)"
    review_action_merge = "Merge into: {name}"
    review_submit = "Create selected characters"
    review_created = "Created {characters} character(s) and {aliases} alias(es)."
    review_skipped = "Skipped (already in the database): {names}."
    review_unresolved = (
        "Could not merge these because their target was not created: {names}. "
        "They were not added — please re-run detection or add them manually."
    )

    # --- Image view / manual correction dialog ---
    image_edit_dialog_title = "Edit image"
    rotation_header = "Rotation"
    rotate_left_button = "↺ 90° left"
    rotate_right_button = "↻ 90° right"
    rotate_180_button = "180°"
    fine_adjustment_label = "Fine adjustment (degrees)"
    crop_header = "Crop margins (%)"
    crop_left_label = "Left"
    crop_right_label = "Right"
    crop_top_label = "Top"
    crop_bottom_label = "Bottom"
    preview_caption = "Preview"
    save_corrected_button = "💾 Save as corrected image"
    discard_button = "✕ Discard"
    show_original_toggle = "Show original photo"
    showing_original_caption = "Showing original photo"
    auto_corrected_caption = "✓ Auto-corrected"
    auto_correction_unavailable_caption = "⚠ Auto-correction unavailable — showing original photo"
    edit_image_button = "✏ Edit image"
    enlarge_button = "🔍 Enlarge"

    # --- Text entry / navigation controls ---
    contains_story_label = "Does this page contain story text?"
    add_character_button = "Add character"
    add_alias_button = "Add alias"
    detect_button = "Detect characters (AI)"
    page_text_label = "Enter page text"
    save_page_button = "Save page"
    cancel_character_button = "Cancel adding character"
    cancel_alias_button = "Cancel adding alias"
    previous_page_button = "Previous page"
    next_page_button = "Next page"
    page_indicator = "Showing page %d of %d."
    back_to_menu_button = "Back to menu"
    finish_submit_button = "Finish and submit book"

    # --- Detected-character review form ---
    review_character_heading = "**Character {n}**"
    review_name_label = "Name"
    review_gender_label = "Gender"
    review_human_label = "Is human?"
    review_protagonist_label = "Is protagonist?"
    review_plural_label = "Is plural?"
    review_aliases_label = "Aliases (comma-separated)"
    review_action_label = "Action"
    back_to_text_button = "Back to text"
    cancel_button = "Cancel"
    run_detection_button = "Run detection"


class CharacterForm:

    header = "Please enter details of this character"

    gender_options = ["Female", "Male", "Non-specific", "Transgender"]
    gender_help = """
        Please select character gender based only on pronouns. If it is not clear
        because gendered pronouns are not used, please select `Non-specific`.
        Please do not use the illustrations to infer gender and only select
        `Transgender` if this is explicit in the text.
    """

    # TODO: confirm taxonomy with team
    ethnicity_options = [
        "Not specified",
        "Asian",
        "Black / African",
        "Hispanic / Latino",
        "Middle Eastern",
        "Native / Indigenous",
        "White / European",
        "Mixed / Other",
    ]
    ethnicity_help = """
        Please select the character's apparent ethnicity as represented in the text or illustrations.
        If it is not clear, please select `Not specified`.
        Note: this vocabulary is a placeholder — the final taxonomy will be confirmed by the team.
    """

    # TODO: confirm taxonomy with team
    disability_options = [
        "Not specified",
        "Physical disability",
        "Sensory disability",
        "Cognitive / Learning disability",
        "Mental health condition",
        "Chronic illness",
        "No disability",
    ]
    disability_help = """
        Please select the character's apparent disability status as represented in the text.
        If it is not clear, please select `Not specified`.
        Note: this vocabulary is a placeholder — the final taxonomy will be confirmed by the team.
    """

    character_exists = """
            This character already exists in the database.
            If you want to create a new alias for an existing character, please
            cancel this entry and select `Add alias`.
        """
    rename_exists = """
            Another character with that name already exists in this book.
            Please choose a different name.
        """
    name_required = "Character name is required."
    protagonist_help = """
        The protagonist is the main character.
    """
    plural_help = """
            Select if this is a group or collection of characters e.g. 'the witches'.
        """
    human_help = """
            Select if character is human (rather than non-human - animal, monster etc).
        """

    # --- to_form() widget labels (data_structures/character.py) ---
    name_label = "Name"
    gender_label = "Gender"
    ethnicity_label = "Ethnicity"
    disability_label = "Disability"
    protagonist_label = "Is protagonist?"
    human_label = "Is human?"
    plural_label = "Is plural?"
    save_button = "Save character"

    # --- edit_form() (data_structures/character.py) ---
    edit_header = "Edit this character's details"
    update_button = "Save changes"


class AddCharacterPage:
    """Strings for the standalone add-character page (pages/add_character.py)."""

    header = "Please enter the details of the new character."
    name_label = "Full name (as most commonly used)"
    alias_label = "Enter their alias"
    gender_label = "Gender"
    gender_options = [
        'Female',
        'Male',
        'Non-binary/Genderqueer/Gender non-conforming',
        'Not specified'
    ]
    ethnicity_label = "Ethnicity"
    disability_label = "Disability"
    plural_label = "Is this a group or collection of characters? (e.g. 'the cavemen')"
    human_label = "Is this character human?"
    submit_button = "Submit"
    name_required = "Character name is required."
    cancel_button = "Cancel adding new character."


class ResultsDashboard:

    page_title = "Research Results"

    intro = """
        Explore aggregated results for the books in our collection. This is an
        early preview — the first result type is a breakdown of character counts
        by gender.
    """

    gender_column_label = "Gender"
    count_column_label = "Number of characters"

    combined_chart_title = "All characters by gender"
    human_chart_title = "Human characters by gender"
    nonhuman_chart_title = "Non-human characters by gender"

    scope_all_caption = "Showing results across all books in the collection."
    scope_collection_caption = (
        "Showing results for the selected collection of {n} book(s)."
    )

    empty_message = """
        There are no characters in the current selection yet, so there is nothing
        to chart. Enter some character data (or choose a different collection) and
        check back.
    """

    work_in_progress_header = "Work in progress"
    work_in_progress_intro = """
        More result types are on the way. Planned breakdowns include:
    """
    work_in_progress_items = [
        "Speech statistics — how much each character speaks.",
        "Ethnicity breakdowns per character (see issue #46).",
        "Disability breakdowns per character (see issue #46).",
        "Author, illustrator and publisher diversity summaries.",
    ]


class AliasForm:

    header = "Please select a character and enter their alias:"
    character_exists = """
            This alias already exists in the database.
    """
    no_characters = """
        There are no characters in this book yet. Please add a character first,
        then you can record their aliases.
    """

    # --- to_form() widget labels (data_structures/alias.py) ---
    select_character_label = "Select character"
    alias_label = "Alias"
    save_button = "Save alias"


class ManageCharacters:

    header = "Manage characters and aliases"
    intro = """
        Below are the characters you have recorded for this book. You can edit a
        character's details, delete a character — which also deletes all of its
        aliases — or delete an individual alias. Deletions are permanent and
        cannot be undone.
    """
    no_characters = "No characters have been added to this book yet."
    aliases_label = "Aliases:"
    no_aliases = "No aliases recorded for this character."
    manage_button = "Manage characters"
    manage_help = """
        View the characters recorded for this book, edit their details, and
        delete characters or their aliases.
    """
    edit_character_button = "Edit character"
    cancel_edit_button = "Cancel editing"
    delete_character_button = "Delete character"
    delete_alias_button = "Delete"
    done_button = "Done"
    delete_character_dialog_title = "Delete character?"
    delete_character_warning = (
        "Are you sure you want to delete '{name}'? This will also delete all of "
        "this character's aliases. This action cannot be undone."
    )
    delete_alias_dialog_title = "Delete alias?"
    delete_alias_warning = (
        "Are you sure you want to delete the alias '{name}'? This action cannot "
        "be undone."
    )
    confirm_delete_button = "Yes, delete"
    cancel_button = "Cancel"
    character_deleted = "Character deleted."
    alias_deleted = "Alias deleted."


class UserForm:

    header = "Account Settings"
    save_button_text = "Save changes"
    birth_year_question = "What is your birth year?"
    newsletter_label = (
        "Keep me updated with research findings and project news from Fair Tales "
        "(max. one email per month). You can opt out at any time."
    )
    save_success = "Your account settings have been updated."
    page_header = "Your Account"
    page_intro = "Update your personal details below. Changes are saved when you click 'Save changes'."


class PasswordReset:

    # --- Login page "Forgot your password?" area ---
    request_email_label = "Enter your email address"
    request_button_text = "Send password reset link"
    request_blank_email = "Please enter your email address."
    # Shown unconditionally after a reset request to avoid revealing whether an
    # account exists for the entered address (account-enumeration protection).
    request_acknowledgement = (
        "If an account exists for that email address, we've sent it a password "
        "reset link. Please check your inbox (and junk folder). The link expires "
        "in one hour."
    )

    # --- Reset email contents ---
    email_subject = "Reset your account password"
    # %s is the recipient's name.
    email_body = (
        "Dear %s,\n\n"
        "We received a request to reset the password for your account on our "
        "data entry tool. Click the link below to choose a new password. This "
        "link will expire in one hour.\n\n"
        "If you did not request a password reset, you can safely ignore this "
        "email — your password will not be changed.\n\n"
        "Thanks,\n"
        "The Fair Tales team\n\n"
    )

    # --- Reset page (pages/reset_password.py) ---
    page_title = "Reset Your Password"
    new_password_label = "New password"
    confirm_password_label = "Confirm new password"
    submit_button_text = "Set new password"
    invalid_link = (
        "This password reset link is invalid or has already been used. Please "
        "request a new one from the login page."
    )
    expired_link = (
        "This password reset link has expired. Please request a new one from the "
        "login page."
    )
    blank_password = "Please enter and confirm your new password."
    passwords_do_not_match = "The passwords you entered do not match."
    reset_success = (
        "Your password has been reset. You can now log in with your new password "
        "by selecting `Home` from the navigation menu."
    )
    reset_failed = "Password reset failed. Please try again or request a new link."


class ReportFeedback:

    page_title = "Report a Bug / Request a Feature"
    instruction = (
        "Spotted a bug or have an idea? Tell us briefly what happened and what you "
        "expected — or describe the feature you'd like. If it's a bug, include the "
        "page and the steps so we can reproduce it."
    )
    type_label = "Is this a bug report or a feature request?"
    type_options = ["Bug", "Feature"]
    text_label = "Your report"
    text_placeholder = "Describe the bug or feature here…"
    submit_label = "Submit"
    success_message = "Thank you — your report has been submitted!"
    empty_text_warning = "Please enter some text before submitting."


class FeedbackExport:

    header = "Feedback export"
    description = (
        "Download all submitted bug reports and feature requests (from the "
        "Report a Bug / Request a Feature page) for review. May take a moment "
        "for large datasets."
    )
    prepare_button = "Prepare feedback download"
    download_button = "⬇ Download feedback (CSV)"
    file_name = "fairtales_feedback.csv"
    empty_message = "No feedback has been submitted yet."
    error_message = "Could not load the feedback collection: {error}"


class Login:
    """Strings for the sign-in / sign-out / register page (pages/login.py)."""

    # --- Signed-in (sign out) view ---
    sign_out_title = "Sign Out"
    signed_in_as = "Currently signed in as {username}"
    sign_out_prompt = "Would you like to sign out?"
    sign_out_button = "Sign Out"

    # --- Signed-out view ---
    sign_in_title = "Sign In"
    menu_login = "Login"
    menu_register = "Register"
    login_header = "Login"
    email_label = "Email"
    password_label = "Password"
    remember_me_checkbox = "Remember me"
    remember_me_help = "Stay signed in on this browser for 7 days, even after a reload."
    confirm_button = "Confirm"
    resend_button = "Resend confirmation email"
    forgot_password_expander = "Forgot your password?"
    register_header = "Register"
    accept_checkbox = "Accept"


class Nav:
    """Sidebar navigation labels (utilities.page_layout).

    Kept out of page bodies so the sidebar reads from one place (#137/#138/#140).
    """

    # Auth toggle: 'Login' when logged out, 'Sign out' when logged in (#138).
    login = "Login"
    sign_out = "Sign out"
    # Logged-out sidebar shows only Login + Donate (#137).
    donate = "Donate"
    # Authenticated sidebar links.
    home = "Home"
    books_we_need = "Books We Need"
    settings = "Settings"
    report = "Report a Bug / Feature"
    data_validation = "Data validation"
    admin = "Admin"
    back = "← Back"
    # Unobtrusive current-user caption so a wrong account is noticed (#140).
    signed_in_as = "Signed in as {username}"


class BookEditHome:
    """Strings for the book edit/home page (pages/book_edit_home.py)."""

    # %s placeholders / format fields filled at render time.
    editing_book_title = "Editing book: {title}"
    no_api_key = "AI theme suggestion requires an Anthropic API key."
    no_story_text = "No story text found. Please enter text for the book pages first."
    analysing_spinner = "Analysing book text for themes..."
    detection_failed = "Theme detection failed: {error}"
    themes_suggested = "Themes suggested: {themes}. Reasoning: {reasoning}"
    no_new_themes = "No new themes to add. Reasoning: {reasoning}"

    # option_menu items (also used as the navigation dispatch keys)
    menu_instructions = "Instructions"
    menu_edit_metadata = "Edit metadata"
    menu_upload_photos = "Upload photos"
    menu_enter_text = "Enter text"
    menu_manage_characters = "Manage characters"

    suggest_themes_button = "🏷 Suggest themes"
    back_to_home_button = "Back to home menu."
    finish_submit_button = "Finish and submit book"


class Admin:
    """Strings for the admin page (pages/admin.py)."""

    not_admin = "This page is only accessible to admin users."
    title = "Admin"
    validation_link_label = "→ Go to data validation"

    # Reconstruct-orphans section, moved out of the sidebar to the bottom of this
    # page (#141). The reconstruct page itself stays team-and-above gated.
    reconstruct_section_header = "Reconstruct orphaned books"
    reconstruct_section_description = (
        "Rebuild a book from a storage folder of page photos that has no matching "
        "book record. Opens the reconstruction tool (team members and admins)."
    )
    reconstruct_link_label = "→ Reconstruct orphaned books"

    user_data_header = "User data"
    user_data_description = "Download all available fields for confirmed users (excluding sensitive fields such as password and confirmation token) for analysis."
    prepare_user_download_button = "Prepare user data download"
    download_user_button = "⬇ Download user list (CSV)"
    user_file_name = "fairtales_users.csv"

    book_export_header = "Book database export"
    book_export_description = "Download a ZIP of CSV files — one per collection — for research use. May take a moment for large datasets."
    prepare_book_download_button = "Prepare book data download"
    download_book_button = "⬇ Download book database (ZIP of CSVs)"
    book_file_name = "fairtales_book_data.zip"

    # Role management (#47 / #83). Admins can grant or revoke roles in-app
    # instead of editing the Firestore user document by hand.
    manage_roles_header = "Manage user roles"
    manage_roles_description = (
        "Set each user's permission tier. Archivists can enter and edit their "
        "own books; Team members can additionally edit others' books and "
        "validate data; Admins can additionally delete users/books and export "
        "data."
    )
    # User-facing labels for the three role values (utilities.VALID_ROLES).
    role_labels = {
        "archivist": "Archivist",
        "team": "Team member",
        "admin": "Admin",
    }
    role_select_label = "Role"
    role_save_button = "Save role"
    role_current_caption = "Current role: {role}"
    role_updated_success = "Updated {username}'s role to {role}."
    role_update_error = "Could not update the role for {username}: {error}"
    roles_load_error = "Could not load the user list: {error}"
    roles_empty_message = "No users found."
    # Self-lockout safeguard: an admin must not demote their own account out of
    # the admin role, or they would lose access to this page.
    role_self_demote_blocked = (
        "You cannot remove your own admin access — this would lock you out of "
        "admin management. Ask another admin to change your role."
    )


class RegisterUser:
    """Strings for the registration form (pages/register_user.py and
    pages/register_user_done.py)."""

    title = "User Registration"
    email_label = "Email"
    name_label = "Name"
    password_label = "Password"
    birth_year_label = "What is your birth year?"
    birth_year_placeholder = "Select year of birth"
    # Validation field name shown in the "{field} cannot be empty" warning.
    birth_year_field = "Birth year"
    newsletter_label = (
        "Keep me updated with research findings and project news from Fair Tales "
        "(max. one email per month). You can opt out at any time."
    )
    register_button = "Register"


class PhotoUpload:
    """Strings for the photo-upload page (pages/page_photo_upload.py)."""

    enter_book_data_title = "Enter book data: {title}"
    link_line = "Or you can use the following link: [%s](%s)"
    finished_instruction = "When you have finished, you can continue below to enter the text for this book, or return to the menu."

    # Shared upload-method chooser + QR-to-phone option, reused by every direct-to-S3
    # upload surface (add_book_photos / add_books_batch / collection_picker, #143).
    method_upload_here = "Upload here"
    method_go_to_phone = "Go to phone"
    qr_instruction = (
        "Take the photos on your phone, then scan this QR code to open the uploader "
        "there. Each photo uploads straight to secure storage."
    )
    qr_return_instruction = (
        "Once every photo has finished uploading on your phone, come back to this "
        "screen and continue."
    )
    continue_button = "Continue"
    back_to_menu_button = "Back to menu"
    continue_to_text_button = "Continue to enter text"
    replace_button = "Replace / re-upload photos"

    # AI page-extraction failures (#132). Shown after processing so the user knows
    # which pages the AI couldn't read and therefore need entering by hand. The raw
    # API error is NEVER shown here — full details go to the extraction_errors debug
    # log for Chris to review later. Used by the single-book and reconstruction
    # flows (which know the exact page numbers); the batch flow, whose page numbers
    # reset per book, uses the aggregate ``_batch`` variant.
    extraction_partial_fail = (
        "{failed} of {total} page(s) couldn't be read automatically — "
        "you'll need to enter those manually. Affected page(s): {pages}."
    )
    extraction_partial_fail_batch = (
        "{count} page(s) across the uploaded books couldn't be read "
        "automatically — you'll need to enter those manually."
    )
    # Shown when no Anthropic API key is configured, so automatic text
    # recognition (OCR) is skipped entirely and every page is saved blank for
    # manual entry. Mirrors the batch (BatchBookEntry.no_api_key) and
    # reconstruction (ReconstructOrphans.no_api_key) notices so the single-book
    # page-upload flow never silently skips OCR with no message shown (#153).
    no_api_key = (
        "Automatic text recognition is unavailable because no AI API key is "
        "configured — pages have been saved blank for you to enter by hand."
    )


class ReviewBooks:
    """Strings for the review-my-books page (pages/review_my_books.py)."""

    header = "Review my books"
    select_label = "My entered books:"
    # Team-member / admin variants: they may edit books uploaded by anyone (#83).
    all_header = "Review all books"
    all_select_label = "All entered books:"
    edit_button = "Edit this book."
    cancel_button = "Cancel editing books."


class Confirm:
    """Strings for the email-confirmation page (pages/confirm.py)."""

    already_confirmed = "User account already confirmed. Please proceed to login by selecting `Home` in navigation menu."
    success = "User registration successful! You can now proceed to login by selecting `Home` from the navigation menu."
    invalid_link = "Invalid or expired confirmation link. Please request a new confirmation email."
    failed = "Registration failed. Please try again."


class UserHome:
    """Strings for the user home / search page (pages/user_home.py)."""

    book_search_label = "Search by book title (filters as you type)"
    book_search_help = "You can enter either all or part of the title."
    results_found = "Results ({count} found):"
    unknown = "Unknown"
    not_recorded = "Not recorded"
    unknown_title = "Unknown title"
    publisher_label = "**Publisher:** {name}"
    illustrator_label = "**Illustrator:** {name}"
    book_expander = "{title}{year_str}  —  {author}"
    author_expander = "{name}  —  b. {birth_year}  |  {gender}"
    no_books_for_author = "No books found for this author."
    books_label = "**Books:**"

    # option_menu items (also used as the navigation dispatch keys)
    menu_search_books = "Search Books"
    menu_search_authors = "Search Authors"
    menu_add_book = "Add a Book"
    menu_edit_books = "Edit my Books"


class Uploader:
    """Strings for the shared upload widget (pages/uploader.py)."""

    select_photos_label = "Select page photos to upload"
    status_header = "Processing your photos..."
    saving_photo = "Saving photo {current} of {total}..."
    photos_saved = "Photos saved."
    processing_page = "Processing page {page} of {total} (correcting image, extracting text)..."
    # Fine-grained per-page sub-steps (#110). Updating the status at every sub-step
    # gives the browser frequent messages so the websocket does not look hung /
    # drop to "Connecting…" during the long synchronous AI pipeline.
    substep_correcting = "Page {page} of {total}: straightening and cropping the image..."
    substep_checking_crop = "Page {page} of {total}: checking the crop..."
    substep_detecting_rotation = "Page {page} of {total}: checking the orientation..."
    substep_extracting = "Page {page} of {total}: reading the text..."
    page_corrected = "✓ Page {page} of {total} — auto-corrected ({method})"
    page_correction_unavailable = "⚠ Page {page} of {total} — correction unavailable, using original"
    processing_complete = "Processing complete."
    isbn_metadata_found = "Found book metadata via ISBN {isbn}: {title}"
    upload_complete = "Page photo upload complete, you may continue."
    continue_button = "Continue"

    # Direct-to-S3 browser upload (#114/#118). Replaces st.file_uploader so the
    # native photo picker no longer drops the Streamlit websocket on mobile; each
    # photo PUTs straight from the device to S3 at full resolution.
    direct_upload_instructions = (
        "Tap **Select page photos** and choose this book's pages in page order. "
        "Each photo uploads straight from your device — watch the progress bars, "
        "then tap **Process photos** once they have all finished."
    )
    process_button = "Process photos"
    no_photos_uploaded = (
        "We couldn't find any uploaded photos yet. Please select your page photos "
        "above and wait for every progress bar to finish, then try again."
    )


class BookDataEntry:
    """Strings for the legacy book-data-entry page (pages/book_data_entry.py)."""

    # option_menu items (also used as the navigation dispatch keys)
    menu_upload_photos = "Upload page photos"
    menu_enter_text = "Enter text"
    menu_add_character = "Add a Character"
    save_button = "Save"
    not_implemented = "Not implemented yet!"


class QrLanding:
    """Strings for the QR deep-link upload page (pages/qr_landing.py)."""

    title = "Photo uploader."
    # Shown in the generic flow/session direct-upload mode (#143): the phone only
    # PUTs the photos into the computer surface's temp prefix, then the user
    # returns to the computer to continue.
    phone_done_instruction = (
        "When every photo has finished uploading above, return to the computer and "
        "continue there. You can then close this page."
    )


class Validation:
    """Strings for the data-validation page (pages/validation.py)."""

    intro = "Here you may validate inputted data"
    # Shown when an archivist (below team tier) tries to open the validation page.
    not_authorised = "This page is only accessible to project team members and admins."

    # --- Awaiting-validation list (issue #47, Part A) ---
    list_header = "Books to validate"
    list_intro = (
        "Select any book to review and validate. Every book that has not yet "
        "been validated appears here."
    )
    none_pending = "There are no books awaiting validation right now."
    select_book_label = "Books:"
    open_review_button = "Open for review"
    submitted_only_toggle = "Show only books submitted for validation"
    # Scope control (#131): validators see ALL books by default, with the option to
    # narrow to just the books they themselves entered.
    scope_label = "Show"
    scope_all = "All books"
    scope_mine = "Just mine"

    # --- Review surface ---
    review_header = "Reviewing: {title}"
    entered_by_label = "Originally entered by: {name}"
    review_intro = (
        "Correct any errors in the metadata, page text and characters below. "
        "Every correction you make is recorded — together with the original "
        "value — to the edit log. When the entry is correct and complete, "
        "approve it at the bottom of the page."
    )
    back_to_list_button = "← Back to list (do not approve)"
    tab_metadata = "Metadata"
    tab_pages = "Page text"
    tab_characters = "Characters & aliases"

    # --- Metadata editor ---
    metadata_header = "Book metadata"
    title_readonly_caption = (
        "The title is the book's identity (it keys its pages and characters) and "
        "cannot be changed here."
    )
    none_option = "—"
    save_metadata_button = "Save metadata corrections"
    metadata_saved = "Metadata corrections saved."

    # --- Page-text editor ---
    pages_header = "Page text"
    no_pages = "This book has no pages recorded yet."
    page_select_label = "Page:"
    page_contains_story_label = "This page contains story text"
    page_text_label = "Page text (correct the transcription):"
    page_not_entered = "No text has been entered for this page yet — you can add it below."
    save_page_button = "Save page-text corrections"
    page_saved = "Page-text corrections saved."

    # --- Characters & aliases editor ---
    characters_header = "Characters & aliases"
    no_characters = "This book has no characters recorded yet."
    character_select_label = "Character:"
    save_character_button = "Save character corrections"
    character_saved = "Character corrections saved."
    character_name_required = "A character must have a name."
    rename_exists = "Another character with that name already exists in this book."
    aliases_label = "Aliases"
    no_aliases = "No aliases are recorded for this character."
    alias_name_label = "Alias name:"
    save_alias_button = "Save alias"
    alias_saved = "Alias correction saved."
    alias_exists = "Another alias with that name already exists in this book."

    # --- Approval ---
    approve_header = "Approve this entry"
    approve_help = (
        "Mark this book as validated once you are confident the data is correct "
        "and complete. This records you as the validator."
    )
    approve_button = "Approve & mark validated"
    approved_success = "“{title}” has been approved and marked as validated."


class CollectionPicker:
    """Strings for the results collection-picker page (pages/collection_picker.py, #75)."""

    page_title = "Choose a book collection"
    intro = """
        Pick which collection of books you want to see results for. You can choose
        a ready-made collection, search our database to build your own, or upload
        photos of a stack of books and we will match them for you.
    """

    # Method menu (option_menu)
    menu_search = "Search & select"
    menu_predefined = "Predefined collections"
    menu_photo = "From photos"

    # --- Current selection panel ---
    selection_header = "Your selection"
    selection_empty = (
        "No books selected yet. Use one of the methods below to build a "
        "collection, or view results across all books."
    )
    selection_count = "{n} book(s) in your collection."
    remove_book_button = "Remove"
    clear_selection_button = "Clear selection"
    view_results_button = "View results for this collection"
    view_all_button = "View results for all books instead"

    # --- Method 1: search & select ---
    search_header = "Search our database and tick the books you want"
    search_label = "Search book titles"
    search_results_found = "{count} matching book(s):"
    add_book_checkbox = "{title}"

    # --- Method 2: predefined collections ---
    predefined_header = "Browse predefined collections"
    predefined_none = "There are no predefined collections yet."
    predefined_select_label = "Choose a collection"
    predefined_owner_label = "Scope: {owner}"
    predefined_books_label = "Books in this collection:"
    predefined_use_button = "Load this collection into my selection"
    predefined_view_button = "View results for this collection"
    predefined_empty_collection = "This collection has no books."

    create_header = "Save your current selection as a predefined collection"
    create_help = (
        "Creating predefined collections is intended for the FairTales team / "
        "admins. You can still save one here; please use a clear, descriptive name."
    )
    create_name_label = "Collection name"
    create_owner_label = "Owner / scope (optional — e.g. a school name)"
    create_nothing_selected = (
        "Select some books first (via search or photos), then save them as a "
        "collection."
    )
    create_name_required = "Please give the collection a name."
    create_exists = "A collection with that name and scope already exists."
    create_button = "Save collection"
    create_success = "Saved collection '{name}' with {n} book(s)."

    # --- Method 3: from photos ---
    photo_header = "Upload photos of your books"
    photo_instructions = """
        Upload one or more photos showing several books — either their front
        covers, or a stack/shelf with the spines facing the camera so the titles
        and authors are legible. We will read off the titles and match them to
        books in our database.
    """
    photo_upload_label = "Upload book photo(s)"
    # Direct-to-S3 browser upload (#118). These cover/spine photos are transient —
    # they are only used to read titles, never archived — so the temp S3 prefix is
    # cleaned up straight after they are read.
    photo_direct_upload_instructions = (
        "Tap **Select book photos** and choose your cover/spine photo(s). Each "
        "uploads straight from your device — watch the progress bars, then tap "
        "**Read books from photo(s)** once they have all finished."
    )
    photo_no_photos_uploaded = (
        "We couldn't find any uploaded photos yet. Please select your book photo(s) "
        "above and wait for every progress bar to finish, then try again."
    )
    photo_extract_button = "Read books from photo(s)"
    photo_no_api_key = (
        "Photo matching is unavailable because no AI API key is configured."
    )
    photo_extracting = "Reading the books in your photo(s)…"
    photo_extract_failed = "Could not read the photo(s): {error}"
    photo_none_found = (
        "No book titles could be read from the photo(s). Try clearer, closer "
        "photos with the titles facing the camera."
    )
    photo_matched_header = "Matched to our database ({count}):"
    photo_unmatched_header = "Could not match these ({count}):"
    photo_matched_item = "{extracted} → {matched}"
    photo_add_matched_button = "Add matched books to my selection"
    photo_added = "Added {n} matched book(s) to your selection."


class Reconstruction:
    """Progress + error strings for the shared book-reconstruction core
    (``book_reconstruction.py``), used by both the orphan-reconstruction admin
    page (#122) and the fully-automated upload flow (#123)."""

    # Pipeline progress (passed to the caller's progress callback).
    extracting_metadata = "Reading the book's title page and details…"
    saving_photo = "Saving photo {current} of {total}…"
    processing_page = "Processing page {page} of {total} (correcting image, reading text)…"
    detecting_characters = "Looking for characters across the story…"
    detecting_progress = "Identifying characters ({done} of {total})…"
    finalising = "Finishing up and sending the book to the validation queue…"

    # Errors (raised as ValueError, surfaced by the caller).
    error_no_photos = "No photos were provided to reconstruct a book from."
    error_no_title = (
        "Could not read a title for this book, and no folder name was available "
        "to fall back on. Reconstruction needs at least one of these."
    )
    error_book_exists = (
        "A book titled '{title}' already exists in the database, so it was not "
        "recreated. Open it in the existing tools instead of reconstructing it."
    )
    error_folder_collision = (
        "The destination photo folder 'sawimages/{title}/' already holds "
        "{count} page photo(s) from a different folder ('{source_folder}'), so "
        "reconstruction was stopped rather than overwriting them. Rename the "
        "source folder or remove the conflicting folder, then try again."
    )


class ReconstructOrphans:
    """Strings for the admin 'Reconstruct orphaned books' page
    (pages/reconstruct_orphans.py, #122)."""

    header = "Reconstruct orphaned books"
    intro = (
        "Some image folders in storage hold a complete set of page photos but have "
        "no matching book record — usually because the book was deleted or lost. "
        "Pick one below to rebuild the book from its photos using the AI pipeline. "
        "The reconstructed book is sent straight to the validation queue for a "
        "human to review."
    )
    # Shown when a below-team user reaches the page.
    not_authorised = "This page is only accessible to project team members and admins."
    no_api_key = (
        "Reconstruction is unavailable because no AI API key is configured."
    )

    refresh_button = "Refresh the list"
    scanning = "Scanning storage for orphaned photo folders…"
    none_found = "No orphaned photo folders were found. Nothing to reconstruct."
    found_count = "Found {count} orphaned photo folder(s)."
    select_label = "Orphaned photo folder:"
    folder_option = "{folder} ({count} photos)"
    reconstruct_button = "Reconstruct this book"

    status_header = "Reconstructing the book…"
    success_header = "Book reconstructed and sent for validation."
    success_summary = (
        "Created '{title}' with {pages} page(s), {characters} character(s) and "
        "{aliases} alias(es). It now appears in the **Data validation** queue for "
        "review."
    )
    moved_notice = (
        "The reconstructed book's photos were written to the canonical folder "
        "'{photos_folder}', which differs from the original folder "
        "'{source_folder}'. The now-redundant '{source_folder}' folder was removed "
        "automatically, so it no longer appears as an orphan."
    )
    error = "Could not reconstruct the book: {error}"
    no_photos_in_folder = (
        "No page photos could be read from that folder. It may have been emptied "
        "since the list was built — refresh and try another."
    )
    validation_link_label = "Go to Data validation"
