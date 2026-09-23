"""[Участник 3] HTTP API. Запуск: uvicorn app.api:app --reload"""
from fastapi import FastAPI

from app.catalog import load_catalog
from app.models import CATEGORIES, CITIES, EVENT_FORMATS, LANGUAGES, RecommendRequest, RecommendResponse
from app.recommender import recommend

app = FastAPI(title="Event Vendor Matcher", version="0.1.0")


@app.get("/health")
def health():
    return {"status": "ok", "vendors": len(load_catalog())}


@app.get("/options")
def options():
    """Допустимые значения для формы."""
    return {"cities": CITIES, "categories": CATEGORIES, "event_formats": EVENT_FORMATS, "languages": LANGUAGES}


@app.post("/recommend", response_model=RecommendResponse)
def recommend_endpoint(req: RecommendRequest) -> RecommendResponse:
    return recommend(req)
