const planId = new URLSearchParams(window.location.search).get("plan_id")?.toLowerCase();
const storageKey = "property-v2-inspection-intake-v1:" + planId;
const form = document.querySelector("#inspection-form");
const fields = document.querySelector("#inspection-form-fields");
const send = document.querySelector("#inspection-send");
const newButton = document.querySelector("#inspection-new");
const reload = document.querySelector("#inspection-reload-context");
const message = document.querySelector("#inspection-submit-message");
const contextMessage = document.querySelector("#inspection-context-message");
const findings = {
    unable_to_verify: "无法核实", no_issue_observed: "未见异常", issue_observed: "发现异常",
};
let submission = null, busy = false, blocked = false, ready = false, loading = false;
let names = new Map();

function field(name) {
    return form.elements.namedItem(name);
}
function timeText(value) {
    return value ? new Date(value).toLocaleString("zh-CN", {
        timeZone: "Asia/Shanghai", hour12: false,
    }) : "待核实";
}
function locationText(id) {
    if (id === null) return "具体点位待核实";
    return names.has(id) ? names.get(id) + "（" + id + "）" : id + "（地点名称暂未取得）";
}
function render() {
    const phase = submission?.phase;
    const locked = ["pending", "accepted", "conflict"].includes(phase);
    fields.disabled = blocked || busy || !ready || locked;
    field("location_id").disabled = field("scope_mode").value !== "known";
    send.disabled = blocked || busy || ["accepted", "conflict"].includes(phase)
        || (!ready && phase !== "pending");
    send.textContent = busy ? "正在保存…" : phase === "pending" ? "原样重试本次提交" : "保存巡查记录";
    reload.disabled = loading;
    newButton.hidden = phase !== "accepted";
    document.querySelector("#inspection-submit-reference").hidden = !submission;
    document.querySelector("#inspection-request-id").textContent = submission?.payload.request_id ?? "";
    document.querySelector("#inspection-retry-note").hidden = phase !== "pending";
    document.querySelector("#inspection-receipt").hidden = phase !== "accepted";
    if (phase === "accepted") {
        const saved = submission.receipt;
        document.querySelector("#inspection-receipt-content").textContent = [
            "巡查记录编号：" + saved.id,
            "提交编号：" + saved.request_id,
            "计划编号：" + saved.plan_id,
            "巡查人：" + saved.inspector_name,
            "巡查观察：" + findings[saved.finding],
            "具体点位（当前台账）：" + locationText(saved.location_id),
            "观察时间（北京时间）：" + timeText(saved.observed_at),
            "录入时间（北京时间）：" + timeText(saved.recorded_at),
            "已保存的原始描述：",
            saved.description,
        ].join("\n");
    }
}
function remember(next) {
    sessionStorage.setItem(storageKey, JSON.stringify(next));
    submission = next;
}
function validPayload(payload) {
    return typeof planId === "string" && payload && typeof payload.request_id === "string"
        && payload.plan_id === planId && typeof payload.inspector_name === "string"
        && typeof payload.description === "string" && Object.hasOwn(findings, payload.finding)
        && (payload.location_id === null || typeof payload.location_id === "string")
        && (payload.observed_at === null || typeof payload.observed_at === "string");
}
function validReceipt(body, payload) {
    const sameTime = body?.observed_at === null || payload.observed_at === null
        ? body?.observed_at === payload.observed_at
        : Number.isFinite(Date.parse(body?.observed_at))
            && Date.parse(body.observed_at) === Date.parse(payload.observed_at);
    return validPayload(body) && typeof body.id === "string" && body.id.length > 0
        && body.request_id === payload.request_id && sameTime
        && ["inspector_name", "description", "finding", "location_id"].every(name => body[name] === payload[name])
        && typeof body.recorded_at === "string" && Number.isFinite(Date.parse(body.recorded_at))
        && Number.isInteger(body.version) && body.version >= 1;
}
function restoreFields(payload) {
    for (const name of ["inspector_name", "description", "finding"]) {
        field(name).value = payload[name];
    }
    field("scope_mode").value = payload.location_id === null ? "unknown" : "known";
    field("observed_at").value = payload.observed_at?.replace(/\+08:00$/, "") ?? "";
}
async function getJSON(url) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 10000);
    try {
        const response = await fetch(url, {signal: controller.signal, cache: "no-store"});
        if (!response.ok) throw new Error("读取失败");
        return await response.json();
    } finally {
        clearTimeout(timeout);
    }
}
async function loadContext() {
    if (!planId) {
        contextMessage.textContent = "请从计划列表的“查看巡查记录”进入，才能录入巡查。";
        return;
    }
    if (loading) return;
    const previous = field("location_id").value || submission?.payload.location_id || "";
    loading = true;
    ready = false;
    render();
    try {
        const [plan, locations] = await Promise.all([
            getJSON("/api/plans/" + encodeURIComponent(planId)),
            getJSON("/api/locations").catch(() => []),
        ]);
        if (plan.id !== planId || !Array.isArray(plan.location_ids)
            || !plan.location_ids.length || !plan.location_ids.every(id => typeof id === "string")) {
            throw new Error("计划范围异常");
        }
        names = new Map((Array.isArray(locations) ? locations : [])
            .filter(item => item && typeof item.id === "string" && typeof item.display_path === "string")
            .map(item => [item.id, item.display_path]));
        const placeholder = document.createElement("option");
        placeholder.value = "";
        placeholder.textContent = "请选择一个明确点位";
        const options = plan.location_ids.map(id => {
            const option = document.createElement("option");
            option.value = id;
            option.textContent = locationText(id);
            return option;
        });
        field("location_id").replaceChildren(placeholder, ...options);
        field("location_id").value = plan.location_ids.includes(previous) ? previous : "";
        ready = true;
        contextMessage.textContent = "明确点位只从本计划中单选；不同点位请分别记录。未知地点和时间可留待核实。";
    } catch {
        contextMessage.textContent = "计划范围未能读取，请重新加载；已有待确认提交仍可原样重试。";
    } finally {
        loading = false;
        render();
    }
}

