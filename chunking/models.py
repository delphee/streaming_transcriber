'''
This is chunking/models.py
For the new chunking app
'''

# Create your models here.
from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone
from datetime import timedelta


class ChunkedConversation(models.Model):
    """
    Hybrid conversation recording with:
    - Chunks for preliminary monitoring
    - Complete file for final high-quality analysis
    """
    # Primary key (UUID from iOS)
    id = models.CharField(max_length=100, primary_key=True)
    recorded_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name='chunked_conversations')

    # ServiceTitan data
    job_number = models.CharField(max_length=100, blank=True, null=True)
    customer_name = models.CharField(max_length=255, blank=True, null=True)

    # Timing
    started_at = models.DateTimeField(default=timezone.now)
    ended_at = models.DateTimeField(null=True, blank=True)
    total_duration_seconds = models.IntegerField(default=0)

    # Metadata
    title = models.CharField(max_length=255, blank=True)
    notes = models.TextField(blank=True)

    # === CHUNK SYSTEM (Preliminary Monitoring) ===
    chunks_folder_path = models.CharField(max_length=500, blank=True)  # S3 folder path
    chunk_count = models.IntegerField(default=0)
    received_chunks = models.JSONField(default=list)  # [1, 2, 3, 5...] for tracking
    last_preliminary_transcription = models.DateTimeField(null=True, blank=True)

    # === COMPLETE FILE SYSTEM (Final Analysis) ===
    final_audio_url = models.URLField(max_length=500, blank=True)  # Complete preprocessed FLAC from iOS
    audio_uploaded_at = models.DateTimeField(null=True, blank=True)

    # === MULTIPART UPLOAD TRACKING ===
    multipart_upload_id = models.CharField(max_length=255, blank=True)
    multipart_s3_key = models.CharField(max_length=500, blank=True)
    multipart_parts = models.JSONField(default=list)

    # === STATUS FLAGS ===
    is_chunks_complete = models.BooleanField(default=False)  # All chunks received
    is_final_uploaded = models.BooleanField(default=False)  # Complete file uploaded
    is_analyzed = models.BooleanField(default=False)  # Final transcription complete
    is_transcribing = models.BooleanField(default=False)

    # === TRANSCRIPTION SETTINGS ===
    speakers_expected = models.IntegerField(default=2)  # Expected number of speakers (1-6)

    # === TRANSCRIPTION RESULTS ===
    full_transcript = models.TextField(blank=True)  # Complete conversation transcript
    preliminary_transcript = models.TextField(blank=True)  # Stitched chunk transcripts
    formatted_transcript = models.TextField(blank=True)  # Formatted with speaker names and timestamps

    # === AI ANALYSIS RESULTS ===
    summary = models.TextField(blank=True)  # Conversation summary
    action_items = models.JSONField(default=list)  # Extracted action items
    key_topics = models.JSONField(default=list)  # Main topics discussed
    sentiment = models.CharField(max_length=50, blank=True)  # Overall sentiment
    coaching_feedback = models.TextField(blank=True)  # Role-specific coaching

    # Error tracking
    transcription_error = models.TextField(blank=True)  # Track transcription errors
    analysis_error = models.TextField(blank=True)  # Track analysis errors

    # === SHARING & PROMPTS ===
    is_shared = models.BooleanField(default=False)  # Visible to admins in desktop UI
    prompt_used = models.ForeignKey(
        'streaming.AnalysisPrompt',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='chunked_conversations'
    )  # Which prompt was used for analysis

    # === DELETION POLICY ===
    save_permanently = models.BooleanField(default=False)  # Override auto-deletion
    scheduled_deletion_date = models.DateTimeField(null=True, blank=True)

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-started_at']
        verbose_name = 'Chunked Conversation'
        verbose_name_plural = 'Chunked Conversations'

    def __str__(self):
        return f"Conversation {self.id} by {self.recorded_by.username}"

    def schedule_deletion(self, days=7):
        """Schedule this conversation for deletion"""
        if not self.save_permanently:
            self.scheduled_deletion_date = timezone.now() + timedelta(days=days)
            self.save()

    def mark_permanent(self):
        """Mark this conversation to never be auto-deleted"""
        self.save_permanently = True
        self.scheduled_deletion_date = None
        self.save()

    def get_duration_display(self):
        """Human-readable duration"""
        minutes = self.total_duration_seconds // 60
        seconds = self.total_duration_seconds % 60
        return f"{minutes}m {seconds}s"


