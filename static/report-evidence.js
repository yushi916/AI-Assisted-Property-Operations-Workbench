const reportId = document.querySelector("#report-evidence").dataset.reportId;
const refreshButton = document.querySelector("#refresh-report-evidence");
const message = document.querySelector("#report-evidence-message");
const result = document.querySelector("#report-evidence-result");
const claims = {completed: "已完成", partially_completed: "部分完成", not_completed: "未完成", unknown: "完成情况待核实"};
const findings = {no_issue_observed: "未发现异常", issue_observed: "发现异常", unable_to_verify: "无法核实"};
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

function recordHeading(kind, rows, locationId, scopeMatches) {
    const isExecution = kind === "执行";
    const title = isExecution ? "工作执行记录" : "现场检查记录";
    const unknown = rows.filter(row => isExecution
        ? row.location_ids.length === 0 : row.location_id === null).length;
    if (!scopeMatches) {
        return [node("h4", title), node("p", `该计划共有 ${rows.length} 条记录，其中 ${unknown} 条未注明地点。`)];
    }
    const matched = rows.filter(row => isExecution
        ? row.location_ids.includes(locationId) : row.location_id === locationId).length;
    return [node("h4", title), node("p", `本报事地点的记录：${matched} 条；未注明地点的记录：${unknown} 条。`)];
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
    const place = id => id === null ? "地点未注明，需核实" : names?.has(id)
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
        details.append(node("summary", `${link.status === "active" ? "已添加" : "已移除"}：计划 ${link.plan_id}`), fields([
            ["操作记录编号", link.id], ["计划编号", link.plan_id], ["记录版本", link.version], ["添加人", link.linked_by],
            ["添加时间（北京时间）", timeText(link.linked_at)], ["选择理由", link.reason],
        ]));
        if (link.status === "revoked") details.append(fields([
            ["移除人", link.revoked_by], ["移除时间（北京时间）", timeText(link.revoked_at)],
            ["移除理由", link.revoke_reason],
        ]));
        return details;
    });
    const selected = new Set();
    const plans = data.linked_plans.map((item, index) => {
        const link = links.get(item.link_id);
        requireValue(link?.status === "active" && link.plan_id === item.plan?.id && !selected.has(item.link_id)
            && typeof item.scope_matches === "boolean"
            && (!item.scope_matches || typeof report.location_id === "string")
            && [item.plan.location_ids, item.executions, item.inspections, item.report_comparisons, item.warnings].every(Array.isArray)
            && [item.excluded_execution_count, item.excluded_inspection_count].every(x => Number.isInteger(x) && x >= 0));
        selected.add(item.link_id);
        const article = node("article");
        article.append(node("h3", `计划 ${index + 1}：${item.plan.title}`));
        if (!item.scope_matches) {
            const warning = node("p", "请确认这项计划是否相关：服务类别或地点与本条报事未能对应，以下记录仅供参考，暂不比较时间先后。");
            warning.className = "scope-warning";
            article.append(warning);
        }
        article.append(fields([
            ["计划编号", item.plan.id], ["负责人", item.plan.owner_name],
            ["计划服务地点", item.plan.location_ids.map(place).join("、")],
            ["计划开始（北京时间）", timeText(item.plan.planned_start_at)],
            ["计划结束（北京时间）", timeText(item.plan.planned_end_at)],
            ["计划是否适用", item.scope_matches ? "服务类别和地点相符，仍需查看具体记录" : "需要确认"],
        ]), node("h4", "工作要求"), node("pre", item.plan.requirements));
        const executionLink = node("a", "查看执行记录");
        executionLink.href = "/executions?plan_id=" + encodeURIComponent(item.plan.id)
            + "&report_id=" + encodeURIComponent(reportId);
        article.append(executionLink);
        const warnings = node("ul");
        for (const text of item.warnings) {
            requireValue(typeof text === "string");
            warnings.append(node("li", text));
        }
        article.append(warnings, node("p", item.scope_matches
            ? `其他地点的记录未显示在这里：执行 ${item.excluded_execution_count} 条，现场检查 ${item.excluded_inspection_count} 条。`
            : "以下记录尚未按本报事地点筛选。"));
        article.append(...recordHeading("执行", item.executions, report.location_id, item.scope_matches));
        article.append(node("p", "工作开始、完成时间由执行人员填报，是否完成仍需结合现场检查核实。录入时间是记录保存到系统的时间。"));
        const executions = new Map();
        for (const record of item.executions) {
            requireValue(record.plan_id === item.plan.id && Object.hasOwn(claims, record.completion_claim)
                && Array.isArray(record.location_ids) && !executions.has(record.id)
                && (!item.scope_matches || !record.location_ids.length || record.location_ids.includes(report.location_id)));
            executions.set(record.id, record);
            const details = node("details");
            details.append(node("summary", `执行人员填报：${claims[record.completion_claim]} · ${record.executor_name}`), fields([
                ["执行记录编号", record.id], ["执行人员", record.executor_name],
                ["工作地点", record.location_ids.length ? record.location_ids.map(place).join("、") : "地点未注明，需核实"],
                ["工作开始时间（北京时间）", timeText(record.claimed_started_at)],
                ["工作完成时间（北京时间）", timeText(record.claimed_finished_at)],
                ["录入时间（北京时间）", timeText(record.recorded_at)],
            ]), node("h5", "执行说明"), node("pre", record.description));
            article.append(details);
        }
        if (!item.executions.length) article.append(node("p", "暂未查到相关执行记录，是否已经开展工作仍需确认。"));
        article.append(...recordHeading("巡查", item.inspections, report.location_id, item.scope_matches));
        article.append(node("p", "检查结果只反映记录中的时间和地点，不代表整项工作已验收完成。"));
        for (const record of item.inspections) {
            requireValue(record.plan_id === item.plan.id && Object.hasOwn(findings, record.finding)
                && (!item.scope_matches || record.location_id === null || record.location_id === report.location_id));
            const details = node("details");
            details.append(node("summary", `现场检查：${findings[record.finding]} · ${record.inspector_name}`), fields([
                ["检查记录编号", record.id], ["检查人员", record.inspector_name], ["检查地点", place(record.location_id)],
                ["观察时间（北京时间）", timeText(record.observed_at)],
                ["录入时间（北京时间）", timeText(record.recorded_at)],
            ]), node("h5", "现场检查说明"), node("pre", record.description));
            article.append(details);
        }
        if (!item.inspections.length) article.append(node("p", "暂未查到相关现场检查记录，是否已经检查仍需确认。"));
        article.append(node("h4", item.scope_matches
            ? "时间对比" : "时间对比暂不可用"));
        if (item.scope_matches) article.append(node("p", "对比居民看到问题的时间与执行人员填报的完成时间，帮助判断事情发生的先后。"));
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
        if (!compared.size) article.append(node("p", "暂无填报为已完成的执行记录可作时间比较，其他执行记录仍可查看。"));
        return article;
    });
    requireValue(selected.size === data.links.filter(link => link.status === "active").length);
    document.querySelector("#joint-report-fields").replaceChildren(summary);
    document.querySelector("#joint-report-description").textContent = report.description;
    document.querySelector("#joint-links").replaceChildren(...(history.length ? history : [node("p", "还没有添加或移除计划的记录。")]));
    document.querySelector("#joint-plans").replaceChildren(...(plans.length ? plans : [node("p", "尚未由人员补充工作计划。需要时可在下方添加；这不影响AI自动核查，也不表示问题已经解决。" )]));
}

async function loadEvidence() {
    if (busy) return;
    busy = true;
    refreshButton.disabled = true;
    result.hidden = true;
    publishEvidence(null);
    message.textContent = "正在读取报事和人员补充的记录…";
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
        message.textContent = "报事和人员补充的记录已更新；AI核查结果需另行更新。" + (names ? "" : " 地点名称暂无法读取，已保留地点编号。");
    } catch (error) {
        message.textContent = error.status === 404 ? "报事不存在，请核对报事编号。"
            : error.name === "AbortError" ? "报事与补充记录读取超时，请刷新重试；AI核查可独立使用。"
            : "报事与补充记录读取未完成，请刷新重试；不能把查询失败当作没有记录，AI核查可独立读取依据。";
    } finally {
        clearTimeout(timeout);
        busy = false;
        refreshButton.disabled = false;
    }
}

refreshButton.addEventListener("click", loadEvidence);
loadEvidence();
