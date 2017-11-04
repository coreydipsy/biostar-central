import hjson
import logging
import mistune

from django.db import models
from biostar.accounts.models import User, Group
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

from django.urls import reverse
from django.utils import timezone

from biostar import settings
from . import util
from .const import *

logger = logging.getLogger("engine")

# The maximum length in characters for a typical name and text field.
MAX_NAME_LEN = 256
MAX_TEXT_LEN = 10000
MAX_LOG_LEN = 20 * MAX_TEXT_LEN


def join(*args):
    return os.path.abspath(os.path.join(*args))


class Bunch(object):
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


def make_html(text):
    return mistune.markdown(text)


def get_datatype(file):
    return Data.FILE


def filter_by_type():
    return


def data_upload_path(instance, filename):
    # Name the data by the filename.
    pieces = os.path.basename(filename).split(".")
    # File may have multiple extensions
    exts = ".".join(pieces[1:]) or "data"
    dataname = f"data-{instance.uid}.{exts}"
    return join(instance.project.get_project_dir(), f"{instance.data_dir}", dataname)


def image_path(instance, filename):
    # Name the data by the filename.
    name, ext = os.path.splitext(filename)

    uid = util.get_uuid(6)
    dirpath = instance.get_project_dir()
    imgname = f"image-{uid}{ext}"

    # Uploads need to go relative to media directory.
    path = os.path.relpath(dirpath, settings.MEDIA_ROOT)

    imgpath = os.path.join(path, imgname)

    return imgpath


class Project(models.Model):
    PUBLIC, SHAREABLE, PRIVATE = 1, 2, 3
    PRIVACY_CHOICES = [(PRIVATE, "Private"), (SHAREABLE, "Shareable Link"), (PUBLIC, "Public")]

    ACTIVE, DELETED = 1, 2
    STATE_CHOICES  = [(ACTIVE, "Active"), (DELETED, "Deleted")]

    privacy = models.IntegerField(default=SHAREABLE, choices=PRIVACY_CHOICES)
    state = models.IntegerField(default=ACTIVE, choices=STATE_CHOICES)

    image = models.ImageField(default=None, blank=True, upload_to=image_path)
    name = models.CharField(max_length=256, default="no name")
    summary = models.TextField(default='no summary')

    owner = models.ForeignKey(User)
    text = models.TextField(default='no description', max_length=MAX_TEXT_LEN)

    html = models.TextField(default='html')
    date = models.DateTimeField(auto_now_add=True)

    # Each project belongs to a single group.
    group = models.OneToOneField(Group)
    uid = models.CharField(max_length=32, unique=True)

    def save(self, *args, **kwargs):
        now = timezone.now()
        self.date = self.date or now
        self.html = make_html(self.text)

        self.uid = self.uid or util.get_uuid(8)
        if not os.path.isdir(self.get_project_dir()):
            os.makedirs(self.get_project_dir())

        super(Project, self).save(*args, **kwargs)

    def __str__(self):
        return self.name

    def url(self):
        return reverse("project_view", kwargs=dict(id=self.id))

    def get_project_dir(self):
        return join(settings.MEDIA_ROOT, "projects", f"proj-{self.uid}")


@receiver(pre_save, sender=Project)
def create_project_group(sender, instance, **kwargs):
    """
    Creates a group for the project
    """
    instance.uid = instance.uid or util.get_uuid(8)
    group, created = Group.objects.get_or_create(name=instance.uid)
    instance.group = group

class Data(models.Model):
    FILE, COLLECTION = 1, 2
    PENDING, READY, ERROR, DELETED = 1, 2, 3, 4

    FILETYPE_CHOICES = [(FILE, "File"), (COLLECTION, "Collection")]

    STATE_CHOICES = [(PENDING, "Pending"), (READY, "Ready"), (ERROR, "Error"), (DELETED, "Deleted") ]

    name = models.CharField(max_length=256, default="no name")
    summary = models.TextField(default='no summary')
    image = models.ImageField(default=None, blank=True, upload_to=image_path)

    owner = models.ForeignKey(User)
    text = models.TextField(default='no description', max_length=MAX_TEXT_LEN)
    html = models.TextField(default='html')
    date = models.DateTimeField(auto_now_add=True)
    file_type = models.IntegerField(default=FILE, choices=FILETYPE_CHOICES)

    data_type = models.IntegerField(default=GENERIC_TYPE)
    project = models.ForeignKey(Project)
    size = models.CharField(null=True, max_length=256)

    state = models.IntegerField(default=PENDING, choices=STATE_CHOICES)
    file = models.FileField(null=True, upload_to=data_upload_path, max_length=500)
    uid = models.CharField(max_length=32)

    # Will be false if the objects is to be deleted.
    valid = models.BooleanField(default=True)

    # Data directory.
    data_dir = models.FilePathField(default="")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def save(self, *args, **kwargs):
        now = timezone.now()
        self.uid = self.uid or util.get_uuid(8)
        self.date = self.date or now
        self.html = make_html(self.text)

        # Build the data directory.
        data_dir = self.get_datadir()
        if not os.path.isdir(data_dir):
            os.makedirs(data_dir)
        self.data_dir = data_dir
        super(Data, self).save(*args, **kwargs)

    def peek(self):
        """
        Returns a preview of the data
        """
        return util.smart_preview(self.get_path())

    def set_size(self):
        """
        Sets the size of the data.
        """
        try:
            size = os.path.getsize(self.get_path())
        except:
            size = 0
        Data.objects.filter(id=self.id).update(size=size)

    def __str__(self):
        return self.name

    def get_datadir(self):
        return join(self.project.get_project_dir(), f"store-{self.uid}")

    def get_project_dir(self):
        return self.project.get_project_dir()

    def get_path(self):
        return self.file.path

    def can_unpack(self):
        cond = str(self.file.path).endswith("tar.gz")
        return cond

    def fill_dict(self, obj):
        """
        Mutates a dictionary to add more information.
        """
        obj['path'] = self.get_path()
        obj['data_id'] = self.id
        obj['name'] = self.name
        obj['uid'] = self.uid


