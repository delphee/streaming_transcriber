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
from chunking.views import st_webhook_receiver
urlpatterns = [
    path('admin/', admin.site.urls),
    path('dispatch/', st_webhook_receiver, name='st_webhook_receiver'),
    path('', include('streaming.urls')),
    path('chunking/', include('chunking.urls')),
    path('conversations/', include('chunking.web_urls')),
]
