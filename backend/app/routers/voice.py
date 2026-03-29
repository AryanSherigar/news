import asyncio
import audioop
import json
import logging
import time
import uuid
from collections.abc import AsyncGenerator
from contextlib import suppress
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.config import get_settings
from app.schemas import ChatAnswer
from app.services.ai_orchestration import generate_topic_voice_response
from app.services.response_refresh import refresh_answer_with_fresh_news_if_needed
from app.services.source_validator import (
    SourcePolicyViolationError,
    validate_chat_sources_or_raise,
    violations_to_response_payload,
)
from app.services.voice_tts import VoiceTtsError, synthesize_pcm_audio

router = APIRouter(prefix="/api", tags=["voice"])
logger = logging.getLogger(__name__)


class StreamingSpeechPipeline:
    """Lightweight streaming speech pipeline with VAD-style segmentation.

    This is an STT-equivalent fallback that turns binary PCM ingress into
    interim/final transcript events and end-of-utterance boundaries.
    """

    def __init__(
        self,
        *,
        sample_rate_hz: int,
        silence_timeout_ms: int,
        max_silence_ms: int,
        partial_timeout_ms: int,
        reconnect_backoff_ms: int,
    ) -> None:
        self.sample_rate_hz = sample_rate_hz
        self.silence_timeout_ms = max(200, silence_timeout_ms)
        self.max_silence_ms = max(self.silence_timeout_ms, max_silence_ms)
        self.partial_timeout_ms = max(500, partial_timeout_ms)
        self.reconnect_backoff_ms = max(100, reconnect_backoff_ms)

        self._speaking = False
        self._speech_bytes = 0
        self._speech_started_at = 0.0
        self._last_audio_at = 0.0
        self._last_partial_at = 0.0
        self._interim_seq = 0
        self._session_started_at = time.monotonic()

    async def reconnect(self) -> None:
        # Placeholder for external STT reconnection (AWS Transcribe Streaming/etc).
        await asyncio.sleep(self.reconnect_backoff_ms / 1000)

    def _is_voiced(self, chunk: bytes) -> bool:
        if not chunk:
            return False
        try:
            return audioop.rms(chunk, 2) >= 180
        except audioop.error:
            return False

    def ingest_audio(self, chunk: bytes) -> list[dict[str, Any]]:
        now = time.monotonic()
        events: list[dict[str, Any]] = []
        self._last_audio_at = now

        if not chunk:
            return events

        if self._is_voiced(chunk):
            if not self._speaking:
                self._speaking = True
                self._speech_bytes = 0
                self._speech_started_at = now
                self._interim_seq = 0

            self._speech_bytes += len(chunk)
            if now - self._last_partial_at >= self.partial_timeout_ms / 1000:
                self._interim_seq += 1
                events.append(
                    {
                        "type": "user_interim",
                        "text": f"[listening… {self._speech_bytes} bytes]",
                        "is_final": False,
                        "segment_seq": self._interim_seq,
                    }
                )
                self._last_partial_at = now

            return events

        if self._speaking:
            duration_ms = int((now - self._speech_started_at) * 1000)
            transcript = f"[voice utterance {duration_ms}ms/{self._speech_bytes} bytes]"
            events.append(
                {
                    "type": "user_final",
                    "text": transcript,
                    "is_final": True,
                    "end_of_utterance": True,
                    "duration_ms": duration_ms,
                }
            )
            self._speaking = False
            self._speech_bytes = 0
            self._interim_seq = 0

        return events

    def guardrail_events(self) -> list[dict[str, Any]]:
        now = time.monotonic()
        events: list[dict[str, Any]] = []

        if self._last_audio_at and (now - self._last_audio_at) * 1000 >= self.max_silence_ms:
            events.append(
                {
                    "type": "voice_guardrail",
                    "code": "max_silence",
                    "detail": "No incoming audio detected within max silence window",
                }
            )
            self._last_audio_at = now

        if self._speaking and self._last_partial_at and (now - self._last_partial_at) * 1000 >= self.partial_timeout_ms:
            events.append(
                {
                    "type": "voice_guardrail",
                    "code": "partial_timeout",
                    "detail": "No partial transcript update within timeout",
                }
            )
            self._last_partial_at = now

        if (now - self._session_started_at) * 1000 >= 30 * 60 * 1000:
            events.append(
                {
                    "type": "voice_guardrail",
                    "code": "reconnect",
                    "detail": "Refreshing streaming transcription session",
                }
            )
            self._session_started_at = now

        return events



def _build_story_context(topic: str, timeline_slice: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "topic": topic,
        "timeline": timeline_slice,
    }


