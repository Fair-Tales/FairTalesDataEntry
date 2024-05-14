import json

class Book:

    fields = {
        'title': "",
        'character_count': -1,
        'page_count': -1,
        'word_count': -1,
        'sentence_count': -1,
        'datetime_created': -1,
        'entered_by': None,
        'entry_status': 'started',
        'first_content_page': -1,
        'last_content_page': -1,
        'illustrator': None,
        'publisher': None,
        'last_updated': -1,
        'published': 2024,
        'validated': False,
        'validated_by': None
    }

    form_fields = {
        'title': 'Title',
        'published': 'Date published'
    }

    def __init__(self, db_object=None):
        if db_object is None:
            for key in self.fields.keys():
                setattr(self, key, self.fields[key])

        else:
            for key in self.fields.keys():
                setattr(self, key, db_object[key])

    def to_dict(self):

        return {
                key: getattr(self, key)
                for key in self.fields.keys()
            }
