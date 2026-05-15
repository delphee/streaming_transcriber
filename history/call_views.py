"""
Admin views for ServiceTitan call import, transcription, and analysis.

Endpoints:
- GET /call-admin/import-call/ - Render the call import form
- POST /call-admin/search-calls/ - Search for calls by datetime
- POST /call-admin/import-call/ - Import and process a call
- GET /call-admin/call-status/<uuid:session_id>/ - Check processing status
"""

from django.views import View
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator
from django.shortcuts import render
from django.core.files.base import ContentFile
from django.db import connection
from django.utils import timezone
import json
import logging
import threading

from .models import ServiceTitanCallSession, CallAudioChunk, CallAnalysis
from .st_api import get_calls_by_timerange, download_call_recording

logger = logging.getLogger(__name__)


@method_decorator(csrf_exempt, name='dispatch')
class CallSearchView(View):
    """Search for ServiceTitan calls within a time range"""

    def post(self, request):
        """Search for calls with recordings in a 1-minute window"""
        try:
            data = json.loads(request.body)
            datetime_str = data.get('datetime', '').strip()

            if not datetime_str:
                return JsonResponse({
                    'success': False,
                    'error': 'DateTime is required'
                }, status=400)

            logger.info(f"Searching for ServiceTitan calls at {datetime_str}")

            # Get calls from ServiceTitan
            calls = get_calls_by_timerange(datetime_str)

            # Check if any calls were already imported
            for call in calls:
                call_id = call.get('id')
                if call_id:
                    existing = ServiceTitanCallSession.objects.filter(
                        session_name__contains=f"Call {call_id}"
                    ).exists()
                    call['already_imported'] = existing
                else:
                    call['already_imported'] = False

            return JsonResponse({
                'success': True,
                'calls': calls,
                'total_found': len(calls),
                'message': f'Found {len(calls)} calls with recordings'
            })

        except json.JSONDecodeError:
            return JsonResponse({'success': False, 'error': 'Invalid JSON'}, status=400)
        except Exception as e:
            logger.error(f"Call search error: {str(e)}")
            return JsonResponse({'success': False, 'error': str(e)}, status=500)


@method_decorator(csrf_exempt, name='dispatch')
class CallImportView(View):
    """Import and process call recordings from ServiceTitan"""

    def get(self, request):
        """Render the call import form"""
        return render(request, 'history/call_import.html')

    def post(self, request):
        """Process call import request"""
        try:
            data = json.loads(request.body)
            call_id = data.get('call_id', '').strip()
            duration = data.get('duration', '')
            received_on = data.get('received_on', '')
            agent_name = data.get('agent_name', '')
            customer_name = data.get('customer_name', '')
            direction = data.get('direction', '')
            from_number = data.get('from_number', '')
            to_number = data.get('to_number', '')

            if not call_id:
                return JsonResponse({'success': False, 'error': 'Call ID required'}, status=400)

            # Check if already imported
            if ServiceTitanCallSession.objects.filter(session_name__contains=f"Call {call_id}").exists():
                return JsonResponse({
                    'success': False,
                    'error': f'Call {call_id} has already been imported'
                }, status=400)

            # Download recording
            mp3_data = download_call_recording(call_id)

            # Create session and start processing
            session = self._process_call_recording(
                call_id, mp3_data, duration, received_on,
                agent_name, customer_name, direction,
                from_number, to_number
            )

            return JsonResponse({
                'success': True,
                'message': f'Call {call_id} imported successfully',
                'session_id': str(session.id),
                'total_chunks': session.chunks.count()
            })

        except json.JSONDecodeError:
            return JsonResponse({'success': False, 'error': 'Invalid JSON'}, status=400)
        except Exception as e:
            logger.error(f"Call import error: {str(e)}")
            return JsonResponse({'success': False, 'error': str(e)}, status=500)

    def _process_call_recording(self, call_id, mp3_data, duration=None, received_on=None,
                                 agent_name=None, customer_name=None, direction=None,
                                 from_number=None, to_number=None):
        """Process ServiceTitan call recording"""

        # Parse duration string (format: "00:05:32" -> seconds)
        duration_seconds = None
        if duration and ':' in str(duration):
            try:
                parts = str(duration).split(':')
                if len(parts) == 3:  # HH:MM:SS
                    duration_seconds = int(parts[0]) * 3600 + int(parts[1]) * 60 + int(float(parts[2]))
                elif len(parts) == 2:  # MM:SS
                    duration_seconds = int(parts[0]) * 60 + int(float(parts[1]))
            except ValueError:
                logger.warning(f"Could not parse duration: {duration}")

        # Create ServiceTitanCallSession
        session = ServiceTitanCallSession.objects.create(
            session_name=f"ServiceTitan Call {call_id}",
            processing_status='downloading',
            is_completed=False,
            session_metadata={
                'source': 'servicetitan',
                'call_id': call_id,
                'original_duration': duration,
                'duration_seconds': duration_seconds,
                'actual_call_datetime': received_on,
                'agent_name': agent_name,
                'customer_name': customer_name,
                'direction': direction,
                'from_number': from_number,
                'to_number': to_number
            }
        )

        # Store MP3 as single chunk
        chunk = CallAudioChunk.objects.create(
            session=session,
            chunk_order=0,
            audio_file=ContentFile(mp3_data, name=f'call_{call_id}.mp3'),
            file_size=len(mp3_data),
            status='uploaded'
        )

        session.processing_status = 'transcribing'
        session.save()

        # Start background processing
        thread = threading.Thread(
            target=self._process_chunk_background,
            args=(chunk.id,),
            daemon=True
        )
        thread.start()

        return session

    def _process_chunk_background(self, chunk_id):
        """Background thread to process transcription and AI analysis"""
        try:
            from .call_transcription import transcribe_call_mp3, process_call_with_ai

            chunk = CallAudioChunk.objects.get(id=chunk_id)
            chunk.status = 'processing'
            chunk.save()

            session = chunk.session

            # Read the audio file
            chunk.audio_file.seek(0)
            mp3_data = chunk.audio_file.read()

            # Transcribe with AssemblyAI
            result = transcribe_call_mp3(mp3_data)

            # Update chunk
            chunk.transcript_text = result['transcript'].strip()
            chunk.speaker_segments = result['speaker_data']
            chunk.status = 'transcribed'
            chunk.processed_at = timezone.now()

            # Use ServiceTitan duration from metadata if available
            if (session.session_metadata and
                    session.session_metadata.get('duration_seconds')):
                chunk.duration_seconds = session.session_metadata['duration_seconds']
            elif result.get('duration'):
                chunk.duration_seconds = result['duration']

            chunk.save()

            # Update session
            session.full_transcript = result['transcript'].strip()
            session.processing_status = 'analyzing'

            # Build enhanced speaker-aware transcript
            self._build_enhanced_transcript(session)

            session.save()

            # Run AI analysis
            process_call_with_ai(session)

            # Mark as completed
            session.processing_status = 'completed'
            session.is_completed = True
            session.completed_at = timezone.now()
            session.save()

            logger.info(f"Successfully processed call {chunk_id}")

        except Exception as e:
            logger.error(f"Error processing chunk: {str(e)}")
            import traceback
            traceback.print_exc()
            try:
                chunk = CallAudioChunk.objects.get(id=chunk_id)
                chunk.status = 'error'
                chunk.processing_error = str(e)
                chunk.save()

                session = chunk.session
                session.processing_status = 'error'
                session.processing_error = str(e)
                session.save()
            except:
                pass
        finally:
            connection.close()

    def _build_enhanced_transcript(self, session):
        """Build speaker-aware transcript and store in metadata"""
        chunks = session.chunks.filter(status='transcribed').order_by('chunk_order')

        speaker_lines = []
        for chunk in chunks:
            if chunk.speaker_segments and chunk.speaker_segments.get('speaker_transcript'):
                speaker_lines.append(chunk.speaker_segments['speaker_transcript'])
            elif chunk.transcript_text:
                speaker_lines.append(f"[Speaker]: {chunk.transcript_text}")

        if not session.session_metadata:
            session.session_metadata = {}

        session.session_metadata['enhanced_transcripts'] = {
            'speaker_aware_transcript': '\n'.join(speaker_lines),
            'transcript_updated_at': timezone.now().isoformat()
        }


