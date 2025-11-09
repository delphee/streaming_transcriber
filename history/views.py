from django.shortcuts import render
import json
from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator
from history.models import DispatchJob, HistoryJob
from streaming.models import AuthToken, UserProfile
from history.models import DeviceToken
from django.utils import timezone
from django_q.tasks import async_task
from django.conf import settings
from django.views import View
from django.contrib.auth.models import User
from datetime import datetime, timedelta
import secrets
from history.push_notifications import send_tech_status_push


TOKEN_LIFETIME = timedelta(days=7)
REFRESH_WINDOW = timedelta(hours=24)  # if less than this remaining, refresh

# Create your views here.

# ENDPOINT FOR iOS TO CHECK IF TECH HAS ARRIVED, or DONE
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
    tech_id = user_profile.st_id
    dispatch_jobs = DispatchJob.objects.filter(tech_id=tech_id, active=True)
    if len(dispatch_jobs) == 0:
        #send_tech_status_push(user, 0)
        return JsonResponse({"result":0,}, status=200) # 0 means take no action, but if recording should stop
    dispatch_job = dispatch_jobs[0]
    if dispatch_job.status=="Working":
        dispatch_job.save()
        #send_tech_status_push(user, 1)
        return JsonResponse({"result":1,}, status=200) # 1 means start recording
    if dispatch_job.status=="Dispatched":
        history_jobs = HistoryJob.objects.filter(job_id=dispatch_job.job_id, appointment_id=dispatch_job.appointment_id)
        if len(history_jobs) == 0:
            #
            #   CREATE HISTORY JOB & TRIGGER RESEARCH TASK
            #
            HistoryJob.objects.create(job_id=dispatch_job.job_id, appointment_id=dispatch_job.appointment_id)
            async_task("history.tasks.compile_document", dispatch_job.job_id)
            #send_tech_status_push(user, 2)
            return JsonResponse({"result":2,}, status=200) # 2 means history has been triggered (may be unnecessary)
        history_job = history_jobs[0]
        if history_job.ready:
            #send_tech_status_push(user, 3, data=history_job.data)
            return JsonResponse({"result":3, "data":history_job.data}, status=200)





@csrf_exempt
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


