"""
URL routing for chunking app web UI (Desktop/Admin interface)
"""

from django.urls import path
from . import web_views

app_name = 'chunking_web'

urlpatterns = [
    # Dashboard
    path('', web_views.dashboard, name='dashboard'),

    # Conversation views
    path('conversations/', web_views.conversation_list, name='conversation_list'),
    path('conversations/<str:conversation_id>/', web_views.conversation_detail, name='conversation_detail'),
    path('conversations/<str:conversation_id>/analysis/', web_views.conversation_analysis,
         name='conversation_analysis'),

    # User-specific views
    path('users/<int:user_id>/conversations/', web_views.user_conversations, name='user_conversations'),

    # Export functions
    path('conversations/<str:conversation_id>/export/transcript/', web_views.export_transcript,
         name='export_transcript'),
    path('conversations/<str:conversation_id>/export/analysis/', web_views.export_analysis, name='export_analysis'),
]