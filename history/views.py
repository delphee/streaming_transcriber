from django.shortcuts import render
import json
from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt
# Create your views here.

@csrf_exempt
def receive_webhook(request):
    try:
        body_unicode = request.body.decode('utf-8')
        wh = json.loads(body_unicode)
    except Exception as e:
        print(f"Webhook data decode error: {e}")
        return HttpResponse(status=200)
    try:
        data = wh["data"]
        job = data["job"]
        locationId = job["locationId"]
        customerId = job["customerId"]
        jobId = job["jobId"]
        appointmentId = job["appointment"]["id"]
        appointmentNumber = job["appointment"]["appointmentNumber"]
        recallForId = job["recallForId"]
        leadSourceId = job["jobGeneratedLeadSource"]["employeeId"]
    except Exception as e:
        print(f"Webhook data error: {e}")
    return HttpResponse(status=200)