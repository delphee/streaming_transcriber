from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone
import uuid
# Create your models here.

TECHS = {
    "3027961":"Ethan Ficklin",
    "190999251":"Kevin Stanley",
    "3027975":"Ronnie Bland",
    "162915344":"Brett Allen",
    "141471729":"Josue Rodriguez",
    "383003734":"AJ Ruths",
    "128166026":"Jake West",
    "356406954":"Michael Ouden Jr",
    "384234754":"Christopher Franklin",
    "7129641":"David Elphee",
    "144096740":"Jayden Barlow",
    "383003261":"John Sayers",
    "138699985":"John Williams",
    "273358904":"Josh Jenkins",
    "43715608":"Justin Barron",
    "345283118":"Osman Harooni",
    "380471230":"Riley Woodward",
    "224925184":"Shawn Hollingsworth",
    "133853401":"Stephen Starner",
    "67321105":"Thomas Shawaryn",
    "114376585":"Tim Miller",
    "125325480":"Jake Simpson",
    "111119391":"Dewayne McCauley",
    "226615332":"Jason OBrien"
}




class AccessToken(models.Model):
    token =models.TextField()
    when = models.DateTimeField()


class DispatchJob(models.Model):
    status_choices = (
        ('Scheduled','Scheduled'),('Dispatched', 'Dispatched'),('Working', 'Working'),('Done', 'Done')
    )
    job_id = models.CharField(max_length=50)
    appointment_id = models.CharField(max_length=50)
    tech_id = models.CharField(max_length=50)
    active = models.BooleanField(default=True)
    last_updated = models.DateTimeField(auto_now=True)
    status = models.CharField(max_length=20, choices=status_choices)
    polling_active = models.BooleanField(default=True, help_text="Server is polling ST for 'Working' status")
    recording_active = models.BooleanField(default=False, help_text="iOS has acknowledged server's 'Working' push notification")
    recording_stopped = models.BooleanField(default=False, help_text="iOS has acknowledged server's 'Done' push notification")
    notified_working = models.BooleanField(default=False, help_text="Push sent for status=Working (result:1)")
    notified_done = models.BooleanField(default=False, help_text="Push sent for status=Done (result:2)")
    notified_history = models.BooleanField(default=False, help_text="Push sent for history ready (result:3)")
    ai_document_s3_key = models.TextField(blank=True, null=True, help_text="S3 key for AI job data document")
    ai_document_built = models.BooleanField(default=False, help_text="AI document has been built and uploaded to S3")
    out_of_order = models.BooleanField(default=False, help_text="Appointment returned to 'Scheduled' status after Dispatch")

    def __str__(self):
        return (f"{self.job_id} - {self.appointment_id} {'Polling...' if self.polling_active else ''} "
                f"{TECHS[self.tech_id] if self.tech_id in TECHS else ''} {'DOC' if self.ai_document_built else ''}")


class DeviceToken(models.Model):

    user = models.ForeignKey(User, on_delete=models.CASCADE)
    device_token = models.CharField(max_length=255, unique=True)
    platform = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.user.username}"


# === SERVICETITAN CALL IMPORT MODELS ===

