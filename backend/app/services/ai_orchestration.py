from __future__ import annotations

import json
from typing import Any

from app.schemas import ChatAnswer, Citation, PlayerProfile, StoryData
from app.prompts import get_analyze_prompt, get_deep_dive_prompt, get_topic_chat_prompt
from app.config import get_settings
from app.services.source_policy import build_prompt_policy_context


def _build_chat_model(model_id: str):
    from langchain_aws import ChatBedrockConverse

    settings = get_settings()
    return ChatBedrockConverse(
        model_id=model_id,
        region_name=settings.aws_region,
        temperature=settings.bedrock_llm_temperature,
    )


def _is_simple_request(*, topic: str, context_text: str) -> bool:
    settings = get_settings()
    combined_len = len(topic.strip()) + len((context_text or "").strip())
    return combined_len <= settings.bedrock_llm_simple_context_char_threshold


def _analyze_quality_check(result: StoryData) -> None:
    # Keep this narrow so fallback is only used for obvious quality misses.
    if not result.timeline and not result.insights:
        raise ValueError("Analyze output failed quality checks: empty timeline/insights")


def _profile_quality_check(result: PlayerProfile) -> None:
    if not result.summary.strip() or not result.outlook.strip():
        raise ValueError("Profile output failed quality checks: empty summary/outlook")


def _select_primary_model(*, topic: str, context_text: str):
    settings = get_settings()
    if _is_simple_request(topic=topic, context_text=context_text):
        return _build_chat_model(settings.bedrock_llm_simple_model_id)
    return _build_chat_model(settings.bedrock_llm_default_model_id)


def _fallback_model():
    settings = get_settings()
    return _build_chat_model(settings.bedrock_llm_fallback_model_id)


def _chat_model():
    settings = get_settings()
    return _build_chat_model(settings.bedrock_llm_chat_model_id)


def _voice_model():
    settings = get_settings()
    return _build_chat_model(settings.bedrock_llm_voice_model_id)


def get_gemini_model(topic: str = "", context_text: str = ""):
    """
    Backward-compatible model factory name used by existing call sites.

    Returns a Bedrock chat model routed by request complexity.
    """
    return _select_primary_model(topic=topic, context_text=context_text)


def _chat_quality_check(result: ChatAnswer) -> None:
    if not result.message.strip() or not result.citations:
        raise ValueError("Chat output failed quality checks: missing message/citations")


def _coerce_first_citation_from_story_context(story_context: dict[str, Any]) -> Citation | None:
    timeline = story_context.get("timeline")
    if not isinstance(timeline, list):
        return None

    for event in timeline:
        if not isinstance(event, dict):
            continue
        citations = event.get("citations")
        if not isinstance(citations, list):
            continue
        for citation in citations:
            if not isinstance(citation, dict):
                continue
            source_name = str(citation.get("source_name", "")).strip()
            url = str(citation.get("url", "")).strip()
            published_at = str(citation.get("published_at", "")).strip()
            snippet = str(citation.get("snippet", "")).strip()
            if source_name and url and published_at and snippet:
                return Citation(
                    source_name=source_name,
                    url=url,
                    published_at=published_at,
                    snippet=snippet,
                )

    return None


async def _invoke_with_optional_fallback(
    *,
    prompt: Any,
    schema: type[StoryData] | type[PlayerProfile] | type[ChatAnswer],
    payload: dict[str, Any],
    quality_check,
    primary_model,
):
    primary_chain = prompt | primary_model.with_structured_output(schema)
    settings = get_settings()

    try:
        result = await primary_chain.ainvoke(payload)
        quality_check(result)
        return result
    except Exception:
        if not settings.bedrock_llm_enable_fallback:
            raise

    fallback_chain = prompt | _fallback_model().with_structured_output(schema)
    fallback_result = await fallback_chain.ainvoke(payload)
    quality_check(fallback_result)
    return fallback_result


async def analyze_story(topic: str, news_context: str) -> StoryData:
    """
    Analyze a story topic and return structured StoryData.
    
    Uses LangChain with Bedrock chat models and enforces StoryData schema.
    """
    model = get_gemini_model(topic=topic, context_text=news_context)
    policy_context = build_prompt_policy_context()
    prompt = get_analyze_prompt(
        allowed_source_policy_label=policy_context.allowed_source_policy_label,
        fallback_text=policy_context.fallback_text,
        unsupported_source_behavior=policy_context.unsupported_source_behavior,
    )

    result = await _invoke_with_optional_fallback(
        prompt=prompt,
        schema=StoryData,
        payload={
            "topic": topic,
            "news_context": news_context,
        },
        quality_check=_analyze_quality_check,
        primary_model=model,
    )

    return result


