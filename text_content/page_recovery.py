class PageRecovery:
    save_failed = (
        'The page could not be saved. Your draft is retained in this session. '
        'Retry the save before continuing.'
    )
    collision = (
        'Another save has already created this page. Your draft has been retained '
        'and has not overwritten the saved page. Load the saved page to review it '
        'and copy any changes you still want to make.'
    )
    reload_failed = 'The saved page could not be loaded. Your draft is still retained; try again.'
    page_disappeared = 'The saved page is no longer available. Your draft is still retained.'
    retry_save = 'Retry save'
    load_saved = 'Load saved page'
    draft_label = 'Your retained local draft'
    retained_draft_label = 'Previous local drafts — copy and review before applying changes'
    draft_story = 'Draft: this page contains story text.'
    draft_not_story = 'Draft: this page does not contain story text.'
