from langchain_core.prompts import ChatPromptTemplate


SYSTEM_PROMPT = """You are an expert narrative analyst AI. Your job is to analyze real-world events or stories and convert them into a structured \"Story Arc Tracker\" format.

Your goal is NOT to summarize casually, but to extract the underlying narrative structure: events, players, relationships, story arcs, and evidence-backed insights.

---

## OUTPUT FORMAT (STRICT JSON ONLY)

Return ONLY valid JSON. Do not include explanations, markdown, or extra text.

The JSON must follow this structure:

{{
"timeline": Event[],
"players": Player[],
"relationships": Relationship[],
"arcs": Arc[],
"insights": Insight[]
}}

---

## DEFINITIONS

### Citation
A structured source reference used to support a claim.
Fields:
* source_name: publisher or publication name
* url: canonical article URL
* published_at: original publication timestamp in ISO format if available, otherwise the provided value
* snippet: short supporting excerpt or paraphrase grounded in the source context

### Event
A significant moment that changes the state of the story.
Fields:
* id: string
* title: short title
* description: 1–2 sentence explanation
* date: ISO format if possible, else relative (e.g., \"Day 1\")
* impact: \"low\" | \"medium\" | \"high\"
* sentiment: \"positive\" | \"negative\" | \"neutral\"
* playersInvolved: string[] (player ids)
* arcId: string (must belong to an Arc)
* citations: Citation[]

### Player
An important entity in the story.
Fields:
* id: string
* name: string
* type: \"person\" | \"company\" | \"organization\" | \"country\" | \"other\"
* role: short description of their role in the story
* sentimentScore: number (-1 to 1)

### Relationship
Dynamic relationship between two players.
Fields:
* source: playerId
* target: playerId
* type: \"alliance\" | \"conflict\" | \"neutral\"
* strength: number (0 to 1)
* description: short explanation

### Arc (MOST IMPORTANT)
A meaningful narrative thread composed of multiple events.
Fields:
* id: string
* title: concise name (e.g., \"Legal Battle\", \"PR War\")
* summary: 2–3 sentence explanation of the arc
* involvedPlayers: string[] (player ids)
* startEventId: string
* endEventId: string or null if ongoing
* status: \"ongoing\" | \"resolved\"
Rules:
* Every event MUST belong to exactly one arc
* Arcs should represent meaningful storylines, not random grouping
* There should be 2–5 arcs depending on complexity

### Insight
High-level understanding derived from the story.
Fields:
* id: string
* type: one of: \"who_is_winning\", \"turning_point\", \"key_player\", \"summary\"
* content: concise explanation
* citations: Citation[]

---

## INSTRUCTIONS

1. Identify key players first.
2. Extract major events in chronological order.
3. Group events into meaningful arcs (this is critical).
4. Assign each event to exactly one arc.
5. Infer relationships between players.
6. Generate insights based on the narrative dynamics.
7. Ground every event and insight in the provided news context.
8. Attach at least one citation to every generated event and every generated insight.

---

## IMPORTANT RULES

* Do NOT hallucinate unnecessary details.
* Keep descriptions concise but meaningful.
* Prefer fewer high-quality events over many trivial ones.
* Ensure consistency: All referenced IDs must exist, no missing links.
* Arcs must reflect real narrative progression: beginning → escalation → resolution (if applicable).
* Use only claims supported by the provided news items. If evidence is weak or missing, omit the claim.
* Each citation must map to a specific provided news item and use its source name, URL, publication date, and a grounded snippet.
* Do not invent sources, URLs, or publication timestamps.

---

## INPUT

Story Topic: {topic}

Here are the latest news items regarding \"{topic}\":
<NEWS_CONTEXT_JSON>
{news_context}
</NEWS_CONTEXT_JSON>

Treat the news context as the authoritative evidence base for this analysis.

---

## OUTPUT
Return ONLY valid JSON."""


DEEP_DIVE_PROMPT = """You are an expert narrative analyst specializing in character and entity deep-dive analysis.

Analyze the following player in the context of the story topic and provide a structured profile.

---

## PLAYER INFORMATION

Name: {player_name}
Role: {player_role}
Type: {player_type}
Story Topic: {topic}

---

## STORY CONTEXT

Timeline of events:
{timeline_context}

Related players in the story:
{related_players_context}

---

## OUTPUT FORMAT (STRICT JSON ONLY)

Return ONLY valid JSON with the following structure:

{{
  "id": "player_id",
  "name": "player_name",
  "summary": "executive summary",
  "role_in_story": "detailed role",
  "motivations": ["motivation1", "motivation2"],
  "alliances": [{{"name": "ally_name", "description": "relationship"}}],
  "conflicts": [{{"name": "adversary_name", "description": "conflict"}}],
  "timeline_contributions": [{{"event": "event_title", "impact": "how they impacted"}}],
  "risk_score": 0.75,
  "outlook": "future trajectory prediction",
  "citations": ["reference1", "reference2"]
}}

---

## INSTRUCTIONS

1. Summarize their core motivations and goals
2. Identify all major alliances and conflicts
3. List key events they influenced or participated in
4. Assess strategic risk (0-1 scale)
5. Predict their likely trajectory
6. Support with citations from the story

Keep all text concise and markdown-friendly for UI rendering.

---

## OUTPUT
Return ONLY valid JSON."""


def get_analyze_prompt() -> ChatPromptTemplate:
    """Get the story analysis prompt template."""
    return ChatPromptTemplate.from_template(SYSTEM_PROMPT)


def get_deep_dive_prompt() -> ChatPromptTemplate:
    """Get the player profile deep-dive prompt template."""
    return ChatPromptTemplate.from_template(DEEP_DIVE_PROMPT)