async def _stream_answer(answer: ChatAnswer) -> AsyncGenerator[dict[str, Any], None]:
    words = answer.message.split()
    if not words:
        words = [answer.message]

    for idx, word in enumerate(words):
        suffix = " " if idx < len(words) - 1 else ""
        yield {"type": "assistant_delta", "delta": f"{word}{suffix}"}
        await asyncio.sleep(0.01)


def _current_millis() -> int:
    return int(time.time() * 1000)


def _latency_ms(start_perf: float, end_perf: float | None = None) -> float:
    now = end_perf if end_perf is not None else time.perf_counter()
    return max(0.0, (now - start_perf) * 1000.0)


def _classify_disconnect(code: int | None) -> str:
    if code in (1000, 1001):
        return "client_closed"
    if code is None:
        return "unknown"
    return f"close_code_{code}"


@router.websocket("/voice/chat")
async def voice_chat(websocket: WebSocket) -> None:
    await websocket.accept()

    settings = get_settings()
    if not settings.voice_duplex_enabled:
        await websocket.send_json(
            {
                "type": "error",
                "status": 503,
                "detail": "Duplex voice is currently disabled",
            }
        )
        await websocket.close(code=1013)
        return

    sample_rate_hz = settings.voice_sample_rate_hz
    audio_chunk_bytes = settings.voice_output_chunk_bytes

    session_id = str(uuid.uuid4())
    session_started_at = time.perf_counter()
    topic = ""
    timeline_slice: list[dict[str, Any]] = []
    history: list[dict[str, str]] = []
    turn_counter = 0
    disconnect_reason = "unknown"
    audio_ingress_bytes = 0
    audio_ingress_chunks = 0

    speech_pipeline = StreamingSpeechPipeline(
        sample_rate_hz=sample_rate_hz,
        silence_timeout_ms=settings.voice_stt_silence_timeout_ms,
        max_silence_ms=settings.voice_stt_max_silence_ms,
        partial_timeout_ms=settings.voice_stt_partial_timeout_ms,
        reconnect_backoff_ms=settings.voice_stt_reconnect_backoff_ms,
    )

    send_lock = asyncio.Lock()
    active_turn_id: str | None = None
    canceled_turn_ids: set[str] = set()
    active_turn_task: asyncio.Task[None] | None = None

    async def send_json(payload: dict[str, Any]) -> None:
        async with send_lock:
            await websocket.send_json(payload)

    async def send_bytes(payload: bytes) -> None:
        async with send_lock:
            await websocket.send_bytes(payload)

    async def cancel_active_turn(reason: str) -> None:
        nonlocal active_turn_task, active_turn_id
        had_active_turn = bool(active_turn_id) or (active_turn_task is not None and not active_turn_task.done())
        if active_turn_id:
            canceled_turn_ids.add(active_turn_id)

        if active_turn_task and not active_turn_task.done():
            active_turn_task.cancel()
            with suppress(asyncio.CancelledError):
                await active_turn_task

        active_turn_task = None
        active_turn_id = None

        if had_active_turn:
            await send_json(
                {
                    "type": "barge_in_ack",
                    "session_id": session_id,
                    "reason": reason,
                    "timestamp_ms": _current_millis(),
                }
            )

    async def run_turn(turn_id: str, user_message: str, turn_history: list[dict[str, str]], story_context: dict[str, Any]) -> None:
        nonlocal history, active_turn_id
        turn_started_at = time.perf_counter()
        first_text_delta_at: float | None = None
        first_audio_chunk_at: float | None = None

        try:
            answer = await generate_topic_voice_response(
                topic=topic,
                message=user_message,
                history=turn_history,
                story_context=story_context,
            )
            answer = await refresh_answer_with_fresh_news_if_needed(
                topic=topic,
                message=user_message,
                history=turn_history,
                story_context=story_context,
                answer=answer,
                generate_response=generate_topic_voice_response,
            )
            validate_chat_sources_or_raise(answer)

            async for event in _stream_answer(answer):
                if turn_id in canceled_turn_ids:
                    return

                if event.get("type") == "assistant_delta" and first_text_delta_at is None:
                    first_text_delta_at = time.perf_counter()

                await send_json(
                    {
                        **event,
                        "session_id": session_id,
                        "turn_id": turn_id,
                        "timestamp_ms": _current_millis(),
                    }
                )

            if turn_id in canceled_turn_ids:
                return

            try:
                pcm_bytes = await synthesize_pcm_audio(
                    text=answer.message,
                    sample_rate_hz=sample_rate_hz,
                    voice_id=settings.voice_tts_voice_id,
                )
            except VoiceTtsError as exc:
                logger.warning(
                    "voice_tts_failed session_id=%s turn_id=%s code=%s detail=%s",
                    session_id,
                    turn_id,
                    exc.code,
                    exc.detail,
                )
                await send_json(
                    {
                        "type": "error",
                        "status": 502,
                        "session_id": session_id,
                        "turn_id": turn_id,
                        "code": "assistant_voice_unavailable",
                        "detail": {
                            "message": "Assistant voice unavailable. Showing text response instead.",
                            "tts_code": exc.code,
                            "tts_detail": exc.detail,
                            "action": "Continue reading the assistant text response and retry voice later.",
                        },
                        "timestamp_ms": _current_millis(),
                    }
                )
                pcm_bytes = exc.fallback_audio or b""

            await send_json(
                {
                    "type": "assistant_audio_start",
                    "session_id": session_id,
                    "turn_id": turn_id,
                    "sample_rate_hz": sample_rate_hz,
                    "channels": 1,
                    "encoding": "pcm_s16le",
                    "timestamp_ms": _current_millis(),
                }
            )

            seq = 0
            for start in range(0, len(pcm_bytes), audio_chunk_bytes):
                if turn_id in canceled_turn_ids:
                    return
                chunk = pcm_bytes[start:start + audio_chunk_bytes]
                if not chunk:
                    continue
                if first_audio_chunk_at is None:
                    first_audio_chunk_at = time.perf_counter()
                await send_bytes(chunk)
                await send_json(
                    {
                        "type": "assistant_audio_chunk_meta",
                        "session_id": session_id,
                        "turn_id": turn_id,
                        "seq": seq,
                        "bytes": len(chunk),
                        "timestamp_ms": _current_millis(),
                    }
                )
                seq += 1

            await send_json(
                {
                    "type": "assistant_audio_end",
                    "session_id": session_id,
                    "turn_id": turn_id,
                    "chunks": seq,
                    "timestamp_ms": _current_millis(),
                }
            )

            final_latency = _latency_ms(turn_started_at)
            await send_json(
                {
                    "type": "assistant_final",
                    "session_id": session_id,
                    "turn_id": turn_id,
                    "answer": answer.model_dump(mode="json"),
                    "metrics": {
                        "first_token_latency_ms": _latency_ms(turn_started_at, first_text_delta_at) if first_text_delta_at else None,
                        "first_audio_latency_ms": _latency_ms(turn_started_at, first_audio_chunk_at) if first_audio_chunk_at else None,
                        "final_latency_ms": final_latency,
                    },
                    "timestamp_ms": _current_millis(),
                }
            )

            history.extend(
                [
                    {"role": "user", "content": user_message},
                    {"role": "assistant", "content": answer.message},
                ]
            )
            history = history[-20:]

        except asyncio.CancelledError:
            logger.info(
                "voice_turn_cancelled session_id=%s turn_id=%s",
                session_id,
                turn_id,
            )
            raise
        except SourcePolicyViolationError as exc:
            await send_json(
                {
                    "type": "error",
                    "status": 422,
                    "session_id": session_id,
                    "turn_id": turn_id,
                    "detail": violations_to_response_payload(exc.violations, provider="gnews"),
                    "timestamp_ms": _current_millis(),
                }
            )
        except Exception as exc:
            await send_json(
                {
                    "type": "error",
                    "status": 500,
                    "session_id": session_id,
                    "turn_id": turn_id,
                    "detail": f"Failed to generate voice response: {str(exc)}",
                    "timestamp_ms": _current_millis(),
                }
            )
        finally:
            if active_turn_id == turn_id:
                active_turn_id = None

    try:
        while True:
            incoming = await websocket.receive()

            if incoming.get("type") == "websocket.disconnect":
                disconnect_reason = _classify_disconnect(incoming.get("code"))
                break

            if incoming.get("bytes") is not None:
                chunk = incoming.get("bytes") or b""
                if chunk:
                    audio_ingress_bytes += len(chunk)
                    audio_ingress_chunks += 1
                    # Keep barge-in active when user starts speaking.
                    await cancel_active_turn(reason="user_speaking")

                stt_events = speech_pipeline.ingest_audio(chunk)
                for event in stt_events:
                    await send_json(
                        {
                            **event,
                            "session_id": session_id,
                            "timestamp_ms": _current_millis(),
                        }
                    )

                    if event.get("type") == "user_final" and event.get("end_of_utterance"):
                        if not topic or not timeline_slice:
                            await send_json(
                                {
                                    "type": "error",
                                    "status": 400,
                                    "detail": "Voice session is not initialized",
                                }
                            )
                            continue

                        user_message = str(event.get("text") or "").strip()
                        if not user_message:
                            continue

                        story_context = _build_story_context(topic, timeline_slice)
                        turn_history = history[-6:]
                        turn_counter += 1
                        turn_id = f"turn-{turn_counter}-{uuid.uuid4().hex[:8]}"
                        active_turn_id = turn_id
                        active_turn_task = asyncio.create_task(
                            run_turn(
                                turn_id=turn_id,
                                user_message=user_message,
                                turn_history=turn_history,
                                story_context=story_context,
                            )
                        )

                for guardrail in speech_pipeline.guardrail_events():
                    await send_json(
                        {
                            **guardrail,
                            "session_id": session_id,
                            "timestamp_ms": _current_millis(),
                        }
                    )
                    if guardrail.get("code") == "reconnect":
                        await speech_pipeline.reconnect()

                continue

            text_payload = incoming.get("text")
            if not text_payload:
                continue

            try:
                payload = json.loads(text_payload)
            except json.JSONDecodeError:
                await websocket.send_json(
                    {
                        "type": "error",
                        "status": 400,
                        "detail": "Invalid JSON payload",
                    }
                )
                continue
            event_type = payload.get("type")

            if event_type == "session_start":
                topic = str(payload.get("topic") or "").strip()
                timeline_raw = payload.get("timeline_slice") or []
                history_raw = payload.get("history") or []

                if not topic:
                    await websocket.send_json(
                        {
                            "type": "error",
                            "status": 400,
                            "detail": "Topic is required",
                        }
                    )
                    continue

                if not isinstance(timeline_raw, list) or not timeline_raw:
                    await websocket.send_json(
                        {
                            "type": "error",
                            "status": 400,
                            "detail": "Timeline context is required",
                        }
                    )
                    continue

                timeline_slice = [item for item in timeline_raw if isinstance(item, dict)]
                history = [
                    {
                        "role": str(item.get("role") or "user"),
                        "content": str(item.get("content") or ""),
                    }
                    for item in history_raw
                    if isinstance(item, dict)
                ][-10:]

                await websocket.send_json(
                    {
                        "type": "session_ready",
                        "session_id": session_id,
                        "model": "amazon.nova-2-sonic-v1:0",
                        "sample_rate_hz": sample_rate_hz,
                        "channels": 1,
                        "encoding": "pcm_s16le",
                    }
                )
                continue

            if event_type == "barge_in":
                await cancel_active_turn(reason=str(payload.get("reason") or "user_interrupt"))
                continue

            if event_type == "session_end":
                await cancel_active_turn(reason="session_end")
                await send_json(
                    {
                        "type": "session_ended",
                        "session_id": session_id,
                        "timestamp_ms": _current_millis(),
                    }
                )
                await websocket.close()
                return

            if event_type != "user_utterance":
                await websocket.send_json(
                    {
                        "type": "error",
                        "status": 400,
                        "detail": "Unsupported voice event",
                    }
                )
                continue

            user_message = str(payload.get("text") or "").strip()
            if not user_message:
                await websocket.send_json(
                    {
                        "type": "error",
                        "status": 400,
                        "detail": "Utterance text cannot be empty",
                    }
                )
                continue

            if not topic or not timeline_slice:
                await websocket.send_json(
                    {
                        "type": "error",
                        "status": 400,
                        "detail": "Voice session is not initialized",
                    }
                )
                continue

            story_context = _build_story_context(topic, timeline_slice)
            turn_history = history[-6:]

            await cancel_active_turn(reason="new_turn_started")
            turn_counter += 1
            turn_id = f"turn-{turn_counter}-{uuid.uuid4().hex[:8]}"
            active_turn_id = turn_id
            active_turn_task = asyncio.create_task(
                run_turn(
                    turn_id=turn_id,
                    user_message=user_message,
                    turn_history=turn_history,
                    story_context=story_context,
                )
            )

    except WebSocketDisconnect:
        disconnect_reason = "websocket_disconnect"
    finally:
        if active_turn_task and not active_turn_task.done():
            active_turn_task.cancel()
            with suppress(asyncio.CancelledError):
                await active_turn_task

        session_duration_ms = _latency_ms(session_started_at)
        logger.info(
            "voice_session_end session_id=%s duration_ms=%.2f disconnect_reason=%s audio_ingress_bytes=%s audio_ingress_chunks=%s",
            session_id,
            session_duration_ms,
            disconnect_reason,
            audio_ingress_bytes,
            audio_ingress_chunks,
        )
