import assemblyai as aai
from django.conf import settings
from django.utils import timezone
import time

from .models import Conversation, Speaker, TranscriptSegment, ConversationAnalysis
from .s3_utils import get_audio_from_s3
from .ai_utils import identify_speakers_from_transcript, update_speaker_names


def process_conversation_with_batch_api(conversation_id, is_final=False):
    """
    Process a conversation with AssemblyAI's batch API to get speaker diarization.
    This runs in a background thread.

    Args:
        conversation_id: ID of the conversation to process
        is_final: If True, uses high-quality settings for final analysis after conversation ends.
                 If False, uses faster settings for periodic analysis during conversation.
    """
    try:
        analysis_type = "FINAL" if is_final else "PERIODIC"
        print(f"ðŸ”„ Starting {analysis_type} batch processing for conversation {conversation_id}")

        # Get conversation
        conversation = Conversation.objects.get(id=conversation_id)

        if not conversation.audio_url:
            print("âŒ No audio URL available for batch processing")
            return False

        # Generate pre-signed URL for AssemblyAI to access the file
        from .s3_utils import generate_presigned_url
        presigned_url = generate_presigned_url(conversation.audio_url, expiration=7200)  # 2 hours

        if not presigned_url:
            print("âŒ Failed to generate pre-signed URL")
            return False

        # Configure AssemblyAI
        aai.settings.api_key = settings.ASSEMBLYAI_API_KEY

        # Create transcriber config with speaker diarization
        # Use advanced settings for final analysis
        if is_final:
            print(f"ðŸŽ¯ Using BEST speech model for final analysis")
            config = aai.TranscriptionConfig(
                speaker_labels=True,  # Enable speaker diarization
                speakers_expected=None,  # Auto-detect number of speakers
                speech_model=aai.SpeechModel.best,  # Use best (most accurate) model
                # Additional quality settings for final analysis
                punctuate=True,
                format_text=True,
                disfluencies=False,  # Remove "um", "uh" etc for cleaner transcript
            )
        else:
            print(f"âš¡ Using standard settings for periodic analysis")
            config = aai.TranscriptionConfig(
                speaker_labels=True,  # Enable speaker diarization
                speakers_expected=None,  # Auto-detect number of speakers
                # Use default/faster model for periodic checks
            )

        print(f"ðŸ”¤ Submitting audio to AssemblyAI batch API (using pre-signed URL)")

        # Create transcriber and submit with pre-signed URL
        transcriber = aai.Transcriber(config=config)
        transcript = transcriber.transcribe(presigned_url)

        # Wait for completion (AssemblyAI SDK handles polling)
        print(f"â³ Waiting for batch transcription to complete...")

        if transcript.status == aai.TranscriptStatus.error:
            print(f"âŒ Batch transcription failed: {transcript.error}")
            return False

        print(f"âœ… Batch transcription complete!")
        print(f"ðŸ“Š Detected {len(set([u.speaker for u in transcript.utterances]))} speakers")

        # Process the results and update database
        # If we have HQ audio, create new segments; otherwise update existing segments
        if conversation.audio_quality == 'high_quality':
            create_hq_segments(conversation, transcript)
        else:
            update_segments_with_speakers(conversation, transcript)

        # Run GPT-4o speaker identification
        identify_and_update_speakers(conversation)

        # Run conversation analysis if user has assigned prompt
        run_conversation_analysis(conversation)

        # Mark analysis as completed
        mark_batch_analysis_completed(conversation, is_final)

        print(f"âœ… {analysis_type} batch processing complete for conversation {conversation_id}")
        return True

    except Conversation.DoesNotExist:
        print(f"âŒ Conversation {conversation_id} not found")
        return False
    except Exception as e:
        print(f"âŒ Error in batch processing: {e}")
        import traceback
        traceback.print_exc()
        return False


