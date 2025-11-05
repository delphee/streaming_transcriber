from django.urls import path
from . import views

app_name = 'chunking'

urlpatterns = [
    # --- Webhook & chunk uploads ---

    path('chunk/', views.upload_chunk, name='upload_chunk'),

    # --- Search & recent summaries (fixed endpoints first) ---
    path('search/', views.search_conversations, name='search_conversations'),
    path('recent-summaries/', views.recent_summaries, name='recent_summaries'),

    # --- List view ---
    path('conversations/', views.conversation_list, name='conversation_list'),

    # --- Conversation actions (grouped logically) ---
    path('<str:conversation_id>/request-upload/', views.request_upload_url, name='request_upload_url'),
    path('<str:conversation_id>/finalize/', views.finalize_conversation, name='finalize_conversation'),
    path('<str:conversation_id>/share/', views.toggle_share, name='toggle_share'),

    # --- Info & analysis ---
    path('<str:conversation_id>/status/', views.conversation_status, name='conversation_status'),
    path('<str:conversation_id>/analysis/', views.conversation_analysis, name='conversation_analysis'),
    path('<str:conversation_id>/retry-analysis/', views.retry_analysis, name='retry_analysis'),

    # --- Management ---
    path('<str:conversation_id>/save/', views.save_permanently, name='save_permanently'),
    path('<str:conversation_id>/delete/', views.delete_conversation, name='delete_conversation'),

    # --- Catch-all detail view (keep last!) ---
    path('<str:conversation_id>/', views.conversation_detail, name='conversation_detail'),
]
