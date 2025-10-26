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

from .models import Conversation, TranscriptSegment, Speaker, ConversationAnalysis, UserProfile, AuthToken
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
    segments = conversation.segments.filter(is_final=True).select_related('speaker').order_by('created_at')

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
        UserProfile.objects.create(user=user)

        messages.success(request, f'User {user.username} created successfully')
        return redirect('user_management')

    return render(request, 'streaming/user_create.html')


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
        profile.save()

        messages.success(request, f'User {user_to_edit.username} updated successfully')
        return redirect('user_management')

    context = {
        'user_to_edit': user_to_edit,
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

    # Get segments
    segments = conversation.segments.filter(is_final=True).select_related('speaker').order_by('created_at')

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