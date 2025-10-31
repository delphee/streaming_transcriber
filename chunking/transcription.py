"""
Transcription handler for chunked audio system.
"""
from django.conf import settings
from django.utils import timezone
import assemblyai as aai
from .s3_handler import generate_presigned_download_url


def trigger_preliminary_transcription(conversation_id, chunk_ids):
    """
    Trigger preliminary transcription for batches of chunks.
    Uses presigned URLs for private S3 access.
    """
    from .models import AudioChunk

    print(f"🎤 Starting preliminary transcription for {len(chunk_ids)} chunk(s)")

    aai.settings.api_key = settings.ASSEMBLYAI_API_KEY

    for chunk_id in chunk_ids:
        try:
            chunk = AudioChunk.objects.get(id=chunk_id)
            print(f"Transcribing chunk {chunk.chunk_number}...")

            # Generate presigned URL for AssemblyAI (1 hour expiration)
            presigned_url = generate_presigned_download_url(chunk.s3_chunk_url, expiration=3600)

            if not presigned_url:
                print(f"❌ Failed to generate presigned URL for chunk {chunk.chunk_number}")
                continue

            print(f"🔗 Using presigned URL for transcription")

            # Submit to AssemblyAI using presigned URL
            transcript = aai.Transcriber().transcribe(presigned_url)

            if transcript.status == aai.TranscriptStatus.error:
                print(f"❌ Transcription failed for chunk {chunk.chunk_number}: {transcript.error}")
                continue

            # Save preliminary transcript
            chunk.transcript_text = transcript.text or ""
            chunk.transcript_source = 'preliminary'
            chunk.transcribed_at = timezone.now()
            chunk.confidence_score = transcript.confidence if hasattr(transcript, 'confidence') else None
            chunk.save()

            print(f"✅ Chunk {chunk.chunk_number} transcribed successfully")
            print(f"   Transcript: {chunk.transcript_text[:100]}...")

        except Exception as e:
            print(f"❌ Error transcribing chunk {chunk_id}: {str(e)}")
            import traceback
            traceback.print_exc()

    print(f"✅ Preliminary transcription complete")


def trigger_final_transcription(conversation_id):
    """
    Trigger final transcription with speaker diarization.
    Uses presigned URLs for private S3 access.
    """
    from .models import ChunkedConversation, TranscriptSegment, Speaker

    print(f"🎤 Starting FINAL transcription for conversation {conversation_id}")

    try:
        conversation = ChunkedConversation.objects.get(id=conversation_id)

        if not conversation.final_audio_url:
            print(f"❌ No final audio URL for conversation {conversation_id}")
            return

        print(f"Audio URL: {conversation.final_audio_url}")

        # Generate presigned URL for AssemblyAI (1 hour expiration)
        presigned_url = generate_presigned_download_url(conversation.final_audio_url, expiration=3600)

        if not presigned_url:
            print(f"❌ Failed to generate presigned URL for final audio")
            return

        print(f"🔗 Using presigned URL for final transcription")
        print(f"Submitting to AssemblyAI...")

        # Configure AssemblyAI for high-quality transcription with speaker diarization
        aai.settings.api_key = settings.ASSEMBLYAI_API_KEY

        config = aai.TranscriptionConfig(
            speaker_labels=True,  # Enable speaker diarization
            speech_model=aai.SpeechModel.best  # Use best model for quality
        )

        transcript = aai.Transcriber().transcribe(presigned_url, config=config)

        if transcript.status == aai.TranscriptStatus.error:
            print(f"❌ Final transcription failed: {transcript.error}")
            return

        # Save full transcript
        conversation.full_transcript = transcript.text or ""
        conversation.is_analyzed = True
        conversation.save()

        print(f"✅ Final transcription saved")
        print(f"   Length: {len(conversation.full_transcript)} characters")

        # Process speakers and segments if available
        if hasattr(transcript, 'utterances') and transcript.utterances:
            process_speakers_and_segments(conversation, transcript)

        print(f"✅ Final transcription complete for conversation {conversation_id}")

    except Exception as e:
        print(f"❌ Error in final transcription: {str(e)}")
        import traceback
        traceback.print_exc()


def process_speakers_and_segments(conversation, transcript):
    """Process speaker labels and create transcript segments"""
    from .models import Speaker, TranscriptSegment

    print(f"👥 Processing speakers and segments...")

    # Create speaker records
    speakers_map = {}
    for utterance in transcript.utterances:
        speaker_label = utterance.speaker

        if speaker_label not in speakers_map:
            speaker, created = Speaker.objects.get_or_create(
                conversation=conversation,
                speaker_label=speaker_label
            )
            speakers_map[speaker_label] = speaker
            if created:
                print(f"   Created speaker: {speaker_label}")

    # Create transcript segments
    segment_count = 0
    for utterance in transcript.utterances:
        speaker = speakers_map.get(utterance.speaker)

        TranscriptSegment.objects.create(
            conversation=conversation,
            speaker=speaker,
            text=utterance.text,
            start_time=utterance.start,
            end_time=utterance.end,
            confidence=utterance.confidence if hasattr(utterance, 'confidence') else None
        )
        segment_count += 1

    print(f"✅ Created {len(speakers_map)} speakers and {segment_count} segments")


def search_transcripts(conversation_id, query):
    """
    Search transcript text across all chunks in a conversation.
    Returns matches with timing information.
    """
    from .models import AudioChunk

    chunks = AudioChunk.objects.filter(
        conversation_id=conversation_id,
        transcript_text__icontains=query
    ).order_by('chunk_number')

    results = []
    for chunk in chunks:
        # Find the position in the transcript
        text_lower = chunk.transcript_text.lower()
        query_lower = query.lower()
        position = text_lower.find(query_lower)

        if position >= 0:
            # Get context around the match
            context_start = max(0, position - 50)
            context_end = min(len(chunk.transcript_text), position + len(query) + 50)
            context = chunk.transcript_text[context_start:context_end]

            # Calculate time position
            minutes = chunk.start_time_seconds // 60
            seconds = chunk.start_time_seconds % 60
            time_display = f"{minutes}m {seconds}s"

            results.append({
                'chunk_number': chunk.chunk_number,
                'start_time_seconds': chunk.start_time_seconds,
                'time_display': time_display,
                'matching_text': context,
                'full_text': chunk.transcript_text
            })

    return results