const root = document.querySelector("#plan-evidence");
const planId = root.dataset.planId;
const button = document.querySelector("#refresh-evidence");
const message = document.querySelector("#evidence-message");
const result = document.querySelector("#evidence-result");
const kinds = {plan: "计划要求", execution: "执行填报", inspection: "现场检查"};
const observations = {no_issue_observed: "本次该地点观察未发现异常", issue_observed: "本次该地点观察发现异常", unable_to_verify: "本次观察无法核实现场情况"};
const relations = {
    before: "观察时间早于执行人员填报的完成时间",
    same_time: "两个时间相同，无法确定先后",
    after: "观察时间晚于执行人员填报的完成时间",
    unknown: "时间信息不足，无法判断先后",
};
const findingLabels = {
    missing_execution: "缺少执行记录", missing_inspection: "缺少现场检查记录", issue_observed: "检查发现异常",
    completion_not_claimed: "完成填报待核实", unknown_execution_scope: "执行地点待核实",
    unknown_completion_time: "完成时间待核实", unknown_inspection_location: "检查地点待核实",
    unknown_inspection_time: "观察时间待核实", completion_after_deadline: "完成时间晚于计划",
    completion_before_plan_start: "完成时间早于计划", execution_outside_scope: "执行地点超出计划",
    inspection_outside_scope: "检查地点超出计划",
};
let busy = false;
let round = 0;
const sourceControllers = new Set();
const node = (tag, text) => { const element = document.createElement(tag); if (text !== undefined) element.textContent = String(text); return element; };
const time = value => value === null || value === undefined ? "未注明，待核实"
    : Number.isFinite(Date.parse(value)) ? new Date(value).toLocaleString("zh-CN", {timeZone: "Asia/Shanghai", hour12: false}) : "时间格式待核实";
