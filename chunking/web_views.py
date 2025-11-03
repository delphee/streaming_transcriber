"""
Web UI views for chunking app (Desktop/Admin interface)
Only shows conversations marked as is_shared=True
"""

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


@login_required
@staff_member_required
def dashboard(request):
    """
    Main dashboard showing statistics and recent shared conversations.
    Admin only.
    """
    # Get shared conversations only
    shared_conversations = ChunkedConversation.objects.filter(is_shared=True)

    # Recent conversations (last 5)
    recent_conversations = shared_conversations.order_by('-started_at')[:5]

    # Statistics
    total_shared = shared_conversations.count()
    analyzed_count = shared_conversations.filter(is_analyzed=True).count()
    total_users = User.objects.filter(chunked_conversations__is_shared=True).distinct().count()

    # Calculate total duration
    total_duration = sum([c.total_duration_seconds for c in shared_conversations])
    total_hours = total_duration // 3600
    total_minutes = (total_duration % 3600) // 60

    context = {
        'recent_conversations': recent_conversations,
        'total_shared': total_shared,
        'analyzed_count': analyzed_count,
        'total_users': total_users,
        'total_hours': total_hours,
        'total_minutes': total_minutes,
    }

    return render(request, 'chunking/dashboard.html', context)


@login_required
@staff_member_required
def conversation_list(request):
    """
    List all shared conversations with search and filtering.
    Admin only.
    """
    # Get all shared conversations
    conversations = ChunkedConversation.objects.filter(is_shared=True).select_related('recorded_by', 'prompt_used')

    # Search functionality
    search_query = request.GET.get('q', '')
    if search_query:
        conversations = conversations.filter(
            Q(title__icontains=search_query) |
            Q(recorded_by__username__icontains=search_query) |
            Q(recorded_by__first_name__icontains=search_query) |
            Q(recorded_by__last_name__icontains=search_query) |
            Q(full_transcript__icontains=search_query)
        ).distinct()

    # Filter by user
    user_filter = request.GET.get('user', '')
    if user_filter:
        conversations = conversations.filter(recorded_by__username=user_filter)

    # Filter by analysis status
    status_filter = request.GET.get('status', '')
    if status_filter == 'analyzed':
        conversations = conversations.filter(is_analyzed=True)
    elif status_filter == 'pending':
        conversations = conversations.filter(is_analyzed=False)

    # Filter by prompt
    prompt_filter = request.GET.get('prompt', '')
    if prompt_filter:
        conversations = conversations.filter(prompt_used_id=prompt_filter)

    # Sort
    sort_by = request.GET.get('sort', '-started_at')
    conversations = conversations.order_by(sort_by)

    # Get unique users for filter dropdown
    users = User.objects.filter(
        chunked_conversations__is_shared=True
    ).distinct().order_by('username')

    # Get prompts for filter dropdown
    from streaming.models import AnalysisPrompt
    prompts = AnalysisPrompt.objects.filter(
        chunked_conversations__is_shared=True
    ).distinct().order_by('name')

    context = {
        'conversations': conversations,
        'search_query': search_query,
        'user_filter': user_filter,
        'status_filter': status_filter,
        'prompt_filter': prompt_filter,
        'sort_by': sort_by,
        'users': users,
        'prompts': prompts,
    }

    return render(request, 'chunking/conversation_list.html', context)


