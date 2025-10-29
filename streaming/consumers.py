import json
import asyncio
import assemblyai as aai
from channels.generic.websocket import AsyncWebsocketConsumer
from django.conf import settings
from django.utils.timezone import localtime
from django.utils import timezone
from asgiref.sync import sync_to_async
from datetime import timedelta

from assemblyai.streaming.v3 import (
    BeginEvent,
    StreamingClient,
    StreamingClientOptions,
    StreamingError,
    StreamingEvents,
    StreamingSessionParameters,
    TerminationEvent,
    TurnEvent,
)

from .models import Conversation, Speaker, TranscriptSegment
from .auth_views import get_user_from_token
from .audio_buffer import AudioBuffer


class StreamingConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        print("Ã°Å¸â€Âµ WebSocket connect method called")

        # Get token from query parameters
        query_string = self.scope.get('query_string', b'').decode()
        token = None

        if query_string:
            params = dict(param.split('=') for param in query_string.split('&') if '=' in param)
            token = params.get('token')

        if not token:
            print("Ã¢ÂÅ’ No authentication token provided")
            await self.close()
            return

        # Authenticate user
        self.user = await sync_to_async(get_user_from_token)(token)

        if not self.user:
            print("Ã¢ÂÅ’ Invalid authentication token")
            await self.close()
            return

        print(f"Ã¢Å“â€¦ User authenticated: {self.user.username}")

        try:
            await self.accept()
            print("Ã°Å¸â€Âµ WebSocket accepted")

            # Create conversation record
            self.conversation = await self.create_conversation()
            print(f"Ã°Å¸â€œÂ Conversation created: {self.conversation.id}")

            # Initialize audio buffer
            self.audio_buffer = AudioBuffer(sample_rate=16000, channels=1)
            print("Ã°Å¸Å½Âµ Audio buffer initialized")

            # Store the event loop
            self.loop = asyncio.get_event_loop()
            print("Ã°Å¸â€Âµ Event loop stored")

            # Set API key
            aai.settings.api_key = settings.ASSEMBLYAI_API_KEY
            print("Ã°Å¸â€Âµ API key set")

            # Create streaming client with new v3 API
            self.transcriber = StreamingClient(
                StreamingClientOptions(
                    api_key=settings.ASSEMBLYAI_API_KEY,
                    sample_rate=16000,
                )
            )
            print("Ã°Å¸â€Âµ StreamingClient created")

            # Attach event handlers
            self.transcriber.on(StreamingEvents.Begin, self.on_begin)
            self.transcriber.on(StreamingEvents.Turn, self.on_turn)
            self.transcriber.on(StreamingEvents.Error, self.on_error)
            self.transcriber.on(StreamingEvents.Termination, self.on_terminated)
            print("Ã°Å¸â€Âµ Event handlers attached")

            # Create session parameters
            params = StreamingSessionParameters(
                sample_rate=16000,
                encoding='pcm_s16le',
                enable_extra_session_information=True,
            )
            print("Ã°Å¸â€Âµ Session parameters created")

            # Connect to AssemblyAI with parameters
            await asyncio.to_thread(self.transcriber.connect, params)
            print("Ã°Å¸â€Âµ Connected to AssemblyAI")

            await self.send(text_data=json.dumps({
                'type': 'connection',
                'message': 'Connected to streaming service',
                'conversation_id': str(self.conversation.id)
            }))
            print("Ã°Å¸â€Âµ Connection message sent")

        except Exception as e:
            print(f"Ã¢ÂÅ’ Error in connect: {type(e).__name__}: {str(e)}")
            import traceback
            traceback.print_exc()
            await self.close()

    async def disconnect(self, close_code):
        print(f"Ã°Å¸â€Å’ Disconnecting with code: {close_code}")

        # Upload audio to S3 and process with batch API
        if hasattr(self, 'conversation') and hasattr(self, 'audio_buffer'):
            await self.finalize_conversation()

        print("Ã°Å¸â€Å’ Disconnect complete")

    async def receive(self, text_data=None, bytes_data=None):
        if bytes_data:
            # Add to audio buffer for later S3 upload
            if hasattr(self, 'audio_buffer'):
                self.audio_buffer.add_chunk(bytes_data)

            # Stream audio to AssemblyAI for real-time transcription
            await asyncio.to_thread(self.transcriber.stream, bytes_data)

    def on_begin(self, client, event):
        print(f"Ã°Å¸Å½Â¯ AssemblyAI session started: {event.id}")
        asyncio.run_coroutine_threadsafe(
            self.send(text_data=json.dumps({
                'type': 'session_begin',
                'session_id': event.id
            })),
            self.loop
        )

    def on_turn(self, client, event):
        # Save transcript to database (without speaker yet)
        asyncio.run_coroutine_threadsafe(
            self.save_transcript_segment(event),
            self.loop
        )

        # Check if we should run periodic speaker analysis
        asyncio.run_coroutine_threadsafe(
            self.check_speaker_analysis(),
            self.loop
        )

        # Send to client
        asyncio.run_coroutine_threadsafe(
            self.send(text_data=json.dumps({
                'type': 'transcript',
                'text': event.transcript,
                'is_final': event.end_of_turn,
                'turn_order': event.turn_order
            })),
            self.loop
        )

    def on_error(self, client, error):
        print(f"Ã¢ÂÅ’ AssemblyAI error: {error}")
        asyncio.run_coroutine_threadsafe(
            self.send(text_data=json.dumps({
                'type': 'error',
                'message': str(error)
            })),
            self.loop
        )

    def on_terminated(self, client, event):
        print(f"Ã°Å¸ÂÂ AssemblyAI session terminated - Duration: {event.audio_duration_seconds}s")
        asyncio.run_coroutine_threadsafe(
            self.send(text_data=json.dumps({
                'type': 'terminated',
                'audio_duration': event.audio_duration_seconds
            })),
            self.loop
        )

    # Database operations

    @sync_to_async
    def create_conversation(self):
        """Create a new conversation record"""
        from uuid import uuid4

        conversation = Conversation.objects.create(
            id=str(uuid4()),
            recorded_by=self.user,
            started_at=timezone.now(),
            is_active=True,
        )
        return conversation

    @sync_to_async
    def finalize_conversation(self):
        """Upload audio to S3 and trigger FINAL high-quality batch processing"""
        print(f"Ã°Å¸Å½Â¬ Finalizing conversation {self.conversation.id}")

        # Mark conversation as complete
        self.conversation.is_active = False
        self.conversation.ended_at = timezone.now()

        # Calculate duration
        if self.conversation.started_at and self.conversation.ended_at:
            duration = self.conversation.ended_at - self.conversation.started_at
            self.conversation.duration_seconds = int(duration.total_seconds())

        # Generate default title if none exists
        if not self.conversation.title:
            local_time = localtime(self.conversation.started_at)

            # Format using Python's datetime properties (platform-independent)
            month = local_time.strftime('%b')  # Jan, Feb, etc.
            day = local_time.day  # No leading zero automatically
            year = local_time.year
            hour = local_time.hour % 12 or 12  # Convert 24h to 12h, make 0 -> 12
            minute = local_time.strftime('%M')  # Minutes with leading zero
            am_pm = local_time.strftime('%p')  # AM/PM

            self.conversation.title = f"Conversation from {month} {day}, {year} {hour}:{minute} {am_pm}"

        self.conversation.save()
        print(f"Ã¢Å“â€¦ Conversation {self.conversation.id} marked as complete")

        # Get WAV file from buffer
        wav_data = self.audio_buffer.get_wav_file(apply_preprocessing=False)

        if not wav_data:
            print("Ã¢Å¡Â Ã¯Â¸Â No audio data to upload")
            return

        print(f"Ã°Å¸â€œÂ¦ Audio buffer size: {len(wav_data)} bytes")

        # Upload to S3 with username in path
        from .s3_utils import upload_audio_to_s3, schedule_audio_deletion
        username = self.conversation.recorded_by.username
        s3_url = upload_audio_to_s3(self.conversation.id, wav_data, username, filename='streaming_16k.wav')

        if s3_url:
            self.conversation.streaming_audio_url = s3_url
            self.conversation.audio_uploaded_at = timezone.now()
            # audio_quality remains 'streaming_only' (default) until HQ audio arrives
            self.conversation.save()

            # Schedule deletion based on retention policy
            schedule_audio_deletion(self.conversation)

            self.audio_buffer.clear()

            print("Streaming audio uploaded to S3 as backup/reference.")
            # Note: Batch processing will be triggered when iOS uploads the 44.1kHz audio
        else:
            print("Ã¢ÂÅ’ Failed to upload streaming audio to S3")

    @sync_to_async
    def save_transcript_segment(self, event):
        """Save a transcript segment to the database (speaker will be added later)"""
        try:
            # Only save final transcripts to avoid duplicates
            if not event.end_of_turn:
                return

            # Extract timestamps from words array if available
            start_time = None
            end_time = None
            confidence = None

            if hasattr(event, 'words') and event.words:
                # Get start time from first word
                if len(event.words) > 0 and hasattr(event.words[0], 'start'):
                    start_time = event.words[0].start

                # Get end time from last word
                if len(event.words) > 0 and hasattr(event.words[-1], 'end'):
                    end_time = event.words[-1].end

                # Calculate average confidence from all words
                confidences = [w.confidence for w in event.words if hasattr(w, 'confidence') and w.confidence]
                if confidences:
                    confidence = sum(confidences) / len(confidences)

            # Create transcript segment without speaker (will be added by batch processing)
            segment = TranscriptSegment.objects.create(
                conversation=self.conversation,
                speaker=None,  # Will be assigned after batch processing
                text=event.transcript,
                is_final=True,
                turn_order=event.turn_order if hasattr(event, 'turn_order') else None,
                start_time=start_time,
                end_time=end_time,
                confidence=confidence,
            )

            duration = ""
            if start_time and end_time:
                duration_ms = end_time - start_time
                duration = f" ({duration_ms}ms)"

            print(f"Ã°Å¸â€™Â¾ Saved final transcript{duration}: {event.transcript[:50]}...")

        except Exception as e:
            print(f"Ã¢ÂÅ’ Error saving transcript: {e}")
            import traceback
            traceback.print_exc()


    @sync_to_async
    def check_speaker_analysis(self):
        """
        Check if it's time to run PERIODIC speaker analysis.
        Run at 2 minutes, then every 15 minutes.
        Uses faster settings for ongoing monitoring.
        """
        from .ai_utils import should_run_speaker_analysis_v2

        # Only run if conversation is still active and has audio uploaded
        if not self.conversation.is_active or not self.conversation.audio_url:
            return

        # Check timing
        if should_run_speaker_analysis_v2(self.conversation):
            print(f"Ã¢ÂÂ° Running PERIODIC speaker analysis for conversation {self.conversation.id}")
            from .batch_processing import process_conversation_with_batch_api
            import threading

            analysis_thread = threading.Thread(
                target=process_conversation_with_batch_api,
                args=(self.conversation.id,),
                kwargs={'is_final': False}  # Use faster settings for periodic analysis
            )
            analysis_thread.start()