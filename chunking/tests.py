"""
Tests for chunking app.

These tests mock the OpenAI client so they never hit the network and don't
require a real API key.
"""
import json
import os
import tempfile
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

from django.conf import settings
from django.contrib.auth.models import User
from django.test import TestCase

from chunking import transcription
from chunking.models import (
    ChunkedConversation,
    Speaker,
    TranscriptSegment,
)
from streaming.models import AnalysisPrompt, UserProfile


def _fake_chat_response(content):
    """Build a minimal object that looks like an OpenAI chat completion."""
    message = SimpleNamespace(content=content)
    choice = SimpleNamespace(message=message)
    return SimpleNamespace(choices=[choice])


def _make_conversation(user, **extra):
    defaults = {
        "id": "conv-test-1",
        "recorded_by": user,
        "full_transcript": "Speaker A: Hi, this is Sam.\nSpeaker B: Hey Sam, I'm Pat.",
    }
    defaults.update(extra)
    return ChunkedConversation.objects.create(**defaults)


class IdentifySpeakersWithAITests(TestCase):
    """Verify speaker-identification call uses the right model and parameters."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="sam", first_name="Sam", last_name="Tech"
        )
        UserProfile.objects.create(user=self.user)
        self.conversation = _make_conversation(self.user)
        self.speaker = Speaker.objects.create(
            conversation=self.conversation, speaker_label="Speaker A"
        )
        Speaker.objects.create(
            conversation=self.conversation, speaker_label="Speaker B"
        )
        TranscriptSegment.objects.create(
            conversation=self.conversation,
            speaker=self.speaker,
            text="Hi, this is Sam.",
            start_time=0,
            end_time=2000,
        )
        TranscriptSegment.objects.create(
            conversation=self.conversation,
            speaker=Speaker.objects.get(speaker_label="Speaker B", conversation=self.conversation),
            text="Hey Sam, I'm Pat.",
            start_time=2000,
            end_time=4000,
        )

    def _batched_response(self, speakers):
        """Build a fake batched-shape JSON response (one entry per speaker)."""
        return _fake_chat_response(json.dumps({"speakers": speakers}))

    @patch("chunking.transcription.get_openai_client")
    def test_makes_a_single_batched_call(self, mock_client_factory):
        client = MagicMock()
        client.chat.completions.create.return_value = self._batched_response([
            {
                "speaker_label": "Speaker A",
                "identified_name": "Sam Tech",
                "is_recording_user": True,
                "confidence": "high",
                "reasoning": "Self-introduction.",
            },
            {
                "speaker_label": "Speaker B",
                "identified_name": "Pat",
                "is_recording_user": False,
                "confidence": "high",
                "reasoning": "Introduced as Pat.",
            },
        ])
        mock_client_factory.return_value = client

        transcription.identify_speakers_with_ai(self.conversation)

        # One batched call regardless of speaker count — used to be N calls.
        self.assertEqual(client.chat.completions.create.call_count, 1)
        kwargs = client.chat.completions.create.call_args.kwargs
        self.assertEqual(kwargs["model"], settings.OPENAI_SPEAKER_ID_MODEL)
        # GPT-5.x reasoning models reject non-default temperature.
        self.assertNotIn("temperature", kwargs)
        self.assertEqual(
            kwargs.get("reasoning_effort"),
            settings.OPENAI_SPEAKER_ID_REASONING_EFFORT,
        )
        self.assertIn("max_completion_tokens", kwargs)
        self.assertNotIn(
            "max_tokens", kwargs,
            "GPT-5 family uses max_completion_tokens, not max_tokens.",
        )

    @patch("chunking.transcription.get_openai_client")
    def test_prompt_contains_chronological_dialogue_with_labels(self, mock_client_factory):
        # The whole point of batching is cross-speaker reasoning, which only
        # works if the prompt shows the conversation chronologically with
        # speaker labels intact.
        client = MagicMock()
        client.chat.completions.create.return_value = self._batched_response([])
        mock_client_factory.return_value = client

        transcription.identify_speakers_with_ai(self.conversation)

        sent_prompt = client.chat.completions.create.call_args.kwargs["messages"][1]["content"]
        # Both labels appear, with their lines, in the prompt.
        self.assertIn("Speaker A: Hi, this is Sam.", sent_prompt)
        self.assertIn("Speaker B: Hey Sam, I'm Pat.", sent_prompt)

    @patch("chunking.transcription.get_openai_client")
    def test_applies_identifications_per_label(self, mock_client_factory):
        client = MagicMock()
        client.chat.completions.create.return_value = self._batched_response([
            {
                "speaker_label": "Speaker A",
                "identified_name": "Sam Tech",
                "is_recording_user": True,
                "confidence": "high",
                "reasoning": "Self-introduces as Sam.",
            },
            {
                "speaker_label": "Speaker B",
                "identified_name": "Pat",
                "is_recording_user": False,
                "confidence": "high",
                "reasoning": "Introduced as Pat.",
            },
        ])
        mock_client_factory.return_value = client

        transcription.identify_speakers_with_ai(self.conversation)

        speaker_a = Speaker.objects.get(
            conversation=self.conversation, speaker_label="Speaker A"
        )
        speaker_b = Speaker.objects.get(
            conversation=self.conversation, speaker_label="Speaker B"
        )
        self.assertEqual(speaker_a.identified_name, "Sam Tech")
        self.assertTrue(speaker_a.is_recording_user)
        self.assertEqual(speaker_b.identified_name, "Pat")
        self.assertFalse(speaker_b.is_recording_user)

    @patch("chunking.transcription.get_openai_client")
    def test_recording_user_substring_fallback(self, mock_client_factory):
        # If the model forgets to set is_recording_user but the identified
        # name matches the recording user's name, the server-side fallback
        # should still flag them.
        client = MagicMock()
        client.chat.completions.create.return_value = self._batched_response([
            {
                "speaker_label": "Speaker A",
                "identified_name": "Sam Tech",
                "is_recording_user": False,  # AI got it wrong
                "confidence": "high",
                "reasoning": "...",
            },
        ])
        mock_client_factory.return_value = client

        transcription.identify_speakers_with_ai(self.conversation)

        speaker_a = Speaker.objects.get(
            conversation=self.conversation, speaker_label="Speaker A"
        )
        self.assertTrue(speaker_a.is_recording_user)

    @patch("chunking.transcription.get_openai_client")
    def test_unknown_speaker_label_is_ignored(self, mock_client_factory):
        # A hallucinated label shouldn't raise or corrupt other speakers.
        client = MagicMock()
        client.chat.completions.create.return_value = self._batched_response([
            {
                "speaker_label": "Speaker Z",  # doesn't exist
                "identified_name": "Mystery",
                "confidence": "high",
                "reasoning": "...",
            },
            {
                "speaker_label": "Speaker A",
                "identified_name": "Sam Tech",
                "is_recording_user": True,
                "confidence": "high",
                "reasoning": "...",
            },
        ])
        mock_client_factory.return_value = client

        transcription.identify_speakers_with_ai(self.conversation)

        speaker_a = Speaker.objects.get(
            conversation=self.conversation, speaker_label="Speaker A"
        )
        self.assertEqual(speaker_a.identified_name, "Sam Tech")

    @patch("chunking.transcription.get_openai_client")
    def test_no_client_skips_silently(self, mock_client_factory):
        mock_client_factory.return_value = None
        # Should not raise.
        transcription.identify_speakers_with_ai(self.conversation)


class AnalyzeConversationTests(TestCase):
    """Verify the conversation-analysis call uses the right model and parameters."""

    def setUp(self):
        self.user = User.objects.create_user(username="sam")
        UserProfile.objects.create(user=self.user)
        self.conversation = _make_conversation(self.user)

    @patch("chunking.transcription.get_openai_client")
    def test_uses_expected_model_and_params(self, mock_client_factory):
        client = MagicMock()
        client.chat.completions.create.return_value = _fake_chat_response(
            json.dumps({
                "summary": "A brief friendly intro.",
                "action_items": [],
                "key_topics": ["greeting"],
                "sentiment": "neutral",
                "coaching_feedback": "Good start.",
            })
        )
        mock_client_factory.return_value = client

        transcription.analyze_conversation(self.conversation)

        client.chat.completions.create.assert_called_once()
        kwargs = client.chat.completions.create.call_args.kwargs
        self.assertEqual(kwargs["model"], settings.OPENAI_ANALYSIS_MODEL)
        # GPT-5.x reasoning models reject non-default temperature.
        self.assertNotIn("temperature", kwargs)
        self.assertEqual(
            kwargs.get("reasoning_effort"),
            settings.OPENAI_ANALYSIS_REASONING_EFFORT,
        )
        self.assertIn("max_completion_tokens", kwargs)
        self.assertNotIn(
            "max_tokens", kwargs,
            "GPT-5 family uses max_completion_tokens, not max_tokens.",
        )

    @patch("chunking.transcription.get_openai_client")
    def test_writes_summary_and_clears_error(self, mock_client_factory):
        client = MagicMock()
        client.chat.completions.create.return_value = _fake_chat_response(
            json.dumps({
                "summary": "Customer asked about pricing.",
                "action_items": ["Send quote"],
                "key_topics": ["pricing", "scheduling"],
                "sentiment": "positive",
                "coaching_feedback": "Great rapport.",
            })
        )
        mock_client_factory.return_value = client
        self.conversation.analysis_error = "previous failure"
        self.conversation.save()

        transcription.analyze_conversation(self.conversation)
        self.conversation.refresh_from_db()

        self.assertIn("Customer asked about pricing", self.conversation.summary)
        self.assertEqual(self.conversation.action_items, ["Send quote"])
        self.assertEqual(self.conversation.key_topics, ["pricing", "scheduling"])
        self.assertEqual(self.conversation.sentiment, "positive")
        self.assertEqual(self.conversation.analysis_error, "")

    @patch("chunking.transcription.get_openai_client")
    def test_prefers_formatted_transcript_when_available(self, mock_client_factory):
        # When both fields are populated, the speaker-labeled
        # formatted_transcript should be sent — it's strictly more useful
        # for coaching analysis than the unlabeled raw text.
        self.conversation.full_transcript = "Hi there. Hi back."
        self.conversation.formatted_transcript = (
            "[0:00] Sam Tech: Hi there.\n\n[0:02] Customer: Hi back."
        )
        self.conversation.save()

        client = MagicMock()
        client.chat.completions.create.return_value = _fake_chat_response(
            json.dumps({"summary": "ok"})
        )
        mock_client_factory.return_value = client

        transcription.analyze_conversation(self.conversation)

        sent_prompt = client.chat.completions.create.call_args.kwargs["messages"][1]["content"]
        self.assertIn("Sam Tech: Hi there.", sent_prompt)
        self.assertIn("Customer: Hi back.", sent_prompt)

    @patch("chunking.transcription.get_openai_client")
    def test_falls_back_to_full_transcript_when_no_formatted(self, mock_client_factory):
        # If diarization didn't populate the formatted transcript, the
        # unlabeled AssemblyAI text is still better than nothing.
        self.conversation.full_transcript = "Hi there. Hi back."
        self.conversation.formatted_transcript = ""
        self.conversation.save()

        client = MagicMock()
        client.chat.completions.create.return_value = _fake_chat_response(
            json.dumps({"summary": "ok"})
        )
        mock_client_factory.return_value = client

        transcription.analyze_conversation(self.conversation)

        sent_prompt = client.chat.completions.create.call_args.kwargs["messages"][1]["content"]
        self.assertIn("Hi there. Hi back.", sent_prompt)

    @patch("chunking.transcription.get_openai_client")
    def test_full_transcript_is_sent_without_truncation(self, mock_client_factory):
        # Simulate a long sales call (~12 000 chars). The historical bug sliced
        # the transcript at 8 000 chars, cutting off the close of the call.
        long_transcript = "Speaker A: Word word word word. " * 400
        self.assertGreater(len(long_transcript), 8000)
        self.conversation.full_transcript = long_transcript
        self.conversation.save()

        client = MagicMock()
        client.chat.completions.create.return_value = _fake_chat_response(
            json.dumps({"summary": "ok"})
        )
        mock_client_factory.return_value = client

        transcription.analyze_conversation(self.conversation)

        sent_prompt = client.chat.completions.create.call_args.kwargs["messages"][1]["content"]
        # The full transcript should appear verbatim in the user message.
        self.assertIn(long_transcript, sent_prompt)

    @patch("chunking.transcription.get_openai_client")
    def test_records_error_when_response_is_invalid_json(self, mock_client_factory):
        client = MagicMock()
        client.chat.completions.create.return_value = _fake_chat_response("not json")
        mock_client_factory.return_value = client

        transcription.analyze_conversation(self.conversation)
        self.conversation.refresh_from_db()

        self.assertNotEqual(self.conversation.analysis_error, "")
        self.assertEqual(self.conversation.summary, "")


class TranscribeWithWhisperTests(TestCase):
    """
    Verify the Whisper handler reads timestamped segments correctly.

    In openai>=1.x, response.segments contains Pydantic TranscriptionSegment
    objects. The previous implementation called segment.get('start', 0),
    which raises AttributeError on Pydantic models — the bare except
    swallowed it and the conversation lost the Whisper transcript silently.
    These tests pin attribute-access behavior so the regression can't return.
    """

    def setUp(self):
        self.user = User.objects.create_user(username="sam")
        UserProfile.objects.create(user=self.user)
        self.conversation = _make_conversation(self.user)
        # transcribe_with_whisper opens the file before calling the SDK.
        fd, self.audio_path = tempfile.mkstemp(suffix=".flac")
        os.close(fd)

    def tearDown(self):
        if os.path.exists(self.audio_path):
            os.remove(self.audio_path)

    def _whisper_response(self, text, segments):
        """Mimic the openai>=1.x verbose_json shape: typed object, attributes only."""
        seg_objs = [SimpleNamespace(**s) for s in segments]
        return SimpleNamespace(text=text, segments=seg_objs)

    @patch("chunking.transcription.get_openai_client")
    def test_segments_use_attribute_access_not_dict_get(self, mock_client_factory):
        """If the code reverts to segment.get(...), this test fails because
        SimpleNamespace doesn't expose .get() — same as a Pydantic model."""
        client = MagicMock()
        client.audio.transcriptions.create.return_value = self._whisper_response(
            text="Hello there. How are you?",
            segments=[
                {"start": 0.0, "text": "Hello there."},
                {"start": 65.4, "text": " How are you?"},
            ],
        )
        mock_client_factory.return_value = client

        result = transcription.transcribe_with_whisper(self.conversation, self.audio_path)

        self.assertEqual(result, "Hello there. How are you?")
        self.conversation.refresh_from_db()
        self.assertEqual(self.conversation.whisper_transcript, "Hello there. How are you?")
        self.assertIn("[0:00] Hello there.", self.conversation.whisper_formatted_transcript)
        self.assertIn("[1:05] How are you?", self.conversation.whisper_formatted_transcript)

    @patch("chunking.transcription.get_openai_client")
    def test_request_uses_verbose_json_with_segment_timestamps(self, mock_client_factory):
        client = MagicMock()
        client.audio.transcriptions.create.return_value = self._whisper_response(
            text="x", segments=[{"start": 0, "text": "x"}],
        )
        mock_client_factory.return_value = client

        transcription.transcribe_with_whisper(self.conversation, self.audio_path)

        kwargs = client.audio.transcriptions.create.call_args.kwargs
        self.assertEqual(kwargs["model"], settings.OPENAI_TRANSCRIPTION_MODEL)
        self.assertEqual(kwargs["response_format"], "verbose_json")
        self.assertEqual(kwargs["timestamp_granularities"], ["segment"])

    @patch("chunking.transcription.get_openai_client")
    def test_falls_back_to_raw_text_when_no_segments(self, mock_client_factory):
        client = MagicMock()
        client.audio.transcriptions.create.return_value = SimpleNamespace(
            text="Plain transcript with no segments.",
            segments=None,
        )
        mock_client_factory.return_value = client

        result = transcription.transcribe_with_whisper(self.conversation, self.audio_path)

        self.assertEqual(result, "Plain transcript with no segments.")
        self.conversation.refresh_from_db()
        self.assertEqual(
            self.conversation.whisper_formatted_transcript,
            "Plain transcript with no segments.",
        )

    @patch("chunking.transcription.get_openai_client")
    def test_skips_empty_segment_text(self, mock_client_factory):
        client = MagicMock()
        client.audio.transcriptions.create.return_value = self._whisper_response(
            text="A. B.",
            segments=[
                {"start": 0.0, "text": "A."},
                {"start": 1.0, "text": "   "},  # whitespace-only — should be dropped
                {"start": 2.0, "text": "B."},
            ],
        )
        mock_client_factory.return_value = client

        transcription.transcribe_with_whisper(self.conversation, self.audio_path)
        self.conversation.refresh_from_db()
        formatted = self.conversation.whisper_formatted_transcript
        self.assertIn("A.", formatted)
        self.assertIn("B.", formatted)
        # Two non-empty segments → exactly one separator between them.
        self.assertEqual(formatted.count("\n\n"), 1)

    @patch("chunking.transcription.get_openai_client")
    def test_no_client_returns_none(self, mock_client_factory):
        mock_client_factory.return_value = None
        result = transcription.transcribe_with_whisper(self.conversation, self.audio_path)
        self.assertIsNone(result)


