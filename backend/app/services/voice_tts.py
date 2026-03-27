from __future__ import annotations

import asyncio
import logging

import boto3

from app.config import get_settings

logger = logging.getLogger(__name__)


async def synthesize_pcm_audio(*, text: str, sample_rate_hz: int, voice_id: str) -> bytes:
    """Synthesize assistant text to raw PCM16 mono using Amazon Polly.

    Falls back to short silence if synthesis fails.
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
            return b""
        return audio_stream.read()

    try:
        return await asyncio.to_thread(_call_polly)
    except Exception as exc:
        logger.warning("polly_tts_failed reason=%s", str(exc))
        # 250ms of silence as a transport-safe fallback.
        silence_samples = int(sample_rate_hz * 0.25)
        return b"\x00\x00" * silence_samples
