"""
Transcription handler for hybrid chunked audio system.

TWO TRANSCRIPTION MODES:
1. PRELIMINARY: Fast transcription of chunks as they arrive (for monitoring)
2. FINAL: High-quality transcription with speaker diarization (complete file)
"""

import assemblyai as aai
from django.conf import settings
from django.utils import timezone
from openai import OpenAI
import json
import re
from datetime import timedelta

# Initialize clients
aai.settings.api_key = settings.ASSEMBLYAI_API_KEY
openai_client = OpenAI(api_key=settings.OPENAI_API_KEY if hasattr(settings, 'OPENAI_API_KEY') else None)


# === PRELIMINARY TRANSCRIPTION (Fast, for monitoring) ===

def transcribe_chunks_preliminary(conversation_id, chunk_ids):
    """
    Transcribe a batch of chunks quickly for preliminary monitoring.
    Uses standard AssemblyAI settings (no speaker diarization).

    Args:
        conversation_id: ChunkedConversation ID
        chunk_ids: List of AudioChunk IDs to transcribe

    Returns:
        bool: Success status
    """
    from .models import ChunkedConversation, AudioChunk

    try:
        conversation = ChunkedConversation.objects.get(id=conversation_id)
        chunks = AudioChunk.objects.filter(
            id__in=chunk_ids,
            conversation=conversation
        ).order_by('chunk_number')

        if not chunks.exists():
            print(f"⚠️ No chunks found for preliminary transcription")
            return False

        print(f"🎤 Starting preliminary transcription for {chunks.count()} chunk(s)")

        transcriber = aai.Transcriber()

        for chunk in chunks:
            if chunk.transcript_text and chunk.transcript_source == 'preliminary':
                print(f"   Chunk {chunk.chunk_number} already transcribed (preliminary), skipping")
                continue

            print(f"   Transcribing chunk {chunk.chunk_number}...")

            # Configure for speed (no speaker diarization)
            config = aai.TranscriptionConfig(
                speech_model=aai.SpeechModel.nano,  # Fastest model
                punctuate=True,
                format_text=True
            )

            # Submit chunk S3 URL for transcription
            transcript = transcriber.transcribe(
                chunk.s3_chunk_url,
                config=config
            )

            if transcript.status == aai.TranscriptStatus.error:
                print(f"   ❌ Transcription failed for chunk {chunk.chunk_number}: {transcript.error}")
                continue

            # Save preliminary transcript
            chunk.transcript_text = transcript.text
            chunk.transcript_source = 'preliminary'
            chunk.transcribed_at = timezone.now()
            chunk.confidence_score = transcript.confidence if hasattr(transcript, 'confidence') else None
            chunk.save()

            print(f"   ✅ Chunk {chunk.chunk_number} transcribed: {len(transcript.text)} chars")

        # Update conversation's preliminary transcript (stitched)
        stitch_preliminary_transcript(conversation)

        # Update last preliminary transcription timestamp
        conversation.last_preliminary_transcription = timezone.now()
        conversation.save()

        print(f"✅ Preliminary transcription complete")
        return True

    except Exception as e:
        print(f"❌ Error in preliminary transcription: {e}")
        import traceback
        traceback.print_exc()
        return False


def stitch_preliminary_transcript(conversation):
    """
    Stitch together all preliminary chunk transcripts into a single text.
    Adds timing markers for context.

    Args:
        conversation: ChunkedConversation instance
    """
    from .models import AudioChunk

    chunks = AudioChunk.objects.filter(
        conversation=conversation,
        transcript_source='preliminary'
    ).exclude(
        transcript_text=''
    ).order_by('chunk_number')

    if not chunks.exists():
        return

    transcript_parts = []

    for chunk in chunks:
        # Add timing marker
        minutes = chunk.start_time_seconds // 60
        seconds = chunk.start_time_seconds % 60
        timestamp = f"[{minutes}:{seconds:02d}]"

        transcript_parts.append(f"{timestamp} {chunk.transcript_text}")

    # Join with double newlines for readability
    conversation.preliminary_transcript = "\n\n".join(transcript_parts)
    conversation.save()

    print(f"📝 Stitched preliminary transcript: {len(conversation.preliminary_transcript)} chars")


