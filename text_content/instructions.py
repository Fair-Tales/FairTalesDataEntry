class Instructions:
    app_title = "Fair Tales Data Entry Tool"

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
    # Canonical "How to photograph a book" block (#186), rendered on EVERY upload
    # surface — including the phone QR landing page, where users previously saw no
    # guidance at all. Leads with the photos-first order and keeps the key
    # framing/lighting/order tips tight enough to read on a phone.
    photo_instructions_expander_title = "📸 How to photograph the book (tap to read)"
    photo_instructions_canonical = """
        **Take all the photos first, then upload them all together.**

        - Take the photos in reading order, starting with the front cover.
        - Photograph the whole double-page spreads in **landscape**; photograph the front and
          rear covers on their own as **portrait** photos.
        - Get the whole page in frame, including the corners and edges.
        - Keep it clear and high-resolution: good lighting, minimal glare, and hold the book
          as flat as you can (use a thumb at the edge).
        - Use the same phone and camera app for the whole book, then upload the photos in one batch.
    """

    photo_naming_instructions = """
        You don't need to rename your photos. Your phone names them in the order you take
        them, and we put the pages back in that order from the file names — so just take the
        photos in reading order and upload them all together (the order you pick them in
        doesn't matter). Only tip: use the same phone and camera app for the whole book, as
        mixing photos from different devices in one upload can put them out of order.
    """

    # Shown whenever a book already has processed photos — including the moment
    # straight after the user's own upload finishes (#180), so it must read as a
    # "here's what happened / what's next" status, NOT as a duplicate-upload
    # warning. ``count_str`` is e.g. " (14 pages)" or "" when the count is
    # unknown; the caller builds it (pages/page_photo_upload.py).
    photos_already_uploaded = """
        This book's page photos are uploaded and processed{count_str}, and the
        text has been read from them automatically wherever possible.

        **Next step:** continue to check and enter the text for each page — the
        automatically-read text is already filled in for you to correct.

        If any pages need re-taking, re-ordering, or adding, you can replace the
        photos instead. Replacing re-uploads and re-processes the whole set of
        photos for this book.
    """

    # Recommended order stated explicitly (#186): take ALL the photos first, then
    # scan and pick them from the camera roll. The "scan first / use the phone
    # camera" alternative is deliberately NOT offered — photos-first is the one
    # instruction we want every user to follow.
    go_to_phone_instructions = """
        **Take all the photos first**, then scan the QR code below and select them from your
        camera roll in one go.
        (If the QR code does not work, you can also log into this app on your mobile device and go via
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
        If any of them is not listed, choose `None of these` — you'll create the new
        author, publisher or illustrator on the next page (don't type their name into
        the select box here).
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

    landing_blurb = """
        FairTales uses AI to explore how gender and diversity are portrayed in
        children's picture books. Every book and character you enter helps power
        that research — thank you for being part of it!
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

    author_search_label = "Search by author name (filters as you type)"