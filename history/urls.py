from django.urls import path
from history import views




urlpatterns = [
    path('job-complete-webhook/', views.job_complete, name='job_complete'),
    path('history/check-tech-status/', views.check_tech_status, name='check_tech_status'),
    ]
