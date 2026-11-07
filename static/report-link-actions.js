import {watchEvidence} from "./report-evidence.js?v=link-actions-v1";

const reportId = document.querySelector("#report-evidence").dataset.reportId;
const form = document.querySelector("#link-action-form");
const fields = document.querySelector("#link-action-fields");
const send = document.querySelector("#send-link-action");
const startNew = document.querySelector("#new-link-action");
const reloadPlans = document.querySelector("#reload-action-plans");
const message = document.querySelector("#link-action-message");
const storageKey = "property-v2-link-action-v1:" + reportId;
const uuid = value => typeof value === "string" && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value);
const field = name => form.elements.namedItem(name);
let draft = null, snapshot = null, plans = new Map();
let locationNames = null;
let busy = false, blocked = false, catalogReady = false, catalogBusy = false;

function choices(select, entries, selected) {
    if (selected && !entries.some(([id]) => id === selected)) entries.push([selected, "原提交目标：" + selected]);
    select.replaceChildren(...[["", "请选择"], ...entries].map(([value, label]) => {
        const option = document.createElement("option");
        option.value = value;
        option.textContent = label;
        return option;
    }));
    select.value = selected ?? "";
}

function render() {
    const phase = draft?.phase;
    const kind = field("kind").value;
    fields.disabled = blocked || busy || !snapshot || ["pending", "accepted", "conflict"].includes(phase);
    field("kind").disabled = draft !== null;
    field("plan_id").required = kind === "link";
    field("plan_id").disabled = !catalogReady;
    field("link_id").required = kind === "revoke";
    document.querySelector("#action-plan-choice").hidden = kind !== "link";
    document.querySelector("#action-link-choice").hidden = kind !== "revoke";
    send.disabled = blocked || busy || ["accepted", "conflict"].includes(phase)
        || (phase !== "pending" && (!snapshot || (kind === "link" && !catalogReady)));
    send.textContent = busy ? "正在保存…" : phase === "pending" ? "原样重试本次操作" : "保存本次操作";
    startNew.hidden = !["invalid", "accepted", "conflict"].includes(phase);
    startNew.textContent = phase === "invalid" ? "放弃未保存输入，开始新操作" : "核对当前记录后开始新操作";
    startNew.disabled = blocked || busy || !snapshot;
    reloadPlans.disabled = blocked || busy || catalogBusy;
    document.querySelector("#link-action-reference").hidden = !draft;
    document.querySelector("#link-action-request-id").textContent = draft?.payload.request_id ?? "";
    document.querySelector("#link-action-retry-note").hidden = phase !== "pending";
    const receipt = document.querySelector("#link-action-receipt");
    receipt.hidden = phase !== "accepted";
    if (phase === "accepted") receipt.textContent = `报事编号（report_id）：${draft.receipt.report_id}\n关联编号（id）：${draft.receipt.id}\n本次操作提交编号：${draft.payload.request_id}\n回执状态（后续变化见关联历史）：${draft.receipt.status === "active" ? "有效关联" : "已撤销"}\n回执版本：${draft.receipt.version}`;
    const plan = plans.get(field("plan_id").value);
    const link = snapshot?.links.find(x => x.id === field("link_id").value);
    document.querySelector("#action-target").textContent = kind === "link"
        ? plan ? `计划：${plan.title}\n明确地点：${plan.location_ids.map(id => locationNames?.has(id) ? `${locationNames.get(id)}（${id}）` : id).join("、")}\n计划要求：${plan.requirements}` : "请选择计划，提交时程序将核对类别和明确地点。"
        : link ? `关联编号：${link.id}\n计划编号：${link.plan_id}\n当前版本：${link.version}\n原关联理由：${link.reason}` : "请选择当前有效关联。";
}

function remember(next) {
    sessionStorage.setItem(storageKey, JSON.stringify(next));
    draft = next;
}

