# This file is a part of MediaCore, Copyright 2009 Simple Station Inc.
#
# MediaCore is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# MediaCore is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
"""Helper functions

Consists of functions to typically be used within templates, but also
available to Controllers. This module is available to templates as 'h'.
"""
import datetime as dt
import hashlib
import math
import os
import re
import shutil
import time
from PIL import Image
from copy import copy
from datetime import datetime
from urllib import quote, unquote
from urlparse import urlparse

import genshi.core
import pylons.templating
import simplejson
import webob.exc

from BeautifulSoup import BeautifulSoup
from paste.util import mimeparse
from pylons import config, request, response, url as pylons_url
from webhelpers import date, feedgenerator, html, number, misc, text, paginate, containers
from webhelpers.html import tags
from webhelpers.html.converters import format_paragraphs
from webob.exc import HTTPNotFound

from mediacore.lib.htmlsanitizer import Cleaner, entities_to_unicode as decode_entities, encode_xhtml_entities as encode_entities
from mediacore.lib.filetypes import accepted_extensions, pick_media_file_player

def url_for(*args, **kwargs):
    """Compose a URL using the route mappings in :mod:`mediacore.config.routes`.

    This is a wrapper for :func:`pylons.url`, all arguments are passed.

    Using the REPLACE and REPLACE_WITH GET variables, if set,
    this method replaces the first instance of REPLACE in the
    url string. This can be used to proxy an action at a different
    URL.

    For example, by using an apache mod_rewrite rule:

    .. sourcecode:: apacheconf

        RewriteRule ^/proxy_url(/.\*){0,1}$ /proxy_url$1?_REP=/mycont/actionA&_RWITH=/proxyA [qsappend]
        RewriteRule ^/proxy_url(/.\*){0,1}$ /proxy_url$1?_REP=/mycont/actionB&_RWITH=/proxyB [qsappend]
        RewriteRule ^/proxy_url(/.\*){0,1}$ /mycont/actionA$1 [proxy]

    """
    # Convert unicode to str utf-8 for routes
    def to_utf8(value):
        if isinstance(value, unicode):
            return value.encode('utf-8')
        return value

    if args:
        args = [to_utf8(val) for val in args]
    if kwargs:
        kwargs = dict( (key, to_utf8(val)) for key, val in kwargs.items() )

    print "args:"
    print args
    print "kwargs:"
    print kwargs

    # TODO: Rework templates so that we can avoid using .current, and use named
    # routes, as described at http://routes.groovie.org/manual.html#generating-routes-based-on-the-current-url
    # NOTE: pylons.url is a StackedObjectProxy wrapping the routes.url method.
    url = pylons_url.current(*args, **kwargs)

    # If the proxy_prefix config directive is set up, then we need to make sure
    # that the SCRIPT_NAME is prepended to the URL. This SCRIPT_NAME prepending
    # is necessary for mod_proxy'd deployments, and for FastCGI deployments.
    # XXX: Leaking abstraction below. This code is tied closely to Routes 1.12
    #      implementation of routes.util.URLGenerator.__call__()
    # If the arguments given didn't describe a raw URL, then Routes 1.12 didn't
    # prepend the SCRIPT_NAME automatically--we'll need to feed the new URL
    # back to the routing method to prepend the SCRIPT_NAME.
    prefix = config.get('proxy_prefix', None)
    if prefix:
        if args:
            named_route = config['routes.map']._routenames.get(args[0])
            protocol = urlparse(args[0]).scheme
            static = not named_route and (args[0][0]=='/' or protocol)
        else:
            static = False
            protocol = ''

        if not static:
            if kwargs.get('qualified', False):
                offset = len(urlparse(url).scheme+"://")
            else:
                offset = 0
            path_index = url.index('/', offset)
            url = url[:path_index] + prefix + url[path_index:]

    # Make the URL string replacements based on GET vars.
    repl = request.str_GET.getall('_REP')
    repl_with = request.str_GET.getall('_RWITH')
    for i in range(0, min(len(repl), len(repl_with))):
        url = url.replace(repl[i], repl_with[i], 1)

    return url

