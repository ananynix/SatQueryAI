import os
from dotenv import load_dotenv
load_dotenv()
import json
from contextlib import asynccontextmanager
from sqlalchemy.future import select
from sqlalchemy import func

# Set GDAL environment variables for MinIO S3 access before TiTiler is imported
os.environ["AWS_ACCESS_KEY_ID"] = "minioadmin"
os.environ["AWS_SECRET_ACCESS_KEY"] = "minioadmin"
os.environ["AWS_S3_ENDPOINT"] = "localhost:9000"
os.environ["AWS_VIRTUAL_HOSTING"] = "FALSE"
os.environ["AWS_HTTPS"] = "NO"

import boto3
from fastapi import FastAPI, UploadFile, File, Form, Query
from fastapi.middleware.cors import CORSMiddleware
from titiler.core.factory import TilerFactory
from celery import Celery

from database import init_db, AsyncSessionLocal, SpatialFeature

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

    # For TiTiler local access, usually it's best to return the direct S3 URL 
    # but since TiTiler needs to access it, we can pass s3:// bucket format if configured,
    # or http URL. We'll return the s3:// path for TiTiler to use.
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
    """Returns a GeoJSON FeatureCollection of spatial features intersecting the bbox."""
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

from pydantic import BaseModel
from typing import Optional, List
from google import genai
from sqlalchemy import text

class RAGQuery(BaseModel):
    query: str
    bbox: Optional[List[float]] = None

@app.post("/api/rag/query")
async def rag_query(payload: RAGQuery):
    """Runs Hybrid RRF retrieval across document chunks and answers using Gemini."""
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return {"error": "GEMINI_API_KEY is not set."}
        
    client = genai.Client(api_key=api_key)
    
    # 1. Compute Dense Vector for Query
    response = client.models.embed_content(
        model='text-embedding-004',
        contents=payload.query
    )
    query_vector = response.embeddings[0].values
    
    # 2. Reciprocal Rank Fusion (RRF) SQL Query
    rrf_query = """
    WITH dense AS (
        SELECT id, content,
               RANK() OVER (ORDER BY embedding <=> :vector::vector) as dense_rank
        FROM document_chunks
    ),
    sparse AS (
        SELECT id,
               RANK() OVER (ORDER BY ts_rank(tsv, plainto_tsquery('english', :query)) DESC) as sparse_rank
        FROM document_chunks
        WHERE tsv @@ plainto_tsquery('english', :query)
    )
    SELECT COALESCE(d.id, s.id) as id,
           d.content,
           (1.0 / (60 + COALESCE(d.dense_rank, 1000))) +
           (1.0 / (60 + COALESCE(s.sparse_rank, 1000))) as rrf_score
    FROM dense d
    FULL OUTER JOIN sparse s ON d.id = s.id
    ORDER BY rrf_score DESC
    LIMIT 5;
    """
    
    async with AsyncSessionLocal() as session:
        result = await session.execute(text(rrf_query), {
            "vector": str(query_vector), 
            "query": payload.query
        })
        
        context_chunks = [row.content for row in result]
    
    context_text = "\n\n---\n\n".join(context_chunks)
    
    # 3. LLM Synthesis
    prompt = f"Geospatial Context Bounding Box: {payload.bbox}\n\nDatabase Context:\n{context_text}\n\nUser Question: {payload.query}\n\nProvide an evidence-grounded response."
    
    gen_response = client.models.generate_content(
        model='gemini-2.5-flash',
        contents=prompt
    )
    
    return {
        "answer": gen_response.text, 
        "retrieved_chunks": context_chunks
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
