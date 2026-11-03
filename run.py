"""Start Beezy:  python run.py   (then open http://localhost:8000)"""
import os

import uvicorn

if __name__ == "__main__":
    uvicorn.run("backend.app.main:app", host=os.getenv("HOST", "0.0.0.0"), port=int(os.getenv("PORT", "8000")),
                reload=os.getenv("RELOAD") == "1")