function requireValue(condition) { if (!condition) throw new Error("核对结果格式异常"); }
const refKey = ref => `${ref.kind}:${ref.id}`;
function validateRef(ref) {
    requireValue(ref && Object.hasOwn(kinds, ref.kind) && typeof ref.id === "string" && ref.id.length > 0
        && Number.isInteger(ref.version) && ref.version > 0);
}
async function getJSON(path, signal) {
    const response = await fetch(path, {signal, cache: "no-store"});
    if (!response.ok) { const error = new Error("查询未完成"); error.status = response.status; throw error; }
    return response.json();
}
function renderEvidence(data, names, currentRound) {
    requireValue(data && data.plan_id === planId && data.rule_version === "plan-evidence-v1"
        && Number.isInteger(data.plan_version) && data.plan_version > 0
        && typeof data.checked_at === "string" && Number.isFinite(Date.parse(data.checked_at))
        && typeof data.due === "boolean" && [data.coverage, data.findings, data.comparisons, data.evidence].every(Array.isArray));
    data.evidence.forEach(validateRef);
    const refs = new Map(data.evidence.map(ref => [refKey(ref), ref]));
    requireValue(refs.size === data.evidence.length && refs.get(`plan:${planId}`)?.version === data.plan_version);
    const fullPlace = id => id === null ? "地点待核实" : names?.get(id) ?? `${id}（${names ? "当前台账未查到" : "地点名称暂无法读取"}）`;
    const segments = new Map(Array.from(names ?? [], ([id, path]) => [id, path.split("/").map(x => x.trim()).filter(Boolean)]));
    const place = id => {
        if (id === null) return "记录整体提示";
        const parts = segments.get(id);
        if (!parts?.length) return fullPlace(id);
        for (let width = 1; width <= parts.length; width++) {
            const short = parts.slice(-width).join(" / ");
            if (Array.from(segments).every(([other, otherParts]) => other === id || otherParts.slice(-width).join(" / ") !== short)) return short;
        }
        return parts.join(" / ") + `（${id}）`;
    };
    const inferredPlaces = new Map();
    for (const item of data.coverage) {
        requireValue(typeof item.location_id === "string" && [item.execution_ids, item.inspection_ids].every(ids =>
            Array.isArray(ids) && ids.every(id => typeof id === "string")));
        for (const [kind, ids] of [["execution", item.execution_ids], ["inspection", item.inspection_ids]]) {
            for (const id of ids) {
                const key = `${kind}:${id}`; requireValue(refs.has(key));
                if (!inferredPlaces.has(key)) inferredPlaces.set(key, []);
                inferredPlaces.get(key).push(item.location_id);
            }
        }
    }
    const sourceEntries = new Map();
    const originals = document.querySelector("#evidence-references");
    originals.replaceChildren();
    document.querySelector("#evidence-source-details").open = false;
    let citationNumber = 0;
    const currentPlan = document.querySelector("#evidence-current-plan");
    currentPlan.replaceChildren(node("span", "当前计划："));
    function description(entry) {
        const {ref, record, index} = entry;
        if (ref.kind === "plan") return record
            ? `${record.title}｜计划时间（北京时间）：${time(record.planned_start_at)}—${time(record.planned_end_at)}｜负责人：${record.owner_name ?? "未注明，待核实"}`
            : "查看本计划工作要求（展开后显示日期、时间和负责人）";
        const points = record ? ref.kind === "inspection" ? (record.location_id === null ? [] : [record.location_id]) : record.location_ids
            : inferredPlaces.get(refKey(ref));
        const locations = points?.length ? points.map(place).join("、") : record ? "地点待核实" : "地点详见原文";
        if (!record) return `${locations}｜依据${index}（展开后查看日期、时间和人员）`;
        const timestamp = ref.kind === "inspection" ? `观察时间（北京时间）：${time(record.observed_at)}`
            : record.completion_claim === "completed" ? `填报完成时间（北京时间）：${time(record.claimed_finished_at)}`
            : `工作开始时间（北京时间）：${time(record.claimed_started_at)}`;
        const person = ref.kind === "inspection" ? `检查人：${record.inspector_name ?? "未注明，待核实"}`
            : `执行人：${record.executor_name ?? "未注明，待核实"}`;
        return `${locations}｜${timestamp}｜${person}`;
    }
    const label = entry => `${kinds[entry.ref.kind]}｜${description(entry)}`;
    function updateLabels(entry) {
        entry.summaryText.textContent = description(entry);
        for (const element of entry.labels) element.textContent = label(entry);
    }
    function fields(values) {
        const dl = node("dl");
        for (const [key, value] of values) dl.append(node("dt", key), node("dd", value === null || value === undefined ? "未注明，待核实" : value));
        return dl;
    }
    function renderOriginal(entry, row) {
        const {ref, body} = entry;
        const values = [];
        if (ref.kind === "plan") values.push(["计划要求", row.title], ["计划负责人", row.owner_name],
            ["计划开始（北京时间）", time(row.planned_start_at)], ["计划结束（北京时间）", time(row.planned_end_at)]);
        if (ref.kind === "execution") values.push(["执行人员", row.executor_name],
            ["执行人员填报", {completed: "已完成", partially_completed: "部分完成", not_completed: "未完成", unknown: "待核实"}[row.completion_claim]],
            ["工作开始（北京时间）", time(row.claimed_started_at)], ["填报完成（北京时间）", time(row.claimed_finished_at)]);
        if (ref.kind === "inspection") values.push(["检查人", row.inspector_name], ["观察所见", observations[row.finding]],
            ["观察时间（北京时间）", time(row.observed_at)]);
        const points = ref.kind === "inspection" ? (row.location_id === null ? [] : [row.location_id]) : row.location_ids;
        values.push([ref.kind === "plan" ? "计划覆盖地点" : ref.kind === "execution" ? "本次填报工作地点" : "本次观察地点", points.length ? points.map(place).join("、") : "地点待核实"]);
        const metadata = node("details");
        metadata.append(node("summary", "完整地点、编号与版本"), fields([
            ["完整地点路径（当前台账）", points.length ? points.map(id => `${fullPlace(id)}（${id}）`).join("、") : "地点待核实"],
            ["记录编号", row.id], ["记录版本", row.version], ["提交编号", row.request_id],
            ["所属计划编号", ref.kind === "plan" ? row.id : row.plan_id],
            [ref.kind === "plan" ? "创建时间（北京时间）" : "录入时间（北京时间）", time(row.created_at ?? row.recorded_at)],
        ]));
        body.replaceChildren(fields(values), node("h4", `${kinds[ref.kind]}原文`),
            node("pre", ref.kind === "plan" ? row.requirements : row.description), metadata);
    }
    async function loadOriginal(entry) {
        if (entry.record || entry.loading) return;
        entry.loading = true;
        entry.body.replaceChildren(node("p", "正在读取对应版本的原文…"));
        const controller = new AbortController(); sourceControllers.add(controller);
        const timeout = setTimeout(() => controller.abort(), 10000);
        try {
            const paths = {plan: "/api/plans/", execution: "/api/executions/", inspection: "/api/inspections/"};
            const row = await getJSON(paths[entry.ref.kind] + encodeURIComponent(entry.ref.id), controller.signal);
            if (currentRound !== round) return;
            if (row.id !== entry.ref.id || row.version !== entry.ref.version || (entry.ref.kind !== "plan" && row.plan_id !== planId)) {
                entry.body.replaceChildren(node("p", "当前记录的编号、版本或所属计划与本次核对不一致，未展示原文。请点击“重新核对记录”。"));
                return;
            }
            requireValue(entry.ref.kind === "inspection" ? (row.location_id === null || typeof row.location_id === "string")
                : Array.isArray(row.location_ids) && row.location_ids.every(id => typeof id === "string"));
            requireValue(typeof (entry.ref.kind === "plan" ? row.requirements : row.description) === "string");
            renderOriginal(entry, row); entry.record = row; updateLabels(entry);
        } catch (error) {
            if (currentRound === round) entry.body.replaceChildren(node("p", error.status === 404
                ? "对应记录已无法读取，未当作空记录。请重新核对记录。"
                : "原文读取未完成，核对结果仍保留。可再次点击依据重试；未将失败当作查无记录。"));
        } finally {
            clearTimeout(timeout); sourceControllers.delete(controller); entry.loading = false;
        }
    }
    let index = 0;
    for (const ref of data.evidence) {
        const details = node("details"); details.id = `plan-source-${currentRound}-${ref.kind}-${ref.id}`;
        details.className = "plan-source"; details.tabIndex = -1;
        const summary = node("summary"), summaryText = node("span"), typeLabel = node("span", kinds[ref.kind]);
        typeLabel.className = `source-label material-type-label material-type-${ref.kind}`;
        summary.append(typeLabel, summaryText);
        const body = node("div"), back = node("a", "返回引用位置"); back.hidden = true;
        body.append(node("p", "展开后读取本次核对版本的原文。"));
        details.append(summary, body, back);
        const entry = {ref, index: ++index, details, body, back, summaryText, labels: [], record: null, loading: false};
        sourceEntries.set(refKey(ref), entry); updateLabels(entry);
        details.addEventListener("toggle", () => { if (details.open) return loadOriginal(entry); });
        originals.append(details);
    }
    function citation(ref) {
        validateRef(ref);
        const entry = sourceEntries.get(refKey(ref)); requireValue(entry && entry.ref.version === ref.version);
        const link = node("a", label(entry)); link.className = "record-citation";
        link.id = `plan-citation-${currentRound}-${++citationNumber}`;
        link.href = "#" + encodeURIComponent(entry.details.id); entry.labels.push(link);
        link.addEventListener("click", async () => {
            document.querySelector("#evidence-source-details").open = true;
            for (const item of sourceEntries.values()) item.details.className = "plan-source";
            entry.details.open = true; entry.details.className = "plan-source record-focused";
            entry.back.hidden = false; entry.back.href = "#" + link.id;
            entry.details.scrollIntoView?.({block: "start"}); entry.details.focus?.({preventScroll: true});
            await loadOriginal(entry);
        });
        return link;
    }
    function citations(items, heading = "依据原文：") {
        const line = node("div"); line.className = "record-citations"; line.append(node("span", heading));
        for (const ref of items) line.append(citation(ref));
        return line;
    }
    currentPlan.append(citation(refs.get(`plan:${planId}`)));
    const coverage = data.coverage.map(item => {
        const row = node("tr"), point = node("td", place(item.location_id));
        const address = node("details"); address.append(node("summary", "完整地点"), node("p", `${fullPlace(item.location_id)}（${item.location_id}）`)); point.append(address); row.append(point);
        for (const [kind, ids] of [["execution", item.execution_ids], ["inspection", item.inspection_ids]]) {
            const cell = node("td", ids.length ? `${ids.length}条` : "0条明确记录");
            if (ids.length) cell.append(citations(ids.map(id => refs.get(`${kind}:${id}`))));
            row.append(cell);
        }
        return row;
    });
    const findings = data.findings.map(item => {
        requireValue(typeof item.message === "string" && Array.isArray(item.evidence)
            && (item.location_id === null || typeof item.location_id === "string"));
        const li = node("li"); const tag = node("span", findingLabels[item.code] ?? "待核实"); tag.className = "source-label finding-label";
        li.append(tag, node("strong", place(item.location_id)));
        if (["missing_execution", "missing_inspection"].includes(item.code)) {
            const point = data.coverage.find(row => row.location_id === item.location_id);
            requireValue(point && (item.code === "missing_execution" ? point.execution_ids.length : point.inspection_ids.length) === 0);
            const query = node("p", `本次查询结果：${place(item.location_id)}，执行记录${point.execution_ids.length}条、现场检查记录${point.inspection_ids.length}条。`);
            const allPoints = node("a", "查看各地点记录数量"); allPoints.href = "#evidence-coverage-title";
            query.append(allPoints); li.append(query);
            li.append(node("p", item.code === "missing_execution"
                ? "本次核对未查到该地点的执行记录。请核实是否已开展工作、是否遗漏填报，再决定是否需要现场核查。"
                : "请先核实该地点是否已进行现场检查、是否遗漏检查填报或未注明地点，再对照计划要求确认后续事项。"));
            if (item.code === "missing_execution" && point.inspection_ids.length) li.append(node("p", "已有现场检查可供对照，但不能代替执行填报。"));
            requireValue(item.evidence.length > 0 && item.evidence.every(ref => ref.kind === "plan" && ref.id === planId));
            li.append(citations(item.evidence, "对照计划要求："), node("p", "计划用于说明需要核对哪些工作；本次缺记录不表示工作未开展或未完成。"));
            const originalMessage = node("details");
            originalMessage.append(node("summary", "查看规则原始提示"), node("p", item.message));
            li.append(originalMessage);
        } else li.append(node("p", item.message), citations(item.evidence));
        return li;
    });
    const comparisons = data.comparisons.map(item => {
        requireValue(typeof item.location_id === "string" && typeof item.execution_id === "string" && typeof item.inspection_id === "string"
            && Object.hasOwn(observations, item.finding) && Object.hasOwn(relations, item.relation));
        let meaning = "";
        if (item.finding === "no_issue_observed") meaning = "仅描述本次观察时、该地点的情况，不代表其他地点或之后仍然正常。";
        else if (item.finding === "unable_to_verify") meaning = "不能据此判断该地点是否正常。";
        else if (item.relation === "after") meaning = "需核实异常是否持续存在、是否再次出现等可能；仅凭时间不能认定复发或此前未完成。";
        else meaning = "异常情况与原因需核实，不能用这次观察推断其他时段的情况。";
        const li = node("li"); li.append(node("strong", place(item.location_id)),
            node("p", `${observations[item.finding]}；${relations[item.relation]}。${meaning}`),
            citations([refs.get(`execution:${item.execution_id}`), refs.get(`inspection:${item.inspection_id}`)])); return li;
    });
    const summary = document.querySelector("#evidence-summary"); summary.replaceChildren();
    const counts = kind => data.evidence.filter(ref => ref.kind === kind).length;
    for (const [key, value] of [
        ["核对时间（北京时间）", time(data.checked_at)], ["计划版本", data.plan_version], ["规则版本", data.rule_version],
        ["计划截止状态", data.due ? "已到达计划截止时间；仍需查看执行和检查记录" : "尚未到达计划截止时间"],
        ["本次使用的记录", `计划${counts("plan")}份、执行记录${counts("execution")}条、现场检查${counts("inspection")}条`],
    ]) summary.append(node("dt", key), node("dd", value));
    document.querySelector("#evidence-coverage").replaceChildren(...coverage);
    document.querySelector("#evidence-findings").replaceChildren(...(findings.length ? findings : [node("li", "本次未发现上述规则提示；仍需人员复核。") ]));
    document.querySelector("#evidence-comparisons").replaceChildren(...(comparisons.length ? comparisons : [node("li", "暂无可进行时间比较的同地点记录组合。") ]));
}
async function loadEvidence() {
    if (busy) return;
    busy = true; const currentRound = ++round;
    for (const controller of sourceControllers) controller.abort(); sourceControllers.clear();
    button.disabled = true; result.hidden = true;
    document.querySelector("#evidence-current-plan").replaceChildren();
    message.textContent = "正在核对计划记录…";
    const controller = new AbortController(); const timeout = setTimeout(() => controller.abort(), 10000);
    try {
        const data = await getJSON(`/api/plans/${encodeURIComponent(planId)}/evidence-check`, controller.signal);
        let names = null;
        try {
            const locations = await getJSON("/api/locations", controller.signal);
            requireValue(Array.isArray(locations) && locations.every(item => item && typeof item.id === "string" && typeof item.display_path === "string"));
            names = new Map(locations.map(item => [item.id, item.display_path]));
        } catch { /* Keep the original location IDs if names cannot be read. */ }
        renderEvidence(data, names, currentRound); result.hidden = false;
        message.textContent = "记录核对已更新，需要人员复核。" + (names ? "" : " 地点名称暂无法读取，已显示原地点编号。");
    } catch (error) {
        message.textContent = error.status === 404 ? "计划不存在，请核对计划编号。"
            : error.name === "AbortError" ? "记录核对查询超时，请点击重新核对记录重试。" : "记录核对未完成，请点击重新核对记录重试。";
    } finally { clearTimeout(timeout); busy = false; button.disabled = false; }
}
button.addEventListener("click", loadEvidence);
loadEvidence();
