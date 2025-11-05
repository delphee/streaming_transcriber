from django.db import models

# Create your models here.


class AccessToken(models.Model):
    token =models.TextField()
    when = models.DateTimeField()