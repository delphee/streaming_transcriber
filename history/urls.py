from django.urls import path
from history import views




urlpatterns = [
    path('job-complete-webhook/', views.job_complete, name='job_complete'),
    #path('history/check-tech-status/', views.check_tech_status, name='check_tech_status'),
    path('api/register-device-token/', views.register_device_token, name='register_device_token'),
    path('api/confirm-notification/', views.confirm_notification, name='confirm_notification'),
    path('api/ai-conversation/query/', views.ai_conversation_query, name='ai_conversation_query'),

    ]