async def generate_player_profile(
    player_id: str,
    player_name: str,
    player_role: str,
    player_type: str,
    topic: str,
    timeline_context: str,
    related_players_context: str,
    relationship_context: str
) -> PlayerProfile:
    """
    Generate a detailed player profile for the deep-dive feature.
    
    Uses LangChain with Bedrock chat models and structured output to enforce PlayerProfile schema.
    """
    joined_context = "\n".join([timeline_context, related_players_context, relationship_context])
    model = get_gemini_model(topic=topic, context_text=joined_context)
    policy_context = build_prompt_policy_context()
    prompt = get_deep_dive_prompt(
        allowed_source_policy_label=policy_context.allowed_source_policy_label,
        fallback_text=policy_context.fallback_text,
        unsupported_source_behavior=policy_context.unsupported_source_behavior,
    )

    result = await _invoke_with_optional_fallback(
        prompt=prompt,
        schema=PlayerProfile,
        payload={
            "player_name": player_name,
            "player_role": player_role,
            "player_type": player_type,
            "topic": topic,
            "timeline_context": timeline_context,
            "related_players_context": related_players_context,
            "relationship_context": relationship_context,
        },
        quality_check=_profile_quality_check,
        primary_model=model,
    )
    
    # Ensure the ID matches the player_id provided
    result.id = player_id
    
    return result


async def generate_topic_chat_response(
    *,
    topic: str,
    message: str,
    history: list[dict[str, str]],
    story_context: dict[str, Any],
) -> ChatAnswer:
    """Generate a topic-aware chatbot response grounded in analyzed story context."""
    story_context_json = json.dumps(story_context, indent=2)
    history_json = json.dumps(history, indent=2)
    policy_context = build_prompt_policy_context()
    prompt = get_topic_chat_prompt(
        allowed_source_policy_label=policy_context.allowed_source_policy_label,
        fallback_text=policy_context.fallback_text,
        unsupported_source_behavior=policy_context.unsupported_source_behavior,
    )

    try:
        return await _invoke_with_optional_fallback(
            prompt=prompt,
            schema=ChatAnswer,
            payload={
                "topic": topic,
                "message": message,
                "history": history_json,
                "story_context": story_context_json,
            },
            quality_check=_chat_quality_check,
            primary_model=_chat_model(),
        )
    except Exception as e:
        fallback_citation = _coerce_first_citation_from_story_context(story_context)
        if fallback_citation is None:
            raise ValueError("Chat output failed quality checks: no usable citation in context") from e

        return ChatAnswer(
            message=policy_context.fallback_text,
            citations=[fallback_citation],
            outside_topic=False,
            outside_topic_note="Response generated with conservative fallback due to model formatting issues.",
            confidence=0.2,
            suggested_followups=["Can you ask a narrower question about a specific timeline event?"],
        )


async def generate_topic_voice_response(
    *,
    topic: str,
    message: str,
    history: list[dict[str, str]],
    story_context: dict[str, Any],
) -> ChatAnswer:
    """Generate a topic-aware response for voice conversations using the voice model."""
    story_context_json = json.dumps(story_context, indent=2)
    history_json = json.dumps(history, indent=2)
    policy_context = build_prompt_policy_context()
    prompt = get_topic_chat_prompt(
        allowed_source_policy_label=policy_context.allowed_source_policy_label,
        fallback_text=policy_context.fallback_text,
        unsupported_source_behavior=policy_context.unsupported_source_behavior,
    )

    try:
        return await _invoke_with_optional_fallback(
            prompt=prompt,
            schema=ChatAnswer,
            payload={
                "topic": topic,
                "message": message,
                "history": history_json,
                "story_context": story_context_json,
            },
            quality_check=_chat_quality_check,
            primary_model=_voice_model(),
        )
    except Exception as e:
        fallback_citation = _coerce_first_citation_from_story_context(story_context)
        if fallback_citation is None:
            raise ValueError("Voice chat output failed quality checks: no usable citation in context") from e

        return ChatAnswer(
            message=policy_context.fallback_text,
            citations=[fallback_citation],
            outside_topic=False,
            outside_topic_note="Voice response generated with conservative fallback due to model formatting issues.",
            confidence=0.2,
            suggested_followups=["Can you ask a narrower question about a specific timeline event?"],
        )