function matchesReceipt(body, attempt) {
    if (!body || !uuid(body.id) || body.report_id !== reportId || body.plan_id !== attempt.plan_id
        || !Number.isInteger(body.version) || body.version < 1) return false;
    const p = attempt.payload;
    return attempt.kind === "link"
        ? body.request_id === p.request_id && body.linked_by === p.linked_by && body.reason === p.reason
            && ["active", "revoked"].includes(body.status)
        : body.id === attempt.link_id && body.status === "revoked" && body.version === p.expected_version + 1
            && body.revocation_request_id === p.request_id && body.revoked_by === p.revoked_by && body.revoke_reason === p.reason;
}

function validateDraft(value) {
    if (!value || value.report_id !== reportId || !["link", "revoke"].includes(value.kind)
        || !["pending", "invalid", "accepted", "conflict"].includes(value.phase)
        || !uuid(value.plan_id) || !uuid(value.payload?.request_id)) throw Error("无法识别操作暂存");
    const p = value.payload;
    const keys = value.kind === "link" ? ["request_id", "plan_id", "linked_by", "reason"]
        : ["request_id", "expected_version", "revoked_by", "reason"];
    const actor = value.kind === "link" ? p.linked_by : p.revoked_by;
    if (Object.keys(p).length !== keys.length || !keys.every(key => Object.hasOwn(p, key))
        || typeof actor !== "string" || actor.length > 120 || typeof p.reason !== "string" || p.reason.length > 2000
        || (value.kind === "link" && p.plan_id !== value.plan_id)
        || (value.kind === "revoke" && (!uuid(value.link_id) || !Number.isInteger(p.expected_version) || p.expected_version < 1))
        || (value.phase === "accepted" && !matchesReceipt(value.receipt, value))) throw Error("操作暂存内容异常");
}

async function loadPlans() {
    if (catalogBusy) return;
    catalogBusy = true;
    catalogReady = false;
    render();
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 10000);
    const notice = document.querySelector("#action-plan-message");
    try {
        const all = new Map();
        for (let offset = 0; ; offset += 100) {
            const response = await fetch(`/api/plans?limit=100&offset=${offset}`, {signal: controller.signal, cache: "no-store"});
            if (!response.ok) throw Error("计划列表未读完");
            const page = await response.json();
            if (!Array.isArray(page) || page.some(x => !uuid(x.id) || typeof x.title !== "string"
                || typeof x.requirements !== "string" || !Array.isArray(x.location_ids)
                || !Number.isFinite(Date.parse(x.planned_start_at)))) throw Error("计划列表格式异常");
            for (const plan of page) all.set(plan.id, plan);
            if (page.length < 100) break;
        }
        plans = all;
        choices(field("plan_id"), [...plans.values()].map(x => [x.id,
            `${x.title}｜${({cleaning: "保洁", water_supply: "供水", other: "其他"})[x.service_category] ?? x.service_category}｜${new Date(x.planned_start_at).toLocaleString("zh-CN", {timeZone: "Asia/Shanghai", hour12: false})}`]), field("plan_id").value);
        catalogReady = plans.size > 0;
        updateLinks();
        notice.textContent = catalogReady ? "请选择具体计划；关联表示共同核查，不代表同一原因。" : "暂无计划，请先在计划页面录入。";
    } catch {
        notice.textContent = "计划列表加载未完成，请重新加载；已保留的待确认操作仍可原样重试。";
    } finally {
        clearTimeout(timeout);
        catalogBusy = false;
        render();
    }
}

try {
    const stored = sessionStorage.getItem(storageKey);
    if (stored) {
        const value = JSON.parse(stored);
        validateDraft(value);
        draft = value;
        field("kind").value = draft.kind;
        field("actor").value = draft.kind === "link" ? draft.payload.linked_by : draft.payload.revoked_by;
        field("reason").value = draft.payload.reason;
        choices(field("plan_id"), [], draft.plan_id);
        choices(field("link_id"), [], draft.link_id);
        message.textContent = draft.message ?? "已恢复待确认操作，请原样重试。";
    }
} catch {
    blocked = true;
    message.textContent = "操作暂存无法读取，暂不能提交；请保留页面并核对浏览器存储设置。";
}

