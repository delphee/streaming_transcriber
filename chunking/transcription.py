"""
Transcription handler for hybrid chunked audio system.

TWO TRANSCRIPTION MODES:
1. PRELIMINARY: Fast transcription of chunks as they arrive (for monitoring)
2. FINAL: High-quality transcription with speaker diarization (complete file)

PLUS: AI-powered analysis (summary, action items, coaching)
"""

import assemblyai as aai
from django.conf import settings
from django.utils import timezone
from openai import OpenAI
import json
import re
from datetime import timedelta
from .s3_handler_hybrid import generate_presigned_download_url
from functools import lru_cache

# Initialize clients
aai.settings.api_key = settings.ASSEMBLYAI_API_KEY
#openai_client = None
#if hasattr(settings, 'OPENAI_API_KEY') and settings.OPENAI_API_KEY:
#    openai_client = OpenAI(api_key=settings.OPENAI_API_KEY)
#    print("✅ OpenAI client initialized")
#else:
#    print("⚠️ OpenAI API key not found - AI analysis will be skipped")


@lru_cache(maxsize=1)
def get_openai_client():
    """
    Lazily initialize and cache the OpenAI client.
    Ensures it's created once per process and reused safely.
    """
    api_key = getattr(settings, "OPENAI_API_KEY", None)
    if not api_key:
        print("⚠️ OpenAI API key not found - AI analysis will be skipped")
        return None
    print("✅ OpenAI client initialized (cached)")
    return OpenAI(api_key=api_key)


# === PRELIMINARY TRANSCRIPTION (Fast, for monitoring) ===

