import os
from dotenv import load_dotenv
load_dotenv()
from google import genai
from sqlalchemy.orm import Session
from sqlalchemy import text
from database import sync_engine, Document, DocumentChunk, init_db

# Ensure tables exist
init_db()

DOCUMENTS = [
    {
        "filename": "nepal_trishuli_hydro_report.txt",
        "content": "The Trishuli River basin in Nepal experiences severe monsoon flooding between June and September. Historical crest levels exceeded 8.5 meters in 2021. The upper catchment is highly prone to Langtang debris avalanches, which can dam the river and cause outburst floods. High-risk elevation bands are below 1200m."
    },
    {
        "filename": "flood_management_protocol.txt",
        "content": "Emergency flood protocol for Trishuli Basin: When river crest exceeds 7.0m, early warning sirens must be activated. The primary vulnerability is concrete structures near the riverbank that increase imperviousness. Flood runoff velocity increases drastically when soil saturation is > 85%."
    },
    {
        "filename": "dam_capacity_stats.txt",
        "content": "Upper Trishuli 3A hydropower dam capacity is designed for a 1-in-100 year flood event. However, rapid snowmelt combined with intense monsoon rains can exceed the spillway capacity. Evacuation of downstream settlements within 500 meters of the riverbank is mandatory during Level 3 alerts."
    }
]

def seed_database():
    client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))
    with Session(sync_engine) as session:
        for doc_data in DOCUMENTS:
            existing = session.query(Document).filter(Document.filename == doc_data["filename"]).first()
            if existing:
                print(f"Skipping {doc_data['filename']}, already exists.")
                continue
            
            doc = Document(filename=doc_data["filename"], metadata_="{}")
            session.add(doc)
            session.flush() # get ID
            
            response = client.models.embed_content(
                model='text-embedding-004',
                contents=doc_data["content"]
            )
            embedding = response.embeddings[0].values
            
            chunk = DocumentChunk(
                document_id=doc.id,
                content=doc_data["content"],
                embedding=embedding,
                chunk_index=0
            )
            session.add(chunk)
            session.flush()
            
            # Update the TSVector
            session.execute(
                text("UPDATE document_chunks SET tsv = to_tsvector('english', :content) WHERE id = :id"),
                {"content": doc_data["content"], "id": chunk.id}
            )
            
            session.commit()
            print(f"Inserted {doc_data['filename']}")

if __name__ == "__main__":
    if not os.environ.get("GEMINI_API_KEY"):
        print("GEMINI_API_KEY is missing. Please set it in your environment.")
    else:
        seed_database()
        print("Database seeded successfully.")
