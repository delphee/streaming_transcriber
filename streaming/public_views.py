"""
Public landing page view - no authentication required
Shows what the site is and provides login link for authorized users
"""
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt


def public_landing(request):
    """
    Public landing page explaining the site.
    No authentication required - helps prevent phishing flags.
    """
    return render(request, 'streaming/public_landing.html')


def privacy_policy(request):
    """
    Privacy policy page - explains data collection and usage.
    Accessible without authentication.
    """
    return render(request, 'streaming/privacy_policy.html')








