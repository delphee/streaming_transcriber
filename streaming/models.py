'''
This is streaming/models.py
The models for the old streaming app
'''



from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone


class Conversation(models.Model):
    """A recorded conversation session"""
    id = models.CharField(max_length=100, primary_key=True)  # UUID from iOS
    recorded_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name='conversations')
    started_at = models.DateTimeField(default=timezone.now)
    ended_at = models.DateTimeField(null=True, blank=True)
    duration_seconds = models.IntegerField(default=0)

    # Audio storage - dual quality system
    streaming_audio_url = models.URLField(blank=True)  # 16kHz from WebSocket streaming
    audio_url = models.URLField(blank=True)  # 44.1kHz high-quality (main audio)
    audio_uploaded_at = models.DateTimeField(null=True, blank=True)
    audio_delete_at = models.DateTimeField(null=True, blank=True)  # When to delete from S3

    # Audio quality tracking
    audio_quality = models.CharField(
        max_length=20,
        choices=[
            ('streaming_only', 'Streaming Only (16kHz)'),
            ('high_quality', 'High Quality (44.1kHz)'),
        ],
        default='streaming_only'
    )

    # Status
    is_active = models.BooleanField(default=True)  # Currently streaming
    is_analyzed = models.BooleanField(default=False)  # AI analysis complete

    # Metadata
    title = models.CharField(max_length=255, blank=True)  # Auto-generated or user-set
    notes = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-started_at']

    def __str__(self):
        return f"Conversation {self.id} by {self.recorded_by.username}"


class Speaker(models.Model):
    """A participant in a conversation"""
    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name='speakers')

    # Speaker identification
    speaker_label = models.CharField(max_length=50)  # "Speaker A", "Speaker B", etc. from AssemblyAI
    identified_name = models.CharField(max_length=255, blank=True)  # AI-suggested or user-assigned name
    is_recording_user = models.BooleanField(default=False)  # Is this the person who recorded?

    # Confirmation
    name_confirmed = models.BooleanField(default=False)  # User manually confirmed the name

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ['conversation', 'speaker_label']
        ordering = ['speaker_label']

    def __str__(self):
        name = self.identified_name if self.identified_name else self.speaker_label
        return f"{name} in {self.conversation.id}"


class TranscriptSegment(models.Model):
    """Individual transcript line with speaker and timing"""
    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name='segments')
    speaker = models.ForeignKey(Speaker, on_delete=models.CASCADE, related_name='segments', null=True, blank=True)

    # Content
    text = models.TextField()
    is_final = models.BooleanField(default=False)  # Final vs partial transcript

    # Timing (milliseconds)
    start_time = models.IntegerField(null=True, blank=True)
    end_time = models.IntegerField(null=True, blank=True)

    # AssemblyAI metadata
    turn_order = models.IntegerField(null=True, blank=True)
    confidence = models.FloatField(null=True, blank=True)

    # Source of transcription
    source = models.CharField(
        max_length=20,
        choices=[
            ('streaming', 'Streaming Transcription'),
            ('high_quality', 'High Quality Transcription'),
        ],
        default='streaming'
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at']

    def __str__(self):
        speaker_name = self.speaker.identified_name if self.speaker and self.speaker.identified_name else "Unknown"
        return f"{speaker_name}: {self.text[:50]}..."


class ConversationAnalysis(models.Model):
    """AI analysis results for a conversation"""
    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name='analyses')

    # Analysis type (we'll have different analysis templates)
    analysis_type = models.CharField(max_length=100)  # "sales_coaching", "customer_service", etc.
    prompt_template = models.TextField()  # The prompt used for analysis

    # Results
    analysis_result = models.TextField()  # JSON or formatted text from AI

    # Metrics (can be extracted from analysis_result)
    sentiment_score = models.FloatField(null=True, blank=True)
    key_points_detected = models.JSONField(default=list)  # List of talking points found

    # Access control
    visible_to_user = models.BooleanField(default=True)  # User can see this analysis
    visible_to_admin = models.BooleanField(default=True)  # Admin can see this analysis

    created_at = models.DateTimeField(auto_now_add=True)

    # Add after key_points_detected field:
    prompt_used = models.ForeignKey('AnalysisPrompt', on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name='analyses')
    shared_with_admin = models.BooleanField(default=False)  # User shared this result with admin

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.analysis_type} for {self.conversation.id}"


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
        return f"Profile for {self.user.username}"


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