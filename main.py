from pathlib import Path
from uuid import UUID

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from routers.reports import router as reports_router
from fastapi.staticfiles import StaticFiles
from routers.locations import router as locations_router
from routers.plans import router as plans_router
from routers.executions import router as executions_router
from routers.inspections import router as inspections_router
from routers.plan_evidence import router as plan_evidence_router
from routers.plan_links import router as plan_links_router
from routers.report_evidence import router as report_evidence_router
from routers.agent_runs import router as agent_runs_router


BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(title="Property Operations Triage Agent v2")
app.include_router(reports_router)
app.include_router(locations_router)
app.include_router(plans_router)
app.include_router(executions_router)
app.include_router(inspections_router)
app.include_router(plan_evidence_router)
app.include_router(plan_links_router)
app.include_router(report_evidence_router)
app.include_router(agent_runs_router)

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

@app.get(
    "/executions",
    response_class=HTMLResponse,
    include_in_schema=False,
    name="executions_page",
)
def executions_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="executions.html",
        context={"page_title": "计划执行记录"},
    )

@app.get(
    "/inspections",
    response_class=HTMLResponse,
    include_in_schema=False,
    name="inspections_page",
)
def inspections_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="inspections.html",
        context={"page_title": "计划巡查记录"},
    )

@app.get(
    "/plan-evidence",
    response_class=HTMLResponse,
    include_in_schema=False,
    name="plan_evidence_page",
)
def plan_evidence_page(request: Request, plan_id: UUID):
    return templates.TemplateResponse(
        request=request,
        name="plan-evidence.html",
        context={"page_title": "计划证据核对", "plan_id": str(plan_id)},
    )


@app.get(
    "/report-evidence", response_class=HTMLResponse,
    include_in_schema=False, name="report_evidence_page",
)
def report_evidence_page(request: Request, report_id: UUID):
    return templates.TemplateResponse(
        request=request, name="report-evidence.html",
        context={"page_title": "报事与履约证据核查", "report_id": str(report_id)},
    )