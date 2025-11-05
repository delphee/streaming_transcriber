from django.shortcuts import render
import json
from django.http import HttpResponse
# Create your views here.


def receive_webhook(request):
    try:
        body_unicode = request.body.decode('utf-8')
        wh = json.loads(body_unicode)
    except Exception as e:
        print(f"Webhook data decode error: {e}")
        return HttpResponse(status=200)
    try:
        data = wh["data"]
        locationId = data["locationId"]
        customerId = data["customerId"]
        jobId = data["jobId"]
        appointmentId = data["appointment"]["id"]
        appointmentNumber = data["appointment"]["appointmentNumber"]
        recallForId = data["recallForId"]
        leadSourceId = data["jobGeneratedLeadSource"]["employeeId"]
    except Exception as e:
        print(f"Webhook data error: {e}")
    return HttpResponse(status=200)