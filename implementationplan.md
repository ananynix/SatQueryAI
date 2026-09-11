# SatQuery AI: Implementation Plan

## Phase 1: Core Infrastructure & Boilerplate (Days 1-2)
- [ ] Initialize Git monorepo (frontend/, backend/, ml_core/).
- [ ] Setup FastAPI backend with Redis and Celery for asynchronous processing.
- [ ] Build React + Vite frontend with a chat interface and a geospatial image viewer (Leaflet).
- [ ] Implement a mock ML worker to test end-to-end data flow (Text + Image -> Backend -> Bounding Box JSON -> UI Rendering).

## Phase 2: Vision-Language Grounding (Days 3-5)
- [ ] Provision AWS EC2 `g5.xlarge` or Google Colab Pro for model development.
- [ ] Integrate GeoChat/LLaVA-1.5 architecture into the Celery worker.
- [ ] Configure LoRA (Low-Rank Adaptation) scripts for 4-bit quantized inference to reduce VRAM usage.
- [ ] Pre-process Sentinel-2 $512 \times 512$ GeoTIFF patches for input.
- [ ] Evaluate single-target grounding using $Acc@0.5$ and multi-target using $mAP_{50}$.

## Phase 3: Predictive Flood Modeling Pipeline (Days 6-8)
- [ ] Curate historical Sentinel-2 time-series data for known flood events.
- [ ] Train a U-Net architecture to classify "water" vs "no water" on current imagery.
- [ ] Implement a ConvLSTM (Convolutional Long Short-Term Memory) network. Feed historical U-Net segmentation masks, Digital Elevation Models (DEM), and precipitation data into the ConvLSTM to predict $t+1$ flooding zones.
- [ ] Expose the predictive model via a new FastAPI endpoint (`/predict-flood`).

## Phase 4: Integration & Optimization (Days 9-10)
- [ ] Overlay ConvLSTM predictive masks on the frontend map UI as a "Risk Overlay" toggle.
- [ ] Containerize the application using Docker and Docker Compose.
- [ ] Perform latency testing (Target: <400ms for grounding query, <2s for flood prediction).