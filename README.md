# SatQuery AI

SatQuery AI is an intelligent platform designed for satellite imagery analysis and predictive flood modeling using vision-language grounding models and time-series forecasting.

## Architecture

The project is structured as a monorepo with the following components:

- **Frontend**: A modern React + Vite web application offering a chat interface and geospatial image viewer using Leaflet. Located in the `frontend/` directory.
- **Backend**: A high-performance FastAPI application handling API requests, database interactions, and asynchronous job queues. Located in the `backend/` directory.
- **ML Worker**: Asynchronous workers (powered by Celery) to run inference on satellite imagery, including visual grounding (using models like GeoChat/LLaVA) and flood prediction (ConvLSTM / U-Net).
- **Infrastructure**: Containerized services using Docker Compose, including:
  - **PostgreSQL**: For relational data storage.
  - **Redis**: As a message broker for Celery and caching.
  - **MinIO**: S3-compatible object storage for managing Cloud Optimized GeoTIFFs (COGs).

## Key Features

1. **Vision-Language Grounding**: Chat with satellite imagery. Ask questions and get visual grounding on Sentinel-2 patches.
2. **Predictive Flood Modeling**: Utilize historical time-series data, DEMs, and precipitation data to forecast flood zones.
3. **Interactive UI**: View image overlays, bounding boxes, and chat histories seamlessly.

## Getting Started

### Prerequisites
- Docker & Docker Compose
- Node.js (for frontend development)
- Python 3.10+ (for backend and ML development)

### Infrastructure Setup
Start the necessary infrastructure services (Database, Redis, MinIO) using Docker Compose:
```bash
docker-compose up -d
```
MinIO will automatically initialize a public bucket named `satquery-cogs` for storing satellite imagery.

### Backend Setup
```bash
cd backend
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
pip install -r requirements.txt
uvicorn main:app --reload
```

### Frontend Setup
```bash
cd frontend
pnpm install
pnpm dev
```

## Development Roadmap
See `implementationplan.md` for a detailed breakdown of the development phases, from core infrastructure to advanced predictive modeling.
