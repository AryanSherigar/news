from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

import boto3

from app.config import get_settings

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class VoiceTtsError(Exception):
    """Structured Polly synthesis failure details."""

    code: str
    detail: str
    fallback_audio: bytes | None = None

    def __str__(self) -> str:
        return self.detail


async def synthesize_pcm_audio(*, text: str, sample_rate_hz: int, voice_id: str) -> bytes:
    """Synthesize assistant text to raw PCM16 mono using Amazon Polly.

    Raises VoiceTtsError with structured detail if synthesis fails.
    """

    if not text.strip():
        return b""

    settings = get_settings()

    def _call_polly() -> bytes:
        polly = boto3.client("polly", region_name=settings.aws_region)
        response = polly.synthesize_speech(
            Engine="neural",
            LanguageCode="en-US",
            OutputFormat="pcm",
            SampleRate=str(sample_rate_hz),
            Text=text,
            VoiceId=voice_id,
        )
        audio_stream = response.get("AudioStream")
        if audio_stream is None:
            raise VoiceTtsError(
                code="audio_stream_missing",
                detail="TTS provider returned no audio stream",
            )
        return audio_stream.read()

    try:
        return await asyncio.to_thread(_call_polly)
    except VoiceTtsError:
        raise
    except Exception as exc:
        logger.warning("polly_tts_failed reason=%s", str(exc))
        # 250ms of silence as a transport-safe fallback.
        silence_samples = int(sample_rate_hz * 0.25)
        raise VoiceTtsError(
            code="polly_synthesis_failed",
            detail="Amazon Polly failed to synthesize audio",
            fallback_audio=b"\x00\x00" * silence_samples,
        ) from exc