@csrf_exempt
def register_device_token(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed'}, status=405)

    # --- Authorization header ---
    auth_header = request.headers.get('Authorization')
    if not auth_header or not auth_header.startswith('Bearer '):
        return JsonResponse({'error': 'Missing or invalid Authorization header'}, status=401)

    raw_token = auth_header.split('Bearer ')[1].strip()

    # --- Validate AuthToken ---
    try:
        auth_token = AuthToken.objects.get(token=raw_token, is_active=True)
    except AuthToken.DoesNotExist:
        return JsonResponse({'error': 'Invalid or inactive token'}, status=401)

    if auth_token.expires_at < timezone.now():
        return JsonResponse({'error': 'Token expired'}, status=401)

    user = auth_token.user

    # --- Refresh token if near expiry ---
    time_remaining = auth_token.expires_at - timezone.now()
    new_token = None
    if time_remaining < REFRESH_WINDOW:
        auth_token.is_active = False
        auth_token.save()

        new_token_str = secrets.token_hex(32)
        new_auth_token = AuthToken.objects.create(
            user=user,
            token=new_token_str,
            expires_at=timezone.now() + TOKEN_LIFETIME,
            is_active=True,
        )
        new_token = new_auth_token.token

    # --- Parse request body ---
    try:
        body = json.loads(request.body.decode('utf-8'))
        device_token = body.get('device_token')
        platform = body.get('platform', 'ios')
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({'error': 'Invalid JSON body'}, status=400)

    if not device_token:
        return JsonResponse({'error': 'Missing device_token'}, status=400)

    # --- Save / update ---
    obj, created = DeviceToken.objects.update_or_create(
        device_token=device_token,
        defaults={
            'user': user,
            'platform': platform,
            'updated_at': timezone.now(),
        },
    )

    response = {
        'status': 'success',
        'created': created,
        'platform': platform,
    }

    if new_token:
        response['new_token'] = new_token
        response['expires_at'] = (timezone.now() + TOKEN_LIFETIME).isoformat()

    return JsonResponse(response, status=200)


@csrf_exempt
def confirm_notification(request):
    """
    iOS confirms receipt of push notification
    POST body: {"job_id": "12345", "result": 1}
    result: 1=Working, 2=Done, 3=History Ready
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed'}, status=405)

    # Get token from header
    auth_header = request.headers.get('Authorization', '')
    if not auth_header.startswith('Bearer '):
        return JsonResponse({'error': 'Invalid authorization header'}, status=401)

    token = auth_header.split(' ')[1]
    user = get_user_from_token(token)

    if not user:
        return JsonResponse({'error': 'Invalid token'}, status=401)

    # Parse request body
    try:
        body = json.loads(request.body.decode('utf-8'))
        appointment_id = body.get('appointment_id')
        result = body.get('result')
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({'error': 'Invalid JSON body'}, status=400)

    if not appointment_id or result not in [1, 2, 3]:
        return JsonResponse({'error': 'Missing or invalid job_id or result'}, status=400)

    # Get user's tech_id
    try:
        user_profile = UserProfile.objects.get(user=user)
        tech_id = user_profile.st_id
    except UserProfile.DoesNotExist:
        return JsonResponse({'error': 'User profile not found'}, status=404)

    # Find the DispatchJob
    try:
        dispatch_job = DispatchJob.objects.get(appointment_id=str(appointment_id), tech_id=tech_id)
    except DispatchJob.DoesNotExist:
        return JsonResponse({'error': 'Job not found'}, status=404)

    # Update the appropriate confirmation field
    if result == 1: # WORKING (Tech has arrived)
        dispatch_job.notified_working = True
        dispatch_job.recording_active = True # Redundant
        dispatch_job.polling_active = False  # Stop polling ST once iOS confirms 'Working'
        dispatch_job.save()
        return JsonResponse({'status': 'success', 'confirmed': 'working'}, status=200)

    elif result == 2:  # DONE (Tech has completed job)
        dispatch_job.recording_stopped = True
        dispatch_job.polling_active = False
        dispatch_job.notified_done = True
        dispatch_job.save()
        return JsonResponse({'status': 'success', 'confirmed': 'done'}, status=200)

    elif result == 3:
        # iOS acknowledges it knows history is ready
        dispatch_job.notified_history = True
        dispatch_job.save()
        return JsonResponse({'status': 'success', 'confirmed': 'history'}, status=200)

    return JsonResponse({'error': 'Unknown error'}, status=500)


@csrf_exempt
def ai_conversation_query(request):
    """
    iOS endpoint for AI-powered job data queries
    POST body: {
        "job_data": "string",  # Optional, reserved for future use
        "query": "What time is the appointment?",
        "conversation_history": [...]  # Optional
    }
    """
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Method not allowed'}, status=405)

    # Get token from header
    auth_header = request.headers.get('Authorization', '')
    if not auth_header.startswith('Bearer '):
        return JsonResponse({'success': False, 'error': 'Invalid authorization header'}, status=401)

    token = auth_header.split(' ')[1]
    user = get_user_from_token(token)

    if not user:
        return JsonResponse({'success': False, 'error': 'Invalid token'}, status=401)

    # Parse request body
    try:
        body = json.loads(request.body.decode('utf-8'))
        query = body.get('query')
        appointment_id = body.get('appointment_id', '')  # Reserved for future use
        conversation_history = body.get('conversation_history', [])
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({'success': False, 'error': 'Invalid JSON body'}, status=400)

    if not query:
        return JsonResponse({'success': False, 'error': 'Missing query'}, status=400)

    # Get user's current active job
    try:
        user_profile = UserProfile.objects.get(user=user)
        tech_id = user_profile.st_id

        # Get the active dispatch job for this tech
        dispatch_job = DispatchJob.objects.filter(tech_id=tech_id, appointment_id=appointment_id).first()

        if not dispatch_job:
            return JsonResponse({
                'success': False,
                'error': 'No active job found'
            }, status=404)

        # Check if AI document is ready
        if not dispatch_job.ai_document_built or not dispatch_job.ai_document_s3_key:
            return JsonResponse({
                'success': False,
                'error': 'Job document not ready yet. Please try again in a moment.'
            }, status=202)  # 202 Accepted - processing

        # Fetch document from S3
        job_document = fetch_document_from_s3(dispatch_job.ai_document_s3_key)

        if not job_document:
            return JsonResponse({
                'success': False,
                'error': 'Unable to retrieve job document'
            }, status=500)

        # Query AI with the document and conversation history
        ai_response = query_ai_service(
            job_document=job_document,
            user_query=query,
            conversation_history=conversation_history
        )

        return JsonResponse({
            'success': True,
            'answer': ai_response['answer'],
            'tokens_used': ai_response['tokens_used'],
            'timestamp': timezone.now().isoformat()
        }, status=200)

    except UserProfile.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'User profile not found'}, status=404)
    except Exception as e:
        print(f"❌ AI conversation error: {e}")
        import traceback
        traceback.print_exc()
        return JsonResponse({'success': False, 'error': 'Internal server error'}, status=500)


def fetch_document_from_s3(s3_key):
    """
    Fetch document content from S3 using presigned URL
    """
    from chunking.s3_handler import get_s3_client
    import requests

    try:
        s3_client = get_s3_client()

        # Generate presigned download URL (1 hour expiration)
        presigned_url = s3_client.generate_presigned_url(
            'get_object',
            Params={
                'Bucket': settings.AWS_STORAGE_BUCKET_NAME,
                'Key': s3_key
            },
            ExpiresIn=3600
        )

        # Download the document
        response = requests.get(presigned_url)

        if response.status_code == 200:
            return response.text
        else:
            print(f"❌ Failed to fetch document from S3: {response.status_code}")
            return None

    except Exception as e:
        print(f"❌ Error fetching document from S3: {e}")
        import traceback
        traceback.print_exc()
        return None


def query_ai_service(job_document, user_query, conversation_history=None):
    """
    Query AI service with job document and conversation history
    Designed to be easily swappable with other AI services

    Returns: {
        'answer': str,
        'tokens_used': int
    }
    """
    from openai import OpenAI

    client = OpenAI(api_key=settings.OPENAI_API_KEY)

    # Build messages for the conversation
    messages = [
        {
            "role": "system",
            "content": (
                "You are a helpful assistant for field service technicians. "
                "You have access to job and customer information. "
                "Provide accurate, concise, and helpful answers. "
                "If information is not available in the job data, say so politely. "
                "Keep responses brief and actionable - the technician is likely driving or preparing for the job."
            )
        },
        {
            "role": "system",
            "content": f"Here is the current job information:\n\n{job_document}"
        }
    ]

    # Add conversation history if provided
    if conversation_history:
        messages.extend(conversation_history)

    # Add current query
    messages.append({
        "role": "user",
        "content": user_query
    })

    # Call OpenAI
    response = client.chat.completions.create(
        model="gpt-4",
        messages=messages,
        temperature=0.7,
        max_tokens=500
    )

    return {
        'answer': response.choices[0].message.content,
        'tokens_used': response.usage.total_tokens
    }



@csrf_exempt
def testing(request):
    dispatchJob_job_id = "402956116"
    print("Testing!")
    dispatch_job = DispatchJob.objects.get(job_id=dispatchJob_job_id)
    dispatch_job_id = dispatch_job.id
    async_task('history.tasks.build_ai_job_document', dispatch_job_id)

