"""
This is chunking/urls.py
URL routing for the new chunking app.
"""

from django.urls import path
from . import views

app_name = 'chunking'

urlpatterns = [
    # Receive webhook from ST
    path('dispatch/', views.st_webhook_receiver, name='st_webhook_receiver'),
    # Chunk upload
    path('chunk/', views.upload_chunk, name='upload_chunk'),
    # iOS conversation retrieval
    path('recent-summaries/', views.recent_summaries, name='recent_summaries'),

    # Final file upload workflow
    path('<str:conversation_id>/request-upload/', views.request_upload_url, name='request_upload_url'),
    path('<str:conversation_id>/finalize/', views.finalize_conversation, name='finalize_conversation'),

    path('<str:conversation_id>/share/', views.toggle_share, name='toggle_share'),

    # Status and info
    path('<str:conversation_id>/status/', views.conversation_status, name='conversation_status'),
    path('<str:conversation_id>/', views.conversation_detail, name='conversation_detail'),
    path('conversations/', views.conversation_list, name='conversation_list'),

    # Analysis
    path('<str:conversation_id>/analysis/', views.conversation_analysis, name='conversation_analysis'),
    path('<str:conversation_id>/retry-analysis/', views.retry_analysis, name='retry_analysis'),


    # Management
    path('<str:conversation_id>/save/', views.save_permanently, name='save_permanently'),
    path('<str:conversation_id>/delete/', views.delete_conversation, name='delete_conversation'),

    # Search
    path('search/', views.search_conversations, name='search_conversations'),


]