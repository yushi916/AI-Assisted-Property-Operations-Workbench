import "./inspections.js?v=inspection-layout-1";

const requestedPlanId = new URLSearchParams(window.location.search).get("plan_id");
const rows = document.querySelector("#inspection-rows");
const message = document.querySelector("#inspection-message");
const detail = document.querySelector("#inspection-detail");
const detailMessage = document.querySelector("#inspection-detail-message");
const previousButton = document.querySelector("#inspection-previous");
const nextButton = document.querySelector("#inspection-next");
const refreshButton = document.querySelector("#refresh-inspections");
const pageSize = 10;
const findings = {
    no_issue_observed: "未发现异常",
    issue_observed: "发现异常",
    unable_to_verify: "无法核实",
};
let plan = null;
let names = new Map();
let offset = 0;
let hasNext = false;
let listReady = false;
let busy = false;
let detailSequence = 0;

function timeText(value) {
    if (value === null) return "待核实";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) throw new Error("时间格式异常，请重新查询。");
    return date.toLocaleString("zh-CN", {timeZone: "Asia/Shanghai", hour12: false});
}

async function getJSON(path) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 10000);
    try {
        const response = await fetch(path, {signal: controller.signal, cache: "no-store"});
        if (response.status === 404) throw new Error("未找到所查记录，请返回计划列表核对。");
        if (response.status === 422) throw new Error("查询参数无效，请从计划列表重新进入。");
        if (!response.ok) throw new Error("查询暂未成功，请刷新重试。");
        return await response.json();
    } finally {
        clearTimeout(timeout);
    }
}

function failureText(error, timeoutMessage) {
    if (error.name === "AbortError") return timeoutMessage;
    if (["TypeError", "SyntaxError"].includes(error.name)) return "查询未完成，请刷新或重试。";
    return error.message;
}

function locationText(ids) {
    if (!ids.length) return "具体地点待核实";
    return ids.map(id => names.has(id)
        ? names.get(id) + "（" + id + "）"
        : id + "（地点名称暂未取得）").join("、");
}

function showFields(selector, values) {
    const container = document.querySelector(selector);
    container.replaceChildren();
    for (const [label, value] of values) {
        const term = document.createElement("dt");
        const description = document.createElement("dd");
        term.textContent = label;
        description.textContent = value;
        container.append(term, description);
    }
}

function updateButtons() {
    previousButton.disabled = busy || !listReady || offset === 0;
    nextButton.disabled = busy || !listReady || !hasNext;
    refreshButton.disabled = busy || !requestedPlanId;
}

async function openInspection(id) {
    const sequence = ++detailSequence;
    detail.hidden = true;
    detailMessage.textContent = "正在查询现场检查详情…";
    try {
        const item = await getJSON("/api/inspections/" + encodeURIComponent(id));
        if (sequence !== detailSequence) return;
        if (item.id !== id || item.plan_id !== plan.id) {
            throw new Error("现场检查记录与当前所选计划不一致，请刷新核对。");
        }
        showFields("#inspection-detail-fields", [
            ["现场检查编号", item.id], ["提交编号", item.request_id],
            ["关联计划编号", item.plan_id], ["检查人员", item.inspector_name],
            ["现场检查观察", findings[item.finding] ?? item.finding],
            ["现场检查地点（当前台账）", locationText(item.location_id === null ? [] : [item.location_id])],
            ["观察时间（北京时间）", timeText(item.observed_at)],
            ["录入时间（北京时间）", timeText(item.recorded_at)],
            ["记录版本", item.version],
        ]);
        document.querySelector("#inspection-description").textContent = item.description;
        detail.hidden = false;
        detailMessage.textContent = "已读取保存的现场检查记录。";
        document.querySelector("#inspection-detail-title").focus();
    } catch (error) {
        if (sequence !== detailSequence) return;
        detailMessage.textContent = failureText(error, "详情查询超时，请再次点击查看。");
    }
}