def transcribe_chunks_preliminary(conversation_id, chunk_ids):
    """
    Transcribe a batch of chunks quickly for preliminary monitoring.
    Uses standard AssemblyAI settings (no speaker diarization).
    Uses presigned URLs for private S3 access.

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
            print(f"âš ï¸ No chunks found for preliminary transcription")
            return False

        print(f"ðŸŽ¤ Starting preliminary transcription for {chunks.count()} chunk(s)")

        transcriber = aai.Transcriber()

        for chunk in chunks:
            if chunk.transcript_text and chunk.transcript_source == 'preliminary':
                print(f"   Chunk {chunk.chunk_number} already transcribed (preliminary), skipping")
                continue

            print(f"   Transcribing chunk {chunk.chunk_number}...")

            # Generate presigned URL for AssemblyAI (1 hour expiration)
            presigned_url = generate_presigned_download_url(chunk.s3_chunk_url, expiration=3600)

            if not presigned_url:
                print(f"   âŒ Failed to generate presigned URL for chunk {chunk.chunk_number}")
                continue

            print(f"   ðŸ”— Using presigned URL for transcription")

            # Configure for speed (no speaker diarization)
            config = aai.TranscriptionConfig(
                speech_model=aai.SpeechModel.nano,  # Fastest model
                punctuate=True,
                format_text=True
            )

            # Submit chunk presigned URL for transcription
            transcript = transcriber.transcribe(
                presigned_url,
                config=config
            )

            if transcript.status == aai.TranscriptStatus.error:
                print(f"   âŒ Transcription failed for chunk {chunk.chunk_number}: {transcript.error}")
                continue

            # Save preliminary transcript
            chunk.transcript_text = transcript.text
            chunk.transcript_source = 'preliminary'
            chunk.transcribed_at = timezone.now()
            chunk.confidence_score = transcript.confidence if hasattr(transcript, 'confidence') else None
            chunk.save()

            print(f"   âœ… Chunk {chunk.chunk_number} transcribed: {len(transcript.text)} chars")

        # Update conversation's preliminary transcript (stitched)
        stitch_preliminary_transcript(conversation)

        # Update last preliminary transcription timestamp
        conversation.last_preliminary_transcription = timezone.now()
        conversation.save()

        print(f"âœ… Preliminary transcription complete")
        return True

    except Exception as e:
        print(f"âŒ Error in preliminary transcription: {e}")
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

    print(f"ðŸ“ Stitched preliminary transcript: {len(conversation.preliminary_transcript)} chars")


# === FINAL TRANSCRIPTION (High Quality + Speaker Diarization) ===

def transcribe_final_audio(conversation_id):
    """
    Transcribe the complete audio file with high quality and speaker diarization.
    This is the authoritative transcription used for final analysis.
    Uses presigned URLs for private S3 access.

    Args:
        conversation_id: ChunkedConversation ID

    Returns:
        bool: Success status
    """
    from .models import ChunkedConversation, Speaker, TranscriptSegment

    try:
        conversation = ChunkedConversation.objects.get(id=conversation_id)

        if not conversation.final_audio_url:
            error_msg = f"No final audio URL for conversation {conversation_id}"
            print(f"âŒ {error_msg}")
            conversation.transcription_error = error_msg
            conversation.save()
            return False

        print(f"ðŸŽ¤ Starting FINAL transcription for conversation {conversation_id}")
        print(f"   Audio URL: {conversation.final_audio_url}")

        # Generate presigned URL for AssemblyAI (1 hour expiration)
        presigned_url = generate_presigned_download_url(conversation.final_audio_url, expiration=3600)

        if not presigned_url:
            error_msg = "Failed to generate presigned URL for final audio"
            print(f"âŒ {error_msg}")
            conversation.transcription_error = error_msg
            conversation.save()
            return False

        print(f"   ðŸ”— Using presigned URL for final transcription")

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
            presigned_url,
            config=config
        )

        if transcript.status == aai.TranscriptStatus.error:
            error_msg = f"Final transcription failed: {transcript.error}"
            print(f"âŒ {error_msg}")
            conversation.transcription_error = error_msg
            conversation.save()
            return False

        print(f"âœ… Final transcription complete")
        print(f"   Text length: {len(transcript.text)} chars")
        print(f"   Speakers detected: {len(set([u.speaker for u in transcript.utterances])) if transcript.utterances else 0}")

        # Save full transcript text
        conversation.full_transcript = transcript.text
        conversation.transcription_error = ""  # Clear any previous errors
        conversation.save()

        # Create Speaker and TranscriptSegment records
        if transcript.utterances:
            create_speakers_and_segments(conversation, transcript)

        # Identify speakers using AI
        openai_client = get_openai_client()
        if openai_client and transcript.utterances:
            identify_speakers_with_ai(conversation)

        # Generate formatted transcript with speaker names
        if transcript.utterances:
            generate_formatted_transcript(conversation)

        # Generate conversation analysis
        openai_client = get_openai_client()
        if openai_client:
            analyze_conversation(conversation)

        # Mark as analyzed
        conversation.is_analyzed = True
        conversation.save()

        print(f"âœ… Final analysis complete for conversation {conversation_id}")
        return True

    except Exception as e:
        error_msg = f"Error in final transcription: {str(e)}"
        print(f"âŒ {error_msg}")
        import traceback
        traceback.print_exc()

        try:
            conversation = ChunkedConversation.objects.get(id=conversation_id)
            conversation.transcription_error = error_msg
            conversation.save()
        except:
            pass

        return False


def create_speakers_and_segments(conversation, transcript):
    """
    Create Speaker and TranscriptSegment records from AssemblyAI transcript.

    Args:
        conversation: ChunkedConversation instance
        transcript: AssemblyAI transcript object
    """
    from .models import Speaker, TranscriptSegment

    print(f"ðŸ‘¥ Creating speakers and segments...")

    # Get unique speaker labels
    speaker_labels = set([u.speaker for u in transcript.utterances])

    # Create Speaker records
    speakers_map = {}
    for label in speaker_labels:
        speaker, created = Speaker.objects.get_or_create(
            conversation=conversation,
            speaker_label=label
        )
        speakers_map[label] = speaker

        if created:
            print(f"   Created speaker: {label}")

    # Create TranscriptSegment records
    segment_count = 0
    for utterance in transcript.utterances:
        speaker = speakers_map[utterance.speaker]

        TranscriptSegment.objects.create(
            conversation=conversation,
            speaker=speaker,
            text=utterance.text,
            start_time=utterance.start,  # milliseconds
            end_time=utterance.end,  # milliseconds
            confidence=utterance.confidence if hasattr(utterance, 'confidence') else None
        )
        segment_count += 1

    print(f"âœ… Created {len(speakers_map)} speakers and {segment_count} segments")


def identify_speakers_with_ai(conversation):
    """
    Use OpenAI GPT-4 to identify speakers by analyzing their dialogue.
    Enhanced version with better prompting and error handling.

    Args:
        conversation: ChunkedConversation instance
    """
    from .models import Speaker, TranscriptSegment

    openai_client = get_openai_client()
    if not openai_client:
        print(f"âš ï¸ OpenAI client not configured, skipping speaker identification")
        return

    print(f"ðŸ¤– Using AI to identify speakers...")

    speakers = Speaker.objects.filter(conversation=conversation)

    if not speakers.exists():
        print(f"   No speakers to identify")
        return

    # Get recording user's name
    recording_user_name = conversation.recorded_by.get_full_name() or conversation.recorded_by.username

    # Get more context from the conversation
    all_segments = TranscriptSegment.objects.filter(
        conversation=conversation
    ).order_by('start_time')[:50]  # First 50 segments for full context

    for speaker in speakers:
        # Get this speaker's dialogue
        segments = TranscriptSegment.objects.filter(
            conversation=conversation,
            speaker=speaker
        ).order_by('start_time')[:15]  # More segments for better analysis

        if not segments.exists():
            continue

        speaker_dialogue = "\n".join([f"- {seg.text}" for seg in segments])

        # Get OTHER speakers' dialogue (for context)
        other_segments = TranscriptSegment.objects.filter(
            conversation=conversation
        ).exclude(
            speaker=speaker
        ).order_by('start_time')[:15]

        other_dialogue = "\n".join([f"- {seg.text}" for seg in other_segments])

        # Build enhanced prompt
        prompt = f"""You are an expert at analyzing conversations to identify speakers based on their dialogue and context clues.