def should_trigger_preliminary_transcription(conversation):
    """
    Determine if we should trigger preliminary transcription based on settings.

    Args:
        conversation: ChunkedConversation instance

    Returns:
        tuple: (should_transcribe: bool, chunk_ids: list)
    """
    from .models import AudioChunk

    # Get chunks that haven't been transcribed yet
    untranscribed_chunks = AudioChunk.objects.filter(
        conversation=conversation,
        transcript_text=''
    ).order_by('chunk_number')

    batch_size = settings.PRELIMINARY_TRANSCRIPTION_BATCH_SIZE

    if untranscribed_chunks.count() >= batch_size:
        # Transcribe the oldest batch
        chunks_to_transcribe = list(untranscribed_chunks[:batch_size].values_list('id', flat=True))
        return True, chunks_to_transcribe

    return False, []


# === FINAL TRANSCRIPTION (High Quality + Speaker Diarization) ===

def transcribe_final_audio(conversation_id):
    """
    Transcribe the complete audio file with high quality and speaker diarization.
    This is the authoritative transcription used for final analysis.

    Args:
        conversation_id: ChunkedConversation ID

    Returns:
        bool: Success status
    """
    from .models import ChunkedConversation, Speaker, TranscriptSegment

    try:
        conversation = ChunkedConversation.objects.get(id=conversation_id)

        if not conversation.final_audio_url:
            print(f"❌ No final audio URL for conversation {conversation_id}")
            return False

        print(f"🎤 Starting FINAL transcription for conversation {conversation_id}")
        print(f"   Audio URL: {conversation.final_audio_url}")

        # Configure for maximum quality with speaker diarization
        config = aai.TranscriptionConfig(
            speech_model=aai.SpeechModel.best,  # Best quality model
            speaker_labels=True,  # Enable speaker diarization
            punctuate=True,
            format_text=True,
            speakers_expected=2  # Can be adjusted based on use case
        )

        transcriber = aai.Transcriber()

        print(f"   Submitting to AssemblyAI...")
        transcript = transcriber.transcribe(
            conversation.final_audio_url,
            config=config
        )

        if transcript.status == aai.TranscriptStatus.error:
            print(f"❌ Final transcription failed: {transcript.error}")
            return False

        print(f"✅ Final transcription complete")
        print(f"   Text length: {len(transcript.text)} chars")
        print(
            f"   Speakers detected: {len(set([u.speaker for u in transcript.utterances])) if transcript.utterances else 0}")

        # Save full transcript text
        conversation.full_transcript = transcript.text
        conversation.save()

        # Create Speaker and TranscriptSegment records
        if transcript.utterances:
            save_speakers_and_segments(conversation, transcript)

        # Identify speaker names using AI
        identify_speakers(conversation)

        # Mark as analyzed
        conversation.is_analyzed = True
        conversation.save()

        print(f"✅ Final transcription processing complete")

        # Optional: Update individual chunks with final transcription
        # (Map utterances back to chunks based on timing)
        update_chunks_with_final_transcript(conversation, transcript)

        return True

    except Exception as e:
        print(f"❌ Error in final transcription: {e}")
        import traceback
        traceback.print_exc()
        return False


def save_speakers_and_segments(conversation, transcript):
    """
    Create Speaker and TranscriptSegment records from AssemblyAI transcript.

    Args:
        conversation: ChunkedConversation instance
        transcript: AssemblyAI transcript object
    """
    from .models import Speaker, TranscriptSegment

    print(f"💾 Saving speakers and segments...")

    # Clear any existing speakers/segments (in case of re-transcription)
    Speaker.objects.filter(conversation=conversation).delete()
    TranscriptSegment.objects.filter(conversation=conversation).delete()

    # Create speaker records
    speaker_labels = set()
    if transcript.utterances:
        speaker_labels = set([u.speaker for u in transcript.utterances])

    speaker_objects = {}
    for label in speaker_labels:
        speaker = Speaker.objects.create(
            conversation=conversation,
            speaker_label=label
        )
        speaker_objects[label] = speaker
        print(f"   Created speaker: {label}")

    # Create transcript segments
    segment_count = 0
    if transcript.utterances:
        for utterance in transcript.utterances:
            speaker = speaker_objects.get(utterance.speaker)

            TranscriptSegment.objects.create(
                conversation=conversation,
                speaker=speaker,
                text=utterance.text,
                start_time=utterance.start,  # milliseconds
                end_time=utterance.end,
                confidence=utterance.confidence if hasattr(utterance, 'confidence') else None
            )
            segment_count += 1

    print(f"   Created {len(speaker_objects)} speaker(s) and {segment_count} segment(s)")


