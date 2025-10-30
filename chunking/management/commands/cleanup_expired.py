"""
Django management command to delete expired conversations.

Run daily via Heroku Scheduler:
    python manage.py cleanup_expired
"""

from django.core.management.base import BaseCommand
from django.utils import timezone
from chunking.models import ChunkedConversation
from chunking.s3_handler import delete_conversation_audio


class Command(BaseCommand):
    help = 'Delete conversations that have passed their retention period'

    def handle(self, *args, **options):
        self.stdout.write("🗑️  Starting cleanup of expired conversations...")

        # Find expired conversations
        expired = ChunkedConversation.objects.filter(
            scheduled_deletion_date__lte=timezone.now(),
            save_permanently=False
        )

        count = expired.count()
        self.stdout.write(f"Found {count} expired conversation(s)")

        deleted_count = 0
        for conversation in expired:
            self.stdout.write(f"  Deleting {conversation.id}...")

            # Delete audio from S3
            result = delete_conversation_audio(conversation)

            # Delete database record
            conversation.delete()

            deleted_count += 1
            self.stdout.write(f"    ✅ Deleted (chunks: {result['chunks_deleted']}, final: {result['final_deleted']})")

        self.stdout.write(self.style.SUCCESS(f"✅ Cleanup complete: {deleted_count} conversation(s) deleted"))