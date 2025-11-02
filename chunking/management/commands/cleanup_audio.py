"""
Django management command: cleanup_audio.py
Place in: chunking/management/commands/cleanup_audio.py

Run daily via Heroku Scheduler:
python manage.py cleanup_audio
"""

from django.core.management.base import BaseCommand
from django.utils import timezone
from django.conf import settings
from datetime import timedelta
from chunking.models import ChunkedConversation
from chunking.s3_handler_hybrid import delete_chunk_files, delete_conversation_audio


class Command(BaseCommand):
    help = 'Clean up old audio files (chunks after 7 days, conversations after 30 days)'

    def handle(self, *args, **options):
        now = timezone.now()

        chunk_retention_days = getattr(settings, 'CHUNK_AUDIO_RETENTION_DAYS', 7)
        conversation_retention_days = getattr(settings, 'CONVERSATION_RETENTION_DAYS', 30)

        print(f"\n🧹 Starting audio cleanup at {now}")
        print(f"   Chunk audio retention: {chunk_retention_days} days")
        print(f"   Conversation retention: {conversation_retention_days} days")

        # === 1. DELETE CHUNK AUDIO FILES (after 7 days, keep transcripts) ===

        chunk_cutoff = now - timedelta(days=chunk_retention_days)

        conversations_with_old_chunks = ChunkedConversation.objects.filter(
            is_final_uploaded=True,
            audio_uploaded_at__lte=chunk_cutoff,
            chunks_folder_path__isnull=False
        ).exclude(chunks_folder_path='')

        print(f"\n📦 Checking chunk audio files...")
        print(f"   Cutoff date: {chunk_cutoff}")
        print(f"   Conversations to check: {conversations_with_old_chunks.count()}")

        chunks_deleted_total = 0

        for conversation in conversations_with_old_chunks:
            print(f"\n   Conversation {conversation.id}")
            print(f"   Final uploaded: {conversation.audio_uploaded_at}")

            if conversation.chunks_folder_path:
                deleted_count = delete_chunk_files(conversation.chunks_folder_path)
                chunks_deleted_total += deleted_count

                # Clear chunks_folder_path after deletion
                if deleted_count > 0:
                    conversation.chunks_folder_path = ''
                    conversation.save(update_fields=['chunks_folder_path'])
                    print(f"   ✅ Deleted {deleted_count} chunk files")

        print(f"\n✅ Chunk audio cleanup complete")
        print(f"   Total chunk files deleted: {chunks_deleted_total}")

        # === 2. DELETE ENTIRE CONVERSATIONS (after 30 days) ===

        deletion_cutoff = now

        expired_conversations = ChunkedConversation.objects.filter(
            scheduled_deletion_date__lte=deletion_cutoff,
            save_permanently=False
        )

        print(f"\n🗑️  Checking expired conversations...")
        print(f"   Cutoff date: {deletion_cutoff}")
        print(f"   Expired conversations: {expired_conversations.count()}")

        conversations_deleted = 0
        audio_files_deleted = 0

        for conversation in expired_conversations:
            print(f"\n   Conversation {conversation.id}")
            print(f"   Scheduled deletion: {conversation.scheduled_deletion_date}")

            # Delete all audio
            result = delete_conversation_audio(conversation)

            if result['chunks_deleted'] > 0:
                audio_files_deleted += result['chunks_deleted']
                print(f"   Deleted {result['chunks_deleted']} chunk files")

            if result['final_deleted']:
                audio_files_deleted += 1
                print(f"   Deleted final audio file")

            # Delete database records (cascade handles related objects)
            conversation.delete()
            conversations_deleted += 1
            print(f"   ✅ Conversation deleted")

        print(f"\n✅ Conversation cleanup complete")
        print(f"   Conversations deleted: {conversations_deleted}")
        print(f"   Audio files deleted: {audio_files_deleted}")

        # === SUMMARY ===

        print(f"\n" + "=" * 60)
        print(f"📊 CLEANUP SUMMARY")
        print(f"=" * 60)
        print(f"Chunk audio files deleted: {chunks_deleted_total}")
        print(f"Conversations deleted: {conversations_deleted}")
        print(f"Total audio files deleted: {chunks_deleted_total + audio_files_deleted}")
        print(f"=" * 60)
        print(f"\n✅ Audio cleanup complete at {timezone.now()}\n")