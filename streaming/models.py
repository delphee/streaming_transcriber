'''
This is streaming/models.py
The models for the old streaming app
'''



from django.db import models
from django.contrib.auth.models import User





class AnalysisPrompt(models.Model):
    """AI analysis prompts that can be assigned to users"""
    name = models.CharField(max_length=200)  # "Sales Call Quality Check"
    description = models.TextField(blank=True)  # What this prompt is for

    # What the admin wrote in plain English
    plain_text = models.TextField(help_text="What you want the AI to analyze, in plain English")

    # What AI optimized it to
    optimized_prompt = models.TextField(help_text="AI-optimized professional prompt")

    # Status
    is_active = models.BooleanField(default=True)
    is_default = models.BooleanField(default=False, help_text="Default prompt used when user has no assignment")
    is_system = models.BooleanField(default=False, help_text="System prompt that cannot be edited or deleted")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='created_prompts')

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.name

class UserProfile(models.Model):
    """Extended user profile with app-specific settings"""
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    st_id = models.CharField(max_length=50, null=True, blank=True)
    active = models.BooleanField(default=True)
    # Feature flags (modular features)
    enable_real_time_coaching = models.BooleanField(default=False)
    enable_talking_points_monitoring = models.BooleanField(default=False)
    enable_sentiment_alerts = models.BooleanField(default=False)
    enable_speaker_identification = models.BooleanField(default=True)

    # Alert settings
    alert_email = models.EmailField(blank=True)
    alert_on_heated_conversation = models.BooleanField(default=False)

    auto_share = models.BooleanField(default=False, help_text="Automatically share all conversations with admin")

    # Analysis templates assigned to this user
    default_analysis_type = models.CharField(max_length=100, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # Add after default_analysis_type field:
    assigned_prompt = models.ForeignKey('AnalysisPrompt', on_delete=models.SET_NULL, null=True, blank=True,
                                        related_name='assigned_users')

    def __str__(self):
        return f"Profile for {self.user.username} Database ID: {self.id}"


class AuthToken(models.Model):
    """Authentication tokens for iOS app"""
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='auth_tokens')
    token = models.CharField(max_length=64, unique=True)

    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"Token for {self.user.username}"

# Create your models here.