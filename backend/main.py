import os
from dotenv import load_dotenv
load_dotenv()
import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from sqlalchemy.future import select
from sqlalchemy import func

# Set GDAL environment variables for MinIO S3 access before TiTiler is imported
os.environ["AWS_ACCESS_KEY_ID"] = "minioadmin"
os.environ["AWS_SECRET_ACCESS_KEY"] = "minioadmin"
os.environ["AWS_S3_ENDPOINT"] = "localhost:9000"
os.environ["AWS_VIRTUAL_HOSTING"] = "FALSE"
os.environ["AWS_HTTPS"] = "NO"

import boto3
from fastapi import FastAPI, UploadFile, File, Form, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from titiler.core.factory import TilerFactory
from rio_tiler.errors import TileOutsideBounds
from celery import Celery

from database import (
    init_db,
    AsyncSessionLocal,
    SpatialFeature,
    FloodZone,
    NetworkLine,
    AssetPoint,
    ResidentialZone,
    HistoricalFloodEvent,
)

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield

app = FastAPI(title="SatQuery AI Backend", lifespan=lifespan)

# Enable CORS for frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- TiTiler Integration ---
cog = TilerFactory(router_prefix="/cog")
app.include_router(cog.router, prefix="/cog", tags=["Cloud Optimized GeoTIFF"])


@app.exception_handler(TileOutsideBounds)
async def tile_outside_bounds_handler(request: Request, exc: TileOutsideBounds):
    """Not a bug: the map viewport is asking for a tile (x/y/z) that falls
    outside the actual geographic footprint of the requested COG -- e.g. the
    user panned/zoomed away from wherever the uploaded TIFF actually is, or a
    stale tileUrl is still active after panning to a different region.
    Returning an empty tile here is the correct behavior (there is genuinely
    no imagery there) instead of a noisy 500 stack trace."""
    return Response(status_code=204)

# --- Celery Setup ---
celery_app = Celery(
    "satquery_tasks",
    broker=os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/0"),
    backend=os.getenv("CELERY_RESULT_BACKEND", "redis://localhost:6379/0")
)

# --- MinIO (S3) Setup ---
MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "http://localhost:9000")
MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY", "minioadmin")
MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY", "minioadmin")
BUCKET_NAME = "satquery-cogs"

s3_client = boto3.client(
    "s3",
    endpoint_url=MINIO_ENDPOINT,
    aws_access_key_id=MINIO_ACCESS_KEY,
    aws_secret_access_key=MINIO_SECRET_KEY,
)

@app.post("/api/upload")
async def upload_image(file: UploadFile = File(...)):
    """Uploads a GeoTIFF to MinIO and returns the S3 URL."""
    try:
        s3_client.head_bucket(Bucket=BUCKET_NAME)
    except:
        s3_client.create_bucket(Bucket=BUCKET_NAME)

    file_content = await file.read()
    s3_client.put_object(
        Bucket=BUCKET_NAME,
        Key=file.filename,
        Body=file_content,
        ContentType=file.content_type
    )

    s3_url = f"s3://{BUCKET_NAME}/{file.filename}"
    return {"s3_url": s3_url, "filename": file.filename}

@app.post("/api/query")
async def submit_query(query: str = Form(...), s3_url: str = Form(None), bbox: str = Form(None)):
    """Dispatches a query and optional image URL / bbox to the Celery worker."""
    task = celery_app.send_task("worker.process_spatial_query", args=[query, s3_url, bbox])
    return {"task_id": task.id}

@app.get("/api/task/{task_id}")
async def get_task_status(task_id: str):
    """Check the status of a task."""
    task = celery_app.AsyncResult(task_id)
    if task.state == 'PENDING':
        response = {"state": task.state, "status": "Pending..."}
    elif task.state != 'FAILURE':
        response = {"state": task.state, "result": task.result}
    else:
        response = {"state": task.state, "status": str(task.info)}
    return response

