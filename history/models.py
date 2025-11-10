from django.db import models
from django.contrib.auth.models import User
# Create your models here.

TECHS = {
    "3027961":"Ethan Ficklin",
    "190999251":"Kevin Stanley",
    "3027975":"Ronnie Bland",
    "162915344":"Brett Allen",
    "141471729":"Josue Rodriguez",
    "383003734":"AJ Ruths",
    "128166026":"Jake West",
    "356406954":"Michael Ouden Jr",
    "384234754":"Christopher Franklin",
    "7129641":"David Elphee",
    "144096740":"Jayden Barlow",
    "383003261":"John Sayers",
    "138699985":"John Williams",
    "273358904":"Josh Jenkins",
    "43715608":"Justin Barron",
    "345283118":"Osman Harooni",
    "380471230":"Riley Woodward",
    "224925184":"Shawn Hollingsworth",
    "133853401":"Stephen Starner",
    "67321105":"Thomas Shawaryn",
    "114376585":"Tim Miller",
    "125325480":"Jake Simpson"
}




class AccessToken(models.Model):
    token =models.TextField()
    when = models.DateTimeField()


class DispatchJob(models.Model):
    status_choices = (
        ('Scheduled','Scheduled'),('Dispatched', 'Dispatched'),('Working', 'Working'),('Done', 'Done')
    )
    job_id = models.CharField(max_length=50)
    appointment_id = models.CharField(max_length=50)
    tech_id = models.CharField(max_length=50)
    active = models.BooleanField(default=True)
    last_updated = models.DateTimeField(auto_now=True)
    status = models.CharField(max_length=20, choices=status_choices)
    polling_active = models.BooleanField(default=True, help_text="Server is polling ST for 'Working' status")
    recording_active = models.BooleanField(default=False, help_text="iOS has acknowledged server's 'Working' push notification")
    recording_stopped = models.BooleanField(default=False, help_text="iOS has acknowledged server's 'Done' push notification")
    notified_working = models.BooleanField(default=False, help_text="Push sent for status=Working (result:1)")
    notified_done = models.BooleanField(default=False, help_text="Push sent for status=Done (result:2)")
    notified_history = models.BooleanField(default=False, help_text="Push sent for history ready (result:3)")
    ai_document_s3_key = models.TextField(blank=True, null=True, help_text="S3 key for AI job data document")
    ai_document_built = models.BooleanField(default=False, help_text="AI document has been built and uploaded to S3")
    out_of_order = models.BooleanField(default=False, help_text="Appointment returned to 'Scheduled' status after Dispatch")

    def __str__(self):
        return (f"{self.job_id} - {self.appointment_id} {'Polling...' if self.polling_active else ''} "
                f"{TECHS[self.tech_id] if self.tech_id in TECHS else ''} {'DOC' if self.ai_document_built else ''}")


class HistoryJob(models.Model):
    job_id = models.CharField(max_length=50)
    appointment_id = models.CharField(max_length=50)
    ready = models.BooleanField(default=False)
    data = models.TextField()
    active = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.job_id} - {self.appointment_id} {'Ready' if self.ready and self.active else ''}"

class DeviceToken(models.Model):

    user = models.ForeignKey(User, on_delete=models.CASCADE)
    device_token = models.CharField(max_length=255, unique=True)
    platform = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.user.username}"
