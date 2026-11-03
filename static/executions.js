const planId = new URLSearchParams(window.location.search).get("plan_id")?.toLowerCase();
const storageKey = "property-v2-execution-intake-v1:" + planId;
const form = document.querySelector("#execution-form");
const fields = document.querySelector("#execution-form-fields");
const send = document.querySelector("#execution-send");
const newButton = document.querySelector("#execution-new");
const reload = document.querySelector("#execution-reload-context");
const message = document.querySelector("#execution-submit-message");
const contextMessage = document.querySelector("#execution-context-message");
const claims = {
    unknown: "完成情况待核实", completed: "声称已完成",
    partially_completed: "声称部分完成", not_completed: "声称未完成",
};
let submission = null, busy = false, blocked = false, ready = false, loading = false;
let names = new Map();

function field(name) {
    return form.elements.namedItem(name);
}
function selectedIds() {
    return Array.from(field("location_ids").selectedOptions, option => option.value);
}
function timeText(value) {
    return value ? new Date(value).toLocaleString("zh-CN", {
        timeZone: "Asia/Shanghai", hour12: false,
    }) : "待核实";
}
function locationText(ids) {
    return ids.length ? ids.map(id => (names.get(id) ?? id) + "（" + id + "）").join("、")
        : "具体点位待核实";
}
function render() {
    const phase = submission?.phase;
    const locked = ["pending", "accepted", "conflict"].includes(phase);
    fields.disabled = blocked || busy || !ready || locked;
    field("location_ids").disabled = field("scope_mode").value !== "known";
    send.disabled = blocked || busy || ["accepted", "conflict"].includes(phase)
        || (!ready && phase !== "pending");
    send.textContent = busy ? "正在保存…" : phase === "pending" ? "原样重试本次提交" : "保存执行记录";
    reload.disabled = loading;
    newButton.hidden = phase !== "accepted";
    document.querySelector("#execution-submit-reference").hidden = !submission;
    document.querySelector("#execution-request-id").textContent = submission?.payload.request_id ?? "";
    document.querySelector("#execution-retry-note").hidden = phase !== "pending";
    document.querySelector("#execution-receipt").hidden = phase !== "accepted";
    if (phase === "accepted") {
        const saved = submission.receipt;
        document.querySelector("#execution-receipt-content").textContent = [
            "执行记录编号：" + saved.id,
            "计划编号：" + saved.plan_id,
            "执行人：" + saved.executor_name,
            "完成情况：" + claims[saved.completion_claim],
            "具体点位（当前台账）：" + locationText(saved.location_ids),
            "声称开始时间（北京时间）：" + timeText(saved.claimed_started_at),
            "声称完成时间（北京时间）：" + timeText(saved.claimed_finished_at),
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
    return typeof planId === "string" && payload && typeof payload.request_id === "string" && payload.plan_id === planId
        && typeof payload.executor_name === "string" && typeof payload.description === "string"
        && Object.hasOwn(claims, payload.completion_claim)
        && Array.isArray(payload.location_ids)
        && payload.location_ids.every(id => typeof id === "string")
        && ["claimed_started_at", "claimed_finished_at"].every(name =>
            payload[name] === null || typeof payload[name] === "string");
}
function validReceipt(body, payload) {
    return validPayload(body) && typeof body.id === "string"
        && body.request_id === payload.request_id
        && typeof body.recorded_at === "string" && !Number.isNaN(Date.parse(body.recorded_at));
}
function restoreFields(payload) {
    for (const name of ["executor_name", "description", "completion_claim"]) {
        field(name).value = payload[name];
    }
    field("scope_mode").value = payload.location_ids.length ? "known" : "unknown";
    for (const name of ["claimed_started_at", "claimed_finished_at"]) {
        field(name).value = payload[name]?.replace(/\+08:00$/, "") ?? "";
    }
}
async function getJSON(url) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 10000);
    try {
        const response = await fetch(url, { signal: controller.signal });
        if (!response.ok) throw new Error("读取失败");
        return await response.json();
    } finally {
        clearTimeout(timeout);
    }
}
async function loadContext() {
    if (!planId) {
        contextMessage.textContent = "请从计划列表的“查看执行记录”进入，才能录入执行情况。";
        return;
    }
    if (loading) return;
    const previous = ready ? selectedIds() : submission?.payload.location_ids ?? [];
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
            .filter(item => typeof item.id === "string" && typeof item.display_path === "string")
            .map(item => [item.id, item.display_path]));
        const options = plan.location_ids.map(id => {
            const option = document.createElement("option");
            option.value = id;
            option.textContent = (names.get(id) ?? id) + "（" + id + "）";
            option.selected = previous.includes(id);
            return option;
        });
        field("location_ids").replaceChildren(...options);
        ready = true;
        contextMessage.textContent = "点位选项来自当前计划；名称加载失败时显示地点编号。未知点位不会自动扩成整个计划。";
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
    message.textContent = "本页提交记录无法读取，暂不能提交。请先核对已有执行记录及浏览器存储设置。";
}
field("scope_mode").addEventListener("change", render);
reload.addEventListener("click", loadContext);
form.addEventListener("submit", async event => {
    event.preventDefault();
    if (blocked || busy || ["accepted", "conflict"].includes(submission?.phase)) return;
    try {
        if (submission?.phase !== "pending") {
            if (!ready || !form.reportValidity()) return;
            const ids = field("scope_mode").value === "known" ? selectedIds() : [];
            if (field("scope_mode").value === "known" && !ids.length) {
                message.textContent = "请选择至少一个明确点位；无法确认时，请选择“具体点位待核实”。";
                return;
            }
            const timeValue = name => field(name).value ? field(name).value + "+08:00" : null;
            remember({ phase: "pending", payload: {
                request_id: submission?.payload.request_id ?? crypto.randomUUID(),
                plan_id: planId,
                executor_name: field("executor_name").value,
                description: field("description").value,
                completion_claim: field("completion_claim").value,
                location_ids: ids,
                claimed_started_at: timeValue("claimed_started_at"),
                claimed_finished_at: timeValue("claimed_finished_at"),
            } });
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
        const response = await fetch("/api/executions", {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify(submission.payload), signal: controller.signal,
        });
        const body = await response.json();
        if ([200, 201].includes(response.status)) {
            if (!validReceipt(body, submission.payload)) throw new Error("回执不匹配");
            remember({ ...submission, phase: "accepted", receipt: body,
                message: "执行记录已保存，尚未核实。可点击“刷新查询”查看记录。" });
        } else if (response.status === 422) {
            const detail = typeof body.detail === "string" ? body.detail
                : Array.isArray(body.detail) ? body.detail.map(item => item.msg).join("；")
                : "输入未通过校验。";
            remember({ ...submission, phase: "invalid", message: detail + " 请修改后重交，提交编号保持不变。" });
        } else if (response.status === 409) {
            remember({ ...submission, phase: "conflict",
                message: "同一提交编号已用于不同内容。请保留编号，先核对原记录，再由人员处理。" });
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
    message.textContent = "可以录入下一条执行记录。";
    render();
    field("executor_name").focus();
});
render();
loadContext();