class AudioChunk(models.Model):
    """
    Individual 90-second audio chunk for preliminary monitoring.
    Transcribed quickly for real-time feedback.
    """
    conversation = models.ForeignKey(
        ChunkedConversation,
        on_delete=models.CASCADE,
        related_name='chunks'
    )

    # Chunk identification
    chunk_number = models.IntegerField()  # Sequential: 1, 2, 3...

    # Timing (from iOS metadata)
    start_time_seconds = models.IntegerField()  # Offset from conversation start
    duration_seconds = models.IntegerField()  # Actual chunk duration (~90s)

    # S3 Storage
    s3_chunk_url = models.URLField()  # Individual chunk FLAC file
    received_at = models.DateTimeField(auto_now_add=True)

    # === PRELIMINARY TRANSCRIPTION (Fast, for monitoring) ===
    transcript_text = models.TextField(blank=True)
    transcript_source = models.CharField(
        max_length=20,
        choices=[
            ('preliminary', 'Preliminary (Fast)'),
            ('final', 'Final (High Quality)')
        ],
        default='preliminary'
    )
    transcribed_at = models.DateTimeField(null=True, blank=True)
    confidence_score = models.FloatField(null=True, blank=True)

    # === AUDIO QUALITY METADATA (from iOS preprocessing) ===
    rms_level = models.FloatField(null=True, blank=True)  # Root mean square level
    peak_amplitude = models.FloatField(null=True, blank=True)  # Peak level
    speech_percentage = models.FloatField(null=True, blank=True)  # % of chunk with speech

    class Meta:
        ordering = ['-conversation__started_at', 'chunk_number']
        unique_together = ['conversation', 'chunk_number']
        verbose_name = 'Audio Chunk'
        verbose_name_plural = 'Audio Chunks'

    def __str__(self):
        return f"Chunk {self.chunk_number} of {self.conversation.id}"

    def get_time_display(self):
        """Human-readable timestamp"""
        minutes = self.start_time_seconds // 60
        seconds = self.start_time_seconds % 60
        return f"{minutes}:{seconds:02d}"


class Speaker(models.Model):
    """
    Speaker identified in the final transcription.
    Only created after final analysis.
    """
    conversation = models.ForeignKey(
        ChunkedConversation,
        on_delete=models.CASCADE,
        related_name='speakers'
    )

    # Speaker identification
    speaker_label = models.CharField(max_length=50)  # "Speaker A", "Speaker B" from AssemblyAI
    identified_name = models.CharField(max_length=255, blank=True)  # AI-suggested or user-assigned
    is_recording_user = models.BooleanField(default=False)  # Is this the person who recorded?

    # Confirmation
    name_confirmed = models.BooleanField(default=False)  # User manually confirmed

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ['conversation', 'speaker_label']
        ordering = ['speaker_label']
        verbose_name = 'Speaker'
        verbose_name_plural = 'Speakers'

    def __str__(self):
        name = self.identified_name if self.identified_name else self.speaker_label
        return f"{name} in {self.conversation.id}"


class TranscriptSegment(models.Model):
    """
    Individual transcript utterance with speaker and timing.
    Only created after final transcription.
    """
    conversation = models.ForeignKey(
        ChunkedConversation,
        on_delete=models.CASCADE,
        related_name='segments'
    )
    speaker = models.ForeignKey(
        Speaker,
        on_delete=models.CASCADE,
        related_name='segments',
        null=True,
        blank=True
    )

    # Content
    text = models.TextField()

    # Timing (milliseconds from conversation start)
    start_time = models.IntegerField()
    end_time = models.IntegerField()

    # Quality
    confidence = models.FloatField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['start_time']
        verbose_name = 'Transcript Segment'
        verbose_name_plural = 'Transcript Segments'

    def __str__(self):
        speaker_name = self.speaker.identified_name if self.speaker and self.speaker.identified_name else "Unknown"
        return f"{speaker_name}: {self.text[:50]}..."

    def get_time_display(self):
        """Human-readable timestamp"""
        seconds = self.start_time // 1000
        minutes = seconds // 60
        secs = seconds % 60
        return f"{minutes}:{secs:02d}"