def update_chunks_with_final_transcript(conversation, transcript):
    """
    Map final high-quality transcript back to individual chunks.
    Updates AudioChunk records with improved transcription.

    Args:
        conversation: ChunkedConversation instance
        transcript: AssemblyAI transcript object
    """
    from .models import AudioChunk

    if not transcript.utterances:
        return

    print(f"🔄 Updating chunks with final transcript...")

    chunks = AudioChunk.objects.filter(conversation=conversation).order_by('chunk_number')

    for chunk in chunks:
        # Convert chunk timing to milliseconds
        chunk_start_ms = chunk.start_time_seconds * 1000
        chunk_end_ms = chunk_start_ms + (chunk.duration_seconds * 1000)

        # Find all utterances that overlap with this chunk
        chunk_utterances = [
            u for u in transcript.utterances
            if u.start < chunk_end_ms and u.end > chunk_start_ms
        ]

        if chunk_utterances:
            # Combine utterances into chunk transcript
            chunk_text = " ".join([u.text for u in chunk_utterances])

            # Calculate average confidence
            confidences = [u.confidence for u in chunk_utterances if hasattr(u, 'confidence')]
            avg_confidence = sum(confidences) / len(confidences) if confidences else None

            # Update chunk
            chunk.transcript_text = chunk_text
            chunk.transcript_source = 'final'
            chunk.transcribed_at = timezone.now()
            chunk.confidence_score = avg_confidence
            chunk.save()

            print(f"   Updated chunk {chunk.chunk_number} with final transcript")


def identify_speakers(conversation):
    """
    Use GPT-4 to identify speaker names from the final transcript.
    Reuses pattern from existing ai_utils.py

    Args:
        conversation: ChunkedConversation instance

    Returns:
        dict: Speaker label mapping
    """
    from .models import Speaker, TranscriptSegment

    print(f"🤖 Identifying speakers with AI...")

    # Get all segments with speakers
    segments = TranscriptSegment.objects.filter(
        conversation=conversation
    ).select_related('speaker').order_by('start_time')

    if not segments.exists():
        print(f"⚠️ No segments found for speaker identification")
        return {}

    # Get unique speaker labels
    speakers = Speaker.objects.filter(conversation=conversation)
    speaker_labels = [s.speaker_label for s in speakers]

    if not speaker_labels:
        print(f"⚠️ No speakers found")
        return {}

    # Build transcript with speaker labels
    transcript_lines = []
    for segment in segments[:100]:  # Limit to first 100 segments to save tokens
        speaker_label = segment.speaker.speaker_label if segment.speaker else "Unknown"
        transcript_lines.append(f"{speaker_label}: {segment.text}")

    transcript_text = "\n".join(transcript_lines)

    # Get recording user's name
    recording_user = conversation.recorded_by
    recording_user_name = recording_user.get_full_name() or recording_user.username

    # Create prompt for GPT-4
    speakers_list = ", ".join(speaker_labels)

    prompt = f"""Analyze this conversation transcript and identify the real names of the speakers.

IMPORTANT CONTEXT:
- This conversation was recorded by {recording_user_name}
- {recording_user_name} is likely one of the speakers in this conversation
- The speakers are labeled: {speakers_list}
- Look for self-introductions like "Hi, this is [name]" or "My name is [name]"
- Look for others addressing speakers by name
- If you cannot confidently identify a name, use "Unknown"

TRANSCRIPT:
{transcript_text}

Respond with ONLY a JSON object mapping the EXACT speaker labels to identified names.
YOU MUST USE THE EXACT LABELS: {speakers_list}

Format example:
{{
    "{speaker_labels[0]}": "John Smith",
    "{speaker_labels[1] if len(speaker_labels) > 1 else 'B'}": "Unknown"
}}

If a speaker is likely {recording_user_name}, use their full name.

CRITICAL: Use the EXACT speaker labels ({speakers_list}) as keys.
RESPOND WITH ONLY THE JSON OBJECT, NO OTHER TEXT."""

    try:
        response = openai_client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {
                    "role": "system",
                    "content": "You are an expert at analyzing conversations and identifying speakers. Always use exact speaker labels provided."
                },
                {"role": "user", "content": prompt}
            ],
            temperature=0.3,
            max_tokens=500
        )

        response_text = response.choices[0].message.content.strip()

        # Remove markdown code blocks if present
        response_text = re.sub(r'```json\s*', '', response_text)
        response_text = re.sub(r'```\s*', '', response_text)

        speaker_mapping = json.loads(response_text)

        print(f"✅ Speaker identification complete: {speaker_mapping}")

        # Update Speaker records
        update_speaker_names(conversation, speaker_mapping, recording_user_name)

        return speaker_mapping

    except json.JSONDecodeError as e:
        print(f"❌ Failed to parse GPT-4 response: {e}")
        print(f"Response was: {response_text}")
        return {}
    except Exception as e:
        print(f"❌ Error in speaker identification: {e}")
        import traceback
        traceback.print_exc()
        return {}