@method_decorator(csrf_exempt, name='dispatch')
class CallProcessingStatusView(View):
    """Check the processing status of an imported call"""

    def get(self, request, session_id):
        try:
            session = ServiceTitanCallSession.objects.get(id=session_id)

            status_data = {
                'session_id': str(session.id),
                'session_name': session.session_name,
                'transcription_status': session.processing_status,
                'chunks_processed': session.chunks.filter(status='transcribed').count(),
                'total_chunks': session.chunks.count(),
                'ai_analysis_status': 'pending',
                'overall_status': 'processing'
            }

            # Check AI analysis
            try:
                analysis = session.ai_analysis
                status_data['ai_analysis_status'] = analysis.analysis_status

                if analysis.analysis_status == 'completed':
                    status_data['ai_results'] = {
                        'summary': analysis.summary,
                        'tone': analysis.tone,
                        'importance': analysis.importance
                    }

                    # Include structured analysis if available
                    if analysis.structured_analysis:
                        status_data['ai_results']['structured_analysis'] = analysis.structured_analysis
            except CallAnalysis.DoesNotExist:
                pass

            # Determine overall status
            if session.processing_status == 'error':
                status_data['overall_status'] = 'error'
                status_data['error'] = session.processing_error
            elif (session.processing_status == 'completed' and
                  status_data['ai_analysis_status'] == 'completed'):
                status_data['overall_status'] = 'completed'
            else:
                status_data['overall_status'] = 'processing'

            return JsonResponse({'success': True, 'status': status_data})

        except ServiceTitanCallSession.DoesNotExist:
            return JsonResponse({'success': False, 'error': 'Session not found'}, status=404)


@method_decorator(csrf_exempt, name='dispatch')
class CallDetailView(View):
    """Get full details of an imported call including transcript and analysis"""

    def get(self, request, session_id):
        try:
            session = ServiceTitanCallSession.objects.get(id=session_id)

            detail_data = {
                'session_id': str(session.id),
                'session_name': session.session_name,
                'created_at': session.created_at.isoformat(),
                'processing_status': session.processing_status,
                'metadata': session.session_metadata,
                'full_transcript': session.full_transcript,
            }

            # Get speaker-aware transcript
            if (session.session_metadata and
                    session.session_metadata.get('enhanced_transcripts', {}).get('speaker_aware_transcript')):
                detail_data['speaker_transcript'] = session.session_metadata['enhanced_transcripts']['speaker_aware_transcript']

            # Get AI analysis
            try:
                analysis = session.ai_analysis
                detail_data['analysis'] = {
                    'status': analysis.analysis_status,
                    'summary': analysis.summary,
                    'tone': analysis.tone,
                    'importance': analysis.importance,
                    'structured_analysis': analysis.structured_analysis,
                    'full_response': analysis.full_ai_response
                }
            except CallAnalysis.DoesNotExist:
                detail_data['analysis'] = None

            return JsonResponse({'success': True, 'data': detail_data})

        except ServiceTitanCallSession.DoesNotExist:
            return JsonResponse({'success': False, 'error': 'Session not found'}, status=404)
