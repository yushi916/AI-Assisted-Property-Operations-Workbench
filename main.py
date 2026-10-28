from fastapi import FastAPI

app = FastAPI(title="Property Operations Triage Agent v2")


@app.get("/health")
def health_check():
    return {"status": "ok"}