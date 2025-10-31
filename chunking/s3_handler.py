"""
S3 handler for hybrid chunked audio system.

HYBRID APPROACH:
- Phase 1: Upload individual FLAC chunks to S3 for preliminary transcription
- Phase 2: iOS uploads complete preprocessed FLAC directly to S3 via presigned URL
- Cleanup: Delete chunks after final transcription succeeds (optional)
"""

import boto3
from botocore.exceptions import ClientError
from django.conf import settings
from django.utils import timezone
import re


def sanitize_username_for_s3(username):
    """
    Sanitize username to be S3-safe.
    Reuses logic from main s3_utils.py
    """
    safe_name = username.lower()
    safe_name = safe_name.replace(' ', '_')
    safe_name = re.sub(r'[^a-z0-9_-]', '', safe_name)
    if not safe_name:
        safe_name = 'user'
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


# === PHASE 1: CHUNK UPLOAD ===

def upload_chunk_to_s3(conversation_id, chunk_number, chunk_data, username):
    """
    Upload a single audio chunk to S3
    Returns tuple: (s3_url, chunks_folder_path)
    """
    s3_client = get_s3_client()

    safe_username = sanitize_username_for_s3(username)
    s3_key = f"chunks/{safe_username}/{conversation_id}/chunk_{chunk_number}.flac"
    chunks_folder = f"chunks/{safe_username}/{conversation_id}"

    print(f"📦 Uploading chunk {chunk_number} to S3: {s3_key}")
    print(f"Size: {len(chunk_data):,} bytes")

    try:
        # Upload to S3 (private by default, no ACL needed - matches s3_utils.py pattern)
        s3_client.put_object(
            Bucket=settings.AWS_STORAGE_BUCKET_NAME,
            Key=s3_key,
            Body=chunk_data,
            ContentType='audio/flac'
        )

        s3_url = f"https://{settings.AWS_STORAGE_BUCKET_NAME}.s3.{settings.AWS_S3_REGION_NAME}.amazonaws.com/{s3_key}"

        print(f"✅ Chunk {chunk_number} uploaded successfully")
        return s3_url, chunks_folder

    except Exception as e:
        print(f"❌ S3 upload failed: {str(e)}")
        raise


# === PHASE 2: COMPLETE FILE (Direct iOS Upload) ===

def generate_presigned_upload_url(conversation_id, username, expiration=None):
    """
    Generate a presigned URL for iOS to upload the complete FLAC file directly to S3.
    This bypasses Django entirely for the final upload.

    Args:
        conversation_id: Conversation UUID
        username: Username for folder structure
        expiration: URL expiration in seconds (default from settings)

    Returns:
        dict: {
            'upload_url': presigned PUT URL,
            's3_url': final S3 URL after upload,
            'expires_in': expiration seconds
        }
    """
    try:
        s3_client = get_s3_client()
        safe_username = sanitize_username_for_s3(username)

        if expiration is None:
            expiration = settings.PRESIGNED_URL_EXPIRATION

        # S3 key for final complete file
        s3_key = f"final/{safe_username}/{conversation_id}/complete.flac"

        print(f"🔐 Generating presigned upload URL for: {s3_key}")
        print(f"   Expires in: {expiration} seconds")

        # Generate presigned PUT URL
        presigned_url = s3_client.generate_presigned_url(
            'put_object',
            Params={
                'Bucket': settings.AWS_STORAGE_BUCKET_NAME,
                'Key': s3_key,
                'ContentType': 'audio/flac'
            },
            ExpiresIn=expiration,
            HttpMethod='PUT'
        )

        # Final S3 URL (what it will be after upload)
        s3_url = f"https://{settings.AWS_STORAGE_BUCKET_NAME}.s3.{settings.AWS_S3_REGION_NAME}.amazonaws.com/{s3_key}"

        print(f"✅ Presigned URL generated successfully")

        return {
            'upload_url': presigned_url,
            's3_url': s3_url,
            'expires_in': expiration
        }

    except ClientError as e:
        print(f"❌ Error generating presigned URL: {e}")
        import traceback
        traceback.print_exc()
        return None


def generate_presigned_download_url(s3_url, expiration=3600):
    """
    Generate a presigned URL for temporary download access to an S3 file.
    Used for providing temporary access to audio files.

    Args:
        s3_url: Full S3 URL
        expiration: URL expiration in seconds (default 1 hour)

    Returns:
        str: Presigned download URL or None on error
    """
    try:
        s3_client = get_s3_client()

        # Extract key from URL
        key = s3_url.split('.amazonaws.com/')[-1]

        presigned_url = s3_client.generate_presigned_url(
            'get_object',
            Params={
                'Bucket': settings.AWS_STORAGE_BUCKET_NAME,
                'Key': key
            },
            ExpiresIn=expiration
        )

        print(f"🔗 Generated download URL (expires in {expiration}s)")
        return presigned_url

    except ClientError as e:
        print(f"❌ Error generating download URL: {e}")
        return None