def update_speaker_names(conversation, speaker_mapping, recording_user_name):
    """
    Update Speaker records with identified names.

    Args:
        conversation: ChunkedConversation instance
        speaker_mapping: Dict mapping speaker labels to names
        recording_user_name: Name of recording user
    """
    from .models import Speaker

    print(f"👤 Updating speaker names...")

    speakers = Speaker.objects.filter(conversation=conversation)

    for speaker in speakers:
        identified_name = speaker_mapping.get(speaker.speaker_label, "")

        if identified_name and identified_name != "Unknown":
            speaker.identified_name = identified_name

            # Check if this is the recording user
            if (recording_user_name.lower() in identified_name.lower() or
                    identified_name.lower() in recording_user_name.lower()):
                speaker.is_recording_user = True
                print(f"   👤 Marked {speaker.speaker_label} as recording user")

            speaker.save()
            print(f"   ✅ Updated {speaker.speaker_label} -> {identified_name}")


# === SEARCH FUNCTIONALITY ===

def search_transcripts(conversation_id, query):
    """
    Search through chunk transcripts for a specific query.
    Returns matches with timing and context.

    Args:
        conversation_id: ChunkedConversation ID
        query: Search string

    Returns:
        list: [
            {
                'chunk_number': int,
                'start_time_seconds': int,
                'time_display': str,
                'matching_text': str,
                'context': str (surrounding text)
            }
        ]
    """
    from .models import AudioChunk

    if not query:
        return []

    # Search in chunk transcripts (case-insensitive)
    chunks = AudioChunk.objects.filter(
        conversation_id=conversation_id,
        transcript_text__icontains=query
    ).order_by('chunk_number')

    results = []

    for chunk in chunks:
        # Find the query position in the text
        lower_text = chunk.transcript_text.lower()
        lower_query = query.lower()
        position = lower_text.find(lower_query)

        if position == -1:
            continue

        # Extract context (50 chars before and after)
        context_start = max(0, position - 50)
        context_end = min(len(chunk.transcript_text), position + len(query) + 50)
        context = chunk.transcript_text[context_start:context_end]

        # Add ellipsis if truncated
        if context_start > 0:
            context = "..." + context
        if context_end < len(chunk.transcript_text):
            context = context + "..."

        # Extract just the matching portion
        matching_text = chunk.transcript_text[position:position + len(query)]

        # Format time display
        minutes = chunk.start_time_seconds // 60
        seconds = chunk.start_time_seconds % 60
        time_display = f"{minutes}:{seconds:02d}"

        results.append({
            'chunk_number': chunk.chunk_number,
            'start_time_seconds': chunk.start_time_seconds,
            'time_display': time_display,
            'matching_text': matching_text,
            'context': context
        })

    print(f"🔍 Search for '{query}' found {len(results)} result(s)")

    return results