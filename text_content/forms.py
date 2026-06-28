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

class PublisherForm:
    header = "Please enter publisher details."
    publisher_exists = """
        This publisher already exists in the database. Please either select them from the 
        dropdown menu above, or enter a unique name for your new publisher.
    """


class BookPhotoEntry:
    """Strings for the photo-initiated ("photos first") book entry flow (#59)."""

    menu_label = "Add Book from Photos"
    header = "Add a book from photos"
    instructions = """
        Upload photos of the book pages to get started. We'll read the title page
        for you and use it to pre-fill the book details on the next step — you can
        review and correct everything before saving.

        Please include a clear photo of the **title page** (the inside page showing
        the title, author and illustrator). Upload your photos in page order if you
        can; if the title page is not the first photo, you can tell us which one it
        is below.
    """
    upload_label = "Select the book's page photos to upload"
    title_page_label = "Which photo is the title page?"
    title_page_help = (
        "Select the photo that shows the book's title, author and illustrator. "
        "This is the image we send for automatic detail extraction."
    )
    extract_button = "Read title page and pre-fill details"
    extracting = "Reading the title page..."
    extract_failed = (
        "Could not read the title page automatically ({error}). "
        "You can still enter the details manually."
    )
    extract_empty = (
        "We couldn't extract any details from that photo. Please check it is the "
        "title page and try again, or enter the details manually on the next step."
    )
    no_api_key = (
        "Automatic title-page extraction requires an Anthropic API key. "
        "You can still enter the details manually."
    )
    extract_success = "Title page read. Please review and correct the details below."
    reuse_notice = "Using the {count} photo(s) you already uploaded. Processing them now..."
    cancel_text = "Cancel"


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
    protagonist_help = """
        The protagonist is the main character.
    """
    plural_help = """
            Select if this is a group or collection of characters e.g. 'the witches'.
        """
    human_help = """
            Select if character is human (rather than non-human - animal, monster etc).
        """


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


class ManageCharacters:

    header = "Manage characters and aliases"
    intro = """
        Below are the characters you have recorded for this book. You can delete
        a character — which also deletes all of its aliases — or delete an
        individual alias. Deletions are permanent and cannot be undone.
    """
    no_characters = "No characters have been added to this book yet."
    aliases_label = "Aliases:"
    no_aliases = "No aliases recorded for this character."
    manage_button = "Manage characters"
    manage_help = """
        View the characters recorded for this book and delete characters or
        their aliases.
    """
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
