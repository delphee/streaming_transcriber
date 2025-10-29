from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib.admin.views.decorators import staff_member_required
from django.http import JsonResponse
from django.contrib.auth.models import User
from django.contrib import messages
from django.views.decorators.csrf import csrf_exempt
from django.db.models import Q, Count
from django.utils import timezone
import json

from .models import Conversation, TranscriptSegment, Speaker, ConversationAnalysis, UserProfile, AuthToken, \
    AnalysisPrompt
from .auth_views import get_user_from_token


# MARK: - Dashboard & Main Views

@login_required
def dashboard(request):
    """Main dashboard view"""
    user = request.user

    # Get user's conversations
    recent_conversations = Conversation.objects.filter(
        recorded_by=user
    ).order_by('-started_at')[:5]

    # Stats
    total_conversations = Conversation.objects.filter(recorded_by=user).count()
    active_conversations = Conversation.objects.filter(recorded_by=user, is_active=True).count()
    analyzed_conversations = Conversation.objects.filter(recorded_by=user, is_analyzed=True).count()

    context = {
        'recent_conversations': recent_conversations,
        'total_conversations': total_conversations,
        'active_conversations': active_conversations,
        'analyzed_conversations': analyzed_conversations,
    }

    return render(request, 'streaming/dashboard.html', context)


@login_required
def conversation_list(request):
    """List all conversations for the user"""
    user = request.user

    # Get all conversations (admins see all, users see only theirs)
    if user.is_staff:
        conversations = Conversation.objects.all()
    else:
        conversations = Conversation.objects.filter(recorded_by=user)

    # Search functionality
    search_query = request.GET.get('q', '')
    if search_query:
        conversations = conversations.filter(
            Q(title__icontains=search_query) |
            Q(notes__icontains=search_query) |
            Q(segments__text__icontains=search_query)
        ).distinct()

    # Filter by status
    status_filter = request.GET.get('status', '')
    if status_filter == 'active':
        conversations = conversations.filter(is_active=True)
    elif status_filter == 'completed':
        conversations = conversations.filter(is_active=False)
    elif status_filter == 'analyzed':
        conversations = conversations.filter(is_analyzed=True)

    conversations = conversations.order_by('-started_at')

    context = {
        'conversations': conversations,
        'search_query': search_query,
        'status_filter': status_filter,
    }

    return render(request, 'streaming/conversation_list.html', context)


@login_required
def conversation_detail(request, conversation_id):
    """View a specific conversation with transcript"""
    user = request.user

    # Get conversation (check permissions)
    conversation = get_object_or_404(Conversation, id=conversation_id)

    if not user.is_staff and conversation.recorded_by != user:
        messages.error(request, 'You do not have permission to view this conversation')
        return redirect('conversation_list')

    # Get transcript segments
    # Prefer high-quality segments if available, otherwise show streaming segments
    hq_segments = conversation.segments.filter(is_final=True, source='high_quality').select_related('speaker').order_by(
        'start_time')
    if hq_segments.exists():
        segments = hq_segments
    else:
        segments = conversation.segments.filter(is_final=True, source='streaming').select_related('speaker').order_by(
            'created_at')

    # Get speakers
    speakers = conversation.speakers.all()

    # Get analyses
    analyses = conversation.analyses.all()

    context = {
        'conversation': conversation,
        'segments': segments,
        'speakers': speakers,
        'analyses': analyses,
    }

    return render(request, 'streaming/conversation_detail.html', context)


# MARK: - User Management (Admin Only)

@staff_member_required
def user_management(request):
    """Admin view to manage users"""
    users = User.objects.all().select_related('profile').order_by('-date_joined')

    context = {
        'users': users,
    }

    return render(request, 'streaming/user_management.html', context)


