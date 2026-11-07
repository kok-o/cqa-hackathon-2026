from pathlib import Path

from fastapi import FastAPI

from .importer import normalize
from .models import Conversation, DatasetExport


ROOT = Path(__file__).resolve().parents[2]
app = FastAPI(title="CQA Hackathon Starter", version="0.1.0")


@app.get("/health")
def health():
    return {"status": "ok", "phase": "scaffold", "version": app.version}


@app.get("/api/demo/conversations", response_model=list[Conversation])
def demo_conversations():
    source = ROOT / "fixtures" / "synthetic_dev.json"
    export = DatasetExport.model_validate_json(source.read_text(encoding="utf-8"))
    return normalize(export)