class RunWhisperComparisonTests(TestCase):
    """
    The Whisper comparison pass should run only when the conversation's
    transcription_service_preference asks for it. Default is 'assemblyai',
    so it's off by default.
    """

    def setUp(self):
        self.user = User.objects.create_user(username="sam")
        UserProfile.objects.create(user=self.user)

    @patch("chunking.transcription.transcribe_with_whisper")
    @patch("chunking.transcription.download_audio_for_whisper")
    def test_skipped_when_preference_is_assemblyai(self, mock_download, mock_whisper):
        conv = _make_conversation(
            self.user, transcription_service_preference="assemblyai"
        )
        transcription.run_whisper_comparison(conv)
        mock_download.assert_not_called()
        mock_whisper.assert_not_called()

    @patch("chunking.transcription.transcribe_with_whisper")
    @patch("chunking.transcription.download_audio_for_whisper")
    def test_skipped_when_preference_is_whisper_only(self, mock_download, mock_whisper):
        # 'whisper' alone isn't a real mode in this pipeline (analysis needs
        # AssemblyAI's diarized transcript), so it should not trigger the
        # comparison pass either — only the explicit 'both' opts in.
        conv = _make_conversation(
            self.user, transcription_service_preference="whisper"
        )
        transcription.run_whisper_comparison(conv)
        mock_download.assert_not_called()
        mock_whisper.assert_not_called()

    @patch("chunking.transcription.os.path.exists", return_value=False)
    @patch("chunking.transcription.transcribe_with_whisper", return_value="hello")
    @patch("chunking.transcription.download_audio_for_whisper", return_value="/tmp/fake.flac")
    def test_runs_when_preference_is_both(self, mock_download, mock_whisper, _):
        conv = _make_conversation(
            self.user, transcription_service_preference="both"
        )
        transcription.run_whisper_comparison(conv)
        mock_download.assert_called_once_with(conv)
        mock_whisper.assert_called_once_with(conv, "/tmp/fake.flac")

    @patch("chunking.transcription.transcribe_with_whisper")
    @patch("chunking.transcription.download_audio_for_whisper", return_value=None)
    def test_returns_quietly_when_download_fails(self, mock_download, mock_whisper):
        conv = _make_conversation(
            self.user, transcription_service_preference="both"
        )
        transcription.run_whisper_comparison(conv)
        mock_download.assert_called_once()
        mock_whisper.assert_not_called()


