'''
This is chunking/views.py
views for the new chunking app
'''

# Create your views here.
"""
API views for hybrid chunked audio system.

ENDPOINTS:
- POST /chunking/chunk/ - Upload audio chunk
- POST /chunking/<id>/request-upload/ - Get presigned URL for final file
- POST /chunking/<id>/finalize/ - Confirm final upload and trigger transcription
- GET /chunking/<id>/status/ - Check conversation status
- POST /chunking/<id>/save/ - Mark conversation as permanent
- DELETE /chunking/<id>/ - Delete conversation (admin only)
- GET /chunking/search/ - Search transcripts
- GET /chunking/conversations/ - List user's conversations
- GET /chunking/<id>/ - Get conversation details
"""

from django.conf import settings
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.http import HttpResponse
import threading
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib import messages
from django.db.models import Q, Count
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
import json

from .models import ChunkedConversation, Speaker, TranscriptSegment
from streaming.models import User

from .models import ChunkedConversation, AudioChunk, Speaker, TranscriptSegment
from streaming.auth_views import get_user_from_token
from .s3_handler import (
    generate_presigned_upload_url,
    generate_presigned_download_url,
    verify_file_exists,
    get_file_size,
    delete_conversation_audio
)

from .s3_handler_hybrid import (
        start_multipart_upload,
        upload_chunk_hybrid,
        abort_multipart_upload,
        concatenate_and_upload_small_conversation,
        build_multipart_from_chunks
    )

from .transcription import (
    transcribe_chunks_preliminary,
    should_trigger_preliminary_transcription,
    transcribe_final_audio,
    search_transcripts
)

@csrf_exempt
def st_webhook_receiver(request):
    try:
        body_unicode = request.body.decode('utf-8')
        data = json.loads(body_unicode)
        print("Webhook received")
    except:
        print("Webhook data decode error")
        return HttpResponse(status=200)
    data = None
    return HttpResponse(status=200)

def authenticate_request(request):
    print("authenticate_request() is running!......................")
    """
    Helper to authenticate token from Authorization header.
    Returns (user, error_response) tuple.
    """
    auth_header = request.headers.get('Authorization', '')
    if not auth_header.startswith('Bearer '):
        print("Invalid Authorization header!")
        return None, JsonResponse({'error': 'Invalid authorization header'}, status=401)

    token = auth_header.split(' ')[1]
    user = get_user_from_token(token)

    if not user:
        print("Invalid token!")
        return None, JsonResponse({'error': 'Invalid token'}, status=401)

    return user, None


# === CHUNK UPLOAD ===



# === FINAL FILE UPLOAD (Presigned URL) ===

