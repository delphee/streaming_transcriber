'''
This is streaming/views.py
some views for the old streaming app are used in the other apps
'''
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.models import User
from django.contrib import messages
from chunking.transcription import optimize_prompt
from .models import UserProfile, AnalysisPrompt


# MARK: - User Management (Admin Only)

@staff_member_required
def user_management(request):
    """Admin view to manage users"""
    users = User.objects.all().select_related('profile').order_by('-date_joined')

    # Calculate statistics
    total_users = users.count()
    active_users = users.filter(is_active=True).count()
    admin_users = users.filter(is_staff=True).count()
    coaching_enabled = users.filter(profile__enable_real_time_coaching=True).count()

    context = {
        'users': users,
        'total_users': total_users,
        'active_users': active_users,
        'admin_users': admin_users,
        'coaching_enabled': coaching_enabled,
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

        # Create profile with all settings
        profile = UserProfile.objects.create(user=user)

        # Feature flags
        profile.enable_real_time_coaching = request.POST.get('enable_real_time_coaching') == 'on'
        profile.enable_talking_points_monitoring = request.POST.get('enable_talking_points_monitoring') == 'on'
        profile.enable_sentiment_alerts = request.POST.get('enable_sentiment_alerts') == 'on'
        profile.enable_speaker_identification = request.POST.get('enable_speaker_identification') == 'on'

        # Alert settings
        profile.alert_email = request.POST.get('alert_email', '').strip()
        profile.alert_on_heated_conversation = request.POST.get('alert_on_heated_conversation') == 'on'
        profile.auto_share = request.POST.get('auto_share') == 'on'

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
        profile.auto_share = request.POST.get('auto_share') == 'on'
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
# I DON'T THINK THIS IS USED; THE TEMPLATE DOESN'T EXIST!
@login_required
def user_profile(request):
    """User's own profile"""
    user = request.user
    profile = user.profile

    # Get user statistics from chunking app (updated system)
    from chunking.models import ChunkedConversation
    total_conversations = ChunkedConversation.objects.filter(recorded_by=user).count()
    total_duration = sum([c.total_duration_seconds for c in ChunkedConversation.objects.filter(recorded_by=user)])

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


