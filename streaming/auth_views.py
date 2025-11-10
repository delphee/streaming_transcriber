from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.contrib.auth.models import User
from django.utils import timezone
from datetime import timedelta
import json
import secrets

from .models import AuthToken, UserProfile
from history.models import DispatchJob

# MARK: - Web Authentication (Session-based)

def web_login(request):
    """Web login page"""
    if request.user.is_authenticated:
        return redirect('dashboard')

    if request.method == 'POST':
        email_or_username = request.POST.get('email', '').lower().strip()
        password = request.POST.get('password', '')

        # Try to find user by email first, then username
        user = None
        try:
            user = User.objects.get(email=email_or_username)
        except User.DoesNotExist:
            try:
                user = User.objects.get(username=email_or_username)
            except User.DoesNotExist:
                messages.error(request, 'Invalid email/username or password')
                return render(request, 'streaming/login.html')

        # Authenticate
        user = authenticate(request, username=user.username, password=password)

        if user is not None:
            login(request, user)
            return redirect('dashboard')
        else:
            messages.error(request, 'Invalid email/username or password')

    return render(request, 'streaming/login.html')


@login_required
def web_logout(request):
    """Web logout"""
    logout(request)
    messages.success(request, 'You have been logged out successfully')
    return redirect('web_login')


# MARK: - iOS Authentication (Token-based API)

@csrf_exempt
def ios_login(request):
    """iOS token-based login"""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST required'}, status=405)
    print("ios_login running....")
    try:
        data = json.loads(request.body)
        email = data.get('email', '').lower().strip()
        password = data.get('password', '')
        print(f"Attempting to log in >{email}< >{password}<... ")
        if not email or not password:
            return JsonResponse({'success': False, 'error': 'Email and password required'}, status=400)

        # Find user by email
        try:
            user = User.objects.get(email=email)
        except User.DoesNotExist:
            return JsonResponse({'success': False, 'error': 'Invalid credentials'}, status=401)

        # Authenticate
        authenticated_user = authenticate(username=user.username, password=password)

        if authenticated_user is None:
            return JsonResponse({'success': False, 'error': 'Invalid credentials'}, status=401)

        # Create or get user profile
        profile, _ = UserProfile.objects.get_or_create(user=authenticated_user)

        # Generate authentication token
        token = generate_auth_token(authenticated_user)

        current_appointment = None
        dispatch_jobs = DispatchJob.objects.filter(active=True, tech_id=str(profile.tech_id))
        if len(dispatch_jobs) > 0:
            d_job = dispatch_jobs[0]
            appointment_id = d_job.appointment_id
            if d_job.polling_active and d_job.ai_document_built:
                current_appointment = {
                    "appointment_id": appointment_id,
                    "result": 3
                }
        print(f"current_appointment: {current_appointment}")

        return JsonResponse({
            'success': True,
            'token': token.token,
            'user': {
                'id': str(authenticated_user.id),
                'email': authenticated_user.email,
                'username': authenticated_user.username,
                'first_name': authenticated_user.first_name,
                'last_name': authenticated_user.last_name,
                'user_type': 'admin' if authenticated_user.is_staff else 'user',
                'auto_share': profile.auto_share
            },
            'current_appointment':current_appointment
        })

    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'Invalid JSON'}, status=400)
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@csrf_exempt
def ios_verify_token(request):
    """Verify iOS authentication token"""
    if request.method != 'POST':
        return JsonResponse({'valid': False, 'error': 'POST required'}, status=405)

    try:
        data = json.loads(request.body)
        token_string = data.get('token', '')

        if not token_string:
            return JsonResponse({'valid': False, 'error': 'Token required'}, status=400)

        try:
            token = AuthToken.objects.get(token=token_string, is_active=True)

            # Check if token is expired
            if token.expires_at < timezone.now():
                token.is_active = False
                token.save()
                return JsonResponse({'valid': False, 'error': 'Token expired'}, status=401)

            # Token is valid
            user = token.user
            return JsonResponse({
                'valid': True,
                'user': {
                    'id': str(user.id),
                    'email': user.email,
                    'username': user.username,
                    'first_name': user.first_name,
                    'last_name': user.last_name,
                    'is_staff': user.is_staff,
                }
            })

        except AuthToken.DoesNotExist:
            return JsonResponse({'valid': False, 'error': 'Invalid token'}, status=401)

    except json.JSONDecodeError:
        return JsonResponse({'valid': False, 'error': 'Invalid JSON'}, status=400)
    except Exception as e:
        return JsonResponse({'valid': False, 'error': str(e)}, status=500)


@csrf_exempt
def ios_logout(request):
    """Invalidate iOS authentication token"""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST required'}, status=405)

    try:
        data = json.loads(request.body)
        token_string = data.get('token', '')

        if token_string:
            try:
                token = AuthToken.objects.get(token=token_string)
                token.is_active = False
                token.save()
            except AuthToken.DoesNotExist:
                pass

        return JsonResponse({'success': True})

    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'Invalid JSON'}, status=400)
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


# MARK: - Helper Functions

def generate_auth_token(user):
    """Generate a new authentication token for a user"""
    # Deactivate old tokens (optional - or keep multiple active)
    # AuthToken.objects.filter(user=user, is_active=True).update(is_active=False)

    # Generate new token
    token_string = secrets.token_urlsafe(48)
    expires_at = timezone.now() + timedelta(days=30)  # 30-day token

    token = AuthToken.objects.create(
        user=user,
        token=token_string,
        expires_at=expires_at,
        is_active=True
    )

    return token


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