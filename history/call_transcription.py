"""
Transcription and AI analysis service for ServiceTitan phone calls.

Uses:
- AssemblyAI for transcription with speaker diarization
- OpenAI GPT for customer service call analysis
"""

import os
import time
import requests
import tempfile
import logging
import re
from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)


# === SERVICETITAN CUSTOMER SERVICE ANALYSIS PROMPT ===

SERVICETITAN_PROMPT = """You are analyzing a CUSTOMER SERVICE conversation for quality assurance.

CONTEXT:
- This is a customer support interaction
- Focus on issue resolution, customer satisfaction, and protocol adherence
- Account for transcription errors but evaluate service quality

SERVICE ANALYSIS REQUIRED:
1. INTERACTION SUMMARY: Brief summary of the customer issue and resolution
2. ISSUE RESOLUTION: Was the problem resolved? (fully resolved, partially resolved, unresolved, escalated)
3. RESPONSE TIME: How quickly was the customer helped? (immediate, prompt, delayed, very delayed)
4. EMPATHY & RAPPORT: Quality of customer relationship building (excellent, good, fair, poor)
5. PROTOCOL ADHERENCE: Did agent follow company procedures? (fully, mostly, partially, poorly)
6. CUSTOMER SATISFACTION: Likely customer satisfaction level (very satisfied, satisfied, neutral, dissatisfied)
7. TONE: Overall conversation tone (helpful, professional, frustrated, impatient, friendly)
8. IMPROVEMENT AREAS: Specific suggestions for better service

FORMAT YOUR RESPONSE AS:
INTERACTION SUMMARY:
[Issue and resolution summary]

ISSUE RESOLUTION:
[Status with explanation]

RESPONSE TIME:
[Rating with details]

EMPATHY & RAPPORT:
[Rating with examples]

PROTOCOL ADHERENCE:
[Rating with details]

CUSTOMER SATISFACTION:
[Likely satisfaction level with reasoning]

TONE:
[Single word or phrase]

IMPROVEMENT AREAS:
[Specific coaching suggestions]"""


class AssemblyAIService:
    """AssemblyAI transcription service with speaker diarization"""

    def __init__(self):
        self.api_key = getattr(settings, 'ASSEMBLYAI_API_KEY', None)
        if not self.api_key:
            raise ValueError("ASSEMBLYAI_API_KEY not set in settings")

    def transcribe_mp3(self, mp3_data):
        """
        Transcribe MP3 audio data with speaker diarization.

        Args:
            mp3_data: bytes - Raw MP3 audio data

        Returns:
            dict: {
                'transcript': str,
                'speaker_data': {
                    'utterances': [...],
                    'speaker_transcript': str,
                    'speakers_detected': int,
                    'audio_duration': float
                },
                'duration': float
            }
        """
        # Write MP3 to temp file
        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.mp3')
        temp_file.write(mp3_data)
        temp_file.close()

        try:
            # Step 1: Upload to AssemblyAI
            upload_url = self._upload_file(temp_file.name)

            # Step 2: Request transcription with speaker diarization
            transcript_id = self._request_transcription(upload_url)

            # Step 3: Poll for results
            result_data = self._poll_for_results(transcript_id)

            # Step 4: Format results
            return self._format_results(result_data)

        finally:
            if os.path.exists(temp_file.name):
                os.unlink(temp_file.name)

    def _upload_file(self, file_path):
        """Upload audio file to AssemblyAI"""
        upload_url = "https://api.assemblyai.com/v2/upload"
        headers = {
            "authorization": self.api_key,
            "Content-Type": "application/octet-stream"
        }

        with open(file_path, 'rb') as f:
            file_data = f.read()

        response = requests.post(upload_url, headers=headers, data=file_data)

        if response.status_code != 200:
            raise ValueError(f"AssemblyAI upload failed: {response.text}")

        audio_url = response.json()['upload_url']
        logger.info(f"File uploaded to AssemblyAI: {audio_url}")
        return audio_url

    def _request_transcription(self, audio_url):
        """Request transcription with speaker diarization"""
        transcript_url = "https://api.assemblyai.com/v2/transcript"
        headers = {"authorization": self.api_key}

        request_data = {
            "audio_url": audio_url,
            "speaker_labels": True,  # Enable speaker diarization
            "punctuate": True,
            "format_text": True,
            "language_code": "en"
        }

        response = requests.post(transcript_url, json=request_data, headers=headers)

        if response.status_code != 200:
            raise ValueError(f"AssemblyAI transcription request failed: {response.text}")

        transcript_id = response.json()['id']
        logger.info(f"AssemblyAI job submitted: {transcript_id}")
        return transcript_id

    def _poll_for_results(self, transcript_id):
        """Poll AssemblyAI until transcription is complete"""
        result_url = f"https://api.assemblyai.com/v2/transcript/{transcript_id}"
        headers = {"authorization": self.api_key}

        for i in range(120):  # 10 minutes max (120 * 5 seconds)
            time.sleep(5)

            response = requests.get(result_url, headers=headers)
            result_data = response.json()
            status = result_data.get('status', 'unknown')

            logger.info(f"AssemblyAI Poll {i + 1}: Status = {status}")

            if status == 'completed':
                return result_data
            elif status == 'error':
                error_msg = result_data.get('error', 'Unknown error')
                raise ValueError(f"AssemblyAI processing failed: {error_msg}")

        raise ValueError("AssemblyAI processing timed out")

    def _format_results(self, result_data):
        """Format AssemblyAI results into standardized structure"""
        full_transcript = result_data.get('text', '')

        utterances = []
        speaker_transcript_lines = []
        speakers_found = set()

        if 'utterances' in result_data and result_data['utterances']:
            for utterance in result_data['utterances']:
                speaker = utterance.get('speaker', 'Unknown')
                speakers_found.add(speaker)
                text = utterance.get('text', '')

                if text.strip():
                    speaker_transcript_lines.append(f"{speaker}: {text}")

                    utterances.append({
                        'speaker': speaker,
                        'text': text,
                        'start': utterance.get('start', 0) / 1000,  # Convert ms to seconds
                        'end': utterance.get('end', 0) / 1000,
                        'confidence': utterance.get('confidence', 0)
                    })

        # Rebuild transcript from utterances for consistency
        full_transcript = ' '.join([u['text'] for u in utterances]) if utterances else full_transcript
        duration = result_data.get('audio_duration', 0) / 1000 if result_data.get('audio_duration') else 0

        speaker_data = None
        if utterances:
            speaker_data = {
                'utterances': utterances,
                'speaker_transcript': '\n'.join(speaker_transcript_lines),
                'speakers_detected': len(speakers_found),
                'audio_duration': duration
            }

        return {
            'transcript': full_transcript,
            'speaker_data': speaker_data,
            'duration': duration
        }