class ServiceTitanCallSession(models.Model):
    """Container for imported ServiceTitan phone call recording and processing results"""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    session_name = models.CharField(max_length=255, blank=True, null=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)
    is_completed = models.BooleanField(default=False)
    completed_at = models.DateTimeField(null=True, blank=True)

    # ServiceTitan call metadata
    # {
    #     'source': 'servicetitan',
    #     'call_id': '12345',
    #     'original_duration': '00:05:32',
    #     'duration_seconds': 332,
    #     'actual_call_datetime': '2024-01-15T10:30:00Z',
    #     'agent_name': 'John Smith',
    #     'customer_name': 'Jane Doe',
    #     'direction': 'Inbound',
    #     'from_number': '+15551234567',
    #     'to_number': '+15559876543',
    #     'speaker_name_mapping': {...},
    #     'enhanced_transcripts': {...}
    # }
    session_metadata = models.JSONField(blank=True, null=True)

    PROCESSING_STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('downloading', 'Downloading'),
        ('transcribing', 'Transcribing'),
        ('analyzing', 'Analyzing'),
        ('completed', 'Completed'),
        ('error', 'Error'),
    ]
    processing_status = models.CharField(
        max_length=20,
        choices=PROCESSING_STATUS_CHOICES,
        default='pending'
    )

    full_transcript = models.TextField(blank=True, null=True)
    processing_error = models.TextField(blank=True, null=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'ServiceTitan Call Session'
        verbose_name_plural = 'ServiceTitan Call Sessions'

    def __str__(self):
        return f"{self.session_name or 'Call'} - {self.processing_status}"


class CallAudioChunk(models.Model):
    """Audio file for a ServiceTitan call session (typically single chunk for whole call)"""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    session = models.ForeignKey(
        ServiceTitanCallSession,
        on_delete=models.CASCADE,
        related_name='chunks'
    )

    chunk_order = models.PositiveIntegerField(default=0)
    uploaded_at = models.DateTimeField(default=timezone.now)

    audio_file = models.FileField(upload_to='call_recordings/')
    file_size = models.PositiveIntegerField(null=True, blank=True)
    duration_seconds = models.FloatField(null=True, blank=True)

    CHUNK_STATUS_CHOICES = [
        ('uploaded', 'Uploaded'),
        ('processing', 'Processing'),
        ('transcribed', 'Transcribed'),
        ('error', 'Error'),
    ]
    status = models.CharField(max_length=20, choices=CHUNK_STATUS_CHOICES, default='uploaded')

    transcript_text = models.TextField(blank=True, null=True)

    # Speaker diarization data:
    # {
    #     'utterances': [
    #         {'speaker': 'A', 'text': '...', 'start': 0.0, 'end': 5.2, 'confidence': 0.95},
    #         ...
    #     ],
    #     'speaker_transcript': 'Speaker A: ...\nSpeaker B: ...',
    #     'speakers_detected': 2,
    #     'audio_duration': 332.5
    # }
    speaker_segments = models.JSONField(blank=True, null=True)
    processing_error = models.TextField(blank=True, null=True)
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['session', 'chunk_order']
        unique_together = ['session', 'chunk_order']
        verbose_name = 'Call Audio Chunk'
        verbose_name_plural = 'Call Audio Chunks'

    def __str__(self):
        return f"Audio for {self.session.session_name or self.session.id}"


class CallAnalysis(models.Model):
    """AI analysis results for a ServiceTitan call session"""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    session = models.OneToOneField(
        ServiceTitanCallSession,
        on_delete=models.CASCADE,
        related_name='ai_analysis'
    )

    summary = models.TextField(blank=True, null=True)
    tone = models.CharField(max_length=50, blank=True, null=True)
    importance = models.CharField(max_length=50, blank=True, null=True)
    full_ai_response = models.TextField(blank=True, null=True)

    # Structured analysis fields from AI response:
    # {
    #     'analysis_type': 'customer_service',
    #     'fields': {
    #         'INTERACTION SUMMARY': {'value': '...', 'order': 1},
    #         'ISSUE RESOLUTION': {'value': '...', 'order': 2},
    #         ...
    #     },
    #     'metadata': {'parsed_at': '...', 'field_count': 8}
    # }
    structured_analysis = models.JSONField(blank=True, null=True)

    ANALYSIS_STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('processing', 'Processing'),
        ('completed', 'Completed'),
        ('error', 'Error'),
    ]
    analysis_status = models.CharField(max_length=20, choices=ANALYSIS_STATUS_CHOICES, default='pending')

    created_at = models.DateTimeField(default=timezone.now)
    processed_at = models.DateTimeField(null=True, blank=True)
    processing_error = models.TextField(blank=True, null=True)

    class Meta:
        verbose_name = 'Call Analysis'
        verbose_name_plural = 'Call Analyses'

    def __str__(self):
        return f"Analysis for {self.session.session_name or self.session.id}"
