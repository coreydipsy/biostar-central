import sys, time, os, logging
import mailbox, markdown, pyzmail
from django.conf import settings
from django.utils.timezone import utc
from django.utils import timezone, encoding
from email.utils import parsedate
from datetime import datetime
import itertools
from django.core.management.base import BaseCommand, CommandError
from optparse import make_option
from itertools import *
import re, textwrap, urllib
from chardet import detect

from django.db.models import signals

logger = logging.getLogger('simple-logger')

# This needs to be shorter so that the content looks good
# on smaller screens as well.
LINE_WIDTH = 70

TEMPDIR = "import/bioc"
DRY_RUN = False

if not os.path.isdir(TEMPDIR):
    os.mkdir(TEMPDIR)


def path_join(*args):
    return os.path.abspath(os.path.join(*args))


class Command(BaseCommand):
    help = 'migrate data from Biostar 1.*'

    option_list = BaseCommand.option_list + (
        make_option("-f", '--file', dest='file', default=False, help='import file'),
        make_option("-l", '--limit', dest='limit', default=None, help='limit posts'),
        make_option("-t", '--tags', dest='tags', default=None, help='tags'),
        make_option("-d", '--dry', dest='dry', action='store_true', default=False,
                    help='dry run, parses the emails only'),
    )

    def handle(self, *args, **options):
        global DRY_RUN
        fname = options['file']
        tags = options['tags']
        limit = options['limit']
        DRY_RUN = options['dry']
        if fname:
            parse_mboxx(fname, limit=limit, tag_val=tags)


class Bunch(object):
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


def guess_tags(text, tag_val):
    return tag_val


REPLACE_PATT = [
    "[Galaxy-user]",
    "[Galaxy-User]",
    "[galaxy-user]",
    "[BioC]",
]

SKIPPED_SIZE = 0
SIZE_LIMIT = 10000
SKIPPED_REPLY = 0


def create_post(b, author, root=None, parent=None, tag_val=''):
    from biostar.apps.posts.models import Post

    title = b.subj
    body = b.body
    if not parent:
        title = title.strip()
        title = ' '.join(title.splitlines())
        title = ' '.join(title.split())
        title = title.title()
        post = Post(title=title, type=Post.QUESTION, content=body, tag_val=tag_val, author=author)
    else:
        post_type = Post.ANSWER if parent.is_toplevel else Post.COMMENT
        post = Post(type=post_type, content=body, tag_val="galaxy", author=author, parent=parent)

    post.creation_date = post.lastedit_date = b.date

    if not DRY_RUN:
        post.save()

    tag_val = guess_tags(post.content, tag_val)

    if tag_val and not DRY_RUN:
        post.add_tags(tag_val)

    logger.info("--- creating %s: %s" % (post.get_type_display(), title))

    return post


def fix_file(fname):
    "Fixes the obfuscated emails in mbox files"
    new_name = "tmp-output.txt"
    logger.info("*** fixing obfuscated emails: %s" % new_name)
    fp = open(new_name, 'wt')
    for line in file(fname):
        if line.startswith("From: "):
            line = line.replace(" at ", "@")
        fp.write(line)
    fp.close()
    return new_name


def no_junk(line):
    "Gets rid of lines that contain junk"
    # invalid starts
    for word in "> --- From:".split():
        if line.strip().startswith(word):
            return False
    # junk words
    for word in "scrubbed attachment.html wrote: Sent:".split():
        if word in line:
            return False
    return True


def format_text(text):
    global LINE_WIDTH
    lines = text.splitlines()
    lines = filter(no_junk, lines)
    lines = [textwrap.fill(line, width=LINE_WIDTH) for line in lines]
    text = "\n".join(lines)
    text = "<div class='preformatted'>" + text + "</div>"
    return text


def to_unicode_or_bust(obj, encoding='utf-8'):
    if isinstance(obj, basestring):
        if not isinstance(obj, unicode):
            obj = unicode(obj, encoding)
    return obj