RECORDING INFORMATION:
- This recording was made by: {recording_user_name}
- We need to identify who "{speaker.speaker_label}" is in this conversation

{speaker.speaker_label}'S DIALOGUE:
{speaker_dialogue}

OTHER SPEAKER(S)' DIALOGUE (for context):
{other_dialogue}

IDENTIFICATION CRITERIA:
Look for these clues to identify {speaker.speaker_label}:

1. **Direct self-introduction**: "Hi, I'm John" or "This is Sarah calling"
2. **Name mentioned by others**: "Thanks, Michael" or "Susan, can you help?"
3. **Role indicators**: "As your sales rep..." or "I'm calling from..."
4. **Context clues**: Business name mentions, relationship indicators

SPECIAL CASES:
- If {speaker.speaker_label} is clearly the person making the recording (uses first-person about recording, says their own name), indicate they are the recording user
- If you cannot confidently identify the name, respond with "Unknown"
- Be conservative - only provide a name if you have strong evidence

RESPONSE FORMAT:
Respond with ONLY a valid JSON object (no markdown, no explanation):
{{
    "identified_name": "First Last" or "Unknown",
    "confidence": "high" or "medium" or "low",
    "reasoning": "Brief explanation of how you identified this person"
}}"""

        try:
            response = openai_client.chat.completions.create(
                model="gpt-4",
                messages=[
                    {
                        "role": "system",
                        "content": "You are an expert at analyzing conversations to identify speakers. Always respond with valid JSON only, no markdown formatting."
                    },
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                temperature=0.2,  # Lower temperature for more consistent results
                max_tokens=300
            )

            result_text = response.choices[0].message.content.strip()

            # Remove markdown code blocks if present
            result_text = re.sub(r'```json\s*', '', result_text)
            result_text = re.sub(r'```\s*$', '', result_text)
            result_text = result_text.strip()

            result = json.loads(result_text)

            identified_name = result.get('identified_name', 'Unknown')
            confidence = result.get('confidence', 'low')
            reasoning = result.get('reasoning', '')

            print(f"   {speaker.speaker_label}: {identified_name} (confidence: {confidence})")
            print(f"      Reasoning: {reasoning}")

            # Update speaker if we have a confident identification
            if identified_name and identified_name != "Unknown":
                speaker.identified_name = identified_name

                # Check if this is the recording user (case-insensitive comparison)
                if (recording_user_name.lower() in identified_name.lower() or
                        identified_name.lower() in recording_user_name.lower() or
                        "recording user" in reasoning.lower()):
                    speaker.is_recording_user = True
                    print(f"   ðŸ‘¤ Marked {speaker.speaker_label} as recording user")

                speaker.save()
                print(f"   âœ… Updated {speaker.speaker_label} -> {identified_name}")

        except json.JSONDecodeError as e:
            print(f"   âŒ Failed to parse AI response for {speaker.speaker_label}: {e}")
            print(f"      Response was: {result_text[:200]}")
        except Exception as e:
            print(f"   âŒ Error identifying {speaker.speaker_label}: {e}")
            import traceback
            traceback.print_exc()


def generate_formatted_transcript(conversation):
    """
    Generate a formatted transcript with speaker names and timestamps.
    Called after speaker identification completes.

    Format: [MM:SS] Speaker Name: Text

    Args:
        conversation: ChunkedConversation instance
    """
    from .models import TranscriptSegment

    print(f"📝 Generating formatted transcript...")

    segments = TranscriptSegment.objects.filter(
        conversation=conversation
    ).select_related('speaker').order_by('start_time')

    if not segments.exists():
        print(f"   No segments to format")
        return

    formatted_lines = []

    for segment in segments:
        # Get speaker name (identified name or label)
        if segment.speaker:
            speaker_name = segment.speaker.identified_name or segment.speaker.speaker_label
        else:
            speaker_name = "Unknown"

        # Format timestamp (milliseconds to MM:SS)
        total_seconds = segment.start_time // 1000
        minutes = total_seconds // 60
        seconds = total_seconds % 60
        timestamp = f"[{minutes}:{seconds:02d}]"

        # Format line: [MM:SS] Name: Text
        line = f"{timestamp} {speaker_name}: {segment.text}"
        formatted_lines.append(line)

    # Join with double newlines for readability
    conversation.formatted_transcript = "\n\n".join(formatted_lines)
    conversation.save()

    print(f"✅ Formatted transcript generated: {len(formatted_lines)} segments")


def format_analysis_as_text(analysis, prompt=None):
    """
    Convert JSON analysis into human-readable text format.

    Args:
        analysis: Dict from AI response
        prompt: AnalysisPrompt object (optional, for context)

    Returns:
        str: Formatted human-readable text
    """
    lines = []

    # Add prompt name if available
    if prompt:
        lines.append(f"Analysis: {prompt.name}")
        lines.append("=" * 50)
        lines.append("")

    # Format each key-value pair nicely
    for key, value in analysis.items():
        # Convert snake_case to Title Case
        formatted_key = key.replace('_', ' ').title()

        if isinstance(value, list):
            if not value:
                continue
            lines.append(f"{formatted_key}:")
            for item in value:
                if isinstance(item, dict):
                    # Handle dict items with special formatting for score/evidence patterns
                    if 'score' in item and 'evidence' in item:
                        # Format score/evidence naturally
                        score = item.get('score', '')
                        evidence = item.get('evidence', '')
                        lines.append(f"  • Score: {score}")
                        lines.append(f"    {evidence}")
                    else:
                        # Handle other dict structures naturally
                        for k, v in item.items():
                            if v:
                                formatted_subkey = k.replace('_', ' ').title()
                                lines.append(f"  • {formatted_subkey}: {v}")
                else:
                    lines.append(f"  • {item}")
            lines.append("")

        elif isinstance(value, dict):
            # Special handling for score/evidence pattern
            if 'score' in value and 'evidence' in value:
                lines.append(f"{formatted_key}:")
                lines.append(f"  Score: {value['score']}")
                lines.append(f"  {value['evidence']}")
                lines.append("")
            else:
                # Regular dict formatting - make it conversational
                lines.append(f"{formatted_key}:")
                for k, v in value.items():
                    formatted_subkey = k.replace('_', ' ').title()
                    if isinstance(v, str) and len(v) > 100:
                        lines.append(f"  {formatted_subkey}:")
                        lines.append(f"    {v}")
                    else:
                        lines.append(f"  {formatted_subkey}: {v}")
                lines.append("")

        elif isinstance(value, (int, float)):
            lines.append(f"{formatted_key}: {value}")

        elif isinstance(value, str) and value:
            # For longer text, add spacing
            if len(value) > 100:
                lines.append(f"{formatted_key}:")
                lines.append(value)
                lines.append("")
            else:
                lines.append(f"{formatted_key}: {value}")

    return "\n".join(lines)


def analyze_conversation(conversation):
    """
    Perform AI analysis of the conversation using the user's assigned custom prompt.

    The custom prompt has COMPLETE CONTROL over:
    - What analysis is performed
    - What questions are asked
    - What JSON structure is returned

    Standard fields (summary, action_items, key_topics, sentiment, coaching_feedback)
    will be populated IF they exist in the custom prompt's response, maintaining
    backward compatibility. If the custom prompt uses a different structure,
    the full response will be stored in the summary field.

    This allows for flexible, role-specific analysis (e.g., rating technicians,
    extracting pricing info, compliance checking, etc.) without being locked
    into a rigid structure.

    Args:
        conversation: ChunkedConversation instance
    """
    openai_client = get_openai_client()
    if not openai_client:
        print(f"⚠️ OpenAI client not configured, skipping conversation analysis")
        return

    print(f"🔍 Analyzing conversation with AI...")

    try:
        # Get the full transcript
        transcript = conversation.full_transcript

        if not transcript:
            print(f"   No transcript available for analysis")
            return

        # Get user's assigned prompt (or default)
        from streaming.models import AnalysisPrompt

        user_profile = conversation.recorded_by.profile
        assigned_prompt = user_profile.assigned_prompt

        # Fallback to default prompt if no assignment
        if not assigned_prompt:
            assigned_prompt = AnalysisPrompt.objects.filter(
                is_default=True,
                is_active=True
            ).first()

        # Final fallback to generic prompt if no default exists
        if assigned_prompt:
            print(f"   Using prompt: {assigned_prompt.name}")
            custom_instructions = assigned_prompt.optimized_prompt
            conversation.prompt_used = assigned_prompt
        else:
            print(f"   No assigned prompt found, using generic analysis")
            custom_instructions = "Analyze this conversation and provide professional insights."

        # Build analysis prompt using ONLY the custom instructions
        # Do NOT override with hardcoded structure - let the custom prompt dictate everything
        prompt = f"""{custom_instructions}