@staff_member_required
def user_create(request):
    """Create a new user"""
    if request.method == 'POST':
        # Handle user creation
        email = request.POST.get('email', '').lower().strip()
        username = request.POST.get('username', '').lower().strip()
        first_name = request.POST.get('first_name', '').strip()
        last_name = request.POST.get('last_name', '').strip()
        password = request.POST.get('password', '')
        is_staff = request.POST.get('is_staff') == 'on'

        # Validation
        if User.objects.filter(email=email).exists():
            messages.error(request, 'A user with this email already exists')
            return redirect('user_create')

        if User.objects.filter(username=username).exists():
            messages.error(request, 'A user with this username already exists')
            return redirect('user_create')

        # Create user
        user = User.objects.create_user(
            username=username,
            email=email,
            first_name=first_name,
            last_name=last_name,
            password=password,
            is_staff=is_staff
        )

        # Create profile
        profile = UserProfile.objects.create(user=user)

        # Assign prompt if selected
        assigned_prompt_id = request.POST.get('assigned_prompt')
        if assigned_prompt_id:
            profile.assigned_prompt_id = assigned_prompt_id
            profile.save()

        messages.success(request, f'User {user.username} created successfully')
        return redirect('user_management')

    prompts = AnalysisPrompt.objects.filter(is_active=True).order_by('name')

    context = {
        'prompts': prompts,
    }

    return render(request, 'streaming/user_create.html', context)


@staff_member_required
def user_edit(request, user_id):
    """Edit an existing user"""
    user_to_edit = get_object_or_404(User, id=user_id)

    if request.method == 'POST':
        # Update user details
        user_to_edit.email = request.POST.get('email', '').lower().strip()
        user_to_edit.first_name = request.POST.get('first_name', '').strip()
        user_to_edit.last_name = request.POST.get('last_name', '').strip()
        user_to_edit.is_staff = request.POST.get('is_staff') == 'on'
        user_to_edit.is_active = request.POST.get('is_active') == 'on'

        # Update password if provided
        new_password = request.POST.get('password', '').strip()
        if new_password:
            user_to_edit.set_password(new_password)

        user_to_edit.save()

        # Update profile settings
        profile = user_to_edit.profile
        profile.enable_real_time_coaching = request.POST.get('enable_real_time_coaching') == 'on'
        profile.enable_talking_points_monitoring = request.POST.get('enable_talking_points_monitoring') == 'on'
        profile.enable_sentiment_alerts = request.POST.get('enable_sentiment_alerts') == 'on'
        profile.enable_speaker_identification = request.POST.get('enable_speaker_identification') == 'on'
        profile.alert_on_heated_conversation = request.POST.get('alert_on_heated_conversation') == 'on'
        profile.alert_email = request.POST.get('alert_email', '').strip()
        assigned_prompt_id = request.POST.get('assigned_prompt')
        if assigned_prompt_id:
            profile.assigned_prompt_id = assigned_prompt_id
        else:
            profile.assigned_prompt = None
        profile.save()

        messages.success(request, f'User {user_to_edit.username} updated successfully')
        return redirect('user_management')

        # Get all active prompts for assignment
    prompts = AnalysisPrompt.objects.filter(is_active=True).order_by('name')

    context = {
        'user_to_edit': user_to_edit,
        'prompts': prompts,
    }

    return render(request, 'streaming/user_edit.html', context)


@staff_member_required
def user_delete(request, user_id):
    """Delete a user"""
    user_to_delete = get_object_or_404(User, id=user_id)

    if request.method == 'POST':
        username = user_to_delete.username
        user_to_delete.delete()
        messages.success(request, f'User {username} deleted successfully')
        return redirect('user_management')

    context = {
        'user_to_delete': user_to_delete,
    }

    return render(request, 'streaming/user_delete.html', context)


# MARK: - User Profile & Settings

@login_required
def user_profile(request):
    """User's own profile"""
    user = request.user
    profile = user.profile

    # Get user statistics
    total_conversations = Conversation.objects.filter(recorded_by=user).count()
    total_duration = sum([c.duration_seconds for c in Conversation.objects.filter(recorded_by=user)])

    context = {
        'profile': profile,
        'total_conversations': total_conversations,
        'total_duration': total_duration,
    }

    return render(request, 'streaming/user_profile.html', context)


@login_required
def user_settings(request):
    """User settings page"""
    user = request.user
    profile = user.profile

    if request.method == 'POST':
        # Update user info
        user.first_name = request.POST.get('first_name', '').strip()
        user.last_name = request.POST.get('last_name', '').strip()
        user.email = request.POST.get('email', '').lower().strip()

        # Update password if provided
        new_password = request.POST.get('new_password', '').strip()
        if new_password:
            user.set_password(new_password)
            messages.success(request, 'Password updated successfully. Please log in again.')

        user.save()

        # Update profile alert settings
        profile.alert_email = request.POST.get('alert_email', '').strip()
        profile.save()

        messages.success(request, 'Settings updated successfully')
        return redirect('user_settings')

    context = {
        'profile': profile,
    }

    return render(request, 'streaming/user_settings.html', context)


