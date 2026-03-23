<div align="center">
<img width="1200" height="475" alt="GHBanner" src="https://github.com/user-attachments/assets/0aa67016-6eaf-458a-adb2-6e31a0763ed6" />
</div>

# Story Arc Tracker - Narrative Analysis Platform

A story arc analysis application built with React + TypeScript frontend and FastAPI + LangChain backend.

## Architecture

- **Frontend:** React 19 with Vite, real-time narrative visualization with React Flow
- **Backend:** FastAPI with LangChain and Google Gemini AI for orchestration
- **AI:** LangChain provides abstraction for seamless provider switching (Gemini → OpenAI/Anthropic/Llama)
- **Data Validation:** Pydantic schemas enforce strict structured output from the LLM

## Prerequisites

- **Node.js** (v18+) for frontend
- **Python** (v3.10+) for backend
- **Gemini API Key** from [Google AI Studio](https://aistudio.google.com/app/apikey)

## Setup & Run Locally

### 1. Backend Setup

Navigate to the backend directory:
```bash
cd backend
```

Create a `.env` file with your Gemini API key:
```bash
GEMINI_API_KEY=your_gemini_api_key_here
```

Create a Python virtual environment and install dependencies:
```bash
python -m venv venv
source venv/Scripts/activate  # On Windows: venv\Scripts\activate
pip install -r requirements.txt
```

Start the backend server (runs on `http://localhost:8000`):
```bash
python -m uvicorn app.main:app --reload --port 8000
```

### 2. Frontend Setup

In a separate terminal, navigate to the project root:
```bash
npm install
npm run dev
```

The frontend will run on `http://localhost:3000` (or `http://localhost:5173` depending on Vite config).

**Important:** The backend must be running for the frontend API calls to work. The Vite dev server proxies `/api` requests to `http://localhost:8000`.

## Project Structure

```
├── backend/                   # FastAPI backend
│   ├── app/
│   │   ├── main.py           # FastAPI app entry point
│   │   ├── config.py         # Environment configuration
│   │   ├── schemas.py        # Pydantic models (StoryData, PlayerProfile)
│   │   ├── prompts.py        # LangChain prompt templates
│   │   ├── constants.py      # Request/response models
│   │   ├── routers/
│   │   │   ├── health.py     # Health check endpoint
│   │   │   ├── analyze.py    # POST /api/analyze – story analysis
│   │   │   └── profile.py    # POST /api/player-profile – deep-dive profiles
│   │   └── services/
│   │       ├── news_fetcher.py     # News context fetching
│   │       └── ai_orchestration.py # LangChain + Gemini setup
│   ├── .env.example          # Backend environment template
│   └── requirements.txt       # Python dependencies
│
├── src/                        # React frontend
│   ├── App.tsx               # Main React component
│   ├── index.css             # Tailwind styles
│   └── main.tsx              # React entry point
├── vite.config.ts            # Vite config with /api proxy
├── tsconfig.json             # TypeScript config
└── package.json              # NPM dependencies
```

## API Endpoints

### `POST /api/analyze`
Analyzes a story topic and returns structured narrative data.

**Request:**
```json
{
  "topic": "The OpenAI board saga"
}
```

**Response:**
```json
{
  "timeline": [StoryEvent],
  "players": [Player],
  "relationships": [Relationship],
  "arcs": [Arc],
  "insights": [Insight]
}
```

### `POST /api/player-profile`
Generates a detailed profile for a specific player in the story.

**Request:**
```json
{
  "player_id": "player_1",
  "player_name": "Sam Altman",
  "player_role": "CEO of OpenAI",
  "player_type": "person",
  "topic": "The OpenAI board saga"
}
```

**Response:**
```json
{
  "id": "player_1",
  "name": "Sam Altman",
  "summary": "...",
  "role_in_story": "...",
  "motivations": [...],
  "alliances": [...],
  "conflicts": [...],
  "timeline_contributions": [...],
  "risk_score": 0.75,
  "outlook": "...",
  "citations": [...]
}
```

## Environment Variables

### Backend (backend/.env)
```
GEMINI_API_KEY=your_gemini_api_key_here
```

The backend uses Pydantic Settings to load environment variables with proper validation.

### Frontend
The frontend no longer stores API keys. All authentication is handled server-side in the backend.

## Key Design Decisions

1. **Backend owns secrets:** The Gemini API key is stored in the backend `.env` file only. The frontend never has access to credentials.

2. **Provider abstraction via LangChain:** The LLM provider (Gemini, OpenAI, Anthropic, Llama, etc.) can be swapped by modifying only `backend/app/services/ai_orchestration.py`. The rest of the backend logic remains unchanged.

3. **Structured output enforcement:** All LLM responses are validated against Pydantic schemas before being sent to the frontend. Malformed outputs fail fast with clear errors.

4. **Frontend API simplicity:** The React frontend makes simple HTTP POST requests to backend endpoints. No SDK dependencies, no complex client-side orchestration.

5. **Vite proxy for local development:** During development, `/api` requests are proxied from the Vite dev server (port 3000) to the FastAPI backend (port 8000), avoiding CORS issues.

## Caching

The frontend caches analysis results in localStorage using the topic as a key. Cached results are served immediately without making new API requests. Clear your browser cache to force a fresh analysis.

## Known Limitations & Future Scope

- **No persistence:** Analysis results are not stored in a database (localStorage only). Consider adding a backend database for multi-user collaboration or long-term storage.
- **No authentication:** The backend does not authenticate requests. In production, add API key or OAuth-based auth.
- **Single provider model:** The backend currently uses Google Gemini. Adding cost-tracking, provider fallbacks, or multi-model orchestration is out of scope for this version.
- **Synchronous processing:** Large stories may take time to analyze. Async job queues (Celery, Kafka) could improve responsiveness for production use.

## Development Notes

- Both frontend and backend must be running for the app to function. The Vite proxy only works during `npm run dev`.
- The backend logs LangChain execution details; set the appropriate logging level if debugging prompt/model issues.
- To change the underlying LLM provider (e.g., switch from Gemini to OpenAI), update `backend/app/services/ai_orchestration.py` and adjust `backend/requirements.txt` dependencies.
- Prompt engineering and model temperature are configurable in `backend/app/services/ai_orchestration.py` and `backend/app/prompts.py`.

## Deployment

For production deployment:
- Run the frontend build (`npm run build`) and serve the `dist` folder as static assets.
- Deploy the backend to a cloud platform (Heroku, AWS Lambda, GCP Cloud Run, etc.).
- Use a reverse proxy (Nginx, Apache) or API Gateway to route frontend requests to `/api/*` endpoints and serve static frontend assets.
- Store sensitive environment variables (API keys) in your deployment platform's secrets manager.

---

View your app in AI Studio: https://ai.studio/apps/57dc7b46-3166-4cc6-9110-61d90160e6ed
