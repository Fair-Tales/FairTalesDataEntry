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


class AliasForm:

    header = "Please select a character and enter their alias:"
    character_exists = """
            This alias already exists in the database.
    """