@app.get("/api/vectors")
async def get_vectors(bbox: str = Query(..., description="minLng,minLat,maxLng,maxLat")):
    """Returns a GeoJSON FeatureCollection of live ONNX-inference features intersecting the bbox."""
    try:
        min_lng, min_lat, max_lng, max_lat = map(float, bbox.split(','))
    except ValueError:
        return {"error": "Invalid bbox format. Use minLng,minLat,maxLng,maxLat"}

    envelope = func.ST_MakeEnvelope(min_lng, min_lat, max_lng, max_lat, 4326)

    async with AsyncSessionLocal() as session:
        query = select(
            SpatialFeature.id,
            func.ST_AsGeoJSON(SpatialFeature.geom).label('geometry'),
            SpatialFeature.damage_score,
            SpatialFeature.object_type
        ).where(func.ST_Intersects(SpatialFeature.geom, envelope))

        result = await session.execute(query)

        features = []
        for row in result:
            features.append({
                "type": "Feature",
                "geometry": json.loads(row.geometry),
                "properties": {
                    "id": row.id,
                    "damage_score": row.damage_score,
                    "object_type": row.object_type
                }
            })

        return {"type": "FeatureCollection", "features": features}


# --- Multi-Tier GIS Real-Data Engine (curated Rasuwa / Trishuli-Bhote Koshi basin) ---

RISK_RANK = {"ADVISORY": 1, "WARNING": 2, "CRITICAL": 3}


def _status_rank(status: str) -> int:
    """Map a NetworkLine/AssetPoint-ish severity onto the same 1-3 scale as FloodZone.risk_level."""
    return {"ACCESSIBLE": 1, "COMPROMISED": 2, "SUBMERGED": 3}.get(status, 1)


@app.get("/api/gis/layers")
async def get_gis_layers(
    bbox: str = Query(..., description="minLng,minLat,maxLng,maxLat"),
    min_risk: str = Query("ADVISORY", description="Minimum severity to include: ADVISORY | WARNING | CRITICAL"),
):
    """Returns the curated multi-tier disaster layers (flood zone polygons, network
    lines, asset points) for the Rasuwa / Trishuli-Bhote Koshi basin, filtered by
    viewport bbox and a minimum severity threshold."""
    try:
        min_lng, min_lat, max_lng, max_lat = map(float, bbox.split(','))
    except ValueError:
        return {"error": "Invalid bbox format. Use minLng,minLat,maxLng,maxLat"}

    min_rank = RISK_RANK.get(min_risk.upper(), 1)
    envelope = func.ST_MakeEnvelope(min_lng, min_lat, max_lng, max_lat, 4326)

    features = []

    async with AsyncSessionLocal() as session:
        zone_query = select(
            FloodZone.id,
            func.ST_AsGeoJSON(FloodZone.geom).label('geometry'),
            FloodZone.name,
            FloodZone.inundation_depth_m,
            FloodZone.velocity_ms,
            FloodZone.risk_level,
            FloodZone.description,
        ).where(func.ST_Intersects(FloodZone.geom, envelope))
        for row in await session.execute(zone_query):
            if RISK_RANK.get(row.risk_level, 1) < min_rank:
                continue
            features.append({
                "type": "Feature",
                "geometry": json.loads(row.geometry),
                "properties": {
                    "layer": "flood_zone",
                    "id": row.id,
                    "name": row.name,
                    "inundation_depth_m": row.inundation_depth_m,
                    "velocity_ms": row.velocity_ms,
                    "risk_level": row.risk_level,
                    "description": row.description,
                },
            })

        line_query = select(
            NetworkLine.id,
            func.ST_AsGeoJSON(NetworkLine.geom).label('geometry'),
            NetworkLine.name,
            NetworkLine.line_type,
            NetworkLine.status,
            NetworkLine.description,
        ).where(func.ST_Intersects(NetworkLine.geom, envelope))
        for row in await session.execute(line_query):
            # River channels are always shown regardless of min_risk (they're a basemap reference layer);
            # roads are filtered by severity like everything else.
            if row.line_type == "road" and _status_rank(row.status) < min_rank:
                continue
            features.append({
                "type": "Feature",
                "geometry": json.loads(row.geometry),
                "properties": {
                    "layer": "network_line",
                    "id": row.id,
                    "name": row.name,
                    "line_type": row.line_type,
                    "status": row.status,
                    "description": row.description,
                },
            })

        point_query = select(
            AssetPoint.id,
            func.ST_AsGeoJSON(AssetPoint.geom).label('geometry'),
            AssetPoint.asset_id,
            AssetPoint.name,
            AssetPoint.elevation_m,
            AssetPoint.damage_prob,
            AssetPoint.asset_type,
        ).where(func.ST_Intersects(AssetPoint.geom, envelope))
        for row in await session.execute(point_query):
            prob_rank = 3 if (row.damage_prob or 0) >= 0.8 else 2 if (row.damage_prob or 0) >= 0.5 else 1
            if prob_rank < min_rank:
                continue
            features.append({
                "type": "Feature",
                "geometry": json.loads(row.geometry),
                "properties": {
                    "layer": "asset_point",
                    "id": row.id,
                    "asset_id": row.asset_id,
                    "name": row.name,
                    "elevation_m": row.elevation_m,
                    "damage_prob": row.damage_prob,
                    "asset_type": row.asset_type,
                },
            })

        residential_query = select(
            ResidentialZone.id,
            func.ST_AsGeoJSON(ResidentialZone.geom).label('geometry'),
            ResidentialZone.name,
            ResidentialZone.population_estimate,
            ResidentialZone.population_source,
            ResidentialZone.overlap_risk_level,
        ).where(func.ST_Intersects(ResidentialZone.geom, envelope))
        for row in await session.execute(residential_query):
            features.append({
                "type": "Feature",
                "geometry": json.loads(row.geometry),
                "properties": {
                    "layer": "residential_zone",
                    "id": row.id,
                    "name": row.name,
                    "population_estimate": row.population_estimate,
                    "population_source": row.population_source,
                    "overlap_risk_level": row.overlap_risk_level,
                },
            })

        # Historical flood events are shown on a padded envelope (not the strict
        # min_risk filter) so panning near -- not just directly onto -- a
        # documented event still surfaces it.
        pad_lng = max((max_lng - min_lng), 0.5)
        pad_lat = max((max_lat - min_lat), 0.5)
        padded_envelope = func.ST_MakeEnvelope(min_lng - pad_lng, min_lat - pad_lat, max_lng + pad_lng, max_lat + pad_lat, 4326)
        history_query = select(
            HistoricalFloodEvent.id,
            func.ST_AsGeoJSON(HistoricalFloodEvent.geom).label('geometry'),
            HistoricalFloodEvent.name,
            HistoricalFloodEvent.country,
            HistoricalFloodEvent.region,
            HistoricalFloodEvent.year,
            HistoricalFloodEvent.month,
            HistoricalFloodEvent.rivers,
            HistoricalFloodEvent.summary,
            HistoricalFloodEvent.source_note,
        ).where(func.ST_Intersects(HistoricalFloodEvent.geom, padded_envelope))
        for row in await session.execute(history_query):
            features.append({
                "type": "Feature",
                "geometry": json.loads(row.geometry),
                "properties": {
                    "layer": "historical_flood_event",
                    "id": row.id,
                    "name": row.name,
                    "country": row.country,
                    "region": row.region,
                    "year": row.year,
                    "month": row.month,
                    "rivers": row.rivers,
                    "summary": row.summary,
                    "source_note": row.source_note,
                },
            })

    return {"type": "FeatureCollection", "features": features}