async function loadPage(targetOffset, refreshPlan = false) {
    if (!requestedPlanId) {
        message.textContent = "请从计划列表选择某项计划，再查看现场检查记录。";
        updateButtons();
        return;
    }
    if (busy) return;
    busy = true;
    listReady = false;
    rows.replaceChildren();
    detailSequence++;
    detail.hidden = true;
    detailMessage.textContent = "";
    document.querySelector("#inspection-page-label").textContent = "";
    message.textContent = "正在查询…";
    updateButtons();
    try {
        if (refreshPlan || !plan) {
            document.querySelector("#inspection-plan").hidden = true;
            const [savedPlan, catalog] = await Promise.all([
                getJSON("/api/plans/" + encodeURIComponent(requestedPlanId)),
                getJSON("/api/locations").catch(() => []),
            ]);
            if (typeof savedPlan.id !== "string"
                || savedPlan.id.toLowerCase() !== requestedPlanId.toLowerCase()
                || !Array.isArray(savedPlan.location_ids)) {
                throw new Error("计划格式异常，请重新查询。");
            }
            plan = savedPlan;
            names = new Map(Array.isArray(catalog) ? catalog
                .filter(item => item && typeof item.id === "string" && typeof item.display_path === "string")
                .map(item => [item.id, item.display_path]) : []);
            document.querySelector("#inspection-plan-title").textContent = plan.title;
            showFields("#inspection-plan-fields", [
                ["计划编号", plan.id], ["负责人", plan.owner_name],
                ["计划覆盖地点（当前台账）", locationText(plan.location_ids)],
                ["计划开始时间（北京时间）", timeText(plan.planned_start_at)],
                ["计划结束时间（北京时间）", timeText(plan.planned_end_at)],
                ["计划版本", plan.version],
            ]);
            document.querySelector("#inspection-plan-requirements").textContent = plan.requirements;
            document.querySelector("#inspection-plan").hidden = false;
        }
        const query = new URLSearchParams({
            plan_id: plan.id, limit: String(pageSize + 1), offset: String(targetOffset),
        });
        const items = await getJSON("/api/inspections?" + query);
        if (!Array.isArray(items) || items.some(item =>
            item.plan_id !== plan.id || typeof item.id !== "string"
            || (item.location_id !== null && typeof item.location_id !== "string"))) {
            throw new Error("现场检查列表格式或关联计划异常，请刷新核对。");
        }
        const visible = items.slice(0, pageSize);
        for (const item of visible) {
            const row = document.createElement("tr");
            for (const value of [
                timeText(item.recorded_at), timeText(item.observed_at),
                findings[item.finding] ?? item.finding,
                item.inspector_name, locationText(item.location_id === null ? [] : [item.location_id]),
            ]) {
                const cell = document.createElement("td");
                cell.textContent = value;
                row.append(cell);
            }
            const action = document.createElement("td");
            const button = document.createElement("button");
            button.type = "button";
            button.textContent = "查看详情";
            button.addEventListener("click", () => openInspection(item.id));
            action.append(button);
            row.append(action);
            rows.append(row);
        }
        offset = targetOffset;
        hasNext = items.length > pageSize;
        listReady = true;
        document.querySelector("#inspection-page-label").textContent =
            `第 ${offset / pageSize + 1} 页，本页 ${visible.length} 条`;
        message.textContent = visible.length ? "查询已更新。"
            : offset === 0 ? "该计划暂无现场检查记录。" : "本页没有记录，请返回上一页或刷新。";
    } catch (error) {
        rows.replaceChildren();
        hasNext = false;
        message.textContent = failureText(error, "查询超时，请点击刷新重试。");
    } finally {
        busy = false;
        updateButtons();
    }
}

previousButton.addEventListener("click", () => loadPage(Math.max(0, offset - pageSize)));
nextButton.addEventListener("click", () => loadPage(offset + pageSize));
refreshButton.addEventListener("click", () => loadPage(0, true));
loadPage(0, true);
