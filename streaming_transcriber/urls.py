"""
URL configuration for streaming_transcriber project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path, include
from chunking.views import receive_webhook
from django.http import HttpResponse
from django.views import View

class RobotsTxtView(View):
    """Serve robots.txt to prevent crawling of sensitive endpoints"""
    def get(self, request):
        lines = [
            "User-agent: *",
            "# Block authentication endpoints from being crawled",
            "Disallow: /login/",
            "Disallow: /logout/",
            "Disallow: /api/auth/",
            "Disallow: /accounts/",
            "Disallow: /admin/",
            "# Block API endpoints",
            "Disallow: /api/",
            "Disallow: /dispatch/",
            "# Block user-specific paths",
            "Disallow: /users/",
            "Disallow: /profile/",
            "Disallow: /settings/",
            "# Allow root and public pages",
            "Allow: /",
        ]
        return HttpResponse("\n".join(lines), content_type="text/plain")


class SecurityTxtView(View):
    """Serve security.txt to show legitimacy and provide security contact info"""

    def get(self, request):
        # Get the current domain from the request
        domain = request.get_host()

        lines = [
            "# Internal business application - authorized personnel only",
            "# For security issues, contact your IT administrator",

            "Expires: 2026-12-31T23:59:59.000Z",
            "Preferred-Languages: en",
            f"Canonical: https://{domain}/.well-known/security.txt",
            "",
            "# This is a legitimate internal business tool for field service management",
            "# Not a phishing site - uses standard Django authentication patterns",
            "# Employee access only - appointment scheduling and conversation transcription",
        ]
        return HttpResponse("\n".join(lines), content_type="text/plain")

urlpatterns = [
    path('robots.txt', RobotsTxtView.as_view(), name='robots_txt'),
    path('.well-known/security.txt', SecurityTxtView.as_view(), name='security_txt'),
    path('security.txt', SecurityTxtView.as_view(), name='security_txt_root'),
    path('admin/', admin.site.urls),
    path('dispatch/', receive_webhook, name='st_webhook_receiver'),
    path('', include('streaming.urls')),
    path('', include('history.urls')),
    path('chunking/', include('chunking.urls')),
    path('conversations/', include('chunking.web_urls')),
]
