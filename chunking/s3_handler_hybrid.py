"""
Hybrid S3 handler: Individual chunks + Multipart upload via UploadPartCopy.
Memory-efficient: streams to S3, uses server-side copy for multipart.
"""

import boto3
from botocore.exceptions import ClientError
from django.conf import settings
from django.utils import timezone
import re


def sanitize_username_for_s3(username):
    """Sanitize username to be S3-safe."""
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


def start_multipart_upload(conversation_id, username):
    """
    Start multipart upload for complete conversation file.
    Called when first chunk arrives.
    """
    try:
        s3_client = get_s3_client()
        safe_username = sanitize_username_for_s3(username)

        s3_key = f"conversations/{safe_username}/{conversation_id}/complete.flac"

        print(f"🚀 Starting multipart upload")
        print(f"   S3 key: {s3_key}")

        response = s3_client.create_multipart_upload(
            Bucket=settings.AWS_STORAGE_BUCKET_NAME,
            Key=s3_key,
            ContentType='audio/flac'
        )

        upload_id = response['UploadId']
        s3_url = f"https://{settings.AWS_STORAGE_BUCKET_NAME}.s3.{settings.AWS_S3_REGION_NAME}.amazonaws.com/{s3_key}"

        print(f"✅ Multipart upload started: {upload_id}")

        return {
            'upload_id': upload_id,
            's3_key': s3_key,
            's3_url': s3_url,
            'success': True
        }

    except ClientError as e:
        print(f"❌ Error starting multipart upload: {e}")
        import traceback
        traceback.print_exc()
        return {'success': False, 'error': str(e)}


def upload_chunk_hybrid(conversation_id, chunk_number, chunk_data, username,
                        multipart_upload_id=None, multipart_s3_key=None):
    """
    Hybrid upload: Individual chunk + add to multipart via UploadPartCopy.

    Returns: {
        'chunk_s3_url': str,
        'chunks_folder': str,
        'part_number': int,
        'part_etag': str,
        'success': bool
    }
    """
    try:
        s3_client = get_s3_client()
        safe_username = sanitize_username_for_s3(username)

        # Individual chunk S3 path
        chunk_s3_key = f"chunks/{safe_username}/{conversation_id}/chunk_{chunk_number}.flac"
        chunks_folder = f"chunks/{safe_username}/{conversation_id}"

        print(f"📦 Uploading chunk {chunk_number}")
        print(f"   Individual: {chunk_s3_key}")
        print(f"   Size: {len(chunk_data):,} bytes")

        # 1. Upload individual chunk (for transcription)
        s3_client.put_object(
            Bucket=settings.AWS_STORAGE_BUCKET_NAME,
            Key=chunk_s3_key,
            Body=chunk_data,
            ContentType='audio/flac'
        )

        chunk_s3_url = f"https://{settings.AWS_STORAGE_BUCKET_NAME}.s3.{settings.AWS_S3_REGION_NAME}.amazonaws.com/{chunk_s3_key}"
        print(f"✅ Individual chunk uploaded")

        # 2. Add to multipart via UploadPartCopy (server-side, zero memory!)
        part_number = chunk_number + 1  # S3 parts are 1-based
        part_etag = None

        if multipart_upload_id and multipart_s3_key:
            print(f"   Adding to multipart as part {part_number}")

            copy_source = {
                'Bucket': settings.AWS_STORAGE_BUCKET_NAME,
                'Key': chunk_s3_key
            }

            copy_response = s3_client.upload_part_copy(
                Bucket=settings.AWS_STORAGE_BUCKET_NAME,
                Key=multipart_s3_key,
                PartNumber=part_number,
                UploadId=multipart_upload_id,
                CopySource=copy_source
            )

            part_etag = copy_response['CopyPartResult']['ETag']
            print(f"✅ Added to multipart: part {part_number}, ETag {part_etag}")

        return {
            'chunk_s3_url': chunk_s3_url,
            'chunks_folder': chunks_folder,
            'part_number': part_number,
            'part_etag': part_etag,
            'success': True
        }

    except ClientError as e:
        print(f"❌ Error uploading chunk: {e}")
        import traceback
        traceback.print_exc()
        return {'success': False, 'error': str(e)}


