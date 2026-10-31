import uvicorn
import os
from dotenv import load_dotenv

load_dotenv()

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    host = os.getenv("HOST", "0.0.0.0")
    print(f"🚀 Starting TravelForge RAG Server on {host}:{port}...")
    uvicorn.run("app.main:app", host=host, port=port, reload=True)