def transcribe_call_mp3(mp3_data):
    """
    Transcribe MP3 audio data with speaker diarization.

    Args:
        mp3_data: bytes - Raw MP3 audio data

    Returns:
        dict: Transcription result with speaker data
    """
    service = AssemblyAIService()
    return service.transcribe_mp3(mp3_data)


def analyze_call_with_openai(conversation_text, system_prompt=None):
    """
    Send conversation to OpenAI for analysis.

    Args:
        conversation_text: Formatted conversation string
        system_prompt: System prompt to use (defaults to SERVICETITAN_PROMPT)

    Returns:
        str: AI response or None if failed
    """
    try:
        api_key = getattr(settings, 'OPENAI_API_KEY', None)
        if not api_key:
            logger.error("OPENAI_API_KEY not set")
            return None

        from openai import OpenAI
        client = OpenAI(api_key=api_key)

        if system_prompt is None:
            system_prompt = SERVICETITAN_PROMPT

        user_prompt = f"Please analyze this conversation:\n\n{conversation_text}"

        response = client.chat.completions.create(
            model="gpt-5.2",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            max_completion_tokens=1500,
            temperature=0.3  # Low temperature for consistent output
        )

        ai_response = response.choices[0].message.content
        logger.info(f"OpenAI analysis completed, response length: {len(ai_response)}")

        return ai_response

    except Exception as e:
        logger.error(f"Error calling OpenAI API: {str(e)}")
        return None


