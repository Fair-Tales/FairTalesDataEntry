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


class AuthorForm:
    header = "Please enter author details."
    gender_options = ["Woman", "Man", "Non-binary", "Other", "Unknown"]
    gender_prompt = """
        Please use a web search to enter the gender of the author to the best of your knowledge.
        If it is not clear from a cursory search, please select `Unknown`.
    """