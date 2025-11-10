"""
Django management command to delete expired conversations and old dispatch jobs.

Run daily via Heroku Scheduler:
    python manage.py cleanup_expired
"""

from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta
from django.conf import settings
from chunking.models import ChunkedConversation
from chunking.s3_handler import delete_conversation_audio, get_s3_client
from history.models import DispatchJob


class Command(BaseCommand):
    help = 'Delete conversations that have passed their retention period and old dispatch jobs'

    def handle(self, *args, **options):
        self.stdout.write("🗑️  Starting cleanup...")

        # ===== CLEANUP EXPIRED CONVERSATIONS =====
        self.stdout.write("\n📝 Cleaning up expired conversations...")

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

        self.stdout.write(self.style.SUCCESS(f"✅ Conversations cleanup complete: {deleted_count} deleted"))

        # ===== CLEANUP OLD DISPATCH JOBS =====
        self.stdout.write("\n📋 Cleaning up old dispatch jobs...")

        # Find DispatchJobs older than 7 days
        cutoff_date = timezone.now() - timedelta(days=7)
        old_jobs = DispatchJob.objects.filter(last_updated__lte=cutoff_date)

        jobs_count = old_jobs.count()
        self.stdout.write(f"Found {jobs_count} old dispatch job(s)")

        s3_client = get_s3_client()
        jobs_deleted = 0
        s3_deleted = 0
        s3_errors = 0

        for job in old_jobs:
            self.stdout.write(f"  Deleting job {job.job_id} (appt {job.appointment_id})...")

            # Delete S3 document if it exists
            if job.ai_document_s3_key:
                try:
                    s3_client.delete_object(
                        Bucket=settings.AWS_STORAGE_BUCKET_NAME,
                        Key=job.ai_document_s3_key
                    )
                    s3_deleted += 1
                    self.stdout.write(f"    ✅ Deleted S3 document: {job.ai_document_s3_key}")
                except Exception as e:
                    s3_errors += 1
                    self.stdout.write(f"    ⚠️  S3 deletion error: {e}")

            # Delete database record
            job.delete()
            jobs_deleted += 1

        self.stdout.write(self.style.SUCCESS(
            f"✅ Dispatch jobs cleanup complete: {jobs_deleted} jobs deleted, "
            f"{s3_deleted} S3 documents deleted, {s3_errors} errors"
        ))

        self.stdout.write(self.style.SUCCESS("\n✨ All cleanup tasks complete"))