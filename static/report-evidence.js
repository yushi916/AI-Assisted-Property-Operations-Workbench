const reportId = document.querySelector("#report-evidence").dataset.reportId;
const refreshButton = document.querySelector("#refresh-report-evidence");
const message = document.querySelector("#report-evidence-message");
const result = document.querySelector("#report-evidence-result");
const claims = {completed: "声称完成", partially_completed: "声称部分完成", not_completed: "声称未完成", unknown: "完成情况待核实"};
const findings = {no_issue_observed: "未观察到异常", issue_observed: "发现异常", unable_to_verify: "无法核实"};
const categories = {cleaning: "环境保洁", water_supply: "供水", other: "其他事项"};
let busy = false;
let currentEvidence = null;
let currentNames = null;
const evidenceListeners = [];

export function watchEvidence(listener) {
    evidenceListeners.push(listener);
    listener(currentEvidence, currentNames);
}

function publishEvidence(data, names = null) {
    currentEvidence = data;
    currentNames = names;
    for (const listener of evidenceListeners) listener(data, names);
}

function requireValue(condition) {
    if (!condition) throw new Error("联合证据格式或上下文异常");
}

function node(tag, text) {
    const element = document.createElement(tag);
    if (text !== undefined) element.textContent = text;
    return element;
}

function fields(values) {
    const list = node("dl");
    for (const [label, value] of values) list.append(node("dt", label), node("dd", value));
    return list;
}