function updateLinks() {
    if (snapshot) choices(field("link_id"), snapshot.links.filter(x => x.status === "active")
        .map(x => [x.id, `${plans.get(x.plan_id)?.title ?? "计划 " + x.plan_id}｜关联编号 ${x.id}｜版本 ${x.version}`]), field("link_id").value);
}

watchEvidence((data, names) => {
    snapshot = data;
    locationNames = names;
    updateLinks();
    render();
});
form.addEventListener("change", render);
form.addEventListener("submit", async event => {
    event.preventDefault();
    if (blocked || busy || ["accepted", "conflict"].includes(draft?.phase)) return;
    try {
        if (draft?.phase !== "pending") {
            if (!snapshot || !form.reportValidity()) return;
            const kind = field("kind").value;
            if (!["link", "revoke"].includes(kind)
                || (kind === "link" && (!catalogReady || !plans.has(field("plan_id").value)))) return;
            const link = snapshot.links.find(x => x.id === field("link_id").value && x.status === "active");
            if (kind === "revoke" && !link) return;
            const requestId = draft?.payload.request_id ?? crypto.randomUUID();
            remember({kind, phase: "pending", report_id: reportId,
                plan_id: kind === "link" ? field("plan_id").value : link.plan_id,
                link_id: kind === "revoke" ? link.id : null,
                payload: kind === "link"
                    ? {request_id: requestId, plan_id: field("plan_id").value, linked_by: field("actor").value, reason: field("reason").value}
                    : {request_id: requestId, expected_version: link.version, revoked_by: field("actor").value, reason: field("reason").value},
            });
        }
    } catch {
        message.textContent = "无法暂存本次操作，尚未发送；请检查浏览器存储设置。";
        return;
    }
    busy = true;
    render();
    document.querySelector("#report-evidence-result").hidden = true;
    message.textContent = "正在保存本次操作…";
    const attempt = draft;
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 12000);
    const path = `/api/service-requests/${encodeURIComponent(reportId)}/plan-links`
        + (attempt.kind === "revoke" ? `/${encodeURIComponent(attempt.link_id)}/revoke` : "");
    try {
        const response = await fetch(path, {method: "POST", headers: {"Content-Type": "application/json"},
            body: JSON.stringify(attempt.payload), signal: controller.signal});
        const body = await response.json();
        if (response.status === 200 || (attempt.kind === "link" && response.status === 201)) {
            if (!matchesReceipt(body, attempt)) throw Error("回执与操作不匹配");
            remember({...attempt, phase: "accepted", receipt: body, message: "操作已确认，请核对回执和当前关联历史。"});
            snapshot = null;
            window.location.reload();
        } else if (response.status === 422) {
            const detail = typeof body.detail === "string" ? body.detail
                : Array.isArray(body.detail) ? body.detail.map(x => x.msg).join("；") : "输入未通过校验，请修正。";
            remember({...attempt, phase: "invalid", message: detail});
            if (snapshot) document.querySelector("#report-evidence-result").hidden = false;
        } else if (response.status === 409) {
            remember({...attempt, phase: "conflict", message: "操作发生冲突；请先核对刷新后的关联记录，再决定是否开始新操作。"});
            snapshot = null;
            window.location.reload();
        } else throw Error("尚未收到匹配回执");
        message.textContent = draft.message;
    } catch {
        message.textContent = "暂未确认保存结果。原提交编号和原内容已保留，请原样重试；刷新后仍可继续。";
    } finally {
        clearTimeout(timeout);
        busy = false;
        render();
    }
});
startNew.addEventListener("click", () => {
    if (!snapshot || blocked || busy || !["invalid", "accepted", "conflict"].includes(draft?.phase)) return;
    try { sessionStorage.removeItem(storageKey); }
    catch { message.textContent = "无法结束当前操作，请核对浏览器存储设置。"; return; }
    draft = null;
    form.reset();
    message.textContent = "可以开始下一次操作。";
    render();
});
reloadPlans.addEventListener("click", loadPlans);
render();
loadPlans();