@csrf_exempt
def request_upload_url(request, conversation_id):
    """
    POST /chunking/<conversation_id>/request-upload/

    Generate a presigned URL for iOS to upload the complete FLAC file directly to S3.

    Returns: {
        upload_url: str (presigned PUT URL),
        s3_url: str (final S3 URL after upload),
        expires_in: int (seconds)
    }
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    # Authenticate
    user, error = authenticate_request(request)
    if error:
        return error

    # Get conversation
    try:
        conversation = ChunkedConversation.objects.get(id=conversation_id, recorded_by=user)
    except ChunkedConversation.DoesNotExist:
        print("Conversation not found!")
        return JsonResponse({'error': 'Conversation not found'}, status=404)

    # Check if chunks are complete
    if not conversation.is_chunks_complete:
        print("Chunks not complete yet!")
        return JsonResponse({'error': 'Chunks not complete yet'}, status=400)

    # Check if final file already uploaded
    if conversation.is_final_uploaded:
        print("Final chunk upload already complete!")
        return JsonResponse({'error': 'Final file already uploaded'}, status=400)

    print(f"🔐 Generating presigned upload URL for conversation {conversation_id}")

    # Generate presigned URL
    result = generate_presigned_upload_url(
        conversation_id,
        user.username,
        expiration=settings.PRESIGNED_URL_EXPIRATION
    )

    if not result:
        return JsonResponse({'error': 'Failed to generate presigned URL'}, status=500)

    # Store the expected S3 URL (iOS will upload here)
    s3_url = result['s3_url']
    print(f"📝 Attempting to save S3 URL: {s3_url}")
    print(f"   URL length: {len(s3_url)} characters")

    try:
        conversation.final_audio_url = s3_url
        conversation.save(update_fields=['final_audio_url'])

        # VERIFY it was actually saved by re-fetching from database
        conversation.refresh_from_db()
        saved_url = conversation.final_audio_url

        if saved_url == s3_url:
            print(f"✅ VERIFIED: URL saved correctly")
        else:
            print(f"❌ ERROR: URL mismatch after save!")
            print(f"   Expected: {s3_url}")
            print(f"   Got: {saved_url}")
            return JsonResponse({'error': 'Failed to save upload URL'}, status=500)

    except Exception as e:
        print(f"❌ Exception saving final_audio_url: {e}")
        import traceback
        traceback.print_exc()
        return JsonResponse({'error': f'Database error: {str(e)}'}, status=500)

    print(f"✅ Presigned URL generated, expires in {result['expires_in']}s")

    return JsonResponse(result)


@csrf_exempt
def finalize_conversation(request, conversation_id):
    """
    POST /chunking/<conversation_id>/finalize/

    Called by iOS after successfully uploading the complete file to S3.
    Verifies upload and triggers final transcription with speaker diarization.

    Optional body: {
        "title": "Custom conversation title"
    }

    Returns: {
        success: bool,
        message: str,
        is_final_uploaded: bool,
        transcription_started: bool
    }
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    # Authenticate
    user, error = authenticate_request(request)
    if error:
        return error

    print(f"🎬 FINALIZE called for conversation {conversation_id}")

    # Get conversation
    try:
        conversation = ChunkedConversation.objects.get(id=conversation_id, recorded_by=user)
        print(f"✅ Conversation found: {conversation_id}")
        print(f"   final_audio_url: {conversation.final_audio_url}")
        print(f"   is_final_uploaded: {conversation.is_final_uploaded}")
    except ChunkedConversation.DoesNotExist:
        print(f"❌ Conversation not found: {conversation_id}")
        return JsonResponse({'error': 'Conversation not found'}, status=404)

    # Check if final URL exists
    if not conversation.final_audio_url:
        print(f"❌ FAILED: No final audio URL exists!")
        return JsonResponse({'error': 'No final audio URL - request upload URL first'}, status=400)

    print(f"🎬 Finalizing conversation {conversation_id}")
    print(f"   Checking if file exists in S3: {conversation.final_audio_url}")

    # Verify the file exists in S3
    if not verify_file_exists(conversation.final_audio_url):
        print(f"❌ FAILED: Final audio file not found in S3")
        print(f"   This might mean the upload is still in progress!")
        return JsonResponse({'error': 'Final audio file not found in S3 - upload may still be in progress'}, status=404)

    print(f"✅ File exists in S3")

    # Get file size for logging
    file_size = get_file_size(conversation.final_audio_url)
    if file_size:
        print(f"   Final file size: {file_size:,} bytes ({file_size / 1024 / 1024:.2f} MB)")

    # Update conversation
    conversation.is_final_uploaded = True
    conversation.audio_uploaded_at = timezone.now()

    # Parse optional title from request
    try:
        if request.body:
            data = json.loads(request.body)
            title = data.get('title', '').strip()
            if title:
                conversation.title = title
    except json.JSONDecodeError:
        pass

    # Auto-generate title if not provided
    if not conversation.title:
        duration_display = conversation.get_duration_display()
        conversation.title = f"Conversation - {duration_display}"

    conversation.save()

    print(f"✅ Final upload verified")
    print(f"   Starting final transcription with speaker diarization...")

    # Trigger final transcription in background with error handling
    def transcribe_with_error_handling():
        try:
            transcribe_final_audio(conversation_id)
        except Exception as e:
            print(f"❌ CRITICAL ERROR in final transcription thread: {e}")
            import traceback
            traceback.print_exc()
            # Update conversation to mark transcription failed
            try:
                conv = ChunkedConversation.objects.get(id=conversation_id)
                conv.transcription_error = str(e)
                conv.save()
            except:
                pass

    transcription_thread = threading.Thread(
        target=transcribe_with_error_handling,
        daemon=True  # Daemon thread won't prevent app shutdown
    )
    transcription_thread.start()

    return JsonResponse({
        'success': True,
        'message': 'Final upload verified, transcription started',
        'is_final_uploaded': True,
        'transcription_started': True,
        'title': conversation.title
    })


# === STATUS & INFO ===