def complete_multipart_upload(upload_id, s3_key, parts):
    """
    Complete multipart upload, creating final file.
    Parts: [{'part_number': int, 'etag': str}, ...]
    """
    try:
        s3_client = get_s3_client()

        print(f"🏁 Completing multipart upload")
        print(f"   Upload ID: {upload_id}")
        print(f"   Total parts: {len(parts)}")

        parts_sorted = sorted(parts, key=lambda x: x['part_number'])

        multipart_upload = {
            'Parts': [
                {'PartNumber': p['part_number'], 'ETag': p['etag']}
                for p in parts_sorted
            ]
        }

        response = s3_client.complete_multipart_upload(
            Bucket=settings.AWS_STORAGE_BUCKET_NAME,
            Key=s3_key,
            UploadId=upload_id,
            MultipartUpload=multipart_upload
        )

        s3_url = f"https://{settings.AWS_STORAGE_BUCKET_NAME}.s3.{settings.AWS_S3_REGION_NAME}.amazonaws.com/{s3_key}"

        print(f"✅ Multipart upload complete!")
        print(f"   Final URL: {s3_url}")

        return {'s3_url': s3_url, 'success': True}

    except ClientError as e:
        print(f"❌ Error completing multipart: {e}")
        import traceback
        traceback.print_exc()
        return {'success': False, 'error': str(e)}


def abort_multipart_upload(upload_id, s3_key):
    """Abort multipart upload and clean up parts."""
    try:
        s3_client = get_s3_client()

        print(f"🚫 Aborting multipart upload: {upload_id}")

        s3_client.abort_multipart_upload(
            Bucket=settings.AWS_STORAGE_BUCKET_NAME,
            Key=s3_key,
            UploadId=upload_id
        )

        print(f"✅ Multipart upload aborted")
        return True

    except ClientError as e:
        print(f"❌ Error aborting multipart: {e}")
        return False


def concatenate_and_upload_small_conversation(conversation_id, username, chunk_s3_urls):
    """
    For conversations < 10MB, concatenate chunks and upload as regular file.
    Uses memory temporarily but acceptable for small files.

    Args:
        conversation_id: Conversation UUID
        username: Username for folder structure
        chunk_s3_urls: List of S3 URLs for chunks in order

    Returns:
        dict: {'s3_url': str, 'success': bool}
    """
    try:
        s3_client = get_s3_client()
        safe_username = sanitize_username_for_s3(username)

        print(f"🔗 Concatenating {len(chunk_s3_urls)} chunks (< 10MB)")

        # Download all chunks and concatenate in memory
        concatenated_data = b''

        for idx, chunk_url in enumerate(chunk_s3_urls):
            key = chunk_url.split('.amazonaws.com/')[-1]

            print(f"   Downloading chunk {idx}...")
            response = s3_client.get_object(
                Bucket=settings.AWS_STORAGE_BUCKET_NAME,
                Key=key
            )

            chunk_data = response['Body'].read()
            concatenated_data += chunk_data
            print(f"   ✅ Chunk {idx}: {len(chunk_data):,} bytes")

        print(f"   Total size: {len(concatenated_data):,} bytes")

        # Upload as single file
        s3_key = f"conversations/{safe_username}/{conversation_id}/complete.flac"

        print(f"   Uploading complete file: {s3_key}")

        s3_client.put_object(
            Bucket=settings.AWS_STORAGE_BUCKET_NAME,
            Key=s3_key,
            Body=concatenated_data,
            ContentType='audio/flac'
        )

        s3_url = f"https://{settings.AWS_STORAGE_BUCKET_NAME}.s3.{settings.AWS_S3_REGION_NAME}.amazonaws.com/{s3_key}"

        print(f"✅ Complete file uploaded: {s3_url}")

        return {'s3_url': s3_url, 'success': True}

    except ClientError as e:
        print(f"❌ Error concatenating chunks: {e}")
        import traceback
        traceback.print_exc()
        return {'success': False, 'error': str(e)}


