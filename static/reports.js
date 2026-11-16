const storageKey = "property-v2-intake-v1";
const form = document.querySelector("#report-form");
const fields = document.querySelector("#report-fields");
const sendButton = document.querySelector("#send-button");
const newButton = document.querySelector("#new-button");
const message = document.querySelector("#message");
const receipt = document.querySelector("#receipt");
let submission = null;
let busy = false;
let blocked = false;
const locationPicker = typeof createLocationPicker === "function"
    ? createLocationPicker(
        document.querySelector("#location-picker"), field("location_id"),
        () => blocked || fields.disabled,
    ) : null;

function field(name) {
    return form.elements.namedItem(name);
}

function selectLocation(locationId) {
    const select = field("location_id");
    const id = locationId ?? "";

    if (id && !Array.from(select.options).some(option => option.value === id)) {
        const option = document.createElement("option");
        option.value = id;
        option.textContent = "原提交地点：" + id + "（台账暂未查到）";
        select.append(option);
    }

    select.value = id;
    locationPicker?.sync();
}

async function loadLocations() {
    const select = field("location_id");
    const notice = document.querySelector("#location-message");
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 10000);

    try {
        const response = await fetch("/api/locations", {
            signal: controller.signal,
            cache: "no-store",
        });
        if (!response.ok) throw new Error("地点查询失败");

        const locations = await response.json();
        if (!Array.isArray(locations)) throw new Error("地点格式异常");

        const previous = submission?.payload.location_id ?? select.value;
        const unknown = document.createElement("option");
        unknown.value = "";
        unknown.textContent = "地点待核实";
        const options = [unknown];

        for (const location of locations) {
            if (typeof location.id !== "string" || typeof location.display_path !== "string") {
                throw new Error("地点格式异常");
            }

            const option = document.createElement("option");
            option.value = location.id;
            option.textContent = location.display_path;
            options.push(option);
        }

        select.replaceChildren(...options);
        selectLocation(previous);
        locationPicker?.setLocations(locations);
        notice.textContent = locations.length
            ? "请选择已确认的地点；不确定时保留待核实。"
            : "暂无地点台账，可按待核实受理。";
    } catch {
        notice.textContent = "地点台账暂时无法加载。请在原始描述中保留位置，已有提交的地点不会自动改变。";
    } finally {
        clearTimeout(timeout);
        select.disabled = false;
    }
}

function render() {
    const phase = submission?.phase;
    fields.disabled = busy || ["pending", "accepted", "conflict"].includes(phase);
    sendButton.disabled = blocked || busy || ["accepted", "conflict"].includes(phase);
    sendButton.textContent = busy ? "正在提交…" : phase === "pending" ? "原样重试本次提交" : "提交报事";
    newButton.hidden = phase !== "accepted";
    receipt.hidden = phase !== "accepted";
    const viewSaved = document.querySelector("#view-saved-report");
    viewSaved.hidden = phase !== "accepted";
    if (phase === "accepted") {
        viewSaved.href = "/report-evidence?report_id=" + encodeURIComponent(submission.receipt.id);
    } else {
        viewSaved.removeAttribute("href");
    }
    document.querySelector("#retry-note").hidden = phase !== "pending";
    document.querySelector("#submission-reference").hidden = !submission;
    document.querySelector("#request-id").textContent = submission?.payload.request_id ?? "";
    if (phase === "accepted") {
        const saved = submission.receipt;
        document.querySelector("#report-id").textContent = saved.id;
        document.querySelector("#received-at").textContent = new Date(saved.received_at).toLocaleString(
            "zh-CN", { timeZone: "Asia/Shanghai", hour12: false }
        );
        document.querySelector("#saved-description").textContent = saved.description;
    }
}

function remember(next) {
    sessionStorage.setItem(storageKey, JSON.stringify(next));
    submission = next;
}

function restoreFields(payload) {
    field("service_category").value = payload.service_category;
    field("description").value = payload.description;
    field("risk_status").value = payload.risk_status;
    field("observed_at").value = payload.observed_at?.replace("+08:00", "") ?? "";
    selectLocation(payload.location_id);
}

function errorMessage(body) {
    if (typeof body.detail === "string") return body.detail;
    if (Array.isArray(body.detail)) {
        return body.detail.map(item => item.msg).join("；");
    }
    return "输入未通过校验，请核对后重交。";
}

try {
    const stored = sessionStorage.getItem(storageKey);
    if (stored) {
        const restored = JSON.parse(stored);
        if (!["pending", "accepted", "invalid", "conflict"].includes(restored.phase)
            || typeof restored.payload?.request_id !== "string"
            || (restored.phase === "accepted" && !restored.receipt?.id)) {
            throw new Error("无法识别已保存的提交记录");
        }
        submission = restored;
        restoreFields(submission.payload);
        message.textContent = submission.phase === "pending"
            ? "已恢复尚未确认结果的提交，请使用原样重试按钮。"
            : submission.message;
    }
} catch {
    blocked = true;
    message.textContent = "浏览器提交记录无法读取，暂不能提交。请先核对已有报事及浏览器存储设置。";
}
render();

form.addEventListener("submit", async event => {
    event.preventDefault();
    if (blocked || busy || ["accepted", "conflict"].includes(submission?.phase)) return;

    try {
        if (submission?.phase !== "pending") {
            if (locationPicker?.hasDraft()) {
                message.textContent = "地点查找尚未确认，请点击“确认使用此地点”或“地点待核实”后再提交。";
                return;
            }
            if (!form.reportValidity()) return;
            const observed = field("observed_at").value;
            remember({
                phase: "pending",
                payload: {
                    request_id: submission?.payload.request_id ?? crypto.randomUUID(),
                    service_category: field("service_category").value,
                    description: field("description").value,
                    source: "web",
                    risk_status: field("risk_status").value,
                    location_id: field("location_id").value || null,
                    observed_at: observed ? observed + "+08:00" : null,
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
        const response = await fetch("/api/service-requests", {
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
                message: "报事已保存，尚未进行证据研判。" });
        } else if (response.status === 422) {
            remember({ ...submission, phase: "invalid", message: errorMessage(body) });
        } else if (response.status === 409) {
            remember({ ...submission, phase: "conflict",
                message: "提交编号与已有报事内容冲突。请保留编号，先核对原记录，再由人员处理。" });
        } else {
            throw new Error("尚未获得成功回执");
        }
        message.textContent = submission.message;
    } catch {
        message.textContent = "暂未确认保存结果。原编号和原内容已保留，请原样重试；刷新本页后仍可继续。";
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
    selectLocation(null);
    message.textContent = "可以录入下一条报事。";
    render();
    field("service_category").focus();
});

loadLocations();
