import io
import wave
import numpy as np


class AudioBuffer:
    """Buffer to accumulate audio chunks for S3 upload with audio preprocessing"""

    def __init__(self, sample_rate=16000, channels=1):
        self.sample_rate = sample_rate
        self.channels = channels
        self.audio_chunks = []
        self.total_bytes = 0

    def add_chunk(self, audio_bytes):
        """Add an audio chunk to the buffer"""
        self.audio_chunks.append(audio_bytes)
        self.total_bytes += len(audio_bytes)

    def get_wav_file(self, apply_preprocessing=True):
        """
        Combine all chunks into a WAV file in memory.

        Args:
            apply_preprocessing: If True, applies noise reduction and normalization.
                                If False, returns raw audio without processing.

        Returns bytes of the WAV file
        """
        if not self.audio_chunks:
            return None

        # Combine all audio chunks
        combined_audio = b''.join(self.audio_chunks)

        # Apply audio preprocessing if requested
        if apply_preprocessing:
            try:
                print("🎧 Applying audio preprocessing...")
                combined_audio = self._apply_preprocessing(combined_audio)
                print("✅ Audio preprocessing complete")
            except Exception as e:
                print(f"⚠️ Audio preprocessing failed, using original audio: {e}")
                # If preprocessing fails, continue with original audio
                pass
        else:
            print("⏭️ Skipping audio preprocessing (raw audio)")

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

    # ============================================================================
    # AUDIO PREPROCESSING - ENABLED
    # ============================================================================

    def _apply_preprocessing(self, audio_data):
        """
        Apply audio preprocessing to improve transcription quality.

        Steps:
        1. Convert raw PCM bytes to numpy array
        2. Apply noise reduction to remove background noise
        3. Normalize volume to standard broadcast level
        4. Convert back to PCM bytes

        Args:
            audio_data: Raw PCM audio bytes (16-bit, 16kHz, mono)

        Returns:
            Preprocessed audio bytes
        """
        # Convert bytes to numpy array (16-bit signed integers)
        audio_array = np.frombuffer(audio_data, dtype=np.int16)

        # Convert to float for processing (-1.0 to 1.0 range)
        audio_float = audio_array.astype(np.float32) / 32768.0

        # Step 1: Noise reduction
        print("  ðŸ”‡ Applying noise reduction...")
        audio_float = self._noise_reduction(audio_float)

        # Step 2: Volume normalization (broadcast standard)
        print("  ðŸ“Š Normalizing volume...")
        audio_float = self._normalize_volume(audio_float)

        # Convert back to 16-bit integers
        audio_array = (audio_float * 32768.0).astype(np.int16)

        # Convert back to bytes
        return audio_array.tobytes()

    def _noise_reduction(self, audio_float):
        """
        Remove background noise using spectral gating.

        Uses noisereduce library which analyzes the audio spectrum
        and removes consistent background noise while preserving speech.

        Args:
            audio_float: Audio as float32 numpy array (-1.0 to 1.0)

        Returns:
            Noise-reduced audio as float32 numpy array
        """
        try:
            import noisereduce as nr

            # Apply stationary noise reduction
            # stationary=True assumes background noise is relatively constant
            # prop_decrease controls how aggressive the reduction is (0.0-1.0)
            reduced = nr.reduce_noise(
                y=audio_float,
                sr=self.sample_rate,
                stationary=True,
                prop_decrease=0.8,  # Remove 80% of detected noise
            )

            return reduced

        except ImportError:
            print("  âš ï¸ noisereduce not installed, skipping noise reduction")
            return audio_float
        except Exception as e:
            print(f"  âš ï¸ Noise reduction failed: {e}")
            return audio_float

    def _normalize_volume(self, audio_float):
        """
        Normalize audio volume to broadcast standard level.

        Uses pyloudnorm for ITU-R BS.1770-4 compliant loudness normalization.
        This ensures all speakers have similar perceived loudness, which helps
        AssemblyAI distinguish between different voices.

        Target: -20 LUFS (Loudness Units relative to Full Scale)
        This is a good level for speech - loud enough but not distorted.

        Args:
            audio_float: Audio as float32 numpy array (-1.0 to 1.0)

        Returns:
            Normalized audio as float32 numpy array
        """
        try:
            import pyloudnorm as pyln

            # Create a meter to measure loudness
            meter = pyln.Meter(self.sample_rate)

            # Measure the current loudness
            try:
                loudness = meter.integrated_loudness(audio_float)
            except ValueError:
                # Audio might be too quiet to measure
                print("  âš ï¸ Audio too quiet to measure loudness, applying basic normalization")
                return self._basic_normalization(audio_float)

            # Normalize to -20 LUFS (good level for speech)
            target_loudness = -20.0

            # Only normalize if the audio isn't already close to target
            if abs(loudness - target_loudness) > 1.0:  # More than 1 LUFS difference
                normalized = pyln.normalize.loudness(audio_float, loudness, target_loudness)

                # Prevent clipping - ensure values stay in valid range
                normalized = np.clip(normalized, -1.0, 1.0)

                print(f"  ðŸ“ˆ Normalized from {loudness:.1f} to {target_loudness:.1f} LUFS")
                return normalized
            else:
                print(f"  âœ“ Already at good loudness level ({loudness:.1f} LUFS)")
                return audio_float

        except ImportError:
            print("  âš ï¸ pyloudnorm not installed, using basic normalization")
            return self._basic_normalization(audio_float)
        except Exception as e:
            print(f"  âš ï¸ Loudness normalization failed: {e}, using basic normalization")
            return self._basic_normalization(audio_float)

    def _basic_normalization(self, audio_float):
        """
        Fallback: Basic peak normalization if pyloudnorm isn't available.

        Scales audio so the loudest point reaches -3dB (0.707 in linear scale).
        This leaves some headroom to prevent distortion.

        Args:
            audio_float: Audio as float32 numpy array (-1.0 to 1.0)

        Returns:
            Normalized audio as float32 numpy array
        """
        # Find the peak (loudest point)
        peak = np.abs(audio_float).max()

        # Only normalize if there's actual audio
        if peak > 0.001:  # More than silence
            # Target peak at -3dB (0.707 in linear scale) to leave headroom
            target_peak = 0.707
            gain = target_peak / peak

            # Apply gain but limit to prevent over-amplification
            gain = min(gain, 10.0)  # Max 10x amplification

            normalized = audio_float * gain

            # Clip to valid range
            normalized = np.clip(normalized, -1.0, 1.0)

            print(f"  ðŸ“ˆ Applied peak normalization (gain: {gain:.2f}x)")
            return normalized
        else:
            print("  âš ï¸ Audio is silent, skipping normalization")
            return audio_float

    # ============================================================================
    # ADDITIONAL PREPROCESSING PLACEHOLDERS
    # Currently disabled - can enable if needed for further improvement
    # ============================================================================

    def _high_pass_filter(self, audio_float):
        """
        Apply high-pass filter to remove very low frequencies.
        Human speech is typically 85-255 Hz, so we can filter below ~80 Hz.

        This removes rumble, handling noise, and other sub-speech sounds.

        Args:
            audio_float: Audio as float32 numpy array (-1.0 to 1.0)

        Returns:
            Filtered audio as float32 numpy array
        """
        try:
            from scipy import signal

            # Design a Butterworth high-pass filter
            # Cutoff at 80 Hz (below typical speech)
            # Order 5 for sharp rolloff
            nyquist = self.sample_rate / 2
            cutoff = 80  # Hz
            normalized_cutoff = cutoff / nyquist

            # Create filter coefficients
            b, a = signal.butter(5, normalized_cutoff, btype='high')

            # Apply filter
            filtered = signal.filtfilt(b, a, audio_float)

            return filtered

        except ImportError:
            print("  âš ï¸ scipy not installed, skipping high-pass filter")
            return audio_float
        except Exception as e:
            print(f"  âš ï¸ High-pass filter failed: {e}")
            return audio_float

    def _dynamic_range_compression(self, audio_float):
        """
        Compress dynamic range to make quiet speakers more audible
        and prevent loud speakers from dominating.

        This is like the "compression" effect in music production - it makes
        quiet parts louder and loud parts quieter, resulting in more even volume.

        Args:
            audio_float: Audio as float32 numpy array (-1.0 to 1.0)

        Returns:
            Compressed audio as float32 numpy array
        """
        # Simple compression implementation
        # More sophisticated compression could use envelope following

        threshold = 0.3  # Compress signals above this level
        ratio = 4.0  # Compression ratio (4:1)

        # Create a copy for output
        compressed = audio_float.copy()

        # Find samples above threshold
        above_threshold = np.abs(audio_float) > threshold

        # Apply compression to samples above threshold
        for i in range(len(audio_float)):
            if above_threshold[i]:
                # Calculate how much we're over threshold
                excess = np.abs(audio_float[i]) - threshold
                # Apply compression ratio
                compressed_excess = excess / ratio
                # Rebuild the signal
                sign = np.sign(audio_float[i])
                compressed[i] = sign * (threshold + compressed_excess)

        return compressed