# === CLEANUP & DELETION ===

def delete_chunk_files(chunks_folder_path):
    """
    Delete all chunk files in a conversation's chunks folder.
    Called after successful final transcription.

    Args:
        chunks_folder_path: S3 folder path (e.g., "chunks/username/conv_id")

    Returns:
        int: Number of files deleted
    """
    try:
        s3_client = get_s3_client()

        print(f"🗑️  Deleting chunk files from: {chunks_folder_path}")

        # List all objects in the folder
        response = s3_client.list_objects_v2(
            Bucket=settings.AWS_STORAGE_BUCKET_NAME,
            Prefix=chunks_folder_path
        )

        if 'Contents' not in response:
            print(f"   No files found in folder")
            return 0

        # Delete all objects
        objects_to_delete = [{'Key': obj['Key']} for obj in response['Contents']]

        if objects_to_delete:
            s3_client.delete_objects(
                Bucket=settings.AWS_STORAGE_BUCKET_NAME,
                Delete={'Objects': objects_to_delete}
            )

            print(f"✅ Deleted {len(objects_to_delete)} chunk file(s)")
            return len(objects_to_delete)

        return 0

    except ClientError as e:
        print(f"❌ Error deleting chunk files: {e}")
        import traceback
        traceback.print_exc()
        return 0


def delete_final_file(final_audio_url):
    """
    Delete the complete final audio file from S3.

    Args:
        final_audio_url: Full S3 URL of final file

    Returns:
        bool: True if deleted successfully
    """
    try:
        s3_client = get_s3_client()

        # Extract key from URL
        key = final_audio_url.split('.amazonaws.com/')[-1]

        print(f"🗑️  Deleting final file: {key}")

        s3_client.delete_object(
            Bucket=settings.AWS_STORAGE_BUCKET_NAME,
            Key=key
        )

        print(f"✅ Final file deleted successfully")
        return True

    except ClientError as e:
        print(f"❌ Error deleting final file: {e}")
        import traceback
        traceback.print_exc()
        return False


def delete_conversation_audio(conversation):
    """
    Delete all audio files associated with a conversation.
    Includes both chunks and final file.

    Args:
        conversation: ChunkedConversation instance

    Returns:
        dict: {
            'chunks_deleted': int,
            'final_deleted': bool,
            'success': bool
        }
    """
    results = {
        'chunks_deleted': 0,
        'final_deleted': False,
        'success': False
    }

    print(f"🗑️  Deleting all audio for conversation {conversation.id}")

    # Delete chunk files
    if conversation.chunks_folder_path:
        results['chunks_deleted'] = delete_chunk_files(conversation.chunks_folder_path)

    # Delete final file
    if conversation.final_audio_url:
        results['final_deleted'] = delete_final_file(conversation.final_audio_url)

    results['success'] = True
    print(f"✅ Audio deletion complete for conversation {conversation.id}")

    return results


def verify_file_exists(s3_url):
    """
    Verify that a file exists in S3.
    Useful for confirming iOS uploaded the final file.

    Args:
        s3_url: Full S3 URL

    Returns:
        bool: True if file exists
    """
    try:
        s3_client = get_s3_client()

        # Extract key from URL
        key = s3_url.split('.amazonaws.com/')[-1]

        # Try to get object metadata (doesn't download the file)
        s3_client.head_object(
            Bucket=settings.AWS_STORAGE_BUCKET_NAME,
            Key=key
        )

        return True

    except ClientError as e:
        if e.response['Error']['Code'] == '404':
            return False
        else:
            print(f"❌ Error verifying file: {e}")
            return False


def get_file_size(s3_url):
    """
    Get the size of an S3 file without downloading it.

    Args:
        s3_url: Full S3 URL

    Returns:
        int: File size in bytes, or None on error
    """
    try:
        s3_client = get_s3_client()

        # Extract key from URL
        key = s3_url.split('.amazonaws.com/')[-1]

        response = s3_client.head_object(
            Bucket=settings.AWS_STORAGE_BUCKET_NAME,
            Key=key
        )

        return response['ContentLength']

    except ClientError as e:
        print(f"❌ Error getting file size: {e}")
        return None