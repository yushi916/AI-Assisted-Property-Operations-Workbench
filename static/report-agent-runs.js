import {renderAgentRun, validateAgentRun} from "./agent-run-view.js?v=agent-readability-1";

const reportId = document.querySelector("#report-evidence").dataset.reportId;
const endpoint = `/api/service-requests/${encodeURIComponent(reportId)}/agent-runs`;
const storageKey = `property-v2-agent-run:${reportId}`;
const generate = document.querySelector("#generate-agent-run");
const refresh = document.querySelector("#refresh-agent-runs");
const history = document.querySelector("#agent-run-history");
const message = document.querySelector("#agent-run-message");
const uuid = value => typeof value === "string" && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value);
let busy = false, unavailable = false, activeRunning = false;
let pendingId = null, selectedId = null, savedRuns = [];
try {
    const stored = sessionStorage.getItem(storageKey);
    if (uuid(stored)) pendingId = stored;
    else if (stored) sessionStorage.removeItem(storageKey);
} catch { /* 内存仍可保留本页重试编号。 */ }

function remember(id) {
    pendingId = id;
    try { if (id) sessionStorage.setItem(storageKey, id); else sessionStorage.removeItem(storageKey); }
    catch { /* 禁用存储时可在当前标签页原样重试。 */ }
}

function syncButtons() {
    generate.disabled = busy || unavailable || activeRunning;
    refresh.disabled = busy;
    history.disabled = busy;
    generate.textContent = pendingId ? "重试本次核查" : "AI辅助核查";
}

function historyOptions() {
    history.replaceChildren(...savedRuns.map((run, index) => {
        const option = document.createElement("option"); option.value = run.id;
        const at = new Date(run.created_at).toLocaleString("zh-CN", {timeZone: "Asia/Shanghai", hour12: false});
        option.textContent = `${index + 1}．${at} · ${{running: "核查中", pending_review: "待审核", failed: "失败"}[run.status]}`;
        return option;
    }));
    document.querySelector("#agent-run-history-label").hidden = !savedRuns.length;
    history.value = selectedId ?? "";
}

function describe(run) {
    if (pendingId && run?.id !== pendingId) {
        message.textContent = "上次核查结果尚未确认。可查看已保存结果，或点击“重试本次核查”；将沿用原提交编号。";
    } else if (run?.status === "running" || activeRunning) {
        message.textContent = "有一项核查仍在处理中，请稍后点击“查看已保存结果”。等待期间可查看历史结果。";
    } else if (!run) {
        message.textContent = "还没有核查记录。点击“AI辅助核查”开始。";
    } else if (run.status === "failed") {
        const stages = {evidence_read: "查询或准备依据", suggestion_request: "请求AI分析", validation: "检查分析结果", tool_request: "旧版查询请求", tool_dispatch: "旧版工具查询"};
        message.textContent = `本次核查失败，失败记录已保留：${stages[run.failure_stage] ?? "核查过程"}（${run.error_code ?? "原因待查"}）。请查看失败说明，处理后可点击“AI辅助核查”发起新核查。`;
    } else {
        message.textContent = "";
    }
    message.hidden = !message.textContent;
}

async function request(requestId) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), requestId ? 40000 : 10000);
    const options = {cache: "no-store", signal: controller.signal};
    if (requestId) {
        options.method = "POST"; options.headers = {"Content-Type": "application/json"};
        options.body = JSON.stringify({request_id: requestId});
    }
    try {
        const response = await fetch(endpoint, options);
        if (!response.ok) { const error = new Error("核查操作未完成"); error.status = response.status; throw error; }
        return await response.json();
    } finally { clearTimeout(timeout); }
}

async function updateRuns(create) {
    if (busy || (create && (activeRunning || unavailable))) return;
    busy = true; syncButtons();
    message.hidden = false;
    message.textContent = create ? "正在自动查找已有记录并分析，完成后保存到本条报事…" : "正在读取已保存的核查…";
    try {
        let run;
        if (create) {
            const id = pendingId ?? crypto.randomUUID(); remember(id);
            run = validateAgentRun(await request(id), reportId);
            if (run.id !== id) throw Error("核查编号不一致");
            savedRuns = [run, ...savedRuns.filter(row => row.id !== run.id)].slice(0, 10);
            selectedId = run.id;
        } else {
            const rows = await request();
            if (!Array.isArray(rows)) throw Error("核查列表格式异常");
            const checked = rows.map(row => validateAgentRun(row, reportId));
            if (new Set(checked.map(row => row.id)).size !== checked.length) throw Error("核查列表编号重复");
            savedRuns = checked;
            run = savedRuns.find(row => row.id === pendingId) ?? savedRuns.find(row => row.id === selectedId) ?? savedRuns[0];
            selectedId = run?.id ?? null;
        }
        activeRunning = savedRuns.some(row => row.status === "running");
        renderAgentRun(run, reportId);
        if (run?.id === pendingId && run.status !== "running") remember(null);
        historyOptions(); describe(run);
    } catch (error) {
        if (error.status === 404) {
            unavailable = true; remember(null); selectedId = null; savedRuns = []; activeRunning = false;
            renderAgentRun(null, reportId); historyOptions();
            message.textContent = "未找到这条报事，请返回报事列表重新打开。";
        } else if (create && error.status === 409) {
            remember(null);
            message.textContent = "提交编号发生冲突，请先点击“查看已保存结果”。";
        } else {
            message.textContent = create ? "本次核查结果尚未确认。请查看已保存结果，或点击“重试本次核查”，将使用同一编号。"
                : "暂时无法读取已保存结果，已显示的内容保留。请稍后重试；AI核查会独立读取依据。";
        }
    } finally { busy = false; syncButtons(); }
}

history.addEventListener("change", () => {
    if (busy) return;
    const run = savedRuns.find(row => row.id === history.value);
    if (!run) return;
    selectedId = run.id; renderAgentRun(run, reportId); describe(run);
});
generate.addEventListener("click", () => updateRuns(true));
refresh.addEventListener("click", () => updateRuns(false));
syncButtons();
updateRuns(false);