@csrf_exempt
def conversation_status(request, conversation_id):
    """
    GET /chunking/<conversation_id>/status/

    Check conversation status for iOS retry logic and UI updates.

    Returns: {
        received_chunks: list[int],
        chunk_count: int,
        is_chunks_complete: bool,
        is_final_uploaded: bool,
        is_analyzed: bool,
        total_duration_seconds: int
    }
    """
    if request.method != 'GET':
        return JsonResponse({'error': 'GET required'}, status=405)

    # Authenticate
    user, error = authenticate_request(request)
    if error:
        return error

    # Get conversation
    try:
        conversation = ChunkedConversation.objects.get(id=conversation_id, recorded_by=user)
    except ChunkedConversation.DoesNotExist:
        return JsonResponse({'error': 'Conversation not found'}, status=404)

    return JsonResponse({
        'received_chunks': conversation.received_chunks or [],
        'chunk_count': conversation.chunk_count,
        'is_chunks_complete': conversation.is_chunks_complete,
        'is_final_uploaded': conversation.is_final_uploaded,
        'is_analyzed': conversation.is_analyzed,
        'total_duration_seconds': conversation.total_duration_seconds,
        'title': conversation.title
    })


@csrf_exempt
def conversation_detail(request, conversation_id):
    """
    GET /chunking/<conversation_id>/

    Get full conversation details including transcript and speakers.

    Returns: {
        id: str,
        title: str,
        started_at: str,
        ended_at: str,
        duration_seconds: int,
        is_analyzed: bool,
        preliminary_transcript: str,
        full_transcript: str,
        speakers: [...],
        segments: [...]
    }
    """
    if request.method != 'GET':
        return JsonResponse({'error': 'GET required'}, status=405)

    # Authenticate
    user, error = authenticate_request(request)
    if error:
        return error

    # Get conversation (admins can see all)
    try:
        if user.is_staff:
            conversation = ChunkedConversation.objects.get(id=conversation_id)
        else:
            conversation = ChunkedConversation.objects.get(id=conversation_id, recorded_by=user)
    except ChunkedConversation.DoesNotExist:
        return JsonResponse({'error': 'Conversation not found'}, status=404)

    # Get speakers
    speakers = Speaker.objects.filter(conversation=conversation)
    speakers_data = [
        {
            'label': s.speaker_label,
            'name': s.identified_name,
            'is_recording_user': s.is_recording_user
        }
        for s in speakers
    ]

    # Get segments (limit to 500 for performance)
    segments = TranscriptSegment.objects.filter(
        conversation=conversation
    ).select_related('speaker').order_by('start_time')[:500]

    segments_data = [
        {
            'speaker': s.speaker.identified_name or s.speaker.speaker_label if s.speaker else 'Unknown',
            'text': s.text,
            'start_time': s.start_time,
            'end_time': s.end_time,
            'time_display': s.get_time_display()
        }
        for s in segments
    ]

    return JsonResponse({
        'id': conversation.id,
        'title': conversation.title,
        'started_at': conversation.started_at.isoformat(),
        'ended_at': conversation.ended_at.isoformat() if conversation.ended_at else None,
        'duration_seconds': conversation.total_duration_seconds,
        'duration_display': conversation.get_duration_display(),
        'is_chunks_complete': conversation.is_chunks_complete,
        'is_final_uploaded': conversation.is_final_uploaded,
        'is_analyzed': conversation.is_analyzed,
        'preliminary_transcript': conversation.preliminary_transcript,
        'full_transcript': conversation.full_transcript,
        'formatted_transcript': conversation.formatted_transcript,
        'speakers': speakers_data,
        'segments': segments_data,
        # Analysis results
        'summary': conversation.summary,
        'action_items': conversation.action_items,
        'key_topics': conversation.key_topics,
        'sentiment': conversation.sentiment,
        'coaching_feedback': conversation.coaching_feedback,
        # Errors
        'transcription_error': conversation.transcription_error,
        'analysis_error': conversation.analysis_error
    })

