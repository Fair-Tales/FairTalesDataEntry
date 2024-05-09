class Alerts:
    no_matching_book = """
        No matching books found! Please ensure that the title is spelled correctly.
    """

    user_exists = "Username already in use! Please choose another."
    email_sent = "You have been sent an email - please click the link in the message to continue registration."

    @staticmethod
    def no_blank_field(field_name):
        return f"{field_name} cannot be empty, please complete."