def build_multipart_from_chunks(conversation_id, username, chunk_s3_urls):
    """
    For conversations ≥ 10MB, build multipart upload from existing chunks.
    Uses UploadPartCopy (server-side) to batch chunks into 5MB+ parts.
    Zero server memory usage!

    Args:
        conversation_id: Conversation UUID
        username: Username for folder structure
        chunk_s3_urls: List of S3 URLs for chunks in order

    Returns:
        dict: {'s3_url': str, 'success': bool}
    """
    try:
        s3_client = get_s3_client()
        safe_username = sanitize_username_for_s3(username)

        print(f"🔀 Building multipart from {len(chunk_s3_urls)} chunks (≥ 10MB)")

        # Get chunk sizes to batch into 5MB+ parts
        chunk_info = []
        total_size = 0

        for idx, chunk_url in enumerate(chunk_s3_urls):
            key = chunk_url.split('.amazonaws.com/')[-1]

            # Get chunk size without downloading
            response = s3_client.head_object(
                Bucket=settings.AWS_STORAGE_BUCKET_NAME,
                Key=key
            )

            size = response['ContentLength']
            chunk_info.append({'key': key, 'size': size})
            total_size += size
            print(f"   Chunk {idx}: {size:,} bytes")

        print(f"   Total: {total_size:,} bytes ({total_size / 1024 / 1024:.2f} MB)")

        # Start multipart upload
        s3_key = f"conversations/{safe_username}/{conversation_id}/complete.flac"

        print(f"   Starting multipart: {s3_key}")

        response = s3_client.create_multipart_upload(
            Bucket=settings.AWS_STORAGE_BUCKET_NAME,
            Key=s3_key,
            ContentType='audio/flac'
        )

        upload_id = response['UploadId']
        print(f"   Upload ID: {upload_id}")

        # Batch chunks into 5MB+ parts
        MIN_PART_SIZE = 5 * 1024 * 1024  # 5MB
        parts = []
        part_number = 1

        current_batch = []
        current_batch_size = 0

        for i, chunk in enumerate(chunk_info):
            current_batch.append(chunk)
            current_batch_size += chunk['size']

            # Is this the last chunk OR have we reached 5MB?
            is_last_chunk = (i == len(chunk_info) - 1)
            batch_ready = current_batch_size >= MIN_PART_SIZE

            if batch_ready or is_last_chunk:
                print(f"   Creating part {part_number} from {len(current_batch)} chunk(s), size: {current_batch_size:,} bytes")

                # If batch has 1 chunk, use UploadPartCopy directly
                if len(current_batch) == 1:
                    copy_source = {
                        'Bucket': settings.AWS_STORAGE_BUCKET_NAME,
                        'Key': current_batch[0]['key']
                    }

                    copy_response = s3_client.upload_part_copy(
                        Bucket=settings.AWS_STORAGE_BUCKET_NAME,
                        Key=s3_key,
                        PartNumber=part_number,
                        UploadId=upload_id,
                        CopySource=copy_source
                    )

                    etag = copy_response['CopyPartResult']['ETag']

                else:
                    # Multiple chunks in part: need to concatenate then upload
                    # This only happens when batching small chunks into one part
                    print(f"      Concatenating {len(current_batch)} chunks for this part...")

                    part_data = b''
                    for chunk in current_batch:
                        response = s3_client.get_object(
                            Bucket=settings.AWS_STORAGE_BUCKET_NAME,
                            Key=chunk['key']
                        )
                        part_data += response['Body'].read()

                    # Upload the concatenated part
                    upload_response = s3_client.upload_part(
                        Bucket=settings.AWS_STORAGE_BUCKET_NAME,
                        Key=s3_key,
                        PartNumber=part_number,
                        UploadId=upload_id,
                        Body=part_data
                    )

                    etag = upload_response['ETag']

                parts.append({'PartNumber': part_number, 'ETag': etag})
                print(f"   ✅ Part {part_number} complete, ETag: {etag}")

                # Reset for next part
                part_number += 1
                current_batch = []
                current_batch_size = 0

        # Complete multipart upload
        print(f"   Completing multipart with {len(parts)} parts...")

        s3_client.complete_multipart_upload(
            Bucket=settings.AWS_STORAGE_BUCKET_NAME,
            Key=s3_key,
            UploadId=upload_id,
            MultipartUpload={'Parts': parts}
        )

        s3_url = f"https://{settings.AWS_STORAGE_BUCKET_NAME}.s3.{settings.AWS_S3_REGION_NAME}.amazonaws.com/{s3_key}"

        print(f"✅ Multipart complete: {s3_url}")

        return {'s3_url': s3_url, 'success': True}

    except ClientError as e:
        print(f"❌ Error building multipart: {e}")
        import traceback
        traceback.print_exc()

        # Try to abort if upload_id exists
        if 'upload_id' in locals():
            try:
                s3_client.abort_multipart_upload(
                    Bucket=settings.AWS_STORAGE_BUCKET_NAME,
                    Key=s3_key,
                    UploadId=upload_id
                )
                print(f"   Aborted failed multipart upload")
            except:
                pass

        return {'success': False, 'error': str(e)}


