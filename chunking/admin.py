from django.contrib import admin

# Register your models here.
"""
Django admin interface for chunking app.
"""

from django.contrib import admin
from django.utils.html import format_html
from .models import ChunkedConversation, AudioChunk, Speaker, TranscriptSegment
from .s3_handler_hybrid import delete_conversation_audio

class AudioChunkInline(admin.TabularInline):
    model = AudioChunk
    extra = 0
    readonly_fields = ('chunk_number', 'start_time_seconds', 'duration_seconds', 'transcribed_at', 'transcript_source')
    fields = ('chunk_number', 'start_time_seconds', 'duration_seconds', 'transcript_source', 'transcribed_at')
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


class SpeakerInline(admin.TabularInline):
    model = Speaker
    extra = 0
    readonly_fields = ('speaker_label', 'identified_name', 'is_recording_user')
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(ChunkedConversation)
class ChunkedConversationAdmin(admin.ModelAdmin):
    list_display = (
        'id', 'recorded_by', 'title', 'started_at', 'duration_display',
        'chunk_count', 'status_display', 'save_permanently', 'is_shared', 'prompt_used'
    )
    list_filter = ('is_chunks_complete', 'is_final_uploaded', 'is_analyzed', 'save_permanently')
    search_fields = ('id', 'title', 'recorded_by__username', 'full_transcript', 'preliminary_transcript')
    readonly_fields = (
        'id', 'recorded_by', 'started_at', 'ended_at', 'created_at', 'updated_at',
        'chunks_folder_path', 'final_audio_url', 'received_chunks', 'chunk_count',
        'is_chunks_complete', 'is_final_uploaded', 'is_analyzed', 'audio_uploaded_at',
        'transcription_error', 'analysis_error'
    )
    fieldsets = (
        ('Basic Info', {
            'fields': ('id', 'recorded_by', 'title', 'notes', 'is_shared')
        }),
        ('Timing', {
            'fields': ('started_at', 'ended_at', 'total_duration_seconds')
        }),
        ('Chunk System', {
            'fields': ('chunks_folder_path', 'chunk_count', 'received_chunks', 'is_chunks_complete')
        }),
        ('Final File', {
            'fields': ('final_audio_url', 'audio_uploaded_at', 'is_final_uploaded')
        }),
        ('Transcription', {
            'fields': ('is_analyzed', 'preliminary_transcript', 'full_transcript', 'formatted_transcript',
                       'transcription_error')
        }),
        ('AI Analysis', {
            'fields': ('prompt_used', 'summary', 'action_items', 'key_topics', 'sentiment', 'coaching_feedback',
                       'analysis_error')
        }),
        ('Deletion Policy', {
            'fields': ('save_permanently', 'scheduled_deletion_date')
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        })
    )
    inlines = [AudioChunkInline, SpeakerInline]
    actions = ['mark_save_permanently', 'delete_audio_files']

    def duration_display(self, obj):
        return obj.get_duration_display()

    duration_display.short_description = 'Duration'

    def status_display(self, obj):
        statuses = []
        if obj.is_chunks_complete:
            statuses.append('✅ Chunks')
        if obj.is_final_uploaded:
            statuses.append('✅ Final')
        if obj.is_analyzed:
            statuses.append('✅ Analyzed')
        return ' | '.join(statuses) if statuses else '⏳ In Progress'

    status_display.short_description = 'Status'

    def mark_save_permanently(self, request, queryset):
        count = 0
        for conversation in queryset:
            conversation.mark_permanent()
            count += 1
        self.message_user(request, f"{count} conversation(s) marked as permanent")

    mark_save_permanently.short_description = "Mark selected as permanent (no auto-delete)"

    def delete_audio_files(self, request, queryset):

        total_chunks = 0
        total_finals = 0
        for conversation in queryset:
            result = delete_conversation_audio(conversation)
            total_chunks += result['chunks_deleted']
            total_finals += 1 if result['final_deleted'] else 0
        self.message_user(request, f"Deleted {total_chunks} chunks and {total_finals} final files")

    delete_audio_files.short_description = "Delete audio files from S3"


@admin.register(AudioChunk)
class AudioChunkAdmin(admin.ModelAdmin):
    list_display = (
    'conversation', 'chunk_number', 'time_display', 'duration_seconds', 'transcript_source', 'transcribed_at')
    list_filter = ('transcript_source', 'transcribed_at')
    search_fields = ('conversation__id', 'transcript_text')
    readonly_fields = (
    'conversation', 'chunk_number', 'start_time_seconds', 'duration_seconds', 's3_chunk_url', 'received_at')

    def time_display(self, obj):
        return obj.get_time_display()

    time_display.short_description = 'Start Time'


@admin.register(Speaker)
class SpeakerAdmin(admin.ModelAdmin):
    list_display = ('conversation', 'speaker_label', 'identified_name', 'is_recording_user', 'name_confirmed')
    list_filter = ('is_recording_user', 'name_confirmed')
    search_fields = ('conversation__id', 'speaker_label', 'identified_name')


@admin.register(TranscriptSegment)
class TranscriptSegmentAdmin(admin.ModelAdmin):
    list_display = ('conversation', 'speaker_display', 'time_display', 'text_preview')
    search_fields = ('conversation__id', 'text')
    readonly_fields = ('conversation', 'speaker', 'start_time', 'end_time')

    def speaker_display(self, obj):
        if obj.speaker:
            return obj.speaker.identified_name or obj.speaker.speaker_label
        return 'Unknown'

    speaker_display.short_description = 'Speaker'

    def time_display(self, obj):
        return obj.get_time_display()

    time_display.short_description = 'Time'

    def text_preview(self, obj):
        return obj.text[:100] + '...' if len(obj.text) > 100 else obj.text

    text_preview.short_description = 'Text'