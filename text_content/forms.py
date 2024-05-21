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


class AuthorForm:
    header = "Please enter author details."
    gender_options = ["Woman", "Man", "Non-binary", "Other", "Unknown"]
    gender_prompt = """
        Please use a web search to enter the gender of the author to the best of your knowledge.
        If it is not clear from a cursory search, please select `Unknown`.
    """
    author_exists = """
        This author already exists in the database. Please either select them from the 
        dropdown menu above, or enter a unique name for your new author.
    """


class EnterText:

    header = "Please enter text and add characters"
    instruction = """
        - You only need to enter text for the pages that contain the story (or main content of the book).
        - Please enter with spelling verbatim and use 'enter' for newlines. 
        - When a linebreak appears to mark the end of a sentence please add a full stop. **This is very
        important for our language analysis.**
        - Please also add all characters and their aliases as you encounter them.
    """


class CharacterForm:

    header = "Please enter details of this character"

    gender_options = ["Female", "Male", "Non-specific", "Transgender"]
    gender_help = """
        Please select character gender based only on pronouns. If it is not clear
        because gendered pronouns are not used, please select `Non-specific`.
        Please do not use the illustrations to infer gender and only select
        `Transgender` if this is explicit in the text.
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