class ChunkedConversationDefaultsTests(TestCase):
    """Lock in that new conversations default to AssemblyAI-only."""

    def test_default_preference_is_assemblyai(self):
        user = User.objects.create_user(username="sam")
        UserProfile.objects.create(user=user)
        conv = ChunkedConversation.objects.create(id="c-default", recorded_by=user)
        self.assertEqual(conv.transcription_service_preference, "assemblyai")


class OpenAIModelSettingsTests(TestCase):
    """Lock in that every OpenAI call site reads its model from settings,
    so flipping a model is a one-line config change rather than a grep."""

    def test_all_model_settings_exist_and_are_nonempty(self):
        for name in (
            "OPENAI_ANALYSIS_MODEL",
            "OPENAI_SPEAKER_ID_MODEL",
            "OPENAI_PROMPT_OPTIMIZER_MODEL",
            "OPENAI_VOICE_ASSISTANT_MODEL",
            "OPENAI_TTS_MODEL",
            "OPENAI_TRANSCRIPTION_MODEL",
        ):
            value = getattr(settings, name, None)
            self.assertIsNotNone(value, f"{name} missing from settings")
            self.assertTrue(value, f"{name} is empty")


class GetOpenAIClientTests(TestCase):
    """The SDK client should be configured with retries for transient errors."""

    def setUp(self):
        # The factory is lru_cache'd, so flush before each test so we observe
        # the next OpenAI(...) construction.
        transcription.get_openai_client.cache_clear()

    def tearDown(self):
        transcription.get_openai_client.cache_clear()

    @patch("chunking.transcription.OpenAI")
    def test_client_constructed_with_max_retries(self, mock_openai_cls):
        with self.settings(OPENAI_API_KEY="sk-test"):
            transcription.get_openai_client()

        mock_openai_cls.assert_called_once()
        kwargs = mock_openai_cls.call_args.kwargs
        self.assertEqual(kwargs.get("max_retries"), 3)
        self.assertEqual(kwargs.get("api_key"), "sk-test")


