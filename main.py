from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from routers.reports import router as reports_router



BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(title="Property Operations Triage Agent v2")
app.include_router(reports_router)
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


@app.get("/health")
def health_check():
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"page_title": "物业事项与履约证据工作台"},
    )