"""
Django management command: cleanup_orphaned_s3.py
Place in: chunking/management/commands/cleanup_orphaned_s3.py

Detects and optionally deletes orphaned S3 files that exist in the bucket
but have no corresponding database records.

Run manually or schedule weekly:
python manage.py cleanup_orphaned_s3 --dry-run  # Preview only
python manage.py cleanup_orphaned_s3           # Actually delete
python manage.py cleanup_orphaned_s3 --days-old 7  # Only delete files older than 7 days
"""

from django.core.management.base import BaseCommand
from django.conf import settings
from django.utils import timezone
from datetime import timedelta
from chunking.models import ChunkedConversation
from history.models import DispatchJob
import boto3
from botocore.exceptions import ClientError


class Command(BaseCommand):
    help = 'Detect and clean up orphaned S3 files that have no database records'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Preview what would be deleted without actually deleting',
        )
        parser.add_argument(
            '--days-old',
            type=int,
            default=2,
            help='Only consider files older than this many days (default: 2)',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        days_old = options['days_old']
        now = timezone.now()
        cutoff_date = now - timedelta(days=days_old)

        mode = "DRY RUN - No files will be deleted" if dry_run else "LIVE MODE - Files will be deleted"

        print(f"\n🔍 Starting orphaned S3 file detection")
        print(f"   Mode: {mode}")
        print(f"   Only checking files older than {days_old} days (before {cutoff_date})")
        print("=" * 60)

        # Initialize S3 client
        s3_client = boto3.client(
            's3',
            aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
            aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
            region_name=settings.AWS_S3_REGION_NAME
        )

        bucket_name = settings.AWS_STORAGE_BUCKET_NAME

        # Track statistics
        stats = {
            'chunks_orphaned': 0,
            'chunks_deleted': 0,
            'conversations_orphaned': 0,
            'conversations_deleted': 0,
            'ai_docs_orphaned': 0,
            'ai_docs_deleted': 0,
            'multipart_uploads': 0,
            'multipart_aborted': 0,
        }

        # === 1. CHECK FOR INCOMPLETE MULTIPART UPLOADS ===
        print("\n📦 Checking incomplete multipart uploads...")

        try:
            multipart_response = s3_client.list_multipart_uploads(
                Bucket=bucket_name
            )

            multipart_uploads = multipart_response.get('Uploads', [])
            stats['multipart_uploads'] = len(multipart_uploads)

            if multipart_uploads:
                print(f"   Found {len(multipart_uploads)} incomplete multipart uploads")

                for upload in multipart_uploads:
                    initiated = upload['Initiated']
                    age = now - initiated

                    if initiated < cutoff_date:
                        print(f"\n   ⚠️  Incomplete upload: {upload['Key']}")
                        print(f"      Upload ID: {upload['UploadId']}")
                        print(f"      Initiated: {initiated} ({age.days} days ago)")

                        if not dry_run:
                            try:
                                s3_client.abort_multipart_upload(
                                    Bucket=bucket_name,
                                    Key=upload['Key'],
                                    UploadId=upload['UploadId']
                                )
                                stats['multipart_aborted'] += 1
                                print(f"      ✅ Aborted multipart upload")
                            except ClientError as e:
                                print(f"      ❌ Error aborting: {e}")
                        else:
                            print(f"      [DRY RUN] Would abort this upload")
            else:
                print("   ✅ No incomplete multipart uploads found")

        except ClientError as e:
            print(f"   ❌ Error listing multipart uploads: {e}")

        # === 2. CHECK ORPHANED CHUNK FILES ===
        print("\n📦 Checking orphaned chunk files (chunks/ prefix)...")

        # Get all valid chunk folder paths from database
        valid_chunk_folders = set(
            ChunkedConversation.objects
            .exclude(chunks_folder_path='')
            .exclude(chunks_folder_path__isnull=True)
            .values_list('chunks_folder_path', flat=True)
        )

        print(f"   Valid chunk folders in database: {len(valid_chunk_folders)}")

        # List all chunk files in S3
        try:
            paginator = s3_client.get_paginator('list_objects_v2')
            pages = paginator.paginate(Bucket=bucket_name, Prefix='chunks/')

            for page in pages:
                for obj in page.get('Contents', []):
                    key = obj['Key']
                    last_modified = obj['LastModified']

                    # Only check old files
                    if last_modified >= cutoff_date:
                        continue

                    # Extract folder path (e.g., "chunks/username/conv_id/")
                    parts = key.split('/')
                    if len(parts) >= 4:
                        folder_path = '/'.join(parts[:3]) + '/'

                        # Check if this folder exists in database
                        if folder_path not in valid_chunk_folders:
                            stats['chunks_orphaned'] += 1
                            age = now - last_modified

                            if stats['chunks_orphaned'] <= 10:  # Only print first 10
                                print(f"\n   ⚠️  Orphaned chunk: {key}")
                                print(f"      Last modified: {last_modified} ({age.days} days ago)")
                                print(f"      Size: {obj['Size']} bytes")

                            if not dry_run:
                                try:
                                    s3_client.delete_object(Bucket=bucket_name, Key=key)
                                    stats['chunks_deleted'] += 1

                                    if stats['chunks_deleted'] <= 10:
                                        print(f"      ✅ Deleted")
                                except ClientError as e:
                                    print(f"      ❌ Error deleting: {e}")
                            elif stats['chunks_orphaned'] <= 10:
                                print(f"      [DRY RUN] Would delete this file")

            if stats['chunks_orphaned'] > 10:
                print(f"\n   ... and {stats['chunks_orphaned'] - 10} more orphaned chunks")

            if stats['chunks_orphaned'] == 0:
                print("   ✅ No orphaned chunk files found")

        except ClientError as e:
            print(f"   ❌ Error listing chunk files: {e}")

        # === 3. CHECK ORPHANED CONVERSATION FILES ===
        print("\n📦 Checking orphaned conversation files (conversations/ prefix)...")

        # Get all valid final audio URLs from database
        valid_final_urls = set(
            ChunkedConversation.objects
            .exclude(final_audio_url='')
            .exclude(final_audio_url__isnull=True)
            .values_list('final_audio_url', flat=True)
        )

        # Extract S3 keys from URLs
        valid_final_keys = set()
        for url in valid_final_urls:
            # URL format: https://bucket.s3.region.amazonaws.com/conversations/user/id/complete.flac
            if '/conversations/' in url:
                key = url.split(bucket_name + '.s3.')[1].split('amazonaws.com/')[1] if '.amazonaws.com/' in url else url.split('/')[-4:]
                if isinstance(key, str):
                    valid_final_keys.add(key)
                else:
                    valid_final_keys.add('/'.join(key))

        print(f"   Valid conversation files in database: {len(valid_final_keys)}")

        try:
            paginator = s3_client.get_paginator('list_objects_v2')
            pages = paginator.paginate(Bucket=bucket_name, Prefix='conversations/')

            for page in pages:
                for obj in page.get('Contents', []):
                    key = obj['Key']
                    last_modified = obj['LastModified']

                    # Only check old files
                    if last_modified >= cutoff_date:
                        continue

                    # Check if this file exists in database
                    if key not in valid_final_keys:
                        stats['conversations_orphaned'] += 1
                        age = now - last_modified

                        if stats['conversations_orphaned'] <= 10:
                            print(f"\n   ⚠️  Orphaned conversation: {key}")
                            print(f"      Last modified: {last_modified} ({age.days} days ago)")
                            print(f"      Size: {obj['Size']} bytes")

                        if not dry_run:
                            try:
                                s3_client.delete_object(Bucket=bucket_name, Key=key)
                                stats['conversations_deleted'] += 1

                                if stats['conversations_deleted'] <= 10:
                                    print(f"      ✅ Deleted")
                            except ClientError as e:
                                print(f"      ❌ Error deleting: {e}")
                        elif stats['conversations_orphaned'] <= 10:
                            print(f"      [DRY RUN] Would delete this file")

            if stats['conversations_orphaned'] > 10:
                print(f"\n   ... and {stats['conversations_orphaned'] - 10} more orphaned conversations")

            if stats['conversations_orphaned'] == 0:
                print("   ✅ No orphaned conversation files found")

        except ClientError as e:
            print(f"   ❌ Error listing conversation files: {e}")

        # === 4. CHECK ORPHANED AI DOCUMENTS ===
        print("\n📦 Checking orphaned AI document files (ai_documents/ prefix)...")

        # Get all valid AI document keys from database
        valid_ai_doc_keys = set(
            DispatchJob.objects
            .exclude(ai_document_s3_key='')
            .exclude(ai_document_s3_key__isnull=True)
            .values_list('ai_document_s3_key', flat=True)
        )

        print(f"   Valid AI document files in database: {len(valid_ai_doc_keys)}")

        try:
            paginator = s3_client.get_paginator('list_objects_v2')
            pages = paginator.paginate(Bucket=bucket_name, Prefix='ai_documents/')

            for page in pages:
                for obj in page.get('Contents', []):
                    key = obj['Key']
                    last_modified = obj['LastModified']

                    # Only check old files
                    if last_modified >= cutoff_date:
                        continue

                    # Check if this file exists in database
                    if key not in valid_ai_doc_keys:
                        stats['ai_docs_orphaned'] += 1
                        age = now - last_modified

                        if stats['ai_docs_orphaned'] <= 10:
                            print(f"\n   ⚠️  Orphaned AI document: {key}")
                            print(f"      Last modified: {last_modified} ({age.days} days ago)")
                            print(f"      Size: {obj['Size']} bytes")

                        if not dry_run:
                            try:
                                s3_client.delete_object(Bucket=bucket_name, Key=key)
                                stats['ai_docs_deleted'] += 1

                                if stats['ai_docs_deleted'] <= 10:
                                    print(f"      ✅ Deleted")
                            except ClientError as e:
                                print(f"      ❌ Error deleting: {e}")
                        elif stats['ai_docs_orphaned'] <= 10:
                            print(f"      [DRY RUN] Would delete this file")

            if stats['ai_docs_orphaned'] > 10:
                print(f"\n   ... and {stats['ai_docs_orphaned'] - 10} more orphaned AI documents")

            if stats['ai_docs_orphaned'] == 0:
                print("   ✅ No orphaned AI document files found")

        except ClientError as e:
            print(f"   ❌ Error listing AI document files: {e}")

        # === SUMMARY ===
        print("\n" + "=" * 60)
        print(f"📊 ORPHANED FILE CLEANUP SUMMARY")
        print("=" * 60)
        print(f"Mode: {mode}")
        print(f"\nIncomplete Multipart Uploads:")
        print(f"  Found: {stats['multipart_uploads']}")
        print(f"  Aborted: {stats['multipart_aborted']}")
        print(f"\nOrphaned Chunk Files (chunks/):")
        print(f"  Found: {stats['chunks_orphaned']}")
        print(f"  Deleted: {stats['chunks_deleted']}")
        print(f"\nOrphaned Conversation Files (conversations/):")
        print(f"  Found: {stats['conversations_orphaned']}")
        print(f"  Deleted: {stats['conversations_deleted']}")
        print(f"\nOrphaned AI Documents (ai_documents/):")
        print(f"  Found: {stats['ai_docs_orphaned']}")
        print(f"  Deleted: {stats['ai_docs_deleted']}")
        print(f"\nTotal Orphaned Files:")
        print(f"  Found: {stats['chunks_orphaned'] + stats['conversations_orphaned'] + stats['ai_docs_orphaned']}")
        print(f"  Deleted: {stats['chunks_deleted'] + stats['conversations_deleted'] + stats['ai_docs_deleted']}")
        print("=" * 60)

        if dry_run:
            print(f"\n💡 This was a dry run. Run without --dry-run to actually delete files.")

        print(f"\n✅ Orphaned file cleanup complete at {timezone.now()}\n")
