class Instructions:
    home_intro = """
        Welcome to the data entry tool for the SAWS project!
        Thank you for taking the time to enter data for us.
    """

    @staticmethod
    def last_saved(timestamp):
        """Reassurance message that the book's edits are persisted (issue #53).

        ``timestamp`` is a timezone-aware UTC datetime (Book.last_updated).
        """
        return f"Last saved: {timestamp:%Y-%m-%d %H:%M UTC}"

    advise_to_search = """
        Before adding a book to our database, please search to check that we do not already have the book. You can also 
        search by author to see which books that we have by them.
    """
    photo_upload_header = """
        1. Upload photos of pages
    """
    photo_upload_instructions = """
        The next step is for you to take and upload photos of each page of the book. This is easiest on a 
        mobile device with a camera such as a smart phone. 
        
        Instructions for taking the photos:
        - Please take clear and high resolution images (the clearer the better!).
        - Take landscape photos of the whole double-page spreads (except front and rear covers, these should be separate portrait photos). 
        - Take photos in order, starting with the front cover.
        - Ensure you get the whole page in the photo, including the corners and edges.
        - Ensure good lighting and try to reduce glare.
        - Try to hold the book flat - you can use your thumb or finger.  
        - Please upload all photos in one batch.
    """
    photos_already_uploaded = """
        You have already uploaded photos for this book.

        You can continue straight to entering the text, or replace the photos if
        you need to re-take, re-order or add pages. Replacing will re-upload and
        re-process the whole set of photos for this book.
    """

    go_to_phone_instructions = """
        First take the photos on your phone. 
        Then scan the QR code below, which will take you to the upload page.
        (If this does not work, you can also log into this app on your mobile device and go via 
        the `Edit my books` option on your homepage).
    """

    upload_here_instructions = """
        Or you can upload the photos here, if you prefer.
        
        Transfer the photos to this device and then upload then using the widget below.
    """

    data_entry_instructions = """
        You will now be guided through entering the data for this book.
        First, you will need to take clear photos of each page of the book, and
        upload these by selecting `Upload page photos` in the menu below.
        Then you will need to select `Enter text` and proceed to write out the text 
        for each of the pages in the book. Finally, you will need to add the details of all 
        character in the book.
        
        You can click `Save` at any time and return to complete the data entry later.    
    """

    review_my_books = """
        Here you can select books to edit from those that you started entering previously.
        Any data that you entered previously should have saved automatically.
        Please note that only books that you have not submitted for validation are
        available to edit.
    """

    author_publisher_illustrator_select = """
        Please select author, publisher and illustrator. 
        If not listed, please select `None of these` and you will be guided 
        to enter these details on the next steps.
    """

    book_edit_home_intro = """
        Please make sure that you have uploaded photos of the book pages before you try to enter text!
    """

    book_edit_home_instructions = """
            More instructions to follow...
        """

    upload_success_return = """
        Page photo upload was successful!
        You can now close this window and return to enter the book text on your other device.
    """

    landing_intro = """
        What would you like to do today?
    """

    landing_enter_data_label = "Enter data"

    landing_enter_data_description = """
        Contribute to the project by entering book and character data.
    """

    landing_view_results_label = "View results"

    landing_view_results_description = """
        Explore the research results for our collection of books.
    """

    landing_results_coming_soon = """
        The results viewer is coming soon! Check back later to explore our research findings.
    """

    author_search_label = (
        "Search by author name — results filter as you type. "
        "Enter all or part of the name to find close matches."
    )