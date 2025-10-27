from openai import OpenAI
from django.conf import settings
import json
import re

# Initialize OpenAI client
client = OpenAI(api_key=settings.OPENAI_API_KEY if hasattr(settings, 'OPENAI_API_KEY') else None)


def identify_speakers_from_transcript(conversation):
    """
    Use GPT-4 to identify speaker names from the conversation transcript.
    Returns a dictionary mapping speaker labels to identified names.
    """
    # Get all segments with speakers
    segments = conversation.segments.filter(is_final=True).select_related('speaker').order_by('created_at')

    if not segments.exists():
        print("No segments found for speaker identification")
        return {}

    # Build transcript text with speaker labels
    transcript_lines = []
    for segment in segments:
        speaker_label = segment.speaker.speaker_label if segment.speaker else "Unknown"
        transcript_lines.append(f"{speaker_label}: {segment.text}")

    transcript_text = "\n".join(transcript_lines)

    # Get recording user's name for context
    recording_user = conversation.recorded_by
    recording_user_name = recording_user.get_full_name() or recording_user.username

    # Create prompt for GPT-4
    prompt = f"""Analyze this conversation transcript and identify the real names of the speakers.

IMPORTANT CONTEXT:
- This conversation was recorded by {recording_user_name} on their iOS device
- {recording_user_name} is likely one of the speakers in this conversation
- Look for self-introductions like "Hi, this is [name]" or "My name is [name]"
- Look for others addressing speakers by name
- If you cannot confidently identify a name, leave it as the speaker label

TRANSCRIPT:
{transcript_text}

Respond with ONLY a JSON object mapping speaker labels to identified names. Format:
{{
    "Speaker A": "John Smith",
    "Speaker B": "Unknown"
}}

If a speaker is likely {recording_user_name}, use their full name. If you cannot identify a speaker's name with reasonable confidence, use "Unknown".

RESPOND WITH ONLY THE JSON OBJECT, NO OTHER TEXT."""

    try:
        # Call GPT-4 using the new API
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system",
                 "content": "You are an expert at analyzing conversations and identifying speakers from context clues."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.3,
            max_tokens=500
        )

        # Parse response
        response_text = response.choices[0].message.content.strip()

        # Remove markdown code blocks if present
        response_text = re.sub(r'```json\s*', '', response_text)
        response_text = re.sub(r'```\s*', '', response_text)

        speaker_mapping = json.loads(response_text)

        print(f"✅ Speaker identification complete: {speaker_mapping}")
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


# Rest of the functions remain the same...
def update_speaker_names(conversation, speaker_mapping):
    """
    Update Speaker records with identified names from the mapping.
    Also mark the recording user's speaker.
    """
    recording_user_name = conversation.recorded_by.get_full_name() or conversation.recorded_by.username

    print(f"🔍 Updating speakers for conversation {conversation.id}")
    print(f"📋 Speaker mapping: {speaker_mapping}")
    print(f"👤 Recording user name: {recording_user_name}")

    speakers = conversation.speakers.all()
    print(f"🔢 Found {speakers.count()} speakers to update")

    for speaker in speakers:
        print(f"Processing {speaker.speaker_label}...")

        # Get identified name from mapping
        identified_name = speaker_mapping.get(speaker.speaker_label, "")
        print(f"  Mapped name: '{identified_name}'")

        if identified_name and identified_name != "Unknown":
            print(f"  Updating {speaker.speaker_label} to {identified_name}")
            speaker.identified_name = identified_name

            # Check if this is the recording user
            if recording_user_name.lower() in identified_name.lower() or identified_name.lower() in recording_user_name.lower():
                speaker.is_recording_user = True
                print(f"  👤 Marked {speaker.speaker_label} as recording user ({identified_name})")

            speaker.save()
            print(f"  ✅ Updated {speaker.speaker_label} -> {identified_name}")
        else:
            print(f"  ⏭️ Skipping {speaker.speaker_label} (name is Unknown or empty)")

    return True


def should_run_speaker_analysis(conversation):
    """
    Determine if we should run speaker analysis based on timing.
    - First analysis at 2 minutes
    - Subsequent analyses every 5 minutes
    """
    if not conversation.started_at:
        return False

    from django.utils import timezone
    elapsed_seconds = (timezone.now() - conversation.started_at).total_seconds()

    # Get last analysis time from conversation notes (we'll store it there)
    import json
    last_analysis_time = 0
    if conversation.notes:
        try:
            notes_data = json.loads(conversation.notes)
            last_analysis_time = notes_data.get('last_speaker_analysis', 0)
        except:
            pass

    # First analysis at 2 minutes (120 seconds)
    if last_analysis_time == 0 and elapsed_seconds >= 120:
        return True

    # Subsequent analyses every 5 minutes (300 seconds)
    if last_analysis_time > 0 and (elapsed_seconds - last_analysis_time) >= 300:
        return True

    return False


def mark_analysis_completed(conversation):
    """
    Mark that speaker analysis was just completed.
    Store the timestamp in conversation notes.
    """
    from django.utils import timezone
    import json

    elapsed_seconds = (timezone.now() - conversation.started_at).total_seconds()

    # Load existing notes or create new
    notes_data = {}
    if conversation.notes:
        try:
            notes_data = json.loads(conversation.notes)
        except:
            notes_data = {}

    # Update last analysis time
    notes_data['last_speaker_analysis'] = elapsed_seconds
    conversation.notes = json.dumps(notes_data)
    conversation.save()

    print(f"📝 Marked speaker analysis at {elapsed_seconds:.0f}s")


def analyze_conversation_speakers(conversation):
    """
    Main function to analyze and identify speakers in a conversation.
    Can be called multiple times during a conversation.
    """
    print(f"🔍 Starting speaker identification for conversation {conversation.id}")

    # Identify speakers using GPT-4
    speaker_mapping = identify_speakers_from_transcript(conversation)

    if not speaker_mapping:
        print("⚠️ No speaker mapping generated")
        return False

    # Update speaker records
    success = update_speaker_names(conversation, speaker_mapping)

    if success:
        # Mark that we completed analysis
        mark_analysis_completed(conversation)
        print(f"✅ Speaker identification complete for conversation {conversation.id}")

    return success