function timeText(value) {
    if (value === null) return "未知，待核实";
    requireValue(typeof value === "string" && Number.isFinite(Date.parse(value)));
    return new Date(value).toLocaleString("zh-CN", {timeZone: "Asia/Shanghai", hour12: false});
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
    requireValue(data?.rule_version === "report-evidence-v1" && data.report?.id === reportId
        && [data.links, data.linked_plans].every(Array.isArray));
    const place = id => id === null ? "具体地点待核实" : names?.has(id)
        ? `${names.get(id)}（${id}）` : `${id}（${names ? "当前台账未查到" : "地点名称暂无法读取"}）`;
    const report = data.report;
    const summary = fields([
        ["报事编号", report.id], ["服务类别", categories[report.service_category] ?? report.service_category],
        ["地点（当前台账）", place(report.location_id)],
        ["观察时间（北京时间）", timeText(report.observed_at)],
        ["接收时间（北京时间）", timeText(report.received_at)],
        ["本次核对时间（北京时间）", timeText(data.checked_at)],
    ]);
    const links = new Map(data.links.map(link => {
        requireValue(link.report_id === reportId && typeof link.id === "string"
            && ["active", "revoked"].includes(link.status) && Number.isInteger(link.version) && link.version > 0);
        return [link.id, link];
    }));
    requireValue(links.size === data.links.length);
    const history = data.links.map(link => {
        const details = node("details");
        details.append(node("summary", `${link.status === "active" ? "有效关联" : "已撤销关联"}：${link.id}`), fields([
            ["计划编号", link.plan_id], ["关联版本", link.version], ["填报关联人", link.linked_by],
            ["关联时间（北京时间）", timeText(link.linked_at)], ["原关联理由", link.reason],
        ]));
        if (link.status === "revoked") details.append(fields([
            ["填报撤销人", link.revoked_by], ["撤销时间（北京时间）", timeText(link.revoked_at)],
            ["撤销理由", link.revoke_reason],
        ]));
        return details;
    });
    const selected = new Set();
    const plans = data.linked_plans.map(item => {
        const link = links.get(item.link_id);
        requireValue(link?.status === "active" && link.plan_id === item.plan?.id && !selected.has(item.link_id)
            && typeof item.scope_matches === "boolean"
            && [item.plan.location_ids, item.executions, item.inspections, item.report_comparisons, item.warnings].every(Array.isArray)
            && [item.excluded_execution_count, item.excluded_inspection_count].every(x => Number.isInteger(x) && x >= 0));
        selected.add(item.link_id);
        const article = node("article");
        article.append(node("h3", item.plan.title), fields([
            ["计划编号", item.plan.id], ["负责人", item.plan.owner_name],
            ["计划明确覆盖地点", item.plan.location_ids.map(place).join("、")],
            ["计划开始（北京时间）", timeText(item.plan.planned_start_at)],
            ["计划结束（北京时间）", timeText(item.plan.planned_end_at)],
            ["关联范围核对", item.scope_matches ? "类别和明确地点匹配；仍需核查证据" : "关联范围待重新核实"],
        ]), node("h4", "原计划要求"), node("pre", item.plan.requirements));
        const warnings = node("ul");
        for (const text of item.warnings) {
            requireValue(typeof text === "string");
            warnings.append(node("li", text));
        }
        article.append(warnings, node("p", `其他明确地点记录未纳入本条报事：执行 ${item.excluded_execution_count} 条，巡查 ${item.excluded_inspection_count} 条。`));
        article.append(node("h4", `相关执行记录（${item.executions.length}条，含地点未知）`));
        const executions = new Map();
        for (const record of item.executions) {
            requireValue(record.plan_id === item.plan.id && Object.hasOwn(claims, record.completion_claim)
                && Array.isArray(record.location_ids) && !executions.has(record.id)
                && (!item.scope_matches || !record.location_ids.length || record.location_ids.includes(report.location_id)));
            executions.set(record.id, record);
            const details = node("details");
            details.append(node("summary", `${claims[record.completion_claim]}：${record.id}`), fields([
                ["填报执行人", record.executor_name],
                ["明确执行地点", record.location_ids.length ? record.location_ids.map(place).join("、") : "具体地点待核实"],
                ["声称开始（北京时间）", timeText(record.claimed_started_at)],
                ["声称完成（北京时间）", timeText(record.claimed_finished_at)],
                ["录入时间（北京时间）", timeText(record.recorded_at)],
            ]), node("h5", "原执行说明"), node("pre", record.description));
            article.append(details);
        }
        if (!item.executions.length) article.append(node("p", "未查到本次范围内的执行记录。"));
        article.append(node("h4", `相关巡查记录（${item.inspections.length}条，含地点未知）`));
        for (const record of item.inspections) {
            requireValue(record.plan_id === item.plan.id && Object.hasOwn(findings, record.finding)
                && (!item.scope_matches || record.location_id === null || record.location_id === report.location_id));
            const details = node("details");
            details.append(node("summary", `${findings[record.finding]}：${record.id}`), fields([
                ["填报巡查人", record.inspector_name], ["巡查地点", place(record.location_id)],
                ["观察时间（北京时间）", timeText(record.observed_at)],
                ["录入时间（北京时间）", timeText(record.recorded_at)],
            ]), node("h5", "原巡查说明"), node("pre", record.description));
            article.append(details);
        }
        if (!item.inspections.length) article.append(node("p", "未查到本次范围内的巡查记录。"));
        article.append(node("h4", "居民观察与执行声称完成的时间比较"));
        const compared = new Set();
        for (const comparison of item.report_comparisons) {
            requireValue(executions.get(comparison.execution_id)?.completion_claim === "completed"
                && !compared.has(comparison.execution_id) && typeof comparison.message === "string"
                && ["before", "same_time", "after", "unknown"].includes(comparison.relation)
                && (item.scope_matches || comparison.relation === "unknown"));
            compared.add(comparison.execution_id);
            article.append(node("p", `${comparison.message} 对应执行记录：${comparison.execution_id}`));
        }
        requireValue(compared.size === item.executions.filter(x => x.completion_claim === "completed").length);
        if (!compared.size) article.append(node("p", "暂无声称全部完成的执行记录可作时间比较；其他执行反馈仍保留。"));
        return article;
    });
    requireValue(selected.size === data.links.filter(link => link.status === "active").length);
    document.querySelector("#joint-report-fields").replaceChildren(summary);
    document.querySelector("#joint-report-description").textContent = report.description;
    document.querySelector("#joint-links").replaceChildren(...(history.length ? history : [node("p", "尚无人工关联记录。")]));
    document.querySelector("#joint-plans").replaceChildren(...(plans.length ? plans : [node("p", "当前没有有效关联计划；报事和关联历史仍保留。这个状态不表示问题已经解决。" )]));
}

async function loadEvidence() {
    if (busy) return;
    busy = true;
    refreshButton.disabled = true;
    result.hidden = true;
    publishEvidence(null);
    message.textContent = "正在查询联合证据…";
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 10000);
    try {
        const data = await getJSON(`/api/service-requests/${encodeURIComponent(reportId)}/evidence-check`, controller.signal);
        let names = null;
        try {
            const locations = await getJSON("/api/locations", controller.signal);
            requireValue(Array.isArray(locations) && locations.every(x => typeof x.id === "string" && typeof x.display_path === "string"));
            names = new Map(locations.map(x => [x.id, x.display_path]));
        } catch { /* 名称辅助查询失败时保留地点编号。 */ }
        renderEvidence(data, names);
        result.hidden = false;
        publishEvidence(data, names);
        message.textContent = "联合证据已更新，需人员复核。" + (names ? "" : " 地点名称暂无法读取，已保留地点编号。");
    } catch (error) {
        message.textContent = error.status === 404 ? "报事不存在，请核对报事编号。"
            : error.name === "AbortError" ? "联合证据查询超时，请刷新重试。"
            : "联合证据查询未完成，请刷新重试；不能把查询失败当作没有记录。";
    } finally {
        clearTimeout(timeout);
        busy = false;
        refreshButton.disabled = false;
    }
}

refreshButton.addEventListener("click", loadEvidence);
loadEvidence();