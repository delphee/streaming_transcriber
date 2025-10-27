import assemblyai as aai
from django.conf import settings
from django.utils import timezone
import time

from .models import Conversation, Speaker, TranscriptSegment
from .s3_utils import get_audio_from_s3
from .ai_utils import identify_speakers_from_transcript, update_speaker_names


def process_conversation_with_batch_api(conversation_id):
    """
    Process a conversation with AssemblyAI's batch API to get speaker diarization.
    This runs in a background thread.
    """
    try:
        print(f"🔄 Starting batch processing for conversation {conversation_id}")

        # Get conversation
        conversation = Conversation.objects.get(id=conversation_id)

        if not conversation.audio_url:
            print("❌ No audio URL available for batch processing")
            return False

        # Configure AssemblyAI
        aai.settings.api_key = settings.ASSEMBLYAI_API_KEY

        # Create transcriber config with speaker diarization
        config = aai.TranscriptionConfig(
            speaker_labels=True,  # Enable speaker diarization
            speakers_expected=None,  # Auto-detect number of speakers
        )

        print(f"📤 Submitting audio to AssemblyAI batch API: {conversation.audio_url}")

        # Create transcriber and submit
        transcriber = aai.Transcriber(config=config)
        transcript = transcriber.transcribe(conversation.audio_url)

        # Wait for completion (AssemblyAI SDK handles polling)
        print(f"⏳ Waiting for batch transcription to complete...")

        if transcript.status == aai.TranscriptStatus.error:
            print(f"❌ Batch transcription failed: {transcript.error}")
            return False

        print(f"✅ Batch transcription complete!")
        print(f"📊 Detected {len(set([u.speaker for u in transcript.utterances]))} speakers")

        # Process the results and update database
        update_segments_with_speakers(conversation, transcript)

        # Run GPT-4o speaker identification
        identify_and_update_speakers(conversation)

        # Mark analysis as completed
        mark_batch_analysis_completed(conversation)

        print(f"✅ Batch processing complete for conversation {conversation_id}")
        return True

    except Conversation.DoesNotExist:
        print(f"❌ Conversation {conversation_id} not found")
        return False
    except Exception as e:
        print(f"❌ Error in batch processing: {e}")
        import traceback
        traceback.print_exc()
        return False


def update_segments_with_speakers(conversation, transcript):
    """
    Update TranscriptSegment records with speaker information from batch API.
    Maps utterances from batch API to existing segments by timestamp matching.
    """
    print(f"🔄 Updating segments with speaker information...")

    # Create a mapping of speaker labels from batch API
    speaker_map = {}

    for utterance in transcript.utterances:
        speaker_label = utterance.speaker

        # Get or create Speaker record
        if speaker_label not in speaker_map:
            speaker, created = Speaker.objects.get_or_create(
                conversation=conversation,
                speaker_label=speaker_label,
                defaults={
                    'identified_name': '',
                    'is_recording_user': False,
                }
            )
            speaker_map[speaker_label] = speaker

            if created:
                print(f"👤 Created speaker: {speaker_label}")

    # Update existing segments with speaker assignments
    segments = TranscriptSegment.objects.filter(conversation=conversation).order_by('start_time')

    for segment in segments:
        if segment.start_time is None:
            continue

        # Find matching utterance by timestamp
        for utterance in transcript.utterances:
            utterance_start = utterance.start
            utterance_end = utterance.end

            # Check if segment overlaps with this utterance
            if (segment.start_time >= utterance_start and segment.start_time <= utterance_end) or \
                    (segment.end_time and segment.end_time >= utterance_start and segment.end_time <= utterance_end):

                # Assign speaker to segment
                speaker = speaker_map.get(utterance.speaker)
                if speaker and segment.speaker != speaker:
                    segment.speaker = speaker
                    segment.save()
                    print(f"✅ Assigned {utterance.speaker} to segment: {segment.text[:30]}...")
                break

    print(f"✅ Updated {segments.count()} segments with speaker information")


def identify_and_update_speakers(conversation):
    """
    Use GPT-4o to identify speaker names and update Speaker records.
    This preserves names already identified in previous analyses.
    """
    print(f"🔍 Identifying speaker names with GPT-4o...")

    # Get current speaker mapping (preserve already identified names)
    current_speakers = {}
    for speaker in conversation.speakers.all():
        if speaker.identified_name:
            current_speakers[speaker.speaker_label] = speaker.identified_name
            print(f"📋 Preserving existing name: {speaker.speaker_label} = {speaker.identified_name}")

    # Run GPT-4o identification
    speaker_mapping = identify_speakers_from_transcript(conversation)

    if not speaker_mapping:
        print("⚠️ No speaker mapping generated by GPT-4o")
        return False

    # Merge with existing names (prefer existing names over new ones)
    for label, name in current_speakers.items():
        if label in speaker_mapping and speaker_mapping[label] == "Unknown":
            speaker_mapping[label] = name
            print(f"♻️ Keeping existing name for {label}: {name}")

    # Update speaker records
    update_speaker_names(conversation, speaker_mapping)

    return True


def mark_batch_analysis_completed(conversation):
    """
    Mark that batch analysis was completed.
    Store timestamp in conversation notes.
    """
    import json

    elapsed_seconds = (timezone.now() - conversation.started_at).total_seconds()

    # Load existing notes
    notes_data = {}
    if conversation.notes:
        try:
            notes_data = json.loads(conversation.notes)
        except:
            notes_data = {}

    # Update last batch analysis time
    notes_data['last_batch_analysis'] = elapsed_seconds
    conversation.notes = json.dumps(notes_data)
    conversation.save()

    print(f"📝 Marked batch analysis at {elapsed_seconds:.0f}s")