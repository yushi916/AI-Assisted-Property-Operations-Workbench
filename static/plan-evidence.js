const root = document.querySelector("#plan-evidence");
const planId = root.dataset.planId;
const button = document.querySelector("#refresh-evidence");
const message = document.querySelector("#evidence-message");
const result = document.querySelector("#evidence-result");
const kinds = {plan: "计划", execution: "执行记录", inspection: "巡查记录"};
const observations = {
    no_issue_observed: "未观察到异常", issue_observed: "发现异常", unable_to_verify: "无法核实",
};
const relations = {
    before: "观察时间早于声称完成时间",
    same_time: "两个时间相同，无法确定先后",
    after: "观察时间晚于声称完成时间，需核实是否再次产生等情况",
    unknown: "时间信息不足，无法判断先后",
};
let busy = false;

function requireValue(condition) {
    if (!condition) throw new Error("核对结果格式异常");
}

function refText(ref) {
    requireValue(ref && Object.hasOwn(kinds, ref.kind)
        && typeof ref.id === "string" && ref.id.length > 0
        && Number.isInteger(ref.version) && ref.version > 0);
    return `${kinds[ref.kind]} ${ref.id}（版本 ${ref.version}）`;
}

function fillList(selector, texts, emptyText) {
    const nodes = (texts.length ? texts : [emptyText]).map(text => {
        const item = document.createElement("li");
        item.textContent = text;
        return item;
    });
    document.querySelector(selector).replaceChildren(...nodes);
}

async function getJSON(path, signal) {
    const response = await fetch(path, {signal, cache: "no-store"});
    if (!response.ok) {
        const error = new Error("查询未完成");
        error.status = response.status;
        throw error;
    }
    return response.json();
}

function renderEvidence(data, names) {
    requireValue(data && data.plan_id === planId && data.rule_version === "plan-evidence-v1"
        && Number.isInteger(data.plan_version) && data.plan_version > 0
        && typeof data.checked_at === "string" && Number.isFinite(Date.parse(data.checked_at))
        && typeof data.due === "boolean"
        && [data.coverage, data.findings, data.comparisons, data.evidence].every(Array.isArray));
    const references = data.evidence.map(refText);
    requireValue(data.evidence.some(ref => ref.kind === "plan"
        && ref.id === planId && ref.version === data.plan_version));
    const place = id => id === null ? "记录整体提示"
        : names?.has(id) ? `${names.get(id)}（${id}）`
        : `${id}（${names ? "当前台账未查到" : "地点名称暂无法读取"}）`;
    const coverage = data.coverage.map(item => {
        requireValue(typeof item.location_id === "string"
            && [item.execution_ids, item.inspection_ids].every(ids =>
                Array.isArray(ids) && ids.every(id => typeof id === "string")));
        const row = document.createElement("tr");
        for (const value of [place(item.location_id), ...[item.execution_ids, item.inspection_ids]
            .map(ids => ids.length ? `${ids.length}条：${ids.join("、")}` : "0条明确记录")]) {
            const cell = document.createElement("td");
            cell.textContent = value;
            row.append(cell);
        }
        return row;
    });
    const findings = data.findings.map(item => {
        requireValue(typeof item.message === "string" && Array.isArray(item.evidence)
            && (item.location_id === null || typeof item.location_id === "string"));
        return `${place(item.location_id)}：${item.message} 依据：${item.evidence.map(refText).join("；")}`;
    });
    const comparisons = data.comparisons.map(item => {
        requireValue(typeof item.location_id === "string"
            && typeof item.execution_id === "string" && typeof item.inspection_id === "string"
            && Object.hasOwn(observations, item.finding) && Object.hasOwn(relations, item.relation));
        return `${place(item.location_id)}：${observations[item.finding]}；${relations[item.relation]}。`
            + ` 执行记录 ${item.execution_id}；巡查记录 ${item.inspection_id}`;
    });
    const summary = document.querySelector("#evidence-summary");
    summary.replaceChildren();
    for (const [label, value] of [
        ["核对时间（北京时间）", new Date(data.checked_at).toLocaleString("zh-CN", {
            timeZone: "Asia/Shanghai", hour12: false,
        })],
        ["计划版本", data.plan_version], ["规则版本", data.rule_version],
        ["计划截止状态", data.due ? "已到达计划截止时间；仍需核对履约证据" : "尚未到达计划截止时间"],
        ["本次证据引用数", data.evidence.length],
    ]) {
        const term = document.createElement("dt");
        const description = document.createElement("dd");
        term.textContent = label;
        description.textContent = value;
        summary.append(term, description);
    }
    document.querySelector("#evidence-coverage").replaceChildren(...coverage);
    fillList("#evidence-findings", findings, "本次未发现上述规则提示；仍需人员复核。");
    fillList("#evidence-comparisons", comparisons, "暂无可进行时间比较的同点位记录组合。");
    fillList("#evidence-references", references, "暂无证据引用。");
}

async function loadEvidence() {
    if (busy) return;
    busy = true;
    button.disabled = true;
    result.hidden = true;
    message.textContent = "正在核对证据…";
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 10000);
    try {
        const data = await getJSON(`/api/plans/${encodeURIComponent(planId)}/evidence-check`, controller.signal);
        let names = null;
        try {
            const locations = await getJSON("/api/locations", controller.signal);
            requireValue(Array.isArray(locations) && locations.every(item => item
                && typeof item.id === "string" && typeof item.display_path === "string"));
            names = new Map(locations.map(item => [item.id, item.display_path]));
        } catch {
            // 地点名称是辅助信息；读取失败时仍保留证据中的地点编号。
        }
        renderEvidence(data, names);
        result.hidden = false;
        message.textContent = "证据核对已更新，需要人员复核。"
            + (names ? "" : " 地点名称暂无法读取，已显示原地点编号。");
    } catch (error) {
        message.textContent = error.status === 404 ? "计划不存在，请核对计划编号。"
            : error.name === "AbortError" ? "证据核对查询超时，请点击刷新重试。"
            : "证据核对未完成，请点击刷新重试。";
    } finally {
        clearTimeout(timeout);
        busy = false;
        button.disabled = false;
    }
}

button.addEventListener("click", loadEvidence);
loadEvidence();