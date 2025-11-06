from django.shortcuts import render
import json
from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt

from history.models import DispatchJob, HistoryJob
from streaming.models import AuthToken, UserProfile
from django.utils import timezone
from django_q.tasks import async_task

# Create your views here.


@csrf_exempt
def check_tech_status(request):
    # Get token from header
    auth_header = request.headers.get('Authorization', '')
    if not auth_header.startswith('Bearer '):
        return JsonResponse({'error': 'Invalid authorization header'}, status=401)

    token = auth_header.split(' ')[1]
    user = get_user_from_token(token)

    if not user:
        return JsonResponse({'error': 'Invalid token'}, status=401)

    user_profile = UserProfile.objects.get(user=user)
    tech_id = user_profile.tech_id
    dispatch_jobs = DispatchJob.objects.filter(tech_id=tech_id, active=True)
    if len(dispatch_jobs) == 0:
        return JsonResponse({"result":0,}, status=200) # 0 means take no action, but if recording should stop
    dispatch_job = dispatch_jobs[0]
    if dispatch_job.status=="Working":
        dispatch_job.save()
        return JsonResponse({"result":1,}, status=200) # 1 means start recording
    if dispatch_job.status=="Dispatched":
        history_jobs = HistoryJob.objects.filter(job_id=dispatch_job.job_id, appointment_id=dispatch_job.appointment_id)
        if len(history_jobs) == 0:
            #
            #   CREATE HISTORY JOB & TRIGGER RESEARCH TASK
            #
            HistoryJob.objects.create(job_id=dispatch_job.job_id, appointment_id=dispatch_job.appointment_id)
            async_task("history.tasks.compile_document", dispatch_job.job_id)
            return JsonResponse({"result":2,}, status=200) # 2 means history has been triggered (may be unnecessary)
        history_job = history_jobs[0]
        if history_job.ready:
            return JsonResponse({"result":3, "data":history_job.data}, status=200)






def job_complete(request):
    try:
        body_unicode = request.body.decode('utf-8')
        data = json.loads(body_unicode)
    except Exception as e:
        print(f"Webhook data decode error: {e}")
        return HttpResponse(status=200)
    try:
        print(f"Job Complete received: {data['jobNumber']}")
        dispatch_jobs = DispatchJob.objects.filter(job_id=str(data['jobNumber']))
        if len(dispatch_jobs) > 0:
            dispatch_job = dispatch_jobs[0]
            dispatch_job.status = "Done"
            dispatch_job.save()
    except Exception as e:
        print(f"Exception while Completing DispatchJob: {e}")
    return HttpResponse(status=200)








def get_user_from_token(token_string):
    """Helper to get user from token string"""
    try:
        token = AuthToken.objects.get(token=token_string, is_active=True)
        if token.expires_at < timezone.now():
            token.is_active = False
            token.save()
            return None
        return token.user
    except AuthToken.DoesNotExist:
        return None