TRANSCRIPT:
{transcript[:8000]}

IMPORTANT: Respond with ONLY valid JSON (no markdown, no explanations outside the JSON).
Your response must be a single JSON object that can be parsed.

FORMATTING GUIDELINES:
- Use clear, descriptive field names
- For text fields, write in complete sentences and paragraphs (not bullet points in the JSON)
- For evidence/explanation fields, write naturally as if speaking to the user
- Avoid nested objects where possible - flatten the structure when it makes sense
- The JSON will be converted to human-readable format, so prioritize clarity over structure"""

        response = openai_client.chat.completions.create(
            model="gpt-4",
            messages=[
                {
                    "role": "system",
                    "content": "You are an expert conversation analyst providing actionable insights. Always respond with valid JSON only. Write text fields in a natural, conversational style as they will be read by humans."
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            temperature=0.3,
            max_tokens=1000
        )

        result_text = response.choices[0].message.content.strip()

        # Clean up markdown if present
        result_text = re.sub(r'```json\s*', '', result_text)
        result_text = re.sub(r'```\s*$', '', result_text)
        result_text = result_text.strip()

        analysis = json.loads(result_text)

        print(f"   Raw analysis JSON: {json.dumps(analysis, indent=2)[:500]}...")

        # Convert the JSON analysis to human-readable text
        readable_text = format_analysis_as_text(analysis, assigned_prompt)

        # CRITICAL: Ensure no raw dict/JSON strings remain (remove all brackets)
        # This is a safeguard in case the formatter missed something
        if '{' in readable_text or '[' in readable_text:
            print(f"   ⚠️  Warning: Found brackets in formatted text, applying additional cleanup")
            # Replace common JSON patterns with readable versions
            import re
            # Pattern: {"score": 4, "evidence": "text"}
            readable_text = re.sub(r'\{"score":\s*(\d+),\s*"evidence":\s*"([^"]+)"\}', r'Score: \1\n\2', readable_text)
            # Pattern: {"key": "value"}
            readable_text = re.sub(r'\{"([^"]+)":\s*"([^"]+)"\}', r'\1: \2', readable_text)
            # Remove any remaining braces/brackets
            readable_text = readable_text.replace('{', '').replace('}', '').replace('[', '').replace(']', '')
            readable_text = readable_text.replace('",', ':').replace('":', ':').replace('"', '')

        print(f"   Formatted text length: {len(readable_text)} chars")
        print(f"   Formatted text preview: {readable_text[:200]}...")

        # Store the human-readable text in summary
        conversation.summary = readable_text

        # Still populate structured fields if they exist (for backward compatibility)
        # Handle coaching_feedback - if it's a dict, format it too
        coaching_data = analysis.get('coaching_feedback', '')
        if isinstance(coaching_data, dict):
            # Format the dict as readable text
            coaching_lines = []
            for key, value in coaching_data.items():
                if isinstance(value, dict) and 'score' in value and 'evidence' in value:
                    formatted_key = key.replace('_', ' ').title()
                    coaching_lines.append(f"{formatted_key}:")
                    coaching_lines.append(f"  Score: {value['score']}")
                    coaching_lines.append(f"  {value['evidence']}")
                    coaching_lines.append("")
                elif isinstance(value, str):
                    formatted_key = key.replace('_', ' ').title()
                    coaching_lines.append(f"{formatted_key}: {value}")
            conversation.coaching_feedback = '\n'.join(coaching_lines)
        else:
            conversation.coaching_feedback = coaching_data

        conversation.action_items = analysis.get('action_items', [])
        conversation.key_topics = analysis.get('key_topics', [])
        conversation.sentiment = analysis.get('sentiment', '')

        conversation.analysis_error = ""  # Clear any previous errors

        conversation.save()

        print(f"✅ Conversation analysis complete")
        print(f"   Using custom prompt: {assigned_prompt.name if assigned_prompt else 'generic'}")
        if conversation.summary:
            print(f"   Summary: {conversation.summary[:100]}...")
        if conversation.action_items:
            print(f"   Action items: {len(conversation.action_items)}")
        if conversation.key_topics:
            print(f"   Key topics: {', '.join(conversation.key_topics)}")
        if conversation.sentiment:
            print(f"   Sentiment: {conversation.sentiment}")
        print(f"   Full analysis stored successfully")

    except json.JSONDecodeError as e:
        error_msg = f"Failed to parse AI analysis: {str(e)}"
        print(f"❌ {error_msg}")
        print(f"   Response was: {result_text[:200]}")
        conversation.analysis_error = error_msg
        conversation.save()
    except Exception as e:
        error_msg = f"Error analyzing conversation: {str(e)}"
        print(f"❌ {error_msg}")
        import traceback
        traceback.print_exc()
        conversation.analysis_error = error_msg
        conversation.save()






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

    print(f"ðŸ” Search for '{query}' found {len(results)} result(s)")

    return results


def optimize_prompt(plain_text):
    """
    Use GPT-4 to convert plain English instructions into a professional,
    optimized prompt for conversation analysis.
    """
    system_prompt = """You are an expert prompt engineer. Your job is to take plain English instructions 
and convert them into clear, professional, structured prompts for analyzing conversation transcripts.

Guidelines:
- Make the prompt clear and actionable
- Use numbered lists for multiple analysis points
- Ask for specific evidence/quotes from the conversation
- Include any rating scales or categorizations requested
- Keep it concise but comprehensive
- Format for easy reading

Return ONLY the optimized prompt, no explanations."""

    try:
        client = get_openai_client()
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Convert this into an optimized analysis prompt:\n\n{plain_text}"}
            ],
            temperature=0.7,
            max_tokens=800
        )

        optimized = response.choices[0].message.content.strip()
        print(f"Optimized prompt successfully: {len(optimized)} characters")
        return optimized

    except Exception as e:
        print(f"Error optimizing prompt: {e}")
        import traceback
        traceback.print_exc()
        return plain_text  # Fallback to original

