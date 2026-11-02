const storageKey = "property-v2-plan-intake-v1";
const form = document.querySelector("#plan-form");
const fields = document.querySelector("#plan-fields");
const sendButton = document.querySelector("#send-button");
const newButton = document.querySelector("#new-button");
const reloadButton = document.querySelector("#reload-locations");
const message = document.querySelector("#message");
const receipt = document.querySelector("#receipt");
let submission = null;
let busy = false;
let blocked = false;
let catalogReady = false;

function field(name) {
    return form.elements.namedItem(name);
}

function selectedIds() {
    return Array.from(field("location_ids").selectedOptions, option => option.value);
}

function selectLocations(ids) {
    const select = field("location_ids");
    for (const id of ids) {
        if (!Array.from(select.options).some(option => option.value === id)) {
            const option = document.createElement("option");
            option.value = id;
            option.textContent = "原提交地点：" + id + "（台账暂未查到）";
            select.append(option);
        }
    }
    for (const option of select.options) option.selected = ids.includes(option.value);
}

function displayTime(value) {
    return new Date(value).toLocaleString("zh-CN", {
        timeZone: "Asia/Shanghai", hour12: false,
    });
}

function render() {
    const phase = submission?.phase;
    fields.disabled = blocked || busy || ["pending", "accepted", "conflict"].includes(phase);
    field("location_ids").disabled = !catalogReady;
    sendButton.disabled = blocked || busy || ["accepted", "conflict"].includes(phase)
        || (!catalogReady && phase !== "pending");
    sendButton.textContent = busy ? "正在保存…" : phase === "pending" ? "原样重试本次提交" : "保存计划";
    newButton.hidden = phase !== "accepted";
    receipt.hidden = phase !== "accepted";
    document.querySelector("#retry-note").hidden = phase !== "pending";
    document.querySelector("#submission-reference").hidden = !submission;
    document.querySelector("#request-id").textContent = submission?.payload.request_id ?? "";
    if (phase === "accepted") {
        const saved = submission.receipt;
        const labels = new Map(Array.from(field("location_ids").options, option => [option.value, option.textContent]));
        document.querySelector("#plan-id").textContent = saved.id;
        document.querySelector("#saved-title").textContent = saved.title;
        document.querySelector("#saved-owner").textContent = saved.owner_name;
        document.querySelector("#saved-locations").textContent = saved.location_ids
            .map(id => (labels.get(id) ?? id) + "（" + id + "）").join("、");
        document.querySelector("#saved-start").textContent = displayTime(saved.planned_start_at);
        document.querySelector("#saved-end").textContent = displayTime(saved.planned_end_at);
        document.querySelector("#created-at").textContent = displayTime(saved.created_at);
        document.querySelector("#saved-requirements").textContent = saved.requirements;
    }
}

function remember(next) {
    sessionStorage.setItem(storageKey, JSON.stringify(next));
    submission = next;
}

function restoreFields(payload) {
    for (const name of ["title", "service_category", "plan_kind", "requirements", "owner_name"]) {
        field(name).value = payload[name];
    }
    for (const name of ["planned_start_at", "planned_end_at"]) {
        field(name).value = payload[name].replace("+08:00", "");
    }
    selectLocations(payload.location_ids);
}

function errorMessage(body) {
    if (typeof body.detail === "string") return body.detail;
    if (Array.isArray(body.detail)) return body.detail.map(item => item.msg).join("；");
    return "输入未通过校验，请核对后重交。";
}

async function loadLocations() {
    const notice = document.querySelector("#location-message");
    const select = field("location_ids");
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 10000);
    reloadButton.disabled = true;
    catalogReady = false;
    render();
    try {
        const response = await fetch("/api/locations", { signal: controller.signal });
        if (!response.ok) throw new Error("地点查询失败");
        const locations = await response.json();
        if (!Array.isArray(locations)) throw new Error("地点格式异常");
        const options = locations.map(location => {
            if (typeof location.id !== "string" || typeof location.display_path !== "string") {
                throw new Error("地点格式异常");
            }
            const option = document.createElement("option");
            option.value = location.id;
            option.textContent = location.display_path;
            return option;
        });
        const previous = selectedIds();
        select.replaceChildren(...options);
        selectLocations(previous);
        catalogReady = locations.length > 0;
        notice.textContent = catalogReady
            ? "请选择本次计划明确覆盖的地点。"
            : "地点台账为空，请先维护地点，再重新加载。";
    } catch {
        notice.textContent = "地点台账暂时无法加载，可点击重新加载；已有待确认提交仍可原样重试。";
    } finally {
        clearTimeout(timeout);
        reloadButton.disabled = false;
        render();
    }
}

try {
    const stored = sessionStorage.getItem(storageKey);
    if (stored) {
        const restored = JSON.parse(stored);
        if (!["pending", "accepted", "invalid", "conflict"].includes(restored.phase)
            || typeof restored.payload?.request_id !== "string"
            || !Array.isArray(restored.payload.location_ids)
            || (restored.phase === "accepted" && !restored.receipt?.id)) {
            throw new Error("无法识别本页提交记录");
        }
        submission = restored;
        restoreFields(submission.payload);
        message.textContent = submission.phase === "pending"
            ? "已恢复尚未确认结果的提交，请原样重试。" : submission.message;
    }
} catch {
    blocked = true;
    message.textContent = "浏览器提交记录无法读取，暂不能提交。请先核对已有计划及浏览器存储设置。";
}
render();

form.addEventListener("submit", async event => {
    event.preventDefault();
    if (blocked || busy || ["accepted", "conflict"].includes(submission?.phase)) return;
    try {
        if (submission?.phase !== "pending") {
            if (!catalogReady || !form.reportValidity()) return;
            remember({
                phase: "pending",
                payload: {
                    request_id: submission?.payload.request_id ?? crypto.randomUUID(),
                    title: field("title").value,
                    service_category: field("service_category").value,
                    plan_kind: field("plan_kind").value,
                    requirements: field("requirements").value,
                    owner_name: field("owner_name").value,
                    location_ids: selectedIds(),
                    planned_start_at: field("planned_start_at").value + "+08:00",
                    planned_end_at: field("planned_end_at").value + "+08:00",
                },
            });
        }
    } catch {
        message.textContent = "无法保存本次提交记录，请检查浏览器存储设置后重试。";
        return;
    }
    busy = true;
    render();
    message.textContent = "正在保存，请稍候。";
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 12000);
    try {
        const response = await fetch("/api/plans", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(submission.payload),
            signal: controller.signal,
        });
        const body = await response.json();
        if ([200, 201].includes(response.status)) {
            if (body.request_id !== submission.payload.request_id || typeof body.id !== "string") {
                throw new Error("回执与本次提交不匹配");
            }
            remember({ ...submission, phase: "accepted", receipt: body,
                message: "计划已保存，尚未生成执行或巡查记录。" });
        } else if (response.status === 422) {
            remember({ ...submission, phase: "invalid", message: errorMessage(body) });
        } else if (response.status === 409) {
            remember({ ...submission, phase: "conflict",
                message: "提交编号与已有计划内容冲突。请保留编号，先核对原计划，再由人员处理。" });
        } else {
            throw new Error("尚未获得成功回执");
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
        message.textContent = "无法结束当前录入，请检查浏览器存储设置。";
        return;
    }
    submission = null;
    form.reset();
    selectLocations([]);
    message.textContent = "可以录入下一项计划。";
    render();
    field("title").focus();
});
reloadButton.addEventListener("click", loadLocations);
loadLocations();