def redirect(*args, **kwargs):
    """Compose a URL using :func:`url_for` and raise a redirect.

    :raises: :class:`webob.exc.HTTPFound`
    """
    url = url_for(*args, **kwargs)
    found = webob.exc.HTTPFound(location=url)
    raise found.exception

def duration_from_seconds(total_sec):
    """Return the HH:MM:SS duration for a given number of seconds.

    Does not support durations longer than 24 hours.

    :param total_sec: Number of seconds to convert into hours, mins, sec
    :type total_sec: int
    :rtype: unicode
    :returns: String HH:MM:SS, omitting the hours if less than one.

    """
    if not total_sec:
        return u''
    total = time.gmtime(total_sec)
    if total.tm_hour > 0:
        return u'%d:%02d:%02d' % total[3:6]
    else:
        return u'%d:%02d' % total[4:6]

def duration_to_seconds(duration):
    """Return the number of seconds in a given HH:MM:SS.

    Does not support durations longer than 24 hours.

    :param duration: A HH:MM:SS or MM:SS formatted string
    :type duration: unicode
    :rtype: int
    :returns: seconds
    :raises ValueError: If the input doesn't matched the accepted formats

    """
    if not duration:
        return 0
    try:
        total = time.strptime(duration, '%H:%M:%S')
    except ValueError:
        total = time.strptime(duration, '%M:%S')
    return total.tm_hour * 60 * 60 + total.tm_min * 60 + total.tm_sec

def truncate(string, size, whole_word=True):
    """Truncate a plaintext string to roughly a given size (full words).

    :param string: plaintext
    :type string: unicode
    :param size: Max length
    :param whole_word: Whether to prefer truncating at the end of a word.
        Defaults to True.
    :rtype: unicode
    """
    return text.truncate(string, size, whole_word=whole_word)

# Configuration for HTML sanitization
blank_line = re.compile("\s*\n\s*\n\s*", re.M)
block_tags = 'p br pre blockquote div h1 h2 h3 h4 h5 h6 hr ul ol li form table tr td tbody thead'.split()
block_spaces = re.compile("\s*(</{0,1}(" + "|".join(block_tags) + ")>)\s*", re.M)
block_close = re.compile("(</(" + "|".join(block_tags) + ")>)", re.M)
valid_tags = dict.fromkeys('p i em strong b u a br pre abbr ol ul li sub sup ins del blockquote cite'.split())
valid_attrs = dict.fromkeys('href title'.split())
elem_map = {'b': 'strong', 'i': 'em'}
truncate_filters = ['strip_empty_tags']
cleaner_filters = [
        'add_nofollow', 'br_to_p', 'clean_whitespace', 'encode_xml_specials',
        'make_links', 'rename_tags', 'strip_attrs', 'strip_cdata',
        'strip_comments', 'strip_empty_tags', 'strip_schemes', 'strip_tags'
]
# Map all invalid block elements to be paragraphs.
for t in block_tags:
    if t not in valid_tags:
        elem_map[t] = 'p'
cleaner_settings = dict(
    convert_entities = BeautifulSoup.ALL_ENTITIES,
    valid_tags = valid_tags,
    valid_attrs = valid_attrs,
    elem_map = elem_map,
    filters = cleaner_filters
)

