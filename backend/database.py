import os
from sqlalchemy import Column, Integer, String, Float, create_engine
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
from sqlalchemy import text
from datetime import datetime

class SpatialFeature(Base):
    __tablename__ = "spatial_features"

    id = Column(Integer, primary_key=True, index=True)
    geom = Column(Geometry('POLYGON', srid=4326), nullable=False)
    damage_score = Column(Float, nullable=True)
    object_type = Column(String, nullable=True)
    query_id = Column(String, index=True, nullable=True)
    s3_url = Column(String, index=True, nullable=True)

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
    tsv = Column(TSVECTOR)
    chunk_index = Column(Integer)

def init_db():
    with sync_engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS postgis;"))
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector;"))
    Base.metadata.create_all(bind=sync_engine)