@csrf_exempt
def conversation_analysis(request, conversation_id):
    print("conversation_analysis running!.........")
    """
    GET /chunking/<conversation_id>/analysis/

    Get AI analysis results for a conversation.

    Returns: {
        summary: str,
        action_items: list,
        key_topics: list,
        sentiment: str,
        coaching_feedback: str,
        is_analyzed: bool,
        transcription_error: str,
        analysis_error: str
    }
    """
    if request.method != 'GET':
        return JsonResponse({'error': 'GET required'}, status=405)

    # Authenticate
    user, error = authenticate_request(request)
    if error:
        return error

    # Get conversation (admins can see all)
    try:
        if user.is_staff:
            conversation = ChunkedConversation.objects.get(id=conversation_id)
        else:
            conversation = ChunkedConversation.objects.get(id=conversation_id, recorded_by=user)
    except ChunkedConversation.DoesNotExist:
        return JsonResponse({'error': 'Conversation not found'}, status=404)

    '''return JsonResponse({
        'id': conversation.id,
        'title': conversation.title,
        'is_analyzed': conversation.is_analyzed,
        'summary': conversation.summary,
        'action_items': conversation.action_items,
        'key_topics': conversation.key_topics,
        'sentiment': conversation.sentiment,
        'coaching_feedback': conversation.coaching_feedback,
        'transcription_error': conversation.transcription_error,
        'analysis_error': conversation.analysis_error
    })'''

    '''
    TODO:
    return {'status':'pending',
            'message':'Analysis in progress...',
            }
    WHEN NECESSARY
    or 
    {'status':'failed',
    'error':'Transcription service unavailable'
    }
    
    '''
    if conversation.analysis_error not in ["", None]:
        data = {
            'status':'failed',
            'summary': '',
            'error':conversation.analysis_error
        }
        print(data)

        return JsonResponse(data)

    if not conversation.is_analyzed:
        data = {
            'status': 'pending',
            'summary': '',
            'action_items': [],
        }

    else:

        data = {
            'status': 'complete',
            'summary': conversation.summary,
            'action_items': conversation.action_items,

            }

    print(data)

    return JsonResponse(data)


@csrf_exempt
def retry_analysis(request, conversation_id):
    """
    POST /chunking/<conversation_id>/retry-analysis/

    Retry AI analysis if it failed or needs to be regenerated.
    Admin or owner only.

    Returns: {
        success: bool,
        message: str,
        analysis_started: bool
    }
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    # Authenticate
    user, error = authenticate_request(request)
    if error:
        return error

    # Get conversation (admins can see all)
    try:
        if user.is_staff:
            conversation = ChunkedConversation.objects.get(id=conversation_id)
        else:
            conversation = ChunkedConversation.objects.get(id=conversation_id, recorded_by=user)
    except ChunkedConversation.DoesNotExist:
        return JsonResponse({'error': 'Conversation not found'}, status=404)

    # Check if there's a transcript to analyze
    if not conversation.full_transcript:
        return JsonResponse({'error': 'No transcript available - run transcription first'}, status=400)

    print(f"🔄 Retrying analysis for conversation {conversation_id}")

    # Run analysis in background
    def analyze_with_error_handling():
        try:
            from .transcription import analyze_conversation
            analyze_conversation(conversation)
        except Exception as e:
            print(f"❌ Error in analysis retry: {e}")
            import traceback
            traceback.print_exc()

    analysis_thread = threading.Thread(
        target=analyze_with_error_handling,
        daemon=True
    )
    analysis_thread.start()

    return JsonResponse({
        'success': True,
        'message': 'Analysis retry started',
        'analysis_started': True
    })


@csrf_exempt
def toggle_share(request, conversation_id):
    """
    POST /chunking/<conversation_id>/share/

    Toggle the is_shared status for a conversation.
    Owner or admin only.

    Optional body: {
        "is_shared": true/false  // If provided, sets explicitly. Otherwise toggles.
    }

    Returns: {
        success: bool,
        is_shared: bool,
        message: str
    }
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    # Authenticate
    user, error = authenticate_request(request)
    if error:
        return error

    # Get conversation
    try:
        if user.is_staff:
            conversation = ChunkedConversation.objects.get(id=conversation_id)
        else:
            conversation = ChunkedConversation.objects.get(id=conversation_id, recorded_by=user)
    except ChunkedConversation.DoesNotExist:
        return JsonResponse({'error': 'Conversation not found'}, status=404)

    # Parse optional explicit value from request
    try:
        if request.body:
            data = json.loads(request.body)
            explicit_value = data.get('is_shared')
            if explicit_value is not None:
                conversation.is_shared = bool(explicit_value)
            else:
                # Toggle
                conversation.is_shared = not conversation.is_shared
        else:
            # No body, toggle
            conversation.is_shared = not conversation.is_shared
    except json.JSONDecodeError:
        # Toggle if invalid JSON
        conversation.is_shared = not conversation.is_shared

    conversation.save()

    status_text = "shared" if conversation.is_shared else "private"
    print(f"🔒 Conversation {conversation_id} marked as {status_text}")

    return JsonResponse({
        'success': True,
        'is_shared': conversation.is_shared,
        'message': f'Conversation marked as {status_text}'
    })


