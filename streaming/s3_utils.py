import boto3
import re
from botocore.exceptions import ClientError
from django.conf import settings
from django.utils import timezone
from datetime import timedelta
import io


def sanitize_username_for_s3(username):
    """
    Sanitize username to be S3-safe.
    - Convert to lowercase
    - Replace spaces with underscores
    - Keep only alphanumeric, hyphens, and underscores
    - Remove any other special characters
    """
    # Convert to lowercase
    safe_name = username.lower()

    # Replace spaces with underscores
    safe_name = safe_name.replace(' ', '_')

    # Keep only alphanumeric, hyphens, and underscores
    safe_name = re.sub(r'[^a-z0-9_-]', '', safe_name)

    # Ensure it's not empty (fallback to 'user' if sanitization removes everything)
    if not safe_name:
        safe_name = 'user'

    # Limit length to 50 characters (reasonable for folder names)
    safe_name = safe_name[:50]

    return safe_name


def get_s3_client():
    """Get configured S3 client"""
    return boto3.client(
        's3',
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
        region_name=settings.AWS_S3_REGION_NAME
    )


def upload_audio_to_s3(conversation_id, audio_data, username):
    """
    Upload audio data to S3 with user-based folder structure
    Returns the S3 URL
    """
    try:
        s3_client = get_s3_client()

        # Sanitize username for S3 key
        safe_username = sanitize_username_for_s3(username)

        # Create S3 key with username folder for easier troubleshooting
        # Format: users/{username}/conversations/{conversation_id}/audio.wav
        s3_key = f"users/{safe_username}/conversations/{conversation_id}/audio.wav"

        print(f"📁 S3 path: {s3_key}")

        # Upload to S3
        s3_client.put_object(
            Bucket=settings.AWS_STORAGE_BUCKET_NAME,
            Key=s3_key,
            Body=audio_data,
            ContentType='audio/wav'
        )

        # Generate the S3 URL
        s3_url = f"https://{settings.AWS_STORAGE_BUCKET_NAME}.s3.{settings.AWS_S3_REGION_NAME}.amazonaws.com/{s3_key}"

        print(f"✅ Uploaded audio to S3: {s3_url}")
        return s3_url

    except ClientError as e:
        print(f"❌ Error uploading to S3: {e}")
        return None


def get_audio_from_s3(s3_url):
    """
    Download audio from S3
    Returns audio bytes
    """
    try:
        s3_client = get_s3_client()

        # Extract key from URL
        # URL format: https://bucket.s3.region.amazonaws.com/key
        key = s3_url.split('.amazonaws.com/')[-1]

        # Download from S3
        response = s3_client.get_object(
            Bucket=settings.AWS_STORAGE_BUCKET_NAME,
            Key=key
        )

        audio_data = response['Body'].read()
        print(f"✅ Downloaded audio from S3: {len(audio_data)} bytes")
        return audio_data

    except ClientError as e:
        print(f"❌ Error downloading from S3: {e}")
        return None


def delete_audio_from_s3(s3_url):
    """
    Delete audio from S3
    """
    try:
        s3_client = get_s3_client()

        # Extract key from URL
        key = s3_url.split('.amazonaws.com/')[-1]

        # Delete from S3
        s3_client.delete_object(
            Bucket=settings.AWS_STORAGE_BUCKET_NAME,
            Key=key
        )

        print(f"✅ Deleted audio from S3: {s3_url}")
        return True

    except ClientError as e:
        print(f"❌ Error deleting from S3: {e}")
        return False


def schedule_audio_deletion(conversation):
    """
    Set the deletion date for conversation audio based on retention policy
    """
    if not conversation.audio_url:
        return

    delete_at = timezone.now() + timedelta(days=int(settings.AUDIO_RETENTION_DAYS))
    conversation.audio_delete_at = delete_at
    conversation.save()

    print(f"📅 Audio scheduled for deletion at {delete_at}")


def cleanup_expired_audio():
    """
    Delete audio files that have passed their retention period
    This should be called by a scheduled task (cron job, Celery, etc.)
    """
    from .models import Conversation

    expired_conversations = Conversation.objects.filter(
        audio_delete_at__lte=timezone.now(),
        audio_url__isnull=False
    ).exclude(audio_url='')

    deleted_count = 0
    for conversation in expired_conversations:
        if delete_audio_from_s3(conversation.audio_url):
            conversation.audio_url = ''
            conversation.save()
            deleted_count += 1

    print(f"🗑️ Cleaned up {deleted_count} expired audio files")
    return deleted_count