@login_required
@staff_member_required
def conversation_detail(request, conversation_id):
    """
    View detailed conversation with transcript, speakers, and analysis.
    Admin only, shared conversations only.
    """
    # Get conversation (must be shared)
    conversation = get_object_or_404(
        ChunkedConversation.objects.select_related('recorded_by', 'prompt_used'),
        id=conversation_id,
        is_shared=True
    )

    # Get speakers
    speakers = conversation.speakers.all()

    # Calculate speaker stats BEFORE slicing (using full queryset)
    speaker_stats = {}
    for speaker in speakers:
        speaker_segments = conversation.segments.filter(speaker=speaker)
        speaker_stats[speaker.id] = {
            'speaker': speaker,
            'segment_count': speaker_segments.count(),
            'total_words': sum(len(s.text.split()) for s in speaker_segments)
        }

    # NOW slice segments for display (after stats are calculated)
    segments = conversation.segments.select_related('speaker').order_by('start_time')[:1000]

    context = {
        'conversation': conversation,
        'speakers': speakers,
        'segments': segments,
        'speaker_stats': speaker_stats,
        'has_more_segments': conversation.segments.count() > 1000,
    }

    return render(request, 'chunking/conversation_detail.html', context)


@login_required
@staff_member_required
def conversation_analysis(request, conversation_id):
    """
    View AI analysis results for a conversation.
    Admin only, shared conversations only.
    """
    conversation = get_object_or_404(
        ChunkedConversation.objects.select_related('recorded_by', 'prompt_used'),
        id=conversation_id,
        is_shared=True
    )

    context = {
        'conversation': conversation,
    }

    return render(request, 'chunking/conversation_analysis.html', context)


@login_required
@staff_member_required
def user_conversations(request, user_id):
    """
    View all shared conversations for a specific user.
    Admin only.
    """
    user = get_object_or_404(User, id=user_id)

    conversations = ChunkedConversation.objects.filter(
        recorded_by=user,
        is_shared=True
    ).select_related('prompt_used').order_by('-started_at')

    # Statistics
    total_conversations = conversations.count()
    analyzed_count = conversations.filter(is_analyzed=True).count()
    total_duration = sum([c.total_duration_seconds for c in conversations])

    context = {
        'user': user,
        'conversations': conversations,
        'total_conversations': total_conversations,
        'analyzed_count': analyzed_count,
        'total_duration': total_duration,
    }

    return render(request, 'chunking/user_conversations.html', context)


@login_required
@staff_member_required
def export_transcript(request, conversation_id):
    """
    Export transcript as plain text file.
    Admin only, shared conversations only.
    """
    from django.http import HttpResponse

    conversation = get_object_or_404(
        ChunkedConversation,
        id=conversation_id,
        is_shared=True
    )

    # Use formatted transcript if available, otherwise full transcript
    transcript = conversation.formatted_transcript or conversation.full_transcript

    if not transcript:
        messages.error(request, 'No transcript available for this conversation')
        return redirect('chunking:conversation_detail', conversation_id=conversation_id)

    # Create response with text file
    response = HttpResponse(transcript, content_type='text/plain')
    filename = f"transcript_{conversation.id}_{conversation.recorded_by.username}.txt"
    response['Content-Disposition'] = f'attachment; filename="{filename}"'

    return response


@login_required
@staff_member_required
def export_analysis(request, conversation_id):
    """
    Export analysis as JSON file.
    Admin only, shared conversations only.
    """
    from django.http import HttpResponse

    conversation = get_object_or_404(
        ChunkedConversation,
        id=conversation_id,
        is_shared=True
    )

    # Build analysis data
    analysis_data = {
        'conversation_id': conversation.id,
        'user': conversation.recorded_by.username,
        'title': conversation.title,
        'started_at': conversation.started_at.isoformat(),
        'duration_seconds': conversation.total_duration_seconds,
        'prompt_used': conversation.prompt_used.name if conversation.prompt_used else None,
        'summary': conversation.summary,
        'action_items': conversation.action_items,
        'key_topics': conversation.key_topics,
        'sentiment': conversation.sentiment,
        'coaching_feedback': conversation.coaching_feedback,
    }

    # Create response with JSON file
    response = HttpResponse(
        json.dumps(analysis_data, indent=2),
        content_type='application/json'
    )
    filename = f"analysis_{conversation.id}_{conversation.recorded_by.username}.json"
    response['Content-Disposition'] = f'attachment; filename="{filename}"'

    return response