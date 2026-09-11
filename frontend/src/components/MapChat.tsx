import { useState, useEffect, useRef } from 'react';
import Map, { Source, Layer } from 'react-map-gl/maplibre';
import 'maplibre-gl/dist/maplibre-gl.css';
import axios from 'axios';

interface QueryResult {
  inserted_features: number;
}

// Completely free global satellite layer (Esri World Imagery)
const openSatelliteStyle = {
  version: 8,
  sources: {
    'esri-satellite': {
      type: 'raster',
      tiles: [
        'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}'
      ],
      tileSize: 256,
    }
  },
  layers: [
    {
      id: 'satellite-basemap',
      type: 'raster',
      source: 'esri-satellite',
      minzoom: 0,
      maxzoom: 22
    }
  ]
};

const MapChat = () => {
  const [s3Url, setS3Url] = useState<string>('');
  const [query, setQuery] = useState<string>('');
  const [isProcessing, setIsProcessing] = useState<boolean>(false);
  const [result, setResult] = useState<QueryResult | null>(null);
  const [file, setFile] = useState<File | null>(null);
  
  const [tileUrl, setTileUrl] = useState<string>('');
  const [vectorData, setVectorData] = useState<any>(null);
  const mapRef = useRef<any>(null);

  const fetchVectors = async () => {
    if (!mapRef.current) return;
    const bounds = mapRef.current.getMap().getBounds();
    const bboxStr = `${bounds.getWest()},${bounds.getSouth()},${bounds.getEast()},${bounds.getNorth()}`;
    
    try {
      const res = await axios.get(`http://localhost:8000/api/vectors?bbox=${bboxStr}`);
      setVectorData(res.data);
    } catch (err) {
      console.error("Failed to fetch vectors", err);
    }
  };

  // Fetch initial vectors once the map is loaded
  useEffect(() => {
    // Delay slightly to ensure map is mounted and bounds are valid
    const timer = setTimeout(() => {
      fetchVectors();
    }, 1000);
    return () => clearTimeout(timer);
  }, []);

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files && e.target.files[0]) {
      setFile(e.target.files[0]);
    }
  };

  const fetchMetadata = async (url: string) => {
    try {
      const res = await axios.get(`http://localhost:8000/cog/WebMercatorQuad/tilejson.json?url=${encodeURIComponent(url)}`);
      const b = res.data.bounds;
      if (b && b.length === 4 && mapRef.current) {
        // Mapbox fitBounds expects [[minLng, minLat], [maxLng, maxLat]]
        mapRef.current.fitBounds([
          [b[0], b[1]],
          [b[2], b[3]]
        ], { padding: 40, duration: 1000 });
      }
      
      if (res.data.tiles && res.data.tiles.length > 0) {
        let tUrl = res.data.tiles[0];
        // TiTiler sometimes omits the router prefix in TileJSON
        if (tUrl.includes('/tiles/') && !tUrl.includes('/cog/tiles/')) {
          tUrl = tUrl.replace('/tiles/', '/cog/tiles/');
        }
        setTileUrl(tUrl);
      } else {
        setTileUrl(`http://localhost:8000/cog/tiles/WebMercatorQuad/{z}/{x}/{y}?url=${encodeURIComponent(url)}`);
      }
    } catch (err) {
      console.error("Failed to fetch image metadata", err);
    }
  };

  const handleUpload = async () => {
    if (!file) return;
    setIsProcessing(true);
    setResult(null);
    try {
      const formData = new FormData();
      formData.append('file', file);
      
      const res = await axios.post('http://localhost:8000/api/upload', formData, {
        headers: { 'Content-Type': 'multipart/form-data' }
      });
      const url = res.data.s3_url;
      setS3Url(url);
      
      await fetchMetadata(url);
    } catch (error) {
      console.error('Error uploading file:', error);
    } finally {
      setIsProcessing(false);
    }
  };

  const handleAnalyzeView = async () => {
    if (!mapRef.current) return;
    const bounds = mapRef.current.getMap().getBounds();
    const bboxStr = `${bounds.getWest()},${bounds.getSouth()},${bounds.getEast()},${bounds.getNorth()}`;

    setIsProcessing(true);
    setResult(null);
    try {
      const formData = new FormData();
      formData.append('query', "Analyze current view for floods");
      formData.append('bbox', bboxStr);

      const res = await axios.post('http://localhost:8000/api/query', formData, {
        headers: { 'Content-Type': 'multipart/form-data' }
      });
      const taskId = res.data.task_id;

      const poll = setInterval(async () => {
        const statusRes = await axios.get(`http://localhost:8000/api/task/${taskId}`);
        if (statusRes.data.state === 'SUCCESS') {
          setResult(statusRes.data.result);
          setIsProcessing(false);
          clearInterval(poll);
          fetchVectors();
        } else if (statusRes.data.state === 'FAILURE') {
          setIsProcessing(false);
          clearInterval(poll);
        }
      }, 1000);
    } catch (error) {
      console.error('Error querying:', error);
      setIsProcessing(false);
    }
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!query) return;
    
    let bboxStr = '';
    if (!s3Url && mapRef.current) {
        const bounds = mapRef.current.getMap().getBounds();
        bboxStr = `${bounds.getWest()},${bounds.getSouth()},${bounds.getEast()},${bounds.getNorth()}`;
    }

    setIsProcessing(true);
    setResult(null);
    try {
      const formData = new FormData();
      formData.append('query', query);
      if (s3Url) formData.append('s3_url', s3Url);
      if (bboxStr) formData.append('bbox', bboxStr);

      const res = await axios.post('http://localhost:8000/api/query', formData, {
        headers: { 'Content-Type': 'multipart/form-data' }
      });
      const taskId = res.data.task_id;

      const poll = setInterval(async () => {
        const statusRes = await axios.get(`http://localhost:8000/api/task/${taskId}`);
        if (statusRes.data.state === 'SUCCESS') {
          setResult(statusRes.data.result);
          setIsProcessing(false);
          clearInterval(poll);
          fetchVectors();
        } else if (statusRes.data.state === 'FAILURE') {
          setIsProcessing(false);
          clearInterval(poll);
        }
      }, 1000);

    } catch (error) {
      console.error('Error querying:', error);
      setIsProcessing(false);
    }
  };

  // --- Mapbox Layer Definitions ---
  
  // 1. Dynamic Clustering Bubbles (zoom < 14)
  const clusterLayer: any = {
    id: 'clusters',
    type: 'circle',
    source: 'earth-data-points',
    filter: ['has', 'point_count'],
    paint: {
      'circle-color': [
        'step', 
        ['get', 'point_count'], 
        'rgba(81, 187, 214, 0.85)', 
        10, 
        'rgba(241, 240, 117, 0.85)', 
        50, 
        'rgba(242, 140, 177, 0.85)'
      ],
      'circle-radius': ['step', ['get', 'point_count'], 16, 50, 22, 200, 28]
    }
  };

  const clusterCountLayer: any = {
    id: 'cluster-count',
    type: 'symbol',
    source: 'earth-data-points',
    filter: ['has', 'point_count'],
    layout: {
      'text-field': '{point_count_abbreviated}',
      'text-font': ['Arial Unicode MS Bold'], // safe default
      'text-size': 12
    }
  };

  // 2. Data-Driven Bounding Boxes (zoom >= 14)
  const unclusteredPolygonLayer: any = {
    id: 'unclustered-polygon',
    type: 'fill',
    source: 'earth-data',
    minzoom: 13,
    paint: {
      'fill-color': [
        'case', 
        ['>=', ['get', 'damage_score'], 0.8], 
        '#ff0000', 
        '#fbbc05'
      ],
      'fill-opacity': 0.6,
      'fill-outline-color': '#ffffff'
    }
  };

  const cogLayer: any = {
    id: 'titiler-cog-layer',
    type: 'raster',
    source: 'titiler-cog',
    paint: {
      'raster-opacity': 1.0
    }
  };

  // Analytics Computation
  const getAnalytics = () => {
    if (!vectorData || !vectorData.features || vectorData.features.length === 0) {
      return null;
    }
    const features = vectorData.features;
    let highRiskCount = 0;
    let avgDamage = 0;
    
    features.forEach((f: any) => {
      const score = f.properties.damage_score || 0;
      avgDamage += score;
      if (score >= 0.8) highRiskCount++;
    });
    
    avgDamage = avgDamage / features.length;
    
    return {
      total: features.length,
      highRisk: highRiskCount,
      avgDamage: avgDamage
    };
  };
  const analytics = getAnalytics();

  // Convert polygons to points for MapLibre clustering
  const pointData = vectorData ? {
    type: 'FeatureCollection',
    features: vectorData.features.map((f: any) => {
      let coords = f.geometry.coordinates[0];
      if (!coords) return null;
      let lngSum = 0; let latSum = 0;
      coords.forEach((c: any) => {
        lngSum += c[0];
        latSum += c[1];
      });
      return {
        type: 'Feature',
        geometry: {
          type: 'Point',
          coordinates: [lngSum / coords.length, latSum / coords.length]
        },
        properties: f.properties
      };
    }).filter(Boolean)
  } : null;

  return (
    <div className="flex h-screen bg-neutral-900 text-white font-sans overflow-hidden">
      {/* Sidebar Interface */}
      <div className="w-1/3 max-w-sm bg-neutral-800 p-6 flex flex-col shadow-2xl z-10 border-r border-white/10 backdrop-blur-md overflow-y-auto">
        <h1 className="text-3xl font-extrabold mb-8 bg-gradient-to-r from-blue-400 to-emerald-400 bg-clip-text text-transparent">
          SatQuery AI
        </h1>

        <div className="mb-6 bg-white/5 p-4 rounded-2xl border border-white/10">
          <label className="block text-sm font-medium mb-3 text-neutral-300">
            Global Analysis
          </label>
          <button 
            onClick={handleAnalyzeView}
            disabled={isProcessing}
            className="w-full bg-emerald-600 hover:bg-emerald-500 text-white py-3 rounded-xl font-semibold transition-colors disabled:opacity-50 cursor-pointer shadow-lg shadow-emerald-500/20"
          >
            {isProcessing ? 'Analyzing Area...' : 'Analyze Current View'}
          </button>
        </div>

        <div className="mb-6 flex items-center">
          <div className="flex-1 h-px bg-white/10"></div>
          <div className="px-4 text-xs text-neutral-500 font-semibold uppercase tracking-wide">OR UPLOAD TIFF</div>
          <div className="flex-1 h-px bg-white/10"></div>
        </div>

        <div className="mb-6 bg-white/5 p-4 rounded-2xl border border-white/10">
          <input 
            type="file" 
            onChange={handleFileChange}
            className="block w-full text-sm text-neutral-400
              file:mr-4 file:py-2 file:px-4
              file:rounded-full file:border-0
              file:text-sm file:font-semibold
              file:bg-blue-500/20 file:text-blue-400
              hover:file:bg-blue-500/30 transition-all cursor-pointer"
          />
          <button 
            onClick={handleUpload}
            disabled={!file || isProcessing}
            className="mt-4 w-full bg-blue-600 hover:bg-blue-500 text-white py-2 rounded-xl font-semibold transition-colors disabled:opacity-50 cursor-pointer"
          >
            Upload to MinIO
          </button>
        </div>

        {s3Url && (
          <div className="mb-6 p-4 rounded-2xl bg-emerald-500/10 border border-emerald-500/20 text-emerald-400 text-sm break-all">
            <strong>Active Image:</strong><br/>
            {s3Url}
          </div>
        )}

        {result && (
          <div className="mb-6 p-4 rounded-2xl bg-blue-500/10 border border-blue-500/20 text-blue-400 text-sm">
            Successfully parsed and stored {result.inserted_features} geospatial features into PostGIS.
          </div>
        )}

        {/* Analytics Panel Integrated in Sidebar */}
        <div className="mb-6 bg-neutral-900/50 p-5 rounded-2xl border border-white/5 shadow-inner text-white">
          <h2 className="text-xl font-bold mb-5 flex items-center gap-2">
            <svg className="w-5 h-5 text-blue-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z"></path></svg>
            Area Analytics
          </h2>
          {analytics ? (
            <div className="space-y-5">
              <div>
                <div className="text-[10px] text-neutral-400 font-bold uppercase tracking-widest mb-1">Current Status</div>
                <div className="text-2xl font-light">{analytics.total} <span className="text-sm text-neutral-400 font-normal">Features</span></div>
              </div>
              <div>
                <div className="text-[10px] text-neutral-400 font-bold uppercase tracking-widest mb-1">Issued Warnings</div>
                {analytics.highRisk > 0 ? (
                  <div className="text-red-400 font-semibold text-lg flex items-center gap-2 bg-red-500/10 py-2 px-3 rounded-xl border border-red-500/20">
                    <span className="relative flex h-3 w-3">
                      <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-red-400 opacity-75"></span>
                      <span className="relative inline-flex rounded-full h-3 w-3 bg-red-500"></span>
                    </span>
                    {analytics.highRisk} High Risk Zones
                  </div>
                ) : (
                  <div className="text-emerald-400 font-semibold text-sm flex items-center gap-2 bg-emerald-500/10 py-2 px-3 rounded-xl border border-emerald-500/20">
                    <span className="h-2 w-2 rounded-full bg-emerald-500"></span>
                    All Clear (Normal)
                  </div>
                )}
              </div>
              <div>
                <div className="text-[10px] text-neutral-400 font-bold uppercase tracking-widest mb-1">Damage Severity Index</div>
                <div className="w-full bg-neutral-800 h-2.5 mt-2 rounded-full overflow-hidden shadow-inner">
                  <div 
                    className="h-full bg-gradient-to-r from-emerald-400 via-yellow-400 to-red-500 transition-all duration-1000" 
                    style={{ width: `${Math.min(100, analytics.avgDamage * 100)}%` }} 
                  />
                </div>
                <div className="text-right text-[11px] mt-1.5 font-mono text-neutral-400">{(analytics.avgDamage * 10).toFixed(1)} / 10.0</div>
              </div>
              <div className="pt-4 border-t border-white/10 mt-2">
                <div className="text-[10px] text-neutral-400 font-bold uppercase tracking-widest mb-2">Past Flooding Conditions</div>
                <div className="text-xs leading-relaxed text-neutral-300">
                  {analytics.highRisk > 10 ? 
                    "Historically prone to severe seasonal floods. Ground saturation is highly likely in this bounding region." : 
                    "Moderate historical flood risk. Drainage systems typically handle average rainfall."}
                </div>
              </div>
            </div>
          ) : (
            <div className="text-sm text-neutral-400 animate-pulse bg-white/5 p-4 rounded-xl text-center">
              Pan the map or click "Analyze Current View" to gather data...
            </div>
          )}
        </div>

        <form onSubmit={handleSubmit} className="mt-auto pt-4">
          <div className="relative group">
            <input 
              type="text"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder={s3Url ? "Ask about this TIFF..." : "Ask about the map view..."}
              className="w-full bg-neutral-900 border border-neutral-700 rounded-2xl px-4 py-4 pr-24 text-white focus:outline-none focus:ring-2 focus:ring-blue-500 transition-all shadow-inner"
              disabled={isProcessing}
            />
            <button 
              type="submit"
              disabled={!query || isProcessing}
              className="absolute right-2 top-2 bottom-2 bg-blue-600 hover:bg-blue-500 px-4 rounded-xl font-bold transition-all disabled:opacity-50 cursor-pointer"
            >
              {isProcessing ? 'Wait...' : 'Send'}
            </button>
          </div>
        </form>
      </div>

      {/* Global Mapbox View */}
      <div className="flex-1 relative bg-black">
        <Map
          ref={mapRef}
          initialViewState={{ longitude: 0, latitude: 0, zoom: 2 }}
          mapStyle={openSatelliteStyle as any}
          onMoveEnd={fetchVectors}
          style={{ width: '100%', height: '100%' }}
        >
          {/* Layer 1: TiTiler COG Layer (Base custom image) */}
          {tileUrl && (
            <Source id="titiler-cog" type="raster" tiles={[tileUrl]} tileSize={256}>
              <Layer {...cogLayer} />
            </Source>
          )}

          {/* Layer 2: Vector GeoJSON Layer (AI inferences rendered on top as Fills) */}
          {vectorData && (
            <Source
              id="earth-data"
              type="geojson"
              data={vectorData}
            >
              <Layer {...unclusteredPolygonLayer} />
            </Source>
          )}

          {/* Layer 3: Clustered Points (Zoomed out view) */}
          {pointData && (
            <Source
              id="earth-data-points"
              type="geojson"
              data={pointData}
              cluster={true}
              clusterMaxZoom={13}
              clusterRadius={120}
            >
              <Layer {...clusterLayer} />
              <Layer {...clusterCountLayer} />
            </Source>
          )}
        </Map>
      </div>
    </div>
  );
};

export default MapChat;
