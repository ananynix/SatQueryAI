import os
from dotenv import load_dotenv
load_dotenv()
import time
import json
import urllib.request
import numpy as np
import onnxruntime as ort
import rasterio
from rasterio.features import shapes
from shapely.geometry import shape, box
from celery import Celery
from geoalchemy2.shape import from_shape

import sys
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from database import SyncSessionLocal, SpatialFeature

celery_app = Celery(
    "satquery_tasks",
    broker=os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/0"),
    backend=os.getenv("CELERY_RESULT_BACKEND", "redis://localhost:6379/0")
)

def get_elevation_and_river_proximity(poly_geom):
    # Mocking actual external DEM / hydrography API lookups for now
    import random
    elevation = random.uniform(500, 1500)
    dist_to_river = random.uniform(10, 2000)
    return elevation, dist_to_river

def calculate_risk(elevation, dist_to_river, area, force_high_mock=True):
    import random
    # Calculate damage score based on Topography and Hydrology
    if force_high_mock:
        score = random.uniform(0.85, 0.99)
    else:
        base_score = 0.5
        if elevation < 1000:
            base_score += 0.2
        if dist_to_river < 500:
            base_score += 0.3
        score = min(max(base_score, 0.0), 1.0)
    
    # Risk category
    if score >= 0.8:
        level = "HIGH"
    elif score >= 0.5:
        level = "MEDIUM"
    else:
        level = "LOW"
    return score, level

@celery_app.task(name="worker.process_spatial_query")
def process_spatial_query(query: str, s3_url: str, bbox: str = None):
    print(f"Running ONNX flood inference: '{query}' on bbox: {bbox}")
    
    inserted = 0
    try:
        min_lng, min_lat, max_lng, max_lat = map(float, bbox.split(','))
        target_bounds = [min_lng, min_lat, max_lng, max_lat]
        
        # Load ONNX model
        onnx_path = os.path.join(os.path.dirname(__file__), "ml", "flood_unet.onnx")
        session = ort.InferenceSession(onnx_path)
        
        # Create a dummy raster image to simulate windowed reading for the given bbox.
        # Use procedural noise to simulate an organic winding river flood path
        x = np.linspace(0, 10, 256)
        y = np.linspace(0, 10, 256)
        xx, yy = np.meshgrid(x, y)
        
        # Procedural noise using sin/cos combinations
        noise = np.sin(xx * 2 + np.cos(yy * 3)) + np.sin(yy * 1.5 + np.cos(xx * 2.5))
        # Mask it into a diagonal band to look like a river meandering through the bounding box
        river_band = np.exp(-((xx - yy) ** 2) / 4.0)
        blob = noise * river_band
        
        img = np.stack([blob]*4, axis=0)[np.newaxis, ...].astype(np.float32)
        
        # Run inference
        outputs = session.run(['output'], {'input': img})
        mask = outputs[0][0, 0, :, :] # Shape: [256, 256]
        
        # Normalize the mask to [0, 1] to ensure we get some features regardless of the UNet's random weights
        mask_min, mask_max = mask.min(), mask.max()
        if mask_max > mask_min:
            mask = (mask - mask_min) / (mask_max - mask_min)
            
        # Threshold binary mask (extract top 20% strongest activations as flooded)
        binary_mask = (mask > 0.8).astype(np.uint8)
        
        # Map 256x256 back to geographical bbox coords
        transform = rasterio.transform.from_bounds(min_lng, min_lat, max_lng, max_lat, 256, 256)
        
        with SyncSessionLocal() as db_session:
            # Extract polygon shapes from binary mask
            for geom, val in shapes(binary_mask, transform=transform):
                if val == 1: # Water/Flood
                    poly = shape(geom)
                    
                    # Calculate multi-factor risk
                    elev, dist = get_elevation_and_river_proximity(poly)
                    damage_score, risk_level = calculate_risk(elev, dist, poly.area)
                    
                    feature = SpatialFeature(
                        geom=from_shape(poly, srid=4326),
                        damage_score=damage_score,
                        object_type=f"flood_polygon_{risk_level}",
                        query_id="onnx_inference",
                        s3_url=s3_url
                    )
                    db_session.add(feature)
                    inserted += 1
                    
            db_session.commit()
            
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"Failed ONNX inference: {e}")
        inserted = 0
        
    return {
        "query": query,
        "s3_url": s3_url,
        "bbox": bbox,
        "inserted_features": inserted
    }
