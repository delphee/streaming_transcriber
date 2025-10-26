import json
import asyncio
import assemblyai as aai
from channels.generic.websocket import AsyncWebsocketConsumer
from django.conf import settings
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


class StreamingConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        print("🔵 WebSocket connect method called")

        # Get token from query parameters
        query_string = self.scope.get('query_string', b'').decode()
        token = None

        if query_string:
            params = dict(param.split('=') for param in query_string.split('&') if '=' in param)
            token = params.get('token')

        if not token:
            print("❌ No authentication token provided")
            await self.close()
            return

        # Authenticate user
        self.user = await sync_to_async(get_user_from_token)(token)

        if not self.user:
            print("❌ Invalid authentication token")
            await self.close()
            return

        print(f"✅ User authenticated: {self.user.username}")

        try:
            await self.accept()
            print("🔵 WebSocket accepted")

            # Create conversation record
            self.conversation = await self.create_conversation()
            print(f"📝 Conversation created: {self.conversation.id}")

            # Store the event loop
            self.loop = asyncio.get_event_loop()
            print("🔵 Event loop stored")

            # Set API key
            aai.settings.api_key = settings.ASSEMBLYAI_API_KEY
            print("🔵 API key set")

            # Create streaming client with new v3 API
            self.transcriber = StreamingClient(
                StreamingClientOptions(
                    api_key=settings.ASSEMBLYAI_API_KEY,
                    sample_rate=16000,
                )
            )
            print("🔵 StreamingClient created")

            # Attach event handlers
            self.transcriber.on(StreamingEvents.Begin, self.on_begin)
            self.transcriber.on(StreamingEvents.Turn, self.on_turn)
            self.transcriber.on(StreamingEvents.Error, self.on_error)
            self.transcriber.on(StreamingEvents.Termination, self.on_terminated)
            print("🔵 Event handlers attached")

            # Create session parameters with speaker diarization
            params = StreamingSessionParameters(
                sample_rate=16000,
                encoding='pcm_s16le',
                enable_extra_session_information=True,  # Enable speaker diarization
            )
            print("🔵 Session parameters created")

            # Connect to AssemblyAI with parameters
            await asyncio.to_thread(self.transcriber.connect, params)
            print("🔵 Connected to AssemblyAI")

            await self.send(text_data=json.dumps({
                'type': 'connection',
                'message': 'Connected to streaming service',
                'conversation_id': str(self.conversation.id)
            }))
            print("🔵 Connection message sent")

        except Exception as e:
            print(f"❌ Error in connect: {type(e).__name__}: {str(e)}")
            import traceback
            traceback.print_exc()
            await self.close()

    async def disconnect(self, close_code):
        print(f"🔌 Disconnecting with code: {close_code}")

        # Mark conversation as complete
        if hasattr(self, 'conversation'):
            await self.complete_conversation()

        # WebSocket will close automatically
        print("🔌 Disconnect complete")

    async def receive(self, text_data=None, bytes_data=None):
        if bytes_data:
            # Stream audio to AssemblyAI
            await asyncio.to_thread(self.transcriber.stream, bytes_data)

    def on_begin(self, client, event):
        print(f"🎯 AssemblyAI session started: {event.id}")
        asyncio.run_coroutine_threadsafe(
            self.send(text_data=json.dumps({
                'type': 'session_begin',
                'session_id': event.id
            })),
            self.loop
        )

    def on_turn(self, client, event):
        # Save transcript to database
        asyncio.run_coroutine_threadsafe(
            self.save_transcript_segment(event),
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
        print(f"❌ AssemblyAI error: {error}")
        asyncio.run_coroutine_threadsafe(
            self.send(text_data=json.dumps({
                'type': 'error',
                'message': str(error)
            })),
            self.loop
        )

    def on_terminated(self, client, event):
        print(f"🏁 AssemblyAI session terminated - Duration: {event.audio_duration_seconds}s")
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
    def complete_conversation(self):
        """Mark conversation as complete and calculate duration"""
        self.conversation.is_active = False
        self.conversation.ended_at = timezone.now()

        # Calculate duration
        if self.conversation.started_at and self.conversation.ended_at:
            duration = self.conversation.ended_at - self.conversation.started_at
            self.conversation.duration_seconds = int(duration.total_seconds())

        # Generate default title if none exists
        if not self.conversation.title:
            self.conversation.title = f"Conversation from {self.conversation.started_at.strftime('%b %d, %Y %I:%M %p')}"

        self.conversation.save()
        print(f"✅ Conversation {self.conversation.id} marked as complete")

    @sync_to_async
    def save_transcript_segment(self, event):
        """Save a transcript segment to the database"""
        try:
            # Only save final transcripts to avoid duplicates
            if not event.end_of_turn:
                return

            # Get or create speaker
            speaker = None
            if hasattr(event, 'speaker_label') and event.speaker_label:
                speaker, created = Speaker.objects.get_or_create(
                    conversation=self.conversation,
                    speaker_label=event.speaker_label,
                    defaults={
                        'identified_name': '',
                        'is_recording_user': False,
                    }
                )
                if created:
                    print(f"👤 New speaker created: {event.speaker_label}")

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

            # Create transcript segment
            segment = TranscriptSegment.objects.create(
                conversation=self.conversation,
                speaker=speaker,
                text=event.transcript,
                is_final=True,  # Always true now since we filter above
                turn_order=event.turn_order if hasattr(event, 'turn_order') else None,
                start_time=start_time,
                end_time=end_time,
                confidence=confidence,
            )

            duration = ""
            if start_time and end_time:
                duration_ms = end_time - start_time
                duration = f" ({duration_ms}ms)"

            print(f"💾 Saved final transcript{duration}: {event.transcript[:50]}...")

        except Exception as e:
            print(f"❌ Error saving transcript: {e}")
            import traceback
            traceback.print_exc()