def clean_xhtml(string, p_wrap=True, _cleaner_settings=None):
    """Convert the given plain text or HTML into valid XHTML.

    If there is no markup in the string, apply paragraph formatting.

    :param p_wrap: Wrap the output in <p></p> tags?
    :param _cleaner_settings: Constructor kwargs for
        :class:`mediacore.lib.htmlsanitizer.Cleaner`
    :type _cleaner_settings: dict
    :returns: XHTML
    :rtype: unicode
    """
    if not string or not string.strip():
        # If the string is none, or empty, or whitespace
        return u""

    if _cleaner_settings is None:
        _cleaner_settings = cleaner_settings

    # remove carriage return chars; FIXME: is this necessary?
    string = string.replace(u"\r", u"")

    # remove non-breaking-space characters. FIXME: is this necessary?
    string = string.replace(u"\xa0", u" ")
    string = string.replace(u"&nbsp;", u" ")

    # replace all blank lines with <br> tags
    string = blank_line.sub(u"<br/>", string)

    # initialize and run the cleaner
    string = Cleaner(string, **_cleaner_settings)()
    # FIXME: It's possible that the rename_tags operation creates
    # some invalid nesting. e.g.
    # >>> c = Cleaner("", "rename_tags", elem_map={'h2': 'p'})
    # >>> c('<p><h2>head</h2></p>')
    # u'<p><p>head</p></p>'
    # This is undesirable, so here we... just re-parse the markup.
    # But this ... could be pretty slow.
    cleaner = Cleaner(string, **_cleaner_settings)
    string = cleaner()

    # Wrap in a <p> tag when no tags are used, and there are no blank
    # lines to trigger automatic <p> creation
    # FIXME: This should trigger any time we don't have a wrapping block tag
    # FIXME: This doesn't wrap orphaned text when it follows a <p> tag, for ex
    if p_wrap \
        and len(cleaner.root.contents) == 1 \
        and isinstance(cleaner.root.contents[0], basestring):
        string = u"<p>%s</p>" % string.strip()

    # strip all whitespace from immediately before/after block-level elements
    string = block_spaces.sub(u"\\1", string)

    return string.strip()

def truncate_xhtml(string, size, _strip_xhtml=False, _decode_entities=False):
    """Truncate a XHTML string to roughly a given size (full words).

    :param string: XHTML
    :type string: unicode
    :param size: Max length
    :param _strip_xhtml: Flag to strip out all XHTML
    :param _decode_entities: Flag to convert XHTML entities to unicode chars
    :rtype: unicode
    """
    if not string:
        return u''

    if _strip_xhtml:
        # Insert whitespace after block elements.
        # So they are separated when we strip the xhtml.
        string = block_spaces.sub(u"\\1 ", string)
        string = strip_xhtml(string)

    string = decode_entities(string)

    if len(string) > size:
        string = text.truncate(string, length=size, whole_word=True)

        if _strip_xhtml:
            if not _decode_entities:
                # re-encode the entities, if we have to.
                string = encode_entities(string)
        else:
            if _decode_entities:
                string = Cleaner(string,
                                 *truncate_filters, **cleaner_settings)()
            else:
                # re-encode the entities, if we have to.
                string = Cleaner(string, 'encode_xml_specials',
                                 *truncate_filters, **cleaner_settings)()

    return string.strip()

def strip_xhtml(string, _decode_entities=False):
    """Strip out xhtml and optionally convert HTML entities to unicode.

    :rtype: unicode
    """
    if not string:
        return u''

    string = ''.join(BeautifulSoup(string).findAll(text=True))

    if _decode_entities:
        string = decode_entities(string)

    return string

def line_break_xhtml(string):
    """Add a linebreak after block-level tags are closed.

    :type string: unicode
    :rtype: unicode
    """
    if string:
        string = block_close.sub(u"\\1\n", string).rstrip()
    return string


def list_acceptable_xhtml():
    return dict(
        tags = ", ".join(sorted(valid_tags)),
        attrs = ", ".join(sorted(valid_attrs)),
        map = ", ".join(["%s -> %s" % (t, elem_map[t]) for t in elem_map])
    )

def list_accepted_extensions():
    """Return the extensions allowed for upload for printing.

    :returns: Comma separated extensions
    :rtype: unicode
    """
    e = accepted_extensions()
    if len(e) > 1:
        e[-1] = 'and ' + e[-1]
    return ', '.join(e)

