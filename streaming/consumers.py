import json
import asyncio
import assemblyai as aai
from channels.generic.websocket import AsyncWebsocketConsumer
from django.conf import settings
from assemblyai.streaming.v3 import (
    BeginEvent,
    StreamingClient,
    StreamingClientOptions,
    StreamingError,
    StreamingEvents,
    StreamingParameters,
    StreamingSessionParameters,
    TerminationEvent,
    TurnEvent,
)


class StreamingConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        print("WebSocket connect method called")
        await self.accept()
        print("WebSocket accepted")
        # Store the event loop
        self.loop = asyncio.get_event_loop()

        # Set API key
        aai.settings.api_key = settings.ASSEMBLYAI_API_KEY

        # Create streaming client with new v3 API
        self.transcriber = StreamingClient(
            StreamingClientOptions(
                api_key=settings.ASSEMBLYAI_API_KEY,
                sample_rate=16000,
            )
        )

        # Attach event handlers
        self.transcriber.on(StreamingEvents.Begin, self.on_begin)
        self.transcriber.on(StreamingEvents.Turn, self.on_turn)
        self.transcriber.on(StreamingEvents.Error, self.on_error)
        self.transcriber.on(StreamingEvents.Termination, self.on_terminated)

        # Create session parameters
        params = StreamingSessionParameters(
            sample_rate=16000,
            encoding='pcm_s16le',
        )

        # Connect to AssemblyAI with parameters
        await asyncio.to_thread(self.transcriber.connect, params)

        await self.send(text_data=json.dumps({
            'type': 'connection',
            'message': 'Connected to streaming service'
        }))

    async def disconnect(self, close_code):
        if hasattr(self, 'transcriber'):
            self.transcriber = None

    async def receive(self, text_data=None, bytes_data=None):
        if bytes_data:
            # Stream audio to AssemblyAI
            await asyncio.to_thread(self.transcriber.stream, bytes_data)

    def on_begin(self, client, event: BeginEvent):
        asyncio.run_coroutine_threadsafe(
            self.send(text_data=json.dumps({
                'type': 'session_begin',
                'session_id': event.id
            })),
            self.loop
        )

    def on_turn(self, client, event: TurnEvent):
        asyncio.run_coroutine_threadsafe(
            self.send(text_data=json.dumps({
                'type': 'transcript',
                'text': event.transcript,
                'is_final': event.end_of_turn,
                'turn_order': event.turn_order
            })),
            self.loop
        )

    def on_error(self, client, error: StreamingError):
        asyncio.run_coroutine_threadsafe(
            self.send(text_data=json.dumps({
                'type': 'error',
                'message': str(error)
            })),
            self.loop
        )

    def on_terminated(self, client, event: TerminationEvent):
        asyncio.run_coroutine_threadsafe(
            self.send(text_data=json.dumps({
                'type': 'terminated',
                'audio_duration': event.audio_duration_seconds
            })),
            self.loop
        )