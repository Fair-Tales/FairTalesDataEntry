class Alerts:
    no_matching_book = """
        No matching books found! Please ensure that the title is spelled correctly.
    """

    user_exists = "Username already in use! Please choose another."
    email_sent = """
        You have been sent an email - please click the link in the message to continue registration.
        If you did not receive the email, please check your junk folder.
    """

    @staticmethod
    def no_blank_field(field_name):
        return f"{field_name} cannot be empty, please complete."

    please_enter_gender = """
        Please either select or enter your gender identify. 
        You can select `Prefer not to say` if you would rather not answer this question.
    """

    please_select_other = """
        Please select `other` from the dropdown list if you want to enter your
        gender identity manually.
    """

    invalid_email = "Please enter a valid email address."