def _normalize_thumb_item(item):
    """Pass back the image subdir and id when given a media or podcast."""
    try:
        return item._thumb_dir, item.id or 'new'
    except AttributeError:
        return item

def thumb_path(item, size, exists=False, ext='jpg'):
    """Get the thumbnail path for the given item and size.

    :param item: A 2-tuple with a subdir name and an ID. If given a
        ORM mapped class with _thumb_dir and id attributes, the info
        can be extracted automatically.
    :type item: ``tuple`` or mapped class
    :param size: Size key to display, see ``thumb_sizes`` in
        :mod:`mediacore.config.app_config`
    :type size: str
    :param exists: If enabled, checks to see if the file actually exists.
        If it doesn't exist, ``None`` is returned.
    :type exists: bool
    :param ext: The extension to use, defaults to jpg.
    :type ext: str
    :returns: The absolute system path or ``None``.
    :rtype: str

    """
    if not item:
        return None

    image_dir, item_id = _normalize_thumb_item(item)
    image = '%s/%s%s.%s' % (image_dir, item_id, size, ext)
    image_path = os.path.join(config['image_dir'], image)

    if exists and not os.path.isfile(image_path):
        return None
    return image_path

def thumb_paths(item, **kwargs):
    """Return a list of paths to all sizes of thumbs for a given item.

    :param item: A 2-tuple with a subdir name and an ID. If given a
        ORM mapped class with _thumb_dir and id attributes, the info
        can be extracted automatically.
    :type item: ``tuple`` or mapped class

    """
    image_dir, item_id = _normalize_thumb_item(item)
    return [thumb_path(item, key, **kwargs)
            for key in config['thumb_sizes'][image_dir].iterkeys()]

def thumb_url(item, size, qualified=False, exists=False):
    """Get the thumbnail url for the given item and size.

    :param item: A 2-tuple with a subdir name and an ID. If given a
        ORM mapped class with _thumb_dir and id attributes, the info
        can be extracted automatically.
    :type item: ``tuple`` or mapped class
    :param size: Size key to display, see ``thumb_sizes`` in
        :mod:`mediacore.config.app_config`
    :type size: str
    :param qualified: If ``True`` return the full URL including the domain.
    :type qualified: bool
    :param exists: If enabled, checks to see if the file actually exists.
        If it doesn't exist, ``None`` is returned.
    :type exists: bool
    :returns: The relative or absolute URL.
    :rtype: str

    """
    if not item:
        return None

    image_dir, item_id = _normalize_thumb_item(item)
    image = '%s/%s%s.jpg' % (image_dir, item_id, size)

    if exists and not os.path.isfile(os.path.join(config['image_dir'], image)):
        return None
    return url_for('/images/%s' % image, qualified=qualified)

class ThumbDict(dict):
    """Dict wrapper with convenient attribute access"""

    def __init__(self, url, dimensions):
        self['url'] = url
        self['x'], self['y'] = dimensions

    def __getattr__(self, name):
        return self[name]

def thumb(item, size, qualified=False, exists=False):
    """Get the thumbnail url & dimensions for the given item and size.

    :param item: A 2-tuple with a subdir name and an ID. If given a
        ORM mapped class with _thumb_dir and id attributes, the info
        can be extracted automatically.
    :type item: ``tuple`` or mapped class
    :param size: Size key to display, see ``thumb_sizes`` in
        :mod:`mediacore.config.app_config`
    :type size: str
    :param qualified: If ``True`` return the full URL including the domain.
    :type qualified: bool
    :param exists: If enabled, checks to see if the file actually exists.
        If it doesn't exist, ``None`` is returned.
    :type exists: bool
    :returns: The url, width (x) and height (y).
    :rtype: :class:`ThumbDict` with keys url, x, y OR ``None``

    """
    if not item:
        return None

    image_dir, item_id = _normalize_thumb_item(item)
    url = thumb_url(item, size, qualified, exists)

    if not url:
        return None
    return ThumbDict(url, config['thumb_sizes'][image_dir][size])