# MARK: - Prompt Management (Admin Only)

@staff_member_required
def prompt_management(request):
    """Admin view to manage analysis prompts"""
    prompts = AnalysisPrompt.objects.all().order_by('-created_at')

    context = {
        'prompts': prompts,
    }

    return render(request, 'streaming/prompt_management.html', context)


@staff_member_required
def prompt_create(request):
    """Create a new analysis prompt with AI optimization"""
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        description = request.POST.get('description', '').strip()
        plain_text = request.POST.get('plain_text', '').strip()

        if not name or not plain_text:
            messages.error(request, 'Name and plain text are required')
            return redirect('prompt_create')

        # Store temporarily for optimization
        request.session['prompt_data'] = {
            'name': name,
            'description': description,
            'plain_text': plain_text
        }

        return redirect('prompt_optimize')

    return render(request, 'streaming/prompt_create.html')


@staff_member_required
def prompt_optimize(request):
    """Use AI to optimize the plain text prompt"""
    prompt_data = request.session.get('prompt_data')

    if not prompt_data:
        messages.error(request, 'No prompt data found')
        return redirect('prompt_create')

    if request.method == 'POST':
        # User accepted the optimized prompt (or edited it)
        optimized_prompt = request.POST.get('optimized_prompt', '').strip()

        if not optimized_prompt:
            messages.error(request, 'Optimized prompt cannot be empty')
            return render(request, 'streaming/prompt_optimize.html', {'prompt_data': prompt_data})

        # Create the prompt
        prompt = AnalysisPrompt.objects.create(
            name=prompt_data['name'],
            description=prompt_data['description'],
            plain_text=prompt_data['plain_text'],
            optimized_prompt=optimized_prompt,
            created_by=request.user
        )

        # Clear session data
        del request.session['prompt_data']

        messages.success(request, f'Prompt "{prompt.name}" created successfully')
        return redirect('prompt_management')

    # Generate optimized prompt using AI
    from .ai_utils import optimize_prompt
    optimized = optimize_prompt(prompt_data['plain_text'])

    context = {
        'prompt_data': prompt_data,
        'optimized_prompt': optimized
    }

    return render(request, 'streaming/prompt_optimize.html', context)


@staff_member_required
def prompt_edit(request, prompt_id):
    """Edit an existing prompt"""
    prompt = get_object_or_404(AnalysisPrompt, id=prompt_id)

    # Prevent editing system prompts
    if prompt.is_system:
        messages.error(request, 'System prompts cannot be edited')
        return redirect('prompt_management')

    if request.method == 'POST':
        old_plain_text = prompt.plain_text
        new_plain_text = request.POST.get('plain_text', '').strip()

        prompt.name = request.POST.get('name', '').strip()
        prompt.description = request.POST.get('description', '').strip()
        prompt.plain_text = new_plain_text
        prompt.is_active = request.POST.get('is_active') == 'on'

        # Check if plain text changed - if so, regenerate optimized prompt
        if old_plain_text != new_plain_text:
            print(f"Plain text changed - regenerating optimized prompt")
            from .ai_utils import optimize_prompt
            prompt.optimized_prompt = optimize_prompt(new_plain_text)
            messages.success(request, f'Prompt "{prompt.name}" updated and re-optimized by AI')
        else:
            # Plain text didn't change, so use the manually edited optimized prompt
            prompt.optimized_prompt = request.POST.get('optimized_prompt', '').strip()
            messages.success(request, f'Prompt "{prompt.name}" updated successfully')

        prompt.save()
        return redirect('prompt_management')

    context = {
        'prompt': prompt,
    }

    return render(request, 'streaming/prompt_edit.html', context)


@staff_member_required
def prompt_delete(request, prompt_id):
    """Delete a prompt"""
    prompt = get_object_or_404(AnalysisPrompt, id=prompt_id)

    # Prevent deleting system prompts
    if prompt.is_system:
        messages.error(request, 'System prompts cannot be deleted')
        return redirect('prompt_management')

    if request.method == 'POST':
        name = prompt.name
        prompt.delete()
        messages.success(request, f'Prompt "{name}" deleted successfully')
        return redirect('prompt_management')

    context = {
        'prompt': prompt,
    }

    return render(request, 'streaming/prompt_delete.html', context)


