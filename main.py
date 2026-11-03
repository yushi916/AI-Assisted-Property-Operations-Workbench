from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from routers.reports import router as reports_router
from fastapi.staticfiles import StaticFiles
from routers.locations import router as locations_router
from routers.plans import router as plans_router
from routers.executions import router as executions_router


BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(title="Property Operations Triage Agent v2")
app.include_router(reports_router)
app.include_router(locations_router)
app.include_router(plans_router)
app.include_router(executions_router)
app.mount(
    "/static",
    StaticFiles(directory=BASE_DIR / "static"),
    name="static",
)
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

@app.get(
    "/reports",
    response_class=HTMLResponse,
    include_in_schema=False,
    name="reports_page",
)
def reports_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="reports.html",
        context={"page_title": "报事记录"},
    )


@app.get(
    "/plans",
    response_class=HTMLResponse,
    include_in_schema=False,
    name="plans_page",
)
def plans_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="plans.html",
        context={"page_title": "录入物业计划"},
    )