def resize_thumb(img, size, filter=Image.ANTIALIAS):
    """Resize an image without any stretching by cropping when necessary.

    If the given image has a different aspect ratio than the requested
    size, the tops or sides will be cropped off before resizing.

    Note that stretching will still occur if the target size is larger
    than the given image.

    :param img: Any open image
    :type img: :class:`PIL.Image`
    :param size: The desired width and height
    :type size: tuple
    :param filter: The downsampling filter to use when resizing.
        Defaults to PIL.Image.ANTIALIAS, the highest possible quality.
    :returns: A new, resized image instance

    """
    X, Y, X2, Y2 = 0, 1, 2, 3 # aliases for readability

    src_ratio = float(img.size[X]) / img.size[Y]
    dst_ratio = float(size[X]) / size[Y]

    if dst_ratio != src_ratio and (img.size[X] >= size[X] and
                                   img.size[Y] >= size[Y]):
        crop_size = list(img.size)
        crop_rect = [0, 0, 0, 0] # X, Y, X2, Y2

        if dst_ratio < src_ratio:
            crop_size[X] = int(crop_size[Y] * dst_ratio)
            crop_rect[X] = int(float(img.size[X] - crop_size[X]) / 2)
        else:
            crop_size[Y] = int(crop_size[X] / dst_ratio)
            crop_rect[Y] = int(float(img.size[Y] - crop_size[Y]) / 2)

        crop_rect[X2] = crop_rect[X] + crop_size[X]
        crop_rect[Y2] = crop_rect[Y] + crop_size[Y]

        img = img.crop(crop_rect)

    return img.resize(size, filter)

def create_default_thumbs_for(item):
    """Create copies of the default thumbs for the given item.

    This copies the default files (all named with an id of 'new') to
    use the given item's id. This means there could be lots of duplicate
    copies of the default thumbs, but at least we can always use the
    same url when rendering.

    :param item: A 2-tuple with a subdir name and an ID. If given a
        ORM mapped class with _thumb_dir and id attributes, the info
        can be extracted automatically.
    :type item: ``tuple`` or mapped class

    """
    image_dir, item_id = _normalize_thumb_item(item)
    for key in config['thumb_sizes'][image_dir].iterkeys():
        src_file = thumb_path((image_dir, 'new'), key)
        dst_file = thumb_path(item, key)
        shutil.copyfile(src_file, dst_file)

def append_class_attr(attrs, class_name):
    """Append to the class for any input that Genshi's py:attrs understands.

    This is useful when using XIncludes and you want to append a class
    to the body tag, while still allowing all other tags to remain
    unchanged.

    For example::

        <body py:match="body" py:attrs="h.append_class_attr(select('@*'), 'extra_special')">

    :param attrs: A collection of attrs
    :type attrs: :class:`genshi.core.Stream`, :class:`genshi.core.Attrs`,
        ``list`` of 2-tuples, ``dict``
    :param class_name: The class name to append
    :type class_name: unicode
    :returns: All attrs
    :rtype: ``dict``

    """
    if isinstance(attrs, genshi.core.Stream):
        attrs = list(attrs)
        attrs = attrs and attrs[0] or []
    if not isinstance(attrs, dict):
        attrs = dict(attrs or ())
    attrs['class'] = unicode(attrs.get('class', '') + ' ' + class_name).strip()
    return attrs

excess_whitespace = re.compile('\s\s+', re.M)

def embeddable_player(media):
    """Return a string of XHTML for embedding our player on other sites.

    All URLs include the domain (they're fully qualified).

    Since this returns a plain string, it is automatically escaped by Genshi
    when called in a template.

    :param media: The item to embed
    :type media: :class:`mediacore.model.media.Media` instance
    :returns: XHTML
    :rtype: unicode

    """
    xhtml = pylons.templating.render_genshi(
        'media/_embeddable_player.html',
        extra_vars=dict(media=media),
        method='xhtml'
    )
    xhtml = excess_whitespace.sub(' ', xhtml)
    return xhtml.strip()