def parse_ai_response(analysis, ai_response):
    """
    Parse OpenAI response and extract structured data fields.

    Args:
        analysis: CallAnalysis model instance to update
        ai_response: Raw AI response text
    """
    try:
        analysis.full_ai_response = ai_response

        # Parse ALL sections dynamically
        lines = ai_response.split('\n')
        current_section_name = None
        content_lines = []
        all_fields = {}
        field_order = 0

        # Pattern to match section headers like "SECTION NAME:" or "SECTION_NAME:"
        section_pattern = re.compile(r'^([A-Z][A-Z\s&_-]*):(.*)$')

        for line in lines:
            line_stripped = line.strip()

            if not line_stripped:
                # Empty line - save current section
                if current_section_name and content_lines:
                    content = '\n'.join(content_lines).strip()
                    if content:
                        field_order += 1
                        all_fields[current_section_name] = {
                            'value': content,
                            'order': field_order
                        }
                        _store_legacy_field(analysis, current_section_name, content)

                    current_section_name = None
                    content_lines = []
                continue

            # Check if this line starts a new section
            match = section_pattern.match(line_stripped)
            if match:
                # Save previous section
                if current_section_name and content_lines:
                    content = '\n'.join(content_lines).strip()
                    if content:
                        field_order += 1
                        all_fields[current_section_name] = {
                            'value': content,
                            'order': field_order
                        }
                        _store_legacy_field(analysis, current_section_name, content)

                # Start new section
                current_section_name = match.group(1).strip()
                content_lines = []

                # Check for content on same line as header
                remainder = match.group(2).strip()
                if remainder:
                    content_lines.append(remainder)

            elif current_section_name:
                content_lines.append(line_stripped)

        # Handle last section
        if current_section_name and content_lines:
            content = '\n'.join(content_lines).strip()
            if content:
                field_order += 1
                all_fields[current_section_name] = {
                    'value': content,
                    'order': field_order
                }
                _store_legacy_field(analysis, current_section_name, content)

        # Store structured analysis
        analysis.structured_analysis = {
            'analysis_type': 'customer_service',
            'fields': all_fields,
            'metadata': {
                'parsed_at': timezone.now().isoformat(),
                'field_count': len(all_fields)
            }
        }

        logger.info(f"Parsed {len(all_fields)} fields: {list(all_fields.keys())}")

    except Exception as e:
        logger.error(f"Error parsing AI response: {str(e)}")


def _store_legacy_field(analysis, field_name, content):
    """Store content in legacy fields for backward compatibility"""
    field_name_upper = field_name.upper()

    if 'SUMMARY' in field_name_upper or 'INTERACTION SUMMARY' in field_name_upper:
        analysis.summary = content

    elif field_name_upper == 'TONE':
        analysis.tone = content.split('\n')[0].split('.')[0].strip()

    elif 'IMPORTANCE' in field_name_upper:
        analysis.importance = content.split()[0].lower().strip()

    elif 'CUSTOMER SATISFACTION' in field_name_upper:
        if 'very satisfied' in content.lower():
            analysis.importance = 'important'
        elif 'dissatisfied' in content.lower():
            analysis.importance = 'critical'
        else:
            analysis.importance = 'average'


def process_call_with_ai(session):
    """
    Complete AI analysis pipeline for a ServiceTitan call session.

    Args:
        session: ServiceTitanCallSession instance

    Returns:
        CallAnalysis instance or None
    """
    from .models import CallAnalysis

    try:
        # Check if analysis already exists
        existing = CallAnalysis.objects.filter(session=session).first()
        if existing and existing.analysis_status == 'completed':
            logger.info(f"Analysis already exists for session {session.id}")
            return existing

        # Create or get analysis record
        if existing:
            analysis = existing
        else:
            analysis = CallAnalysis.objects.create(
                session=session,
                analysis_status='processing'
            )

        analysis.analysis_status = 'processing'
        analysis.save()

        # Build conversation text
        # Prefer speaker-aware transcript if available
        conversation_text = ""
        if (session.session_metadata and
                session.session_metadata.get('enhanced_transcripts', {}).get('speaker_aware_transcript')):
            conversation_text = session.session_metadata['enhanced_transcripts']['speaker_aware_transcript']
        else:
            conversation_text = session.full_transcript or ""

        if not conversation_text or len(conversation_text.strip()) < 10:
            analysis.analysis_status = 'error'
            analysis.processing_error = 'Insufficient transcript content'
            analysis.save()
            return None

        # Call OpenAI with ServiceTitan prompt
        ai_response = analyze_call_with_openai(conversation_text, SERVICETITAN_PROMPT)

        if ai_response:
            parse_ai_response(analysis, ai_response)
            analysis.analysis_status = 'completed'
            analysis.processed_at = timezone.now()
        else:
            analysis.analysis_status = 'error'
            analysis.processing_error = 'Failed to get AI response'

        analysis.save()
        return analysis

    except Exception as e:
        logger.error(f"Error processing call with AI: {str(e)}")
        import traceback
        traceback.print_exc()
        return None