@staff_member_required
def prompt_assign(request, prompt_id):
    """Assign a prompt to users"""
    prompt = get_object_or_404(AnalysisPrompt, id=prompt_id)

    if request.method == 'POST':
        user_ids = request.POST.getlist('users')

        # Update user profiles
        UserProfile.objects.filter(user_id__in=user_ids).update(assigned_prompt=prompt)

        messages.success(request, f'Prompt "{prompt.name}" assigned to {len(user_ids)} user(s)')
        return redirect('prompt_management')

    # Get all users with their current prompt assignment
    users = User.objects.all().select_related('profile').order_by('username')

    context = {
        'prompt': prompt,
        'users': users,
    }

    return render(request, 'streaming/prompt_assign.html', context)


# MARK: - iOS API Endpoints

@csrf_exempt
def api_conversation_list(request):
    """API endpoint to get user's conversations"""
    # Get token from header
    auth_header = request.headers.get('Authorization', '')
    if not auth_header.startswith('Bearer '):
        return JsonResponse({'error': 'Invalid authorization header'}, status=401)

    token = auth_header.split(' ')[1]
    user = get_user_from_token(token)

    if not user:
        return JsonResponse({'error': 'Invalid token'}, status=401)

    # Get conversations
    conversations = Conversation.objects.filter(recorded_by=user).order_by('-started_at')[:20]

    data = {
        'conversations': [
            {
                'id': c.id,
                'title': c.title,
                'started_at': c.started_at.isoformat(),
                'duration_seconds': c.duration_seconds,
                'is_active': c.is_active,
                'is_analyzed': c.is_analyzed,
            }
            for c in conversations
        ]
    }

    return JsonResponse(data)


@csrf_exempt
def api_conversation_detail(request, conversation_id):
    """API endpoint to get conversation details"""
    # Get token from header
    auth_header = request.headers.get('Authorization', '')
    if not auth_header.startswith('Bearer '):
        return JsonResponse({'error': 'Invalid authorization header'}, status=401)

    token = auth_header.split(' ')[1]
    user = get_user_from_token(token)

    if not user:
        return JsonResponse({'error': 'Invalid token'}, status=401)

    # Get conversation
    try:
        conversation = Conversation.objects.get(id=conversation_id, recorded_by=user)
    except Conversation.DoesNotExist:
        return JsonResponse({'error': 'Conversation not found'}, status=404)

    # Get segments - prefer high-quality if available
    hq_segments = conversation.segments.filter(is_final=True, source='high_quality').select_related('speaker').order_by(
        'start_time')
    if hq_segments.exists():
        segments = hq_segments
    else:
        segments = conversation.segments.filter(is_final=True, source='streaming').select_related('speaker').order_by(
            'created_at')

    data = {
        'id': conversation.id,
        'title': conversation.title,
        'started_at': conversation.started_at.isoformat(),
        'ended_at': conversation.ended_at.isoformat() if conversation.ended_at else None,
        'duration_seconds': conversation.duration_seconds,
        'is_analyzed': conversation.is_analyzed,
        'segments': [
            {
                'speaker': s.speaker.identified_name if s.speaker else 'Unknown',
                'text': s.text,
                'start_time': s.start_time,
                'end_time': s.end_time,
            }
            for s in segments
        ]
    }

    return JsonResponse(data)


