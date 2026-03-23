from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.routers import health, analyze, profile

app = FastAPI(
    title="Story Arc Tracker API",
    description="Backend API for analyzing narrative arcs and relationships",
    version="1.0.0"
)

# Configure CORS for local development
origins = [
    "http://localhost:3000",
    "http://localhost:5173",
    "http://127.0.0.1:3000",
    "http://127.0.0.1:5173",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register routers
app.include_router(health.router)
app.include_router(analyze.router)
app.include_router(profile.router)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