class FormatAnalysisAsTextTests(TestCase):
    """Pure-function tests for the JSON->human-readable formatter."""

    def test_flat_string_fields_render_as_label_value(self):
        result = transcription.format_analysis_as_text({
            "summary": "Short summary.",
            "sentiment": "neutral",
        })
        self.assertIn("Summary: Short summary.", result)
        self.assertIn("Sentiment: neutral", result)

    def test_long_string_field_breaks_to_its_own_lines(self):
        long_text = "x" * 150
        result = transcription.format_analysis_as_text({"summary": long_text})
        self.assertIn("Summary:", result)
        self.assertIn(long_text, result)

    def test_score_evidence_dict_is_formatted_naturally(self):
        result = transcription.format_analysis_as_text({
            "rapport": {"score": 4, "evidence": "Warm tone throughout."}
        })
        self.assertIn("Rapport:", result)
        self.assertIn("Score: 4", result)
        self.assertIn("Warm tone throughout.", result)

    def test_list_of_strings_renders_as_bullets(self):
        result = transcription.format_analysis_as_text({
            "action_items": ["Send invoice", "Schedule follow-up"],
        })
        self.assertIn("Action Items:", result)
        self.assertIn("• Send invoice", result)
        self.assertIn("• Schedule follow-up", result)

    def test_empty_list_is_omitted(self):
        result = transcription.format_analysis_as_text({"action_items": []})
        self.assertNotIn("Action Items", result)

    def test_includes_prompt_name_when_provided(self):
        prompt = AnalysisPrompt.objects.create(
            name="Sales QA",
            plain_text="Look at sales calls.",
            optimized_prompt="...",
        )
        result = transcription.format_analysis_as_text(
            {"summary": "ok"}, prompt=prompt
        )
        self.assertIn("Analysis: Sales QA", result)
