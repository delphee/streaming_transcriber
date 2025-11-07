from django.db import models
from django.contrib.auth.models import User
# Create your models here.


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
    polling_active = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.job_id} - {self.appointment_id} {'Polling...' if self.polling_active else ''}"


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
