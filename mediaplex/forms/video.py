from tw.forms import ListForm, ListFieldSet, TextField, CalendarDatePicker, SingleSelectField, TextArea, SubmitButton
from formencode.validators import Int, NotEmpty, DateConverter, DateValidator
from tw.api import WidgetsList

class VideoForm(ListForm):
    template = 'mediaplex.templates.admin.video.form'
    id = 'video-form'
    css_class = 'form'
    submit_text = None

    fields = [
        TextField('slug', validator=NotEmpty),
        TextField('title', validator=NotEmpty),
        TextField('author_name', validator=NotEmpty),
        TextField('author_email', validator=NotEmpty),
        TextArea('description', attrs=dict(rows=5, cols=25)),
        TextArea('notes', label_text='Additional Notes', attrs=dict(rows=5, cols=25)),
        TextField('tags'),
        ListFieldSet('details', suppress_label=True, legend='Video Details:', children=[
            TextField('duration'),
            TextField('url', label_text='Video URL')
        ]),
        SubmitButton('save', label_text='Save', css_classes=['btn-save', 'f-rgt']),
    ]

#    def display(self, value=None, **kw):
#        if value is not None:
#            value = newval
#        return super(VideoForm, self).display(value, **kw)
