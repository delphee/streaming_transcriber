from django.urls import path
from . import views, auth_views
from django.views.generic import RedirectView

urlpatterns = [
    # Web Authentication
    path('accounts/login/', RedirectView.as_view(pattern_name='web_login', permanent=True)),
    path('login/', auth_views.web_login, name='web_login'),
    path('logout/', auth_views.web_logout, name='web_logout'),

    # iOS API Authentication
    path('api/auth/login/', auth_views.ios_login, name='ios_login'),
    path('api/auth/verify/', auth_views.ios_verify_token, name='ios_verify_token'),
    path('api/auth/logout/', auth_views.ios_logout, name='ios_logout'),

    # Dashboard & Main Views
    path('', views.dashboard, name='dashboard'),
    path('conversations/', views.conversation_list, name='conversation_list'),
    path('conversations/<str:conversation_id>/', views.conversation_detail, name='conversation_detail'),

    # User Management (Admin only)
    path('users/', views.user_management, name='user_management'),
    path('users/create/', views.user_create, name='user_create'),
    path('users/<int:user_id>/edit/', views.user_edit, name='user_edit'),
    path('users/<int:user_id>/delete/', views.user_delete, name='user_delete'),

    # User Profile & Settings
    path('profile/', views.user_profile, name='user_profile'),
    path('settings/', views.user_settings, name='user_settings'),

    # Prompt Management (Admin only)
    path('prompts/', views.prompt_management, name='prompt_management'),
    path('prompts/create/', views.prompt_create, name='prompt_create'),
    path('prompts/optimize/', views.prompt_optimize, name='prompt_optimize'),
    path('prompts/<int:prompt_id>/edit/', views.prompt_edit, name='prompt_edit'),
    path('prompts/<int:prompt_id>/delete/', views.prompt_delete, name='prompt_delete'),
    path('prompts/<int:prompt_id>/assign/', views.prompt_assign, name='prompt_assign'),

    # API Endpoints for iOS
    path('api/conversations/', views.api_conversation_list, name='api_conversation_list'),
    path('api/conversations/<str:conversation_id>/', views.api_conversation_detail, name='api_conversation_detail'),
    path('api/conversations/<str:conversation_id>/upload-hq-audio/', views.api_upload_hq_audio, name='api_upload_hq_audio'),
]