def get_featured_category():
    from mediacore.model import Category
    feat_id = fetch_setting('featured_category')
    if not feat_id:
        return None
    feat_id = int(feat_id)
    return Category.query.get(feat_id)

def filter_library_controls(query, show='latest'):
    from mediacore.model import Media
    if show == 'latest':
        query = query.order_by(Media.publish_on.desc())
    elif show == 'popular':
        query = query.order_by(Media.popularity_points.desc())
    elif show == 'featured':
        featured_cat = get_featured_category()
        if featured_cat:
            query = query.in_category(featured_cat)
    return query, show

def is_admin():
    """Return True if the logged in user is a part of the Admins group.

    This method will need to be replaced when we improve our user
    access controls.
    """
    return 'Admins' in request.environ\
        .get('repoze.who.identity', {})\
        .get('groups', '')

def fetch_setting(key):
    """Return the value for the setting key.

    Raises a SettingNotFound exception if the key does not exist.
    """
    from mediacore.model import fetch_row, Setting
    from mediacore.model.settings import SettingNotFound
    try:
        return fetch_row(Setting, key=unicode(key)).value
    except HTTPNotFound:
        raise SettingNotFound, 'Key not found: %s' % key

def gravatar_from_email(email, size):
    """Return the URL for a gravatar image matching the povided email address.

    :param email: the email address
    :type email: string or unicode or None
    :param size: the width (or height) of the desired image
    :type size: int
    """
    if email is None:
        email = ''
    # Set your variables here
    gravatar_url = "http://www.gravatar.com/avatar/%s?size=%d" % \
        (hashlib.md5(email).hexdigest(), size)
    return gravatar_url

def pretty_file_size(size):
    """Return the given file size in the largest possible unit of bytes."""
    if not size:
        return u'-'
    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if size < 1024.0:
            return '%3.1f %s' % (size, unit)
        size /= 1024.0
    return '%3.1f %s' % (size, 'PB')

def delete_files(paths, subdir=None):
    """Move the given files to the 'deleted' folder, or just delete them.

    If the config contains a deleted_files_dir setting, then files are
    moved there. If that setting does not exist, or is empty, then the
    files will be deleted permanently instead.

    :param paths: File paths to delete. These files do not necessarily
        have to exist.
    :type paths: list
    :param subdir: A subdir within the configured deleted_files_dir to
        move the given files to. If this folder does not yet exist, it
        will be created.
    :type subdir: str or ``None``

    """
    deleted_dir = config.get('deleted_files_dir', None)
    if deleted_dir and subdir:
        deleted_dir = os.path.join(deleted_dir, subdir)
    if deleted_dir and not os.path.exists(deleted_dir):
        os.mkdir(deleted_dir)
    for path in paths:
        if path and os.path.exists(path):
            if deleted_dir:
                shutil.move(path, deleted_dir)
            else:
                os.remove(path)

def add_transient_message(cookie_name, message_title, message_text):
    """Add a message dict to the serialized list of message dicts stored in
    the named cookie.

    If there is no existing cookie, create one.
    If there is an existing cookie, assumes that it will de-serialize into
    a list object.
    """

    time = datetime.now().strftime('%H:%M, %B %d, %Y')
    msg = dict(
        time = time,
        title = message_title,
        text = message_text,
    )
    old_data = request.cookies.get(cookie_name, None)

    if old_data is not None:
        response.delete_cookie(cookie_name)

    if old_data:
        msgs = simplejson.loads(unquote(old_data))
    else:
        msgs = []
    msgs.append(msg)
    new_data = quote(simplejson.dumps(msgs))
    response.set_cookie(cookie_name, new_data, path='/')