def create_hq_segments(conversation, transcript):
    """
    Create NEW TranscriptSegment records from high-quality transcription.
    These segments will have source='high_quality' and contain better transcription.
    The old streaming segments (source='streaming') are preserved for future tone analysis.
    """
    print("Creating new high-quality segments from batch transcription...")
    print(f"Total utterances in transcript: {len(transcript.utterances)}")

    # Debug: Show all utterances
    for idx, utt in enumerate(transcript.utterances):
        print(f"Utterance {idx}: speaker={utt.speaker}, start={utt.start}, end={utt.end}, text={utt.text[:100]}...")

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
                print(f"Created speaker: {speaker_label}")
            else:
                print(f"Using existing speaker: {speaker_label}")

    print(f"Speaker map has {len(speaker_map)} speakers")

    # Create new high-quality segments from utterances
    segments_created = 0
    for utterance in transcript.utterances:
        speaker = speaker_map.get(utterance.speaker)

        if not speaker:
            print(f"WARNING: No speaker found for {utterance.speaker}")
            continue

        # Calculate average confidence from words if available
        confidence = None
        if hasattr(utterance, 'words') and utterance.words:
            confidences = [w.confidence for w in utterance.words if hasattr(w, 'confidence') and w.confidence]
            if confidences:
                confidence = sum(confidences) / len(confidences)

        # Create new segment with source='high_quality'
        segment = TranscriptSegment.objects.create(
            conversation=conversation,
            speaker=speaker,
            text=utterance.text,
            is_final=True,
            start_time=utterance.start,
            end_time=utterance.end,
            confidence=confidence,
            source='high_quality',
        )
        segments_created += 1
        print(f"Created segment {segments_created}: speaker={utterance.speaker}, text={utterance.text[:50]}...")

    print(f"Created {segments_created} high-quality segments")

    # Mark conversation as analyzed
    conversation.is_analyzed = True
    conversation.save()


def update_segments_with_speakers(conversation, transcript):
    """
    Update TranscriptSegment records with speaker information from batch API.
    Maps utterances from batch API to existing segments by timestamp matching.
    """
    print(f"ðŸ”„ Updating segments with speaker information...")

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
                print(f"ðŸ‘¤ Created speaker: {speaker_label}")

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
                    print(f"âœ… Assigned {utterance.speaker} to segment: {segment.text[:30]}...")
                break

    print(f"âœ… Updated {segments.count()} segments with speaker information")


def identify_and_update_speakers(conversation):
    """
    Use GPT-4o to identify speaker names and update Speaker records.
    This preserves names already identified in previous analyses.
    """
    print(f"ðŸ” Identifying speaker names with GPT-4o...")

    # Get current speaker mapping (preserve already identified names)
    current_speakers = {}
    for speaker in conversation.speakers.all():
        if speaker.identified_name:
            current_speakers[speaker.speaker_label] = speaker.identified_name
            print(f"ðŸ“‹ Preserving existing name: {speaker.speaker_label} = {speaker.identified_name}")

    # Run GPT-4o identification
    speaker_mapping = identify_speakers_from_transcript(conversation)

    if not speaker_mapping:
        print("âš ï¸ No speaker mapping generated by GPT-4o")
        return False

    # Merge with existing names (prefer existing names over new ones)
    for label, name in current_speakers.items():
        if label in speaker_mapping and speaker_mapping[label] == "Unknown":
            speaker_mapping[label] = name
            print(f"â™»ï¸ Keeping existing name for {label}: {name}")

    # Update speaker records
    update_speaker_names(conversation, speaker_mapping)

    return True


def mark_batch_analysis_completed(conversation, is_final=False):
    """
    Mark that batch analysis was completed.
    Store timestamp in conversation notes.

    Args:
        conversation: Conversation object
        is_final: If True, marks as final analysis. If False, marks as periodic analysis.
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

    # Mark if this was the final high-quality analysis
    if is_final:
        notes_data['final_analysis_completed'] = True
        notes_data['final_analysis_time'] = elapsed_seconds
        print(f"ðŸ Marked FINAL analysis at {elapsed_seconds:.0f}s")
    else:
        print(f"ðŸ“ Marked periodic analysis at {elapsed_seconds:.0f}s")

    conversation.notes = json.dumps(notes_data)
    conversation.save()


def run_conversation_analysis(conversation):
    """
    Check if the user has an assigned prompt and run analysis if so.
    """
    try:
        user_profile = conversation.recorded_by.profile

        if not user_profile.assigned_prompt:
            print(f"No prompt assigned to user {conversation.recorded_by.username} - skipping analysis")
            return False

        prompt = user_profile.assigned_prompt

        if not prompt.is_active:
            print(f"Assigned prompt '{prompt.name}' is inactive - skipping analysis")
            return False

        print(f"Running analysis with prompt: {prompt.name}")

        # Run the analysis
        from .ai_utils import analyze_conversation_with_prompt
        analysis_result = analyze_conversation_with_prompt(conversation, prompt)

        if not analysis_result:
            print("Analysis failed - no result returned")
            return False

        # Store the result
        ConversationAnalysis.objects.create(
            conversation=conversation,
            analysis_type=prompt.name,
            prompt_template=prompt.optimized_prompt,
            analysis_result=analysis_result,
            prompt_used=prompt,
            visible_to_user=True,  # User can see by default
            visible_to_admin=True,
        )

        print(f"Analysis saved successfully for conversation {conversation.id}")
        return True

    except Exception as e:
        print(f"Error in conversation analysis: {e}")
        import traceback
        traceback.print_exc()
        return False