# --- Region-generalized historical lookup + transparent predictability index ---
#
# IMPORTANT (anti-hallucination): this is a small, hand-curated demonstration set
# of real, documented flood events (see seed_data.py) -- NOT a comprehensive
# historical-flood database, and the "index" below is a transparent, deterministic
# rule-based score, not a trained/validated hydrological forecast. When no
# historical record exists near a region, that is reported explicitly rather than
# guessed at.

INDIA_MONSOON_MONTHS = {6, 7, 8, 9}  # June-September, South Asian SW monsoon window


async def _nearby_historical_events(session, min_lng, min_lat, max_lng, max_lat):
    pad_lng = max((max_lng - min_lng), 1.0)
    pad_lat = max((max_lat - min_lat), 1.0)
    envelope = func.ST_MakeEnvelope(min_lng - pad_lng, min_lat - pad_lat, max_lng + pad_lng, max_lat + pad_lat, 4326)
    q = select(
        HistoricalFloodEvent.id,
        HistoricalFloodEvent.name,
        HistoricalFloodEvent.country,
        HistoricalFloodEvent.region,
        HistoricalFloodEvent.year,
        HistoricalFloodEvent.month,
        HistoricalFloodEvent.rivers,
        HistoricalFloodEvent.summary,
        HistoricalFloodEvent.source_note,
    ).where(func.ST_Intersects(HistoricalFloodEvent.geom, envelope))
    rows = list(await session.execute(q))
    return [
        {
            "id": r.id, "name": r.name, "country": r.country, "region": r.region,
            "year": r.year, "month": r.month, "rivers": r.rivers,
            "summary": r.summary, "source_note": r.source_note,
        }
        for r in rows
    ]


