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

"""
Publicly Facing Media Controllers
"""
import shutil
import os.path
import simplejson as json
import ftplib
import urllib2
import sha
import time
import logging
import formencode
from urlparse import urlparse
from datetime import datetime, timedelta, date

from tg import config, request, response, tmpl_context
import tg.exceptions
from tg.controllers import CUSTOM_CONTENT_TYPE
from sqlalchemy import orm, sql
from formencode import validators
from paste.deploy.converters import asbool
from paste.util import mimeparse
from akismet import Akismet

from mediacore.lib.base import (BaseController, url_for, redirect,
    expose, expose_xhr, validate, paginate)
from mediacore.model import (DBSession, fetch_row, get_available_slug,
    Media, MediaFile, Comment, Tag, Category, Author, AuthorWithIP, Podcast)
from mediacore.lib import helpers, email
from mediacore.forms.comments import PostCommentForm
from mediacore import __version__ as MEDIACORE_VERSION

log = logging.getLogger(__name__)

post_comment_form = PostCommentForm()


class MediaController(BaseController):
    """
    Media actions -- for both regular and podcast media
    """

    @expose('mediacore.templates.media.index')
    @paginate('media', items_per_page=20)
    def index(self, page=1, **kwargs):
        """List media with pagination.

        The media paginator may be accessed in the template with
        :attr:`c.paginators.media`, see :class:`webhelpers.paginate.Page`.

        :param page: Page number, defaults to 1.
        :type page: int
        :param search: A search query to filter by
        :type search: unicode or None
        :rtype: dict
        :returns:
            media
                The list of :class:`~mediacore.model.media.Media` instances
                for this page.

        """
        media = Media.query\
            .published()\
            .filter(Media.podcast_id == None)\
            .order_by(Media.publish_on.desc())\
            .options(orm.undefer('comment_count_published'))

        return dict(
            media = media,
        )

    @expose('mediacore.templates.media.search')
    @paginate('media', items_per_page=20)
    def search(self, page=1, q=None, **kwargs):
        """Search media with pagination.

        The media paginator may be accessed in the template with
        :attr:`c.paginators.media`, see :class:`webhelpers.paginate.Page`.

        :param page: Page number, defaults to 1.
        :type page: int
        :param search: A search query to filter by
        :type search: unicode or None
        :rtype: dict
        :returns:
            media
                The list of :class:`~mediacore.model.media.Media` instances
                for this page.

        """
        media = Media.query\
            .published()\
            .search(q)\
            .options(orm.undefer('comment_count_published'))

        return dict(
            media = media,
            search_query = q,
            result_count = media.count(),
        )


    @expose('mediacore.templates.media.view')
    def view(self, slug, podcast_slug=None, **kwargs):
        """Display the media player, info and comments.

        :param slug: The :attr:`~mediacore.models.media.Media.slug` to lookup
        :param podcast_slug: The :attr:`~mediacore.models.podcasts.Podcast.slug`
            for podcast this media belongs to. Although not necessary for
            looking up the media, it tells us that the podcast slug was
            specified in the URL and therefore we reached this action by the
            preferred route.
        :rtype dict:
        :returns:
            media
                The :class:`~mediacore.model.media.Media` instance for display.
            comment_form
                The :class:`~mediacore.forms.comments.PostCommentForm` instance.
            comment_form_action
                ``str`` comment form action
            comment_form_values
                ``dict`` form values
            next_episode
                The next episode in the podcast series, if this media belongs to
                a podcast, another :class:`~mediacore.model.media.Media`
                instance.

        """
        media = fetch_row(Media, slug=slug)
        media.increment_views()

        next_episode = None
        if media.podcast_id is not None:
            # Always view podcast media from a URL that shows the context of the podcast
            if url_for() != url_for(podcast_slug=media.podcast.slug):
               redirect(podcast_slug=media.podcast.slug)

            if media.is_published:
                next_episode = Media.query.published()\
                    .filter(Media.podcast_id == media.podcast.id)\
                    .order_by(Media.publish_on)\
                    .first()

        return dict(
            media = media,
            comments = media.comments.published().all(),
            comment_form = post_comment_form,
            comment_form_action = url_for(action='comment', anchor=post_comment_form.id),
            comment_form_values = kwargs,
            next_episode = next_episode,
            related_media = Media.query[:5],
        )


    @expose_xhr()
    def rate(self, slug, **kwargs):
        """Rate up or down the given media.

        :param slug: The media :attr:`~mediacore.model.media.Media.slug`
        :rtype: JSON dict
        :returns:
            succcess
                bool
            upRating
                Pluralized count of up raters, "# people" or "1 person"
            downRating
                Pluralized count of down raters, "# people" or "1 person"

        """
        media = fetch_row(Media, slug=slug)
        likes = media.increment_likes()
        DBSession.add(media)

        if request.is_xhr:
            # TODO: Update return arg naming
            return dict(
                success = True,
                upRating = helpers.text.plural(likes, 'person', 'people'),
                downRating = None,
            )
        else:
            redirect(action='view')


    @expose()
    @validate(post_comment_form, error_handler=view)
    def comment(self, slug, **values):
        """Post a comment from :class:`~mediacore.forms.comments.PostCommentForm`.

        :param slug: The media :attr:`~mediacore.model.media.Media.slug`
        :returns: Redirect to :meth:`view` page for media.

        """
        akismet_key = config.get('akismet_key', None)
        if akismet_key:
            akismet = Akismet(agent='MediaCore/%s' % MEDIACORE_VERSION)
            akismet.key = akismet_key
            akismet.blog_url = config.get('akismet_url',
                                          url_for('/', qualified=True))
            akismet.verify_key()
            data = {'comment_author': values['name'].encode('utf-8'),
                    'user_ip': request.environ.get('REMOTE_ADDR'),
                    'user_agent': request.environ.get('HTTP_USER_AGENT'),
                    'referrer': request.environ.get('HTTP_REFERER',  'unknown'),
                    'HTTP_ACCEPT': request.environ.get('HTTP_ACCEPT')}
            if akismet.comment_check(values['body'].encode('utf-8'), data):
                redirect(action='view', commented=1, spam=1, anchor='top')
        else:
            log.debug('No Akismet API Key specified, spam filter disabled.')

        media = fetch_row(Media, slug=slug)

        c = Comment()
        c.author = AuthorWithIP(values['name'], None, request.environ['REMOTE_ADDR'])
        c.subject = 'Re: %s' % media.title
        c.body = helpers.clean_xhtml(values['body'])

        require_review = asbool(config.get('req_comment_approval', 'false'))
        if not require_review:
            c.reviewed = True
            c.publishable = True

        media.comments.append(c)
        DBSession.add(media)
        email.send_comment_notification(media, c)

        if require_review:
            # TODO: Update this to use a local session, not a GET flag
            redirect(action='view', commented=1, anchor='top')
        else:
            redirect(action='view', anchor='comment-%s' % c.id)


    @expose(content_type=CUSTOM_CONTENT_TYPE)
    @validate(validators={'id': validators.Int()})
    def serve(self, id, slug, type, **kwargs):
        """Serve a :class:`~mediacore.model.media.MediaFile` binary.

        :param id: File ID
        :type id: ``int``
        :param slug: The media :attr:`~mediacore.model.media.Media.slug`
        :type slug: The file :attr:`~mediacore.model.media.MediaFile.type`
        :raises tg.exceptions.HTTPNotFound: If no file exists for the given params.
        :raises tg.exceptions.HTTPNotAcceptable: If an Accept header field
            is present, and if the mimetype of the requested file doesn't
            match, then a 406 (not acceptable) response is returned.

        """
        media = fetch_row(Media, slug=slug)

        for file in media.files:
            if file.id == id and file.type == type:
                # Redirect to an external URL
                if urlparse(file.url)[1]:
                    redirect(file.url.encode('utf-8'))

                # Ensure that the clients request allows for files of this type
                mimetype = mimeparse.best_match([file.mimetype],
                    request.environ.get('HTTP_ACCEPT', '*/*'))
                if mimetype == '':
                    raise tg.exceptions.HTTPNotAcceptable() # 406

                file_name = '%s-%s.%s' % (media.slug, file.id, file.type)
                file_path = os.path.join(config.media_dir, file.url)
                file_handle = open(file_path, 'rb')

                response.headers['Content-Type'] = mimetype
                response.headers['Content-Disposition'] = \
                    'attachment;filename=%s' % file_name.encode('utf-8')
                return file_handle.read()
        else:
            raise tg.exceptions.HTTPNotFound()

    @expose('mediacore.templates.media.explore')
    @paginate('media', items_per_page=20)
    def explore(self, page=1, **kwargs):
        """Display the most recent 15 media.

        :rtype: Dict
        :returns:
            media
                Latest media

        """
        media = Media.query\
            .published()\
            .order_by(Media.publish_on.desc())\
            .filter(Media.podcast_id == None)

        return dict(
            media = media,
        )