@csrf_exempt
def recent_summaries(request):
    print("recent_summaries() is running!.........")
    """
    GET /chunking/recent-summaries/

    Get last 10 analyzed conversations for the authenticated user.
    Only returns conversations where is_analyzed=True.

    Returns: {
        conversations: [
            {
                id: str,
                started_at: str (ISO format),
                ended_at: str (ISO format) or null,
                job_number: str or null,
                customer_name: str or null,
                is_shared: bool,
                total_duration_seconds: int,
                summary: str,
                action_items: list,
                key_topics: list,
                sentiment: str,
                coaching_feedback: str
            }
        ]
    }
    """
    if request.method != 'GET':
        return JsonResponse({'error': 'GET required'}, status=405)

    # Authenticate
    user, error = authenticate_request(request)
    if error:
        return error

    # Get user's analyzed conversations, most recent first
    conversations = ChunkedConversation.objects.filter(
        recorded_by=user,
        is_analyzed=True
    ).order_by('-started_at')[:10]

    # Serialize
    conversations_data = [
        {
            'id': c.id,
            'started_at': c.started_at.isoformat(),
            'ended_at': c.ended_at.isoformat() if c.ended_at else None,
            'job_number': c.job_number,
            'customer_name': c.customer_name,
            'analysis_status': "complete",
            'is_shared': c.is_shared,
            'total_duration_seconds': c.total_duration_seconds,
        }
        for c in conversations
    ]
    print(conversations_data)

    return JsonResponse({
        'conversations': conversations_data
    })

@csrf_exempt
def conversation_list(request):
    """
    GET /chunking/conversations/

    List user's conversations with optional filtering.

    Query params:
        limit: int (default 20)
        offset: int (default 0)
        status: 'complete' | 'incomplete' | 'analyzed'

    Returns: {
        conversations: [...],
        total: int,
        limit: int,
        offset: int
    }
    """
    if request.method != 'GET':
        return JsonResponse({'error': 'GET required'}, status=405)

    # Authenticate
    user, error = authenticate_request(request)
    if error:
        return error

    # Get query params
    limit = int(request.GET.get('limit', 20))
    offset = int(request.GET.get('offset', 0))
    status_filter = request.GET.get('status', '')

    # Build query
    if user.is_staff:
        conversations = ChunkedConversation.objects.all()
    else:
        conversations = ChunkedConversation.objects.filter(recorded_by=user)

    # Apply filters
    if status_filter == 'complete':
        conversations = conversations.filter(is_chunks_complete=True)
    elif status_filter == 'incomplete':
        conversations = conversations.filter(is_chunks_complete=False)
    elif status_filter == 'analyzed':
        conversations = conversations.filter(is_analyzed=True)

    # Get total count
    total = conversations.count()

    # Apply pagination
    conversations = conversations.order_by('-started_at')[offset:offset + limit]

    # Serialize
    conversations_data = [
        {
            'id': c.id,
            'title': c.title,
            'started_at': c.started_at.isoformat(),
            'duration_seconds': c.total_duration_seconds,
            'duration_display': c.get_duration_display(),
            'chunk_count': c.chunk_count,
            'is_chunks_complete': c.is_chunks_complete,
            'is_final_uploaded': c.is_final_uploaded,
            'is_analyzed': c.is_analyzed,
            'save_permanently': c.save_permanently
        }
        for c in conversations
    ]

    return JsonResponse({
        'conversations': conversations_data,
        'total': total,
        'limit': limit,
        'offset': offset
    })


# === MANAGEMENT ===

