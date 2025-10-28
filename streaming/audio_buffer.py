import io
import wave
import numpy as np


class AudioBuffer:
    """Buffer to accumulate audio chunks for S3 upload with audio preprocessing"""

    def __init__(self, sample_rate=16000, channels=1, enable_preprocessing=True):
        self.sample_rate = sample_rate
        self.channels = channels
        self.audio_chunks = []
        self.total_bytes = 0
        self.enable_preprocessing = enable_preprocessing

    def add_chunk(self, audio_bytes):
        """Add an audio chunk to the buffer"""
        self.audio_chunks.append(audio_bytes)
        self.total_bytes += len(audio_bytes)

    def get_wav_file(self):
        """
        Combine all chunks into a WAV file in memory with optional preprocessing.
        Returns bytes of the WAV file.
        """
        if not self.audio_chunks:
            return None

        # Combine all audio chunks
        combined_audio = b''.join(self.audio_chunks)

        # Apply preprocessing if enabled
        if self.enable_preprocessing:
            try:
                combined_audio = self._apply_preprocessing(combined_audio)
            except Exception as e:
                print(f"⚠️ Audio preprocessing failed: {e}. Using original audio.")
                import traceback
                traceback.print_exc()

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
    # MODERN AUDIO PREPROCESSING IMPLEMENTATION
    # Using state-of-the-art packages: noisereduce, pyloudnorm, numpy, soundfile
    # ============================================================================

    def _apply_preprocessing(self, audio_data):
        """
        Apply modern audio preprocessing to improve speaker diarization.

        Pipeline:
        1. Convert bytes to numpy array
        2. Noise reduction (spectral gating)
        3. Loudness normalization (ITU-R BS.1770-4 standard)
        4. Convert back to bytes

        Args:
            audio_data: Raw PCM audio bytes (16-bit, mono)

        Returns:
            Preprocessed audio bytes
        """
        print("🎵 Applying audio preprocessing...")

        # Convert bytes to numpy array (16-bit signed integers)
        audio_array = np.frombuffer(audio_data, dtype=np.int16)

        # Convert to float32 for processing (normalize to -1.0 to 1.0 range)
        audio_float = audio_array.astype(np.float32) / 32768.0

        # Step 1: Noise reduction
        audio_float = self._noise_reduction(audio_float)

        # Step 2: Loudness normalization
        audio_float = self._normalize_loudness(audio_float)

        # Convert back to int16
        audio_array = (audio_float * 32768.0).astype(np.int16)

        # Convert back to bytes
        processed_bytes = audio_array.tobytes()

        print(f"✅ Audio preprocessing complete: {len(audio_data)} → {len(processed_bytes)} bytes")
        return processed_bytes

    def _noise_reduction(self, audio_float):
        """
        Modern noise reduction using spectral gating.
        Uses noisereduce library (v3.0+) - state-of-the-art for speech.

        Args:
            audio_float: Audio as numpy float32 array (-1.0 to 1.0)

        Returns:
            Noise-reduced audio as float32 array
        """
        try:
            import noisereduce as nr

            print("  🔇 Applying noise reduction (spectral gating)...")

            # Use stationary noise reduction (good for constant background noise)
            # prop_decrease: How much to reduce noise (0.0 = none, 1.0 = all)
            # We use 0.8 to be aggressive but not destroy speech
            reduced = nr.reduce_noise(
                y=audio_float,
                sr=self.sample_rate,
                stationary=True,
                prop_decrease=0.8,  # Aggressive noise reduction
                freq_mask_smooth_hz=500,  # Smooth frequency masking
                time_mask_smooth_ms=50,  # Smooth time masking
            )

            print("  ✅ Noise reduction complete")
            return reduced

        except ImportError:
            print("  ⚠️ noisereduce not installed, skipping noise reduction")
            print("     Install with: pip install noisereduce")
            return audio_float
        except Exception as e:
            print(f"  ⚠️ Noise reduction failed: {e}, using original audio")
            return audio_float

    def _normalize_loudness(self, audio_float):
        """
        Loudness normalization using ITU-R BS.1770-4 standard.
        Uses pyloudnorm - broadcast/cinema industry standard.

        This makes all speakers similar volume, helping distinguish voices
        when there's no pause between speakers.

        Args:
            audio_float: Audio as numpy float32 array (-1.0 to 1.0)

        Returns:
            Normalized audio as float32 array
        """
        try:
            import pyloudnorm as pyln

            print("  📊 Applying loudness normalization (ITU-R BS.1770-4)...")

            # Create loudness meter
            meter = pyln.Meter(self.sample_rate)

            # Measure current loudness
            current_loudness = meter.integrated_loudness(audio_float)

            # Target loudness for speech: -16 LUFS (Loudness Units Full Scale)
            # This is between broadcast standard (-23 LUFS) and podcast standard (-16 to -19 LUFS)
            target_loudness = -16.0

            # Normalize to target loudness
            normalized = pyln.normalize.loudness(audio_float, current_loudness, target_loudness)

            # Clip any values that might have exceeded the range
            normalized = np.clip(normalized, -1.0, 1.0)

            print(f"  ✅ Normalized: {current_loudness:.1f} LUFS → {target_loudness:.1f} LUFS")
            return normalized

        except ImportError:
            print("  ⚠️ pyloudnorm not installed, skipping normalization")
            print("     Install with: pip install pyloudnorm")
            return audio_float
        except Exception as e:
            print(f"  ⚠️ Loudness normalization failed: {e}, using original audio")
            return audio_float

    # ============================================================================
    # OPTIONAL: Additional preprocessing methods (currently not used)
    # ============================================================================

    def _high_pass_filter(self, audio_float):
        """
        Apply high-pass filter to remove very low frequencies.
        Human speech is typically 85-255 Hz, so filter below 80 Hz.

        Uses scipy.signal - scientific computing standard.
        """
        try:
            from scipy import signal

            # Design butterworth high-pass filter
            # Cutoff: 80 Hz, Order: 5 (sharp rolloff)
            nyquist = self.sample_rate / 2
            cutoff = 80 / nyquist  # Normalize frequency

            b, a = signal.butter(5, cutoff, btype='high')

            # Apply filter
            filtered = signal.filtfilt(b, a, audio_float)

            return filtered

        except ImportError:
            print("  ⚠️ scipy not installed, skipping high-pass filter")
            return audio_float
        except Exception as e:
            print(f"  ⚠️ High-pass filter failed: {e}")
            return audio_float

    def _dynamic_range_compression(self, audio_float, threshold=-20, ratio=4):
        """
        Simple dynamic range compression.
        Makes quiet speakers louder, loud speakers quieter.

        Args:
            audio_float: Audio array
            threshold: Threshold in dB (above this gets compressed)
            ratio: Compression ratio (4:1 is typical)
        """
        try:
            # Convert to dB
            epsilon = 1e-10  # Avoid log(0)
            audio_db = 20 * np.log10(np.abs(audio_float) + epsilon)

            # Apply compression above threshold
            mask = audio_db > threshold
            compressed_db = audio_db.copy()
            compressed_db[mask] = threshold + (audio_db[mask] - threshold) / ratio

            # Convert back to linear
            compressed = np.sign(audio_float) * np.power(10, compressed_db / 20)

            # Normalize to prevent clipping
            max_val = np.max(np.abs(compressed))
            if max_val > 1.0:
                compressed = compressed / max_val

            return compressed

        except Exception as e:
            print(f"  ⚠️ Compression failed: {e}")
            return audio_float