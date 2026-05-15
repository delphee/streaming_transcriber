from django.urls import path
from history import views
from history.call_views import (
    CallImportView,
    CallSearchView,
    CallProcessingStatusView,
    CallDetailView,
)


urlpatterns = [
    path('job-complete-webhook/', views.job_complete, name='job_complete'),
    path('api/register-device-token/', views.register_device_token, name='register_device_token'),
    path('api/confirm-notification/', views.confirm_notification, name='confirm_notification'),
    path('api/ai-query/', views.ai_conversation_query, name='ai_conversation_query'),
    path('api/ai-conversation/tts/', views.text_to_speech_view, name='text_to_speech'),
    path('api/testing/', views.testing, name='testing'),

    # ServiceTitan Call Import Feature
    path('call-admin/import-call/', CallImportView.as_view(), name='call_import'),
    path('call-admin/search-calls/', CallSearchView.as_view(), name='call_search'),
    path('call-admin/call-status/<uuid:session_id>/', CallProcessingStatusView.as_view(), name='call_status'),
    path('call-admin/call-detail/<uuid:session_id>/', CallDetailView.as_view(), name='call_detail'),
]