@app.get("/api/predict/region")
async def predict_region(bbox: str = Query(..., description="minLng,minLat,maxLng,maxLat")):
    """Transparent, rule-based 'Historical Pattern Index' for whatever region the
    viewport is currently over -- works for any bbox, not just the Trishuli basin.
    This is explicitly NOT a certified flood forecast: it is a demonstration of how
    a real historical-events dataset would feed a risk index, using every input
    shown in the response. Returns events_found=0 and a null index (rather than a
    fabricated number) when this prototype has no historical record nearby."""
    try:
        min_lng, min_lat, max_lng, max_lat = map(float, bbox.split(','))
    except ValueError:
        return {"error": "Invalid bbox format. Use minLng,minLat,maxLng,maxLat"}

    async with AsyncSessionLocal() as session:
        events = await _nearby_historical_events(session, min_lng, min_lat, max_lng, max_lat)

    if not events:
        return {
            "events_found": 0,
            "predictability_index": None,
            "index_band": None,
            "index_label": "No data",
            "methodology": {
                "explanation": (
                    "No historical flood record exists in this prototype's knowledge base "
                    "within roughly the surrounding region of the current viewport. This does "
                    "NOT mean the area is flood-safe -- it means this demo has no data for it. "
                    "A production deployment would need a real historical flood/hydrology "
                    "dataset (e.g. national flood forecasting agency records) covering this area."
                ),
            },
            "events": [],
        }

    now = datetime.now(timezone.utc)
    current_month = now.month
    in_monsoon_window = current_month in INDIA_MONSOON_MONTHS
    most_recent_gap = min(now.year - e["year"] for e in events)

    # Every component below is shown in the response -- nothing hidden in a black box.
    frequency_score = min(len(events), 5) * 12  # up to 60
    recency_score = 25 if most_recent_gap <= 5 else 15 if most_recent_gap <= 15 else 5
    seasonal_score = 15 if in_monsoon_window else 0
    index = min(100, frequency_score + recency_score + seasonal_score)
    band = "ELEVATED" if index >= 70 else "MODERATE" if index >= 40 else "LOW-TO-UNCERTAIN"

    return {
        "events_found": len(events),
        "predictability_index": index,
        "index_band": band,
        "index_label": "Historical Pattern Index (heuristic demo -- NOT a certified hydrological forecast)",
        "methodology": {
            "frequency_score": frequency_score,
            "recency_score": recency_score,
            "seasonal_score": seasonal_score,
            "currently_in_south_asia_monsoon_window": in_monsoon_window,
            "most_recent_event_years_ago": most_recent_gap,
            "explanation": (
                f"{len(events)} documented historical flood event(s) on record near this viewport. "
                f"Most recent was {most_recent_gap} year(s) ago. "
                + ("Currently within the Jun-Sep South Asian monsoon window (seasonal risk applied only in that context)."
                   if in_monsoon_window else
                   "Currently outside the Jun-Sep South Asian monsoon window.")
            ),
        },
        "events": events,
    }


from pydantic import BaseModel
from typing import Optional, List
import rag_engine

class RAGQuery(BaseModel):
    query: str
    bbox: Optional[List[float]] = None

@app.post("/api/rag/query")
async def rag_query(payload: RAGQuery):
    """Runs Hybrid RRF retrieval across document chunks, plus a geospatial lookup
    of any historical flood events near the active viewport (region-generalized --
    works wherever the console is panned, not just the Trishuli basin), and
    answers using Gemini grounded only in what was actually retrieved."""
    try:
        client = rag_engine.get_client()
    except RuntimeError as e:
        return {"error": str(e)}

    async with AsyncSessionLocal() as session:
        retrieved = await rag_engine.hybrid_search(session, client, payload.query)
        nearby_events = []
        if payload.bbox and len(payload.bbox) == 4:
            nearby_events = await _nearby_historical_events(session, *payload.bbox)

    context_chunks = [r["content"] for r in retrieved]
    context_sources = [{"kind": "document_chunk", "score": r["rrf_score"]} for r in retrieved]

    for e in nearby_events:
        summary_line = f"[Historical event: {e['name']}, {e['region'] or e['country']}, {e['month'] or '?'}/{e['year']}] {e['summary']}"
        context_chunks.append(summary_line)
        context_sources.append({"kind": "historical_flood_event", "year": e["year"], "region": e["region"]})

    answer = rag_engine.generate_answer(client, payload.query, context_chunks, payload.bbox)

    return {
        "answer": answer,
        "retrieved_chunks": [
            {"ref": i + 1, "content": chunk, **src}
            for i, (chunk, src) in enumerate(zip(context_chunks, context_sources))
        ],
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