@csrf_exempt
def api_upload_hq_audio(request, conversation_id):
    """
    API endpoint for iOS to upload high-quality 44.1kHz audio.
    This triggers the final transcription and speaker diarization.
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    # Get token from header
    auth_header = request.headers.get('Authorization', '')
    if not auth_header.startswith('Bearer '):
        return JsonResponse({'error': 'Invalid authorization header'}, status=401)

    token = auth_header.split(' ')[1]
    user = get_user_from_token(token)

    if not user:
        return JsonResponse({'error': 'Invalid token'}, status=401)

    # Get conversation
    try:
        conversation = Conversation.objects.get(id=conversation_id, recorded_by=user)
    except Conversation.DoesNotExist:
        return JsonResponse({'error': 'Conversation not found'}, status=404)

    # Check if conversation is already finalized
    if conversation.is_active:
        return JsonResponse({'error': 'Conversation is still active'}, status=400)

    # Get audio file from request
    if 'audio' not in request.FILES:
        return JsonResponse({'error': 'No audio file provided'}, status=400)

    audio_file = request.FILES['audio']

    # Validate file type (should be WAV)
    if not audio_file.name.endswith('.wav'):
        return JsonResponse({'error': 'Only WAV files are supported'}, status=400)

    # Read audio data
    original_audio_data = audio_file.read()

    print(f"📤 Received HQ audio upload for conversation {conversation_id}: {len(original_audio_data)} bytes")

    # Apply preprocessing to HQ audio for better speaker diarization
    print(f"🎛️ Preprocessing HQ audio (44.1kHz)...")
    from .audio_buffer import AudioBuffer
    import wave
    import io

    processed_audio_data = None
    try:
        # Parse the WAV file to extract raw PCM data
        wav_buffer = io.BytesIO(original_audio_data)
        with wave.open(wav_buffer, 'rb') as wav:
            sample_rate = wav.getframerate()
            channels = wav.getnchannels()
            raw_pcm = wav.readframes(wav.getnframes())
            print(f"📊 Original audio: {sample_rate}Hz, {channels} channel(s), {len(raw_pcm)} bytes")

        # Clear wav_buffer immediately after use
        wav_buffer.close()
        del wav_buffer

        # Create audio buffer with HQ sample rate
        audio_buffer = AudioBuffer(sample_rate=sample_rate, channels=channels)
        audio_buffer.add_chunk(raw_pcm)

        # Clear raw_pcm immediately after adding to buffer
        del raw_pcm

        # Apply preprocessing and get processed WAV
        processed_audio_data = audio_buffer.get_wav_file(apply_preprocessing=True)

        # Clear audio buffer immediately after getting WAV
        del audio_buffer

        if processed_audio_data:
            print(f"✅ Preprocessing complete: {len(processed_audio_data)} bytes")
            # Use processed audio, delete original to free memory
            audio_data = processed_audio_data
            del original_audio_data
        else:
            print("⚠️ Preprocessing returned None, using original audio")
            audio_data = original_audio_data

    except Exception as e:
        print(f"⚠️ Preprocessing failed: {e}, using original audio")
        import traceback
        traceback.print_exc()
        # Use original audio if preprocessing fails
        audio_data = original_audio_data

    # Upload to S3 as final_44k.wav
    from .s3_utils import upload_audio_to_s3, schedule_audio_deletion
    username = user.username
    s3_url = upload_audio_to_s3(conversation_id, audio_data, username, filename='final_44k.wav')

    if not s3_url:
        return JsonResponse({'error': 'Failed to upload audio to S3'}, status=500)

    # Update conversation with HQ audio URL and quality
    conversation.audio_url = s3_url
    conversation.audio_quality = 'high_quality'
    conversation.save()

    print(f"âœ… HQ audio uploaded to S3: {s3_url}")

    # Explicit memory cleanup - delete large objects to free memory immediately
    # The 44.1kHz audio files and NumPy arrays from preprocessing can be quite large
    print(f"ðŸ§¹ Cleaning up memory after audio processing...")
    try:
        # audio_data was used for S3 upload, safe to delete now
        del audio_data

        # Also delete processed_audio_data if it still exists
        if 'processed_audio_data' in locals():
            del processed_audio_data

        # Force garbage collection to immediately free memory
        import gc
        gc.collect()
        print(f"âœ… Memory cleanup complete")
    except Exception as cleanup_error:
        print(f"âš ï¸ Memory cleanup warning: {cleanup_error}")
        # Non-critical, continue anyway

    # Schedule deletion for HQ audio (if not already scheduled)
    if not conversation.audio_delete_at:
        schedule_audio_deletion(conversation)

    # Trigger batch processing on high-quality audio
    from .batch_processing import process_conversation_with_batch_api
    import threading

    batch_thread = threading.Thread(
        target=process_conversation_with_batch_api,
        args=(conversation_id,),
        kwargs={'is_final': True}  # Use high-quality speech model
    )
    batch_thread.start()

    print(f"ðŸš€ Started batch processing with HQ audio for conversation {conversation_id}")

    # Return success response
    return JsonResponse({
        'success': True,
        'message': 'High-quality audio uploaded successfully',
        'conversation_id': conversation_id,
        'audio_quality': 'high_quality'
    })