def delete_chunk_files(chunks_folder_path):
    """Delete all chunk files in conversation's chunks folder."""
    try:
        s3_client = get_s3_client()

        print(f"🗑️  Deleting chunks from: {chunks_folder_path}")

        response = s3_client.list_objects_v2(
            Bucket=settings.AWS_STORAGE_BUCKET_NAME,
            Prefix=chunks_folder_path
        )

        if 'Contents' not in response:
            print(f"   No files found")
            return 0

        objects_to_delete = [{'Key': obj['Key']} for obj in response['Contents']]

        if objects_to_delete:
            s3_client.delete_objects(
                Bucket=settings.AWS_STORAGE_BUCKET_NAME,
                Delete={'Objects': objects_to_delete}
            )
            print(f"✅ Deleted {len(objects_to_delete)} chunk files")
            return len(objects_to_delete)

        return 0

    except ClientError as e:
        print(f"❌ Error deleting chunks: {e}")
        import traceback
        traceback.print_exc()
        return 0


def delete_final_file(final_audio_url):
    """Delete complete audio file."""
    try:
        s3_client = get_s3_client()

        key = final_audio_url.split('.amazonaws.com/')[-1]

        print(f"🗑️  Deleting final file: {key}")

        s3_client.delete_object(
            Bucket=settings.AWS_STORAGE_BUCKET_NAME,
            Key=key
        )

        print(f"✅ Final file deleted")
        return True

    except ClientError as e:
        print(f"❌ Error deleting final file: {e}")
        return False


def delete_conversation_audio(conversation):
    """Delete all audio for a conversation."""
    results = {
        'chunks_deleted': 0,
        'final_deleted': False,
        'success': False
    }

    print(f"🗑️  Deleting audio for conversation {conversation.id}")

    if conversation.chunks_folder_path:
        results['chunks_deleted'] = delete_chunk_files(conversation.chunks_folder_path)

    if conversation.final_audio_url:
        results['final_deleted'] = delete_final_file(conversation.final_audio_url)

    results['success'] = True
    print(f"✅ Audio deletion complete")

    return results

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


def verify_file_exists(s3_url):
    """Check if file exists in S3."""
    try:
        s3_client = get_s3_client()
        key = s3_url.split('.amazonaws.com/')[-1]

        s3_client.head_object(
            Bucket=settings.AWS_STORAGE_BUCKET_NAME,
            Key=key
        )
        return True

    except ClientError as e:
        if e.response['Error']['Code'] == '404':
            return False
        print(f"❌ Error verifying file: {e}")
        return False

def get_file_size(s3_url):
    """Get file size without downloading."""
    try:
        s3_client = get_s3_client()
        key = s3_url.split('.amazonaws.com/')[-1]

        response = s3_client.head_object(
            Bucket=settings.AWS_STORAGE_BUCKET_NAME,
            Key=key
        )

        return response['ContentLength']

    except ClientError as e:
        print(f"❌ Error getting file size: {e}")
        return None

def generate_presigned_url(s3_url, expiration=3600):
    """
    Generate a pre-signed URL for temporary access to a private S3 file.
    Default expiration is 1 hour (3600 seconds).
    """
    try:
        s3_client = get_s3_client()

        # Extract key from URL
        key = s3_url.split('.amazonaws.com/')[-1]

        # Generate pre-signed URL
        presigned_url = s3_client.generate_presigned_url(
            'get_object',
            Params={
                'Bucket': settings.AWS_STORAGE_BUCKET_NAME,
                'Key': key
            },
            ExpiresIn=expiration
        )

        print(f"ðŸ”— Generated pre-signed URL (expires in {expiration}s)")
        return presigned_url

    except ClientError as e:
        print(f"âŒ Error generating pre-signed URL: {e}")
        return None