@csrf_exempt
def save_permanently(request, conversation_id):
    """
    POST /chunking/<conversation_id>/save/

    Mark conversation to never be auto-deleted.
    Owner or admin only.

    Returns: {
        success: bool,
        save_permanently: bool
    }
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    # Authenticate
    user, error = authenticate_request(request)
    if error:
        return error

    # Get conversation
    try:
        if user.is_staff:
            conversation = ChunkedConversation.objects.get(id=conversation_id)
        else:
            conversation = ChunkedConversation.objects.get(id=conversation_id, recorded_by=user)
    except ChunkedConversation.DoesNotExist:
        return JsonResponse({'error': 'Conversation not found'}, status=404)

    # Mark as permanent
    conversation.mark_permanent()

    print(f"💾 Conversation {conversation_id} marked as permanent")

    return JsonResponse({
        'success': True,
        'save_permanently': True,
        'message': 'Conversation will not be auto-deleted'
    })


@csrf_exempt
def delete_conversation(request, conversation_id):
    """
    DELETE /chunking/<conversation_id>/

    Delete conversation and all associated audio files.
    Admin only.

    Returns: {
        success: bool,
        deleted: bool
    }
    """
    if request.method != 'DELETE':
        return JsonResponse({'error': 'DELETE required'}, status=405)

    # Authenticate
    user, error = authenticate_request(request)
    if error:
        return error

    # Admin only
    if not user.is_staff:
        return JsonResponse({'error': 'Admin access required'}, status=403)

    # Get conversation
    try:
        conversation = ChunkedConversation.objects.get(id=conversation_id)
    except ChunkedConversation.DoesNotExist:
        return JsonResponse({'error': 'Conversation not found'}, status=404)

    print(f"🗑️  Deleting conversation {conversation_id}")

    # Delete audio from S3
    result = delete_conversation_audio(conversation)

    # Delete database records (cascade will handle related objects)
    conversation.delete()

    print(f"✅ Conversation {conversation_id} deleted")
    print(f"   Chunks deleted: {result['chunks_deleted']}")
    print(f"   Final file deleted: {result['final_deleted']}")

    return JsonResponse({
        'success': True,
        'deleted': True,
        'chunks_deleted': result['chunks_deleted'],
        'final_deleted': result['final_deleted']
    })


# === SEARCH ===

@csrf_exempt
def search_conversations(request):
    """
    GET /chunking/search/

    Search through conversation transcripts.

    Query params:
        q: search query (required)
        conversation_id: limit to specific conversation (optional)

    Returns: {
        query: str,
        results: [
            {
                conversation_id: str,
                conversation_title: str,
                chunk_number: int,
                start_time_seconds: int,
                time_display: str,
                matching_text: str,
                context: str
            }
        ]
    }
    """
    if request.method != 'GET':
        return JsonResponse({'error': 'GET required'}, status=405)

    # Authenticate
    user, error = authenticate_request(request)
    if error:
        return error

    # Get query
    query = request.GET.get('q', '').strip()
    if not query:
        return JsonResponse({'error': 'Query parameter "q" required'}, status=400)

    conversation_id = request.GET.get('conversation_id', '').strip()

    # Search within specific conversation
    if conversation_id:
        try:
            if user.is_staff:
                conversation = ChunkedConversation.objects.get(id=conversation_id)
            else:
                conversation = ChunkedConversation.objects.get(id=conversation_id, recorded_by=user)
        except ChunkedConversation.DoesNotExist:
            return JsonResponse({'error': 'Conversation not found'}, status=404)

        results = search_transcripts(conversation_id, query)

        # Add conversation context
        for result in results:
            result['conversation_id'] = conversation.id
            result['conversation_title'] = conversation.title

    else:
        # Search across all user's conversations
        if user.is_staff:
            conversations = ChunkedConversation.objects.all()
        else:
            conversations = ChunkedConversation.objects.filter(recorded_by=user)

        results = []
        for conversation in conversations:
            conv_results = search_transcripts(conversation.id, query)
            for result in conv_results:
                result['conversation_id'] = conversation.id
                result['conversation_title'] = conversation.title
                results.append(result)

    print(f"🔍 Search for '{query}' returned {len(results)} result(s)")

    return JsonResponse({
        'query': query,
        'results': results,
        'total': len(results)
    })


@csrf_exempt
def upload_chunk(request):
    """
    Upload chunk: Individual file + multipart part via UploadPartCopy.
    Transcribes every 4 chunks (2 minutes).
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    user, error = authenticate_request(request)
    if error:
        return error

    try:
        conversation_id = request.headers.get('X-Conversation-ID')
        chunk_number = int(request.headers.get('X-Chunk-Number'))
        chunk_start_time = int(request.headers.get('X-Chunk-Start-Time'))
        chunk_duration = int(request.headers.get('X-Chunk-Duration'))
        is_final_chunk = request.headers.get('X-Is-Final-Chunk', 'false').lower() == 'true'

        rms_level = request.headers.get('X-RMS-Level')
        peak_amplitude = request.headers.get('X-Peak-Amplitude')
        speech_percentage = request.headers.get('X-Speech-Percentage')

    except (ValueError, TypeError) as e:
        return JsonResponse({'error': f'Invalid headers: {e}'}, status=400)

    if not conversation_id:
        return JsonResponse({'error': 'X-Conversation-ID required'}, status=400)

    chunk_data = request.body
    if not chunk_data:
        return JsonResponse({'error': 'No audio data'}, status=400)

    print(f"📦 Chunk {chunk_number} for {conversation_id}")
    print(f"   Size: {len(chunk_data):,} bytes")
    print(f"   Start: {chunk_start_time}s, Duration: {chunk_duration}s")
    print(f"   Final: {is_final_chunk}")

    # Validate start_time (server-side check)
    expected_start = chunk_number * 30
    if chunk_start_time != expected_start:
        print(f"⚠️  Start time mismatch! Expected {expected_start}, got {chunk_start_time}")
        print(f"   Using calculated: {expected_start}")
        chunk_start_time = expected_start



    conversation, created = ChunkedConversation.objects.get_or_create(
        id=conversation_id,
        defaults={
            'recorded_by': user,
            'started_at': timezone.now()
        }
    )

    if created:
        print(f"✅ Created conversation {conversation_id}")

    # First chunk: start multipart
    if chunk_number == 0:
        print(f"🚀 First chunk - starting multipart upload")

        result = start_multipart_upload(conversation_id, user.username)

        if not result['success']:
            return JsonResponse({
                'error': 'Failed to start multipart upload',
                'details': result.get('error')
            }, status=500)

        conversation.multipart_upload_id = result['upload_id']
        conversation.multipart_s3_key = result['s3_key']
        conversation.final_audio_url = result['s3_url']
        conversation.multipart_parts = []
        conversation.save()

        print(f"✅ Multipart initialized: {result['upload_id']}")

    # Check idempotency
    existing_chunk = AudioChunk.objects.filter(
        conversation=conversation,
        chunk_number=chunk_number
    ).first()

    if existing_chunk:
        print(f"⚠️  Chunk {chunk_number} already exists")
        return JsonResponse({
            'success': True,
            'chunk_number': chunk_number,
            'total_received': len(conversation.received_chunks),
            'is_complete': conversation.is_chunks_complete,
            'message': 'Already received'
        })

    # Upload chunk (individual + multipart)
    if not conversation.multipart_upload_id:
        return JsonResponse({
            'error': 'No multipart upload - chunk 0 must be sent first'
        }, status=400)

    upload_result = upload_chunk_hybrid(
        conversation_id=conversation_id,
        chunk_number=chunk_number,
        chunk_data=chunk_data,
        username=user.username,
        multipart_upload_id=conversation.multipart_upload_id,
        multipart_s3_key=conversation.multipart_s3_key
    )

    if not upload_result['success']:
        print(f"❌ Upload failed, aborting multipart")
        abort_multipart_upload(
            conversation.multipart_upload_id,
            conversation.multipart_s3_key
        )
        return JsonResponse({
            'error': 'Failed to upload chunk',
            'details': upload_result.get('error')
        }, status=500)

    # Save part info
    parts = conversation.multipart_parts or []
    parts.append({
        'part_number': upload_result['part_number'],
        'etag': upload_result['part_etag']
    })
    conversation.multipart_parts = parts

    if not conversation.chunks_folder_path:
        conversation.chunks_folder_path = upload_result['chunks_folder']

    conversation.save()

    print(f"✅ Chunk {chunk_number} uploaded")

    # Create AudioChunk record
    chunk = AudioChunk.objects.create(
        conversation=conversation,
        chunk_number=chunk_number,
        start_time_seconds=chunk_start_time,
        duration_seconds=chunk_duration,
        s3_chunk_url=upload_result['chunk_s3_url'],
        rms_level=float(rms_level) if rms_level else None,
        peak_amplitude=float(peak_amplitude) if peak_amplitude else None,
        speech_percentage=float(speech_percentage) if speech_percentage else None
    )

    # Update received chunks
    received_chunks = conversation.received_chunks or []
    if chunk_number not in received_chunks:
        received_chunks.append(chunk_number)
        received_chunks.sort()
        conversation.received_chunks = received_chunks

    conversation.chunk_count = len(received_chunks)
    conversation.total_duration_seconds = chunk_start_time + chunk_duration
    conversation.save()

    print(f"   Total received: {len(received_chunks)}")

    # Final chunk: check total size and choose method
    if is_final_chunk:
        print(f"🏁 Final chunk - checking total size")

        # Get all chunks in order
        chunks_in_order = AudioChunk.objects.filter(
            conversation=conversation
        ).order_by('chunk_number')

        chunk_s3_urls = [c.s3_chunk_url for c in chunks_in_order]

        # Calculate total size from database or S3
        total_bytes = 0
        for chunk in chunks_in_order:
            # Estimate from chunk_data we have, or fetch from S3
            if chunk.chunk_number == chunk_number:
                # Current chunk - we have the data
                total_bytes += len(chunk_data)
            else:
                # Previous chunks - estimate ~900KB average
                total_bytes += 900000

        total_mb = total_bytes / (1024 * 1024)
        print(f"   Estimated total: {total_bytes:,} bytes ({total_mb:.2f} MB)")

        # Threshold: 10MB
        SIZE_THRESHOLD = 10 * 1024 * 1024

        # Abort the optimistic multipart upload we started
        if conversation.multipart_upload_id:
            print(f"   Aborting optimistic multipart upload")
            abort_multipart_upload(
                conversation.multipart_upload_id,
                conversation.multipart_s3_key
            )

        # Choose method based on size
        if total_bytes < SIZE_THRESHOLD:
            print(f"   📝 Using concatenation (< 10MB)")

            result = concatenate_and_upload_small_conversation(
                conversation_id=conversation_id,
                username=user.username,
                chunk_s3_urls=chunk_s3_urls
            )

            if not result['success']:
                print(f"❌ Failed to concatenate chunks")
                return JsonResponse({
                    'error': 'Failed to create complete file',
                    'details': result.get('error')
                }, status=500)

            conversation.final_audio_url = result['s3_url']
            print(f"✅ Concatenation complete: {result['s3_url']}")

        else:
            print(f"   🔀 Using retroactive multipart (≥ 10MB)")

            result = build_multipart_from_chunks(
                conversation_id=conversation_id,
                username=user.username,
                chunk_s3_urls=chunk_s3_urls
            )

            if not result['success']:
                print(f"❌ Failed to build multipart")
                return JsonResponse({
                    'error': 'Failed to create complete file',
                    'details': result.get('error')
                }, status=500)

            conversation.final_audio_url = result['s3_url']
            print(f"✅ Multipart complete: {result['s3_url']}")

        conversation.is_chunks_complete = True
        conversation.is_final_uploaded = True
        conversation.audio_uploaded_at = timezone.now()
        conversation.ended_at = timezone.now()

        if not conversation.title:
            conversation.title = f"Conversation - {conversation.get_duration_display()}"

        conversation.schedule_deletion(days=settings.CONVERSATION_RETENTION_DAYS)
        conversation.save()

        print(f"📅 Deletion scheduled: {conversation.scheduled_deletion_date}")
        print(f"🎤 Starting final transcription")

        def transcribe_with_error_handling():
            try:
                from .transcription import transcribe_final_audio
                transcribe_final_audio(conversation_id)
            except Exception as e:
                print(f"❌ Transcription error: {e}")
                import traceback
                traceback.print_exc()
                try:
                    conv = ChunkedConversation.objects.get(id=conversation_id)
                    conv.transcription_error = str(e)
                    conv.save()
                except:
                    pass

        transcription_thread = threading.Thread(
            target=transcribe_with_error_handling,
            daemon=True
        )
        transcription_thread.start()

        return JsonResponse({
            'success': True,
            'chunk_number': chunk_number,
            'total_received': len(received_chunks),
            'is_complete': True,
            'transcription_started': True,
            'message': 'Complete'
        })

    # Non-final chunk: check if should transcribe
    batch_size = getattr(settings, 'PRELIMINARY_TRANSCRIPTION_BATCH_SIZE', 4)

    untranscribed = AudioChunk.objects.filter(
        conversation=conversation,
        transcript_text=''
    ).order_by('chunk_number')

    if untranscribed.count() >= batch_size:
        print(f"🎤 Triggering batch transcription ({batch_size} chunks)")

        chunk_ids = list(untranscribed[:batch_size].values_list('id', flat=True))

        def transcribe_batch():
            try:
                from .transcription import transcribe_chunks_preliminary
                transcribe_chunks_preliminary(conversation_id, chunk_ids)
            except Exception as e:
                print(f"❌ Preliminary transcription error: {e}")
                import traceback
                traceback.print_exc()

        threading.Thread(target=transcribe_batch, daemon=True).start()

    return JsonResponse({
        'success': True,
        'chunk_number': chunk_number,
        'total_received': len(received_chunks),
        'is_complete': False,
        'message': 'Uploaded'
    })