try {
    const stored = sessionStorage.getItem(storageKey);
    if (stored) {
        const restored = JSON.parse(stored);
        if (!["pending", "accepted", "invalid", "conflict"].includes(restored.phase)
            || !validPayload(restored.payload)
            || (restored.phase === "accepted" && !validReceipt(restored.receipt, restored.payload))) {
            throw new Error("提交记录异常");
        }
        submission = restored;
        restoreFields(submission.payload);
        message.textContent = submission.phase === "pending"
            ? "已恢复结果尚未确认的提交，请原样重试。" : submission.message;
    }
} catch {
    blocked = true;
    message.textContent = "本页提交记录无法读取，暂不能提交。请先核对已有巡查记录及浏览器存储设置。";
}
field("scope_mode").addEventListener("change", render);
reload.addEventListener("click", loadContext);
form.addEventListener("submit", async event => {
    event.preventDefault();
    if (blocked || busy || ["accepted", "conflict"].includes(submission?.phase)) return;
    try {
        if (submission?.phase !== "pending") {
            if (!ready || !form.reportValidity()) return;
            const id = field("scope_mode").value === "known" ? field("location_id").value : null;
            if (field("scope_mode").value === "known" && !id) {
                message.textContent = "请选择一个明确点位；无法确认时，请选择“具体点位待核实”。";
                return;
            }
            const observed = field("observed_at").value;
            remember({phase: "pending", payload: {
                request_id: submission?.payload.request_id ?? crypto.randomUUID(),
                plan_id: planId,
                inspector_name: field("inspector_name").value,
                description: field("description").value,
                finding: field("finding").value,
                location_id: id,
                observed_at: observed ? observed + "+08:00" : null,
            }});
        }
    } catch {
        message.textContent = "无法暂存本次提交，尚未发送请求。请检查浏览器存储设置。";
        return;
    }
    busy = true;
    render();
    message.textContent = "正在保存，请稍候。";
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 12000);
    try {
        const response = await fetch("/api/inspections", {
            method: "POST", headers: {"Content-Type": "application/json"},
            body: JSON.stringify(submission.payload), signal: controller.signal,
        });
        const body = await response.json();
        if ([200, 201].includes(response.status)) {
            if (!validReceipt(body, submission.payload)) throw new Error("回执不匹配");
            remember({...submission, phase: "accepted", receipt: body,
                message: "巡查记录已保存，尚未作履约判断。可点击“刷新查询”查看记录。"});
        } else if (response.status === 422) {
            const detail = typeof body.detail === "string" ? body.detail
                : Array.isArray(body.detail) ? body.detail.map(item => item.msg).join("；")
                : "输入未通过校验。";
            remember({...submission, phase: "invalid", message: detail + " 请修改后重交，提交编号保持不变。"});
        } else if (response.status === 409) {
            remember({...submission, phase: "conflict",
                message: "同一提交编号已用于不同内容。请保留编号，先核对原记录，再由人员处理。"});
        } else {
            throw new Error("尚未确认保存结果");
        }
        message.textContent = submission.message;
    } catch {
        message.textContent = "暂未确认保存结果。原编号和原内容已保留，请原样重试；刷新后仍可继续。";
    } finally {
        clearTimeout(timeout);
        busy = false;
        render();
    }
});
newButton.addEventListener("click", () => {
    if (submission?.phase !== "accepted") return;
    try {
        sessionStorage.removeItem(storageKey);
    } catch {
        message.textContent = "无法结束本次录入，请检查浏览器存储设置。";
        return;
    }
    submission = null;
    form.reset();
    message.textContent = "可以录入下一条巡查记录。";
    render();
    field("inspector_name").focus();
});
render();
loadContext();