class Analysis(models.Model):
    AUTHORIZED, UNDER_REVIEW = 1, 2

    AUTH_CHOICES = [(AUTHORIZED, "Authorized"), (UNDER_REVIEW, "Under Review")]

    ACTIVE, DELETED = 1, 2
    STATE_CHOICES = [(ACTIVE, "Active"), (DELETED, "Deleted")]


    uid = models.CharField(max_length=32, unique=True)

    name = models.CharField(max_length=256, default="No name")
    summary = models.TextField(default='No summary.')
    text = models.TextField(default='No description.', max_length=MAX_TEXT_LEN)
    html = models.TextField(default='html')
    owner = models.ForeignKey(User)

    auth = models.IntegerField(default=UNDER_REVIEW, choices=AUTH_CHOICES)
    state = models.IntegerField(default=ACTIVE, choices=STATE_CHOICES)

    project = models.ForeignKey(Project)

    json_text = models.TextField(default="{}")
    template = models.TextField(default="makefile")

    date = models.DateTimeField(auto_now_add=True, blank=True)
    image = models.ImageField(default=None, blank=True, upload_to=image_path)

    def __str__(self):
        return self.name

    @property
    def json_data(self):
        "Returns the json_text as parsed json_data"
        return hjson.loads(self.json_text)

    def save(self, *args, **kwargs):
        now = timezone.now()
        self.uid = self.uid or util.get_uuid(8)
        self.date = self.date or now

        self.html = make_html(self.text)
        super(Analysis, self).save(*args, **kwargs)

    def get_project_dir(self):
        return self.project.get_project_dir()

class Job(models.Model):
    AUTHORIZED, UNDER_REVIEW = 1, 2
    AUTH_CHOICES = [(AUTHORIZED, "Authorized"), (UNDER_REVIEW, "Under Review")]

    QUEUED, RUNNING, COMPLETED, ERROR, DELETED = 1, 2, 3, 4, 5
    STATE_CHOICES = [(QUEUED, "Queued"), (RUNNING, "Running"),
                     (COMPLETED, "Completed"), (ERROR, "Error"), (DELETED, "Deleted")]


    name = models.CharField(max_length=256, default="no name")
    summary = models.TextField(default='no summary')
    image = models.ImageField(default=None, blank=True, upload_to=image_path)

    owner = models.ForeignKey(User)
    text = models.TextField(default='no description', max_length=MAX_TEXT_LEN)
    html = models.TextField(default='html')
    date = models.DateTimeField(auto_now_add=True)

    analysis = models.ForeignKey(Analysis)
    project = models.ForeignKey(Project)
    json_text = models.TextField(default="commands")

    uid = models.CharField(max_length=32)
    template = models.TextField(default="makefile")

    # Set the security level.
    security = models.IntegerField(default=UNDER_REVIEW, choices=AUTH_CHOICES)

    # This will be set when the job attempts to run.
    script = models.TextField(default="")

    # Keeps track of errors.
    stdout_log = models.TextField(default="", max_length=MAX_LOG_LEN)

    # Standard error.
    stderr_log = models.TextField(default="", max_length=MAX_LOG_LEN)

    # Will be false if the objects is to be deleted.
    valid = models.BooleanField(default=True)

    state = models.IntegerField(default=1, choices=STATE_CHOICES)

    path = models.FilePathField(default="")

    def is_running(self):
        return self.state == Job.RUNNING

    def __str__(self):
        return self.name

    def get_url(self, path=''):
        "Return the url to the job directory"
        return f"jobs/job-{self.uid}/" + path

    def get_project_dir(self):
        return self.project.get_project_dir()

    @property
    def json_data(self):
        "Returns the json_text as parsed json_data"
        return hjson.loads(self.json_text)

    def save(self, *args, **kwargs):
        now = timezone.now()
        self.name = self.name or self.analysis.name
        self.date = self.date or now
        self.html = make_html(self.text)

        self.uid = self.uid or util.get_uuid(8)
        self.template = self.analysis.template
        self.security = self.analysis.auth

        self.name = self.name or self.analysis.name
        # write an index.html to the file
        if not os.path.isdir(self.path):
            path = join(settings.MEDIA_ROOT, "jobs", f"job-{self.uid}")
            os.makedirs(path)
            self.path = path

        super(Job, self).save(*args, **kwargs)

    def url(self):
        return reverse("job_view", kwargs=dict(id=self.id))

