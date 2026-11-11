import {watchEvidence} from "./report-evidence.js?v=link-actions-v1";

const reportId = document.querySelector("#report-evidence").dataset.reportId;
const endpoint = `/api/service-requests/${encodeURIComponent(reportId)}/agent-runs`;
const storageKey = `property-v2-agent-run:${reportId}`;
const generate = document.querySelector("#generate-agent-run");
const refresh = document.querySelector("#refresh-agent-runs");
const message = document.querySelector("#agent-run-message");
const result = document.querySelector("#agent-run-result");
const draft = document.querySelector("#agent-run-draft");
const statuses = {running: "生成中", pending_review: "待审核", failed: "生成失败"};
let evidenceReady = false;
let busy = false;
let pendingId = null;
try {
    pendingId = sessionStorage.getItem(storageKey);
} catch {
    // 浏览器禁用存储时，当前页面仍可使用同一请求编号重试。
}

function remember(id) {
    pendingId = id;
    try {
        if (id) sessionStorage.setItem(storageKey, id);
        else sessionStorage.removeItem(storageKey);
    } catch {
        // 内存中的编号仍可用于本页重试。
    }
}

function syncButtons() {
    generate.disabled = busy || !evidenceReady;
    refresh.disabled = busy;
    generate.textContent = pendingId ? "重试本次生成" : "生成 Gemini 草稿";
}

function listItem(text) {
    const item = document.createElement("li");
    item.textContent = text;
    return item;
}

function validateRun(run) {
    if (run?.report_id !== reportId || typeof run.id !== "string" || !Object.hasOwn(statuses, run.status)) {
        throw new Error("运行记录与本条报事不一致");
    }
    return run;
}

function renderRun(run) {
    result.hidden = !run;
    if (!run) return;
    draft.hidden = run.status !== "pending_review";
    document.querySelector("#agent-run-meta").textContent =
        `记录编号 ${run.id} · ${run.model_name} · ${statuses[run.status]}`;

    const scopes = Object.entries(run.evidence?.records ?? {}).flatMap(([ref, record]) => {
        const scope = record.inspection_scope;
        return ref.startsWith("plan:") && scope ? [listItem(
            `${record.title ?? ref} · 核对点位 ${scope.report_location_id} · 匹配巡查 ${scope.matched_inspection_count} 条 · 排除其他点位巡查 ${scope.excluded_other_location_inspection_count} 条`,
        )] : [];
    });
    document.querySelector("#agent-run-scope").replaceChildren(...(
        scopes.length ? scopes : [listItem("本次运行记录没有巡查范围摘要。")]
    ));

    if (draft.hidden) return;
    document.querySelector("#agent-run-observations").replaceChildren(...run.suggestion.observations.map(
        item => listItem(`${item.text}（引用：${item.evidence_refs.join("、")}）`),
    ));
    document.querySelector("#agent-run-verification").replaceChildren(
        ...run.suggestion.verification_items.map(listItem),
    );
}

async function request(requestId) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), requestId ? 40000 : 10000);
    const options = {cache: "no-store", signal: controller.signal};
    if (requestId) {
        options.method = "POST";
        options.headers = {"Content-Type": "application/json"};
        options.body = JSON.stringify({request_id: requestId});
    }
    try {
        const response = await fetch(endpoint, options);
        if (!response.ok) {
            const error = new Error("操作未完成");
            error.status = response.status;
            throw error;
        }
        return await response.json();
    } finally {
        clearTimeout(timeout);
    }
}

async function updateRuns(create) {
    if (busy || (create && !evidenceReady)) return;
    busy = true;
    syncButtons();
    message.textContent = create ? "正在生成，完成后会保存到本条报事…" : "正在读取已保存记录…";
    try {
        let run;
        if (create) {
            const id = pendingId ?? crypto.randomUUID();
            remember(id);
            run = validateRun(await request(id));
            if (run.id !== id) throw new Error("运行编号不一致");
        } else {
            const runs = (await request()).map(validateRun);
            run = runs.find(item => item.id === pendingId) ?? runs[0];
        }
        renderRun(run);
        if (run?.id === pendingId) remember(null);

        if (pendingId) {
            message.textContent = "上次提交结果尚未确认，可以使用原编号重试。";
        } else if (!run) {
            message.textContent = "当前报事还没有保存的 Agent 运行记录。";
        } else if (run.status === "failed") {
            message.textContent = `生成失败，运行记录已保留（${run.error_code ?? "原因待查"}）。`;
        } else if (run.status === "running") {
            message.textContent = "该记录仍在生成中，请稍后读取已保存结果。";
        } else {
            message.textContent = "草稿已保存，等待人员审核。";
        }
    } catch (error) {
        if (create && error.status === 409) {
            remember(null);
            message.textContent = "请求编号冲突，请先读取已保存记录。";
        } else {
            message.textContent = create ? "生成结果尚未确认，请读取已保存结果或用原编号重试。"
                : "已保存记录读取失败，请重试读取。";
        }
    } finally {
        busy = false;
        syncButtons();
    }
}

watchEvidence(data => {
    evidenceReady = Boolean(data);
    syncButtons();
});
generate.addEventListener("click", () => updateRuns(true));
refresh.addEventListener("click", () => updateRuns(false));
updateRuns(false);