import os
from sqlalchemy import Column, Integer, String, Float, create_engine, Index
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, declarative_base
from geoalchemy2 import Geometry

# Database URLs
# Synchronous URL for Celery worker (psycopg2)
SYNC_DB_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/satquery")
# Asynchronous URL for FastAPI (asyncpg)
ASYNC_DB_URL = os.getenv("ASYNC_DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:5432/satquery")

# Synchronous Engine (for table creation and Celery)
sync_engine = create_engine(SYNC_DB_URL, echo=False)
SyncSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=sync_engine)

# Asynchronous Engine (for FastAPI)
async_engine = create_async_engine(ASYNC_DB_URL, echo=False)
AsyncSessionLocal = sessionmaker(async_engine, class_=AsyncSession, expire_on_commit=False)

Base = declarative_base()

from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy import text, Computed
from datetime import datetime


# --- Live AI-inference layer (ONNX worker output, uploaded-imagery analysis) ---

class SpatialFeature(Base):
    """Polygons produced at query-time by the ONNX flood-inference worker.

    This is the interactive "analyze this view / this uploaded GeoTIFF" layer,
    distinct from the pre-seeded, curated disaster-zone layers below.
    """
    __tablename__ = "spatial_features"

    id = Column(Integer, primary_key=True, index=True)
    geom = Column(Geometry('POLYGON', srid=4326), nullable=False)
    damage_score = Column(Float, nullable=True)
    object_type = Column(String, nullable=True)
    query_id = Column(String, index=True, nullable=True)
    s3_url = Column(String, index=True, nullable=True)


# --- Curated multi-tier GIS layers (real Rasuwa / Trishuli-Bhote Koshi basin) ---

class FloodZone(Base):
    """Zone Polygons: flood extent / inundation footprint."""
    __tablename__ = "flood_zones"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    geom = Column(Geometry('MULTIPOLYGON', srid=4326), nullable=False)
    inundation_depth_m = Column(Float, nullable=True)
    velocity_ms = Column(Float, nullable=True)
    risk_level = Column(String, nullable=False, index=True)  # CRITICAL | WARNING | ADVISORY
    description = Column(String, nullable=True)


class NetworkLine(Base):
    """Network Lines: river drainage centerlines and transportation arteries."""
    __tablename__ = "network_lines"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    geom = Column(Geometry('LINESTRING', srid=4326), nullable=False)
    line_type = Column(String, nullable=False, index=True)  # river | road
    status = Column(String, nullable=False, index=True)  # SUBMERGED | COMPROMISED | ACCESSIBLE
    description = Column(String, nullable=True)


class AssetPoint(Base):
    """Asset Points: critical infrastructure & residential structures."""
    __tablename__ = "asset_points"

    id = Column(Integer, primary_key=True, index=True)
    asset_id = Column(String, nullable=False, unique=True, index=True)
    name = Column(String, nullable=True)
    geom = Column(Geometry('POINT', srid=4326), nullable=False)
    elevation_m = Column(Float, nullable=True)
    damage_prob = Column(Float, nullable=True)
    asset_type = Column(String, nullable=True, index=True)  # hydropower | bridge | settlement | monitoring_station


class ResidentialZone(Base):
    """Residential-area footprints, shown so responders can see which populated
    areas sit inside/near a flood zone. `population_estimate` is left null when we
    don't have a sourced figure -- we never fabricate a population count."""
    __tablename__ = "residential_zones"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    geom = Column(Geometry('MULTIPOLYGON', srid=4326), nullable=False)
    population_estimate = Column(Integer, nullable=True)
    population_source = Column(String, nullable=True)
    overlap_risk_level = Column(String, nullable=True, index=True)  # derived: CRITICAL | WARNING | ADVISORY | None


# --- Historical flood event records (region-generalized, not Trishuli-specific) ---

class HistoricalFloodEvent(Base):
    """A real, documented past flood event, geolocated so it surfaces for
    whatever region a user pans/zooms to -- not just the Trishuli basin.

    This is intentionally a small, hand-curated reference set (see seed_data.py)
    for demonstration purposes, not a comprehensive historical flood database.
    Coordinates are approximate (district/river-basin level), not point-precise.
    """
    __tablename__ = "historical_flood_events"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    geom = Column(Geometry('POINT', srid=4326), nullable=False, index=True)
    country = Column(String, nullable=True, index=True)
    region = Column(String, nullable=True, index=True)
    year = Column(Integer, nullable=False, index=True)
    month = Column(Integer, nullable=True)  # 1-12, for seasonal pattern analysis
    rivers = Column(String, nullable=True)
    summary = Column(String, nullable=False)
    source_note = Column(String, nullable=True)  # what the facts are sourced from


# --- Hybrid RAG knowledge base ---

class Document(Base):
    __tablename__ = "documents"

    id = Column(Integer, primary_key=True, index=True)
    filename = Column(String, index=True, nullable=False)
    created_at = Column(String, default=lambda: datetime.utcnow().isoformat())
    metadata_ = Column(String, nullable=True)


class DocumentChunk(Base):
    __tablename__ = "document_chunks"

    id = Column(Integer, primary_key=True, index=True)
    document_id = Column(Integer, index=True, nullable=False)
    content = Column(String, nullable=False)
    embedding = Column(Vector(768))
    # Generated/stored column: kept in sync by Postgres itself, no manual UPDATE needed.
    tsv = Column(
        TSVECTOR,
        Computed("to_tsvector('english', content)", persisted=True),
    )
    chunk_index = Column(Integer)


Index("ix_document_chunks_tsv", DocumentChunk.tsv, postgresql_using="gin")


def init_db():
    with sync_engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS postgis;"))
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector;"))
    Base.metadata.create_all(bind=sync_engine)