def bioc_remote_body(body):
    "Attempts to fetch remote body posts"

    # This is a fix for importing the bioconductor email list
    # Fetch the page if it is missing
    if "URL: <https://stat.ethz.ch/pipermail" in body:
        lines = body.splitlines()
        lines = filter(lambda x: x.startswith("URL:"), lines)
        if lines:
            line = lines[0]
            url = line.split()[1]
            url = url[1:-1]
            elems = url.split("/")
            fid = elems[-3] + elems[-2]
            fname = "%s/%s" % (TEMPDIR, fid)
            if not os.path.isfile(fname):
                logger.info(">>> fetching %s" % url)
                web = urllib.urlopen(url)
                text = web.read()
                try:
                    text = to_unicode_or_bust(text)
                except Exception, exc:
                    logger.error(exc)
                    text = "Error: unable to decode %s" % url
                fp = open(fname, 'wt')
                fp.write(text.encode("utf-8"))
                fp.close()
            body = open(fname).read().decode('utf8')
    return body


def unpack_message(data):
    msg = pyzmail.PyzMessage(data)

    # Get the name and email the message is coming from
    name, email = msg.get_address('from')
    email = email.lower()

    # Parse the date
    date = msg.get_decoded_header("Date")
    subj = msg.get_subject()
    if not date or not subj:
        return None

    date = parsedate(date)
    date = datetime(*date[:6])
    date = timezone.make_aware(date, timezone=utc)

    b = Bunch(name=name, email=email, date=date)
    b.id = msg.get_decoded_header("Message-ID")
    b.reply_to = msg.get_decoded_header('In-Reply-To')
    b.subj = subj
    for patt in REPLACE_PATT:
        b.subj = b.subj.replace(patt, "")

    # Get the body of the message
    if not msg.text_part:
        return None

    body = msg.text_part.get_payload()
    charset = detect(body)['encoding']
    body = body.decode(charset).encode('utf-8')

    # Checks for remote body for bioconductor import
    body = bioc_remote_body(body)

    # Reformat the body
    body = format_text(body)

    try:
        b.body = to_unicode_or_bust(body)
    except UnicodeDecodeError, exc:
        # Ignore this post
        return None

    return b


def parse_mboxx(filename, limit=None, tag_val=''):
    from biostar.apps.users.models import User
    from biostar.apps.posts.models import Post

    global SKIPPED_REPLY

    #users = User.objects.all().delete()
    users = User.objects.all()
    users = dict([(u.email, u) for u in users])

    #Post.objects.all().delete()

    logger.info("*** found %s users" % len(users))

    if limit is not None:
        limit = int(limit)

    signals.post_save.disconnect(dispatch_uid="create_messages")

    logger.info("*** parsing mbox %s" % filename)

    new_name = fix_file(filename)

    # Parse the modified mbox.
    mbox = mailbox.mbox(new_name)
    rows = imap(unpack_message, mbox)

    # Remove empty elements
    rows = ifilter(None, rows)
    # Keep only email with sender and subject.
    rows = ifilter(lambda b: b.email, rows)
    rows = ifilter(lambda b: b.subj, rows)

    # Apply limits if necessary.
    rows = islice(rows, limit)

    tree, posts = {}, {}

    for b in rows:
        datefmt = b.date.strftime('%Y-%m-%d')
        logger.info("*** %s parsing %s " % (datefmt, b.subj))

        if b.email not in users:

            logger.info("--- creating user name:%s, email:%s" % (b.name, b.email))
            u = User(email=b.email, name=b.name)
            if not DRY_RUN:
                u.save()
                u.profile.date_joined = b.date
                u.profile.last_login = b.date
                u.profile.save()

            users[u.email] = u

        author = users[b.email]

        parent = posts.get(b.reply_to)
        if parent:
            root = parent.root
            post = create_post(b=b, author=author, parent=parent)
        else:
            post = create_post(b=b, author=author, tag_val=tag_val)

        posts[b.id] = post

    logger.info("*** users %s" % len(users))
    logger.info("*** posts %s" % len(posts))
    logger.info("*** post limit: %s" % limit)
    logger.info("*** skipped posts due to size: %s" % SKIPPED_SIZE)
    logger.info("*** skipped posts due to missing parent: %s" % SKIPPED_REPLY)

    if DRY_RUN:
        logger.info("*** dry run, no data saved")
        sys.exit()

    logger.info("*** updating user scores")
    for user in User.objects.all():
        score = Post.objects.filter(author=user).count()
        user.score = user.full_score = score
        user.save()
        latest = Post.objects.filter(author=user).order_by("-creation_date")[:1]
        if latest:
            user.profile.last_login = latest[0].creation_date
            user.profile.save()


if __name__ == '__main__':
    pass