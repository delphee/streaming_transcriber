import io
import wave


class AudioBuffer:
    """Buffer to accumulate audio chunks for S3 upload"""

    def __init__(self, sample_rate=16000, channels=1):
        self.sample_rate = sample_rate
        self.channels = channels
        self.audio_chunks = []
        self.total_bytes = 0

    def add_chunk(self, audio_bytes):
        """Add an audio chunk to the buffer"""
        self.audio_chunks.append(audio_bytes)
        self.total_bytes += len(audio_bytes)

    def get_wav_file(self):
        """
        Combine all chunks into a WAV file in memory
        Returns bytes of the WAV file
        """
        if not self.audio_chunks:
            return None

        # Combine all audio chunks
        combined_audio = b''.join(self.audio_chunks)

        # Create WAV file in memory
        wav_buffer = io.BytesIO()

        with wave.open(wav_buffer, 'wb') as wav_file:
            wav_file.setnchannels(self.channels)
            wav_file.setsampwidth(2)  # 16-bit PCM
            wav_file.setframerate(self.sample_rate)
            wav_file.writeframes(combined_audio)

        wav_buffer.seek(0)
        return wav_buffer.read()

    def clear(self):
        """Clear the buffer"""
        self.audio_chunks = []
        self.total_bytes = 0