const runStatusLabels = {running: "核查中", pending_review: "待审核", failed: "核查失败"};
const queryLabels = {report: "本条报事", related_reports: "其他居民报事", plans: "工作计划", plan_records: "执行与现场检查", manual_links: "人员补充的计划", locations: "地点台账", assets: "设备及管网资料", work_orders: "维修工单", notices: "通知"};
const queryStatuses = {found: "已找到记录", empty: "本次范围内未找到记录", not_queried: "本次尚未查询", unsupported: "尚未接入", failed: "读取失败"};
const typeLabels = {report: "居民报事", plan: "工作计划", execution: "执行记录", inspection: "现场检查", location: "地点台账", link: "人员补充记录", search: "查询范围", context: "核查说明"};
const categoryLabels = {cleaning: "环境保洁", water_supply: "供水", other: "其他事项"};
const claimLabels = {completed: "执行人员填报：已完成", partially_completed: "执行人员填报：部分完成", not_completed: "执行人员填报：未完成", unknown: "完成情况待核实"};
const findingLabels = {no_issue_observed: "现场检查：未发现异常", issue_observed: "现场检查：发现异常", unable_to_verify: "现场检查：无法核实"};
const scopeLabels = {same_location: "本报事地点", same_building: "同栋其他地点，仅为线索", other_location: "其他地点，仅作背景", location_unknown: "记录地点未注明", report_location_unknown: "报事地点待核实"};
const reasonLabels = {same_service_category: "服务类别相同", same_location: "同一台账地点", same_building: "台账属于同一楼栋", within_report_search_window: "时间在本次检索范围内", observed_at: "按居民观察时间查找", received_at_search_only: "仅用接收时间找线索，实际发生时间未知", plan_period: "计划地点与安排时段符合检索条件", execution_record: "找到地点与时间符合条件的执行记录", inspection_record: "找到地点与时间符合条件的现场检查", recorded_time_search_only: "部分记录仅用录入时间发现线索，实际时间未知", manual_supplement: "工作人员补充的计划", previous_manual_link_revoked: "曾由人员移除，本次系统又找到", report_location_unknown: "报事地点未注明，仅按类别和时间找计划"};
const dispositions = {check_together: "值得一起核查", consider_separately: "暂时分开核查", insufficient_information: "信息不足，暂不能判断"};
const isViewObject = value => value && typeof value === "object" && !Array.isArray(value);
const viewNode = (tag, text) => { const node = document.createElement(tag); if (text !== undefined) node.textContent = text; return node; };
const viewValue = value => value === undefined ? "旧快照未保存" : value === null ? "未注明，待核实" : String(value);
const viewTime = value => value === null || value === undefined ? viewValue(value) : Number.isFinite(Date.parse(value)) ? new Date(value).toLocaleString("zh-CN", {timeZone: "Asia/Shanghai", hour12: false}) : "时间格式待核实";
const viewFields = values => { const list = viewNode("dl"); for (const [label, value] of values) list.append(viewNode("dt", label), viewNode("dd", viewValue(value))); return list; };

export function validateAgentRun(run, reportId) {
    if (!isViewObject(run) || run.report_id !== reportId || typeof run.id !== "string" || !Object.hasOwn(runStatusLabels, run.status)) throw Error("核查记录不匹配");
    const evidence = run.evidence;
    if (evidence !== null && evidence !== undefined && (evidence.report_ref !== `report:${reportId}` || !isViewObject(evidence.records) || !Object.values(evidence.records).every(isViewObject))) throw Error("证据快照不匹配");
    if (run.status === "pending_review") {
        const suggestion = run.suggestion;
        const records = evidence?.records;
        if (!isViewObject(suggestion) || !records?.[`report:${reportId}`] || !Array.isArray(suggestion.observations) || !suggestion.observations.length || !Array.isArray(suggestion.verification_items) || !suggestion.verification_items.length || !suggestion.verification_items.every(x => typeof x === "string")) throw Error("建议不完整");
        let sections = [...suggestion.observations];
        if (evidence.source_rule_version === "automatic-evidence-v1") {
            if (suggestion.schema_version !== "automatic-suggestion-v1" || !Array.isArray(suggestion.candidate_assessments) || !Array.isArray(suggestion.uncertainties) || !suggestion.uncertainties.length) throw Error("自动建议不完整");
            const expected = records["context:investigation"]?.candidate_refs;
            const actual = suggestion.candidate_assessments.map(x => x.candidate_ref);
            if (!Array.isArray(expected) || actual.length !== new Set(actual).size || actual.length !== expected.length || !actual.every(ref => expected.includes(ref)) || !suggestion.candidate_assessments.every(x => Object.hasOwn(dispositions, x.disposition) && x.evidence_refs?.includes(x.candidate_ref) && x.evidence_refs.includes(evidence.report_ref))) throw Error("候选判断不完整");
            sections.push(...suggestion.candidate_assessments, ...suggestion.uncertainties);
        }
        if (!sections.every(item => typeof item.text === "string" && Array.isArray(item.evidence_refs) && item.evidence_refs.length && item.evidence_refs.every(ref => Object.hasOwn(records, ref)))) throw Error("引用不匹配");
    }
    return run;
}

export function renderAgentRun(run, reportId) {
    const target = selector => document.querySelector(selector);
    target("#agent-run-result").hidden = !run;
    for (const id of ["records", "scope", "observations", "assessments", "uncertainties", "verification"]) target(`#agent-run-${id}`).replaceChildren();
    target("#agent-run-draft").hidden = true;
    target("#agent-run-legacy-note").hidden = true;
    for (const id of ["agent-query-details", "agent-original-details", "agent-fact-details", "agent-runtime-details"]) target(`#${id}`).open = false;
    target("#agent-fact-details").hidden = true;
    target("#agent-run-summary").textContent = "";
    target("#agent-run-meta").textContent = "";
    if (!run) return;
    validateAgentRun(run, reportId);
    target("#agent-run-summary").textContent = `${runStatusLabels[run.status]} · 核查时间：${viewTime(run.created_at)}（北京时间）`;
    target("#agent-run-meta").textContent = `核查编号 ${run.id} · ${runStatusLabels[run.status]} · ${viewTime(run.created_at)}（北京时间） · ${run.model_name} · 依据截至 ${viewTime(run.evidence?.checked_at)}`;
    const evidence = run.evidence;
    const records = evidence?.records ?? {};
    const automatic = evidence?.source_rule_version === "automatic-evidence-v1";
    const failureTrace = evidence?.source_rule_version === "automatic-evidence-failure-v1";
    const registry = new Map();
    let locationGroup = null;
    const groupedLocationRefs = new Set();
    const place = id => id === undefined || id === null ? viewValue(id) : `${records[`location:${id}`]?.display_path ?? id}`;
    const places = ids => Array.isArray(ids) ? ids.length ? ids.map(place).join("、") : "地点未注明，需核实" : "旧快照未保存";
    const label = (ref, row) => {
        const kind = ref.split(":")[0];
        if (kind === "execution") return `${claimLabels[row.completion_claim] ?? "执行记录"} · ${viewValue(row.executor_name)} · ${places(row.location_ids)}`;
        if (kind === "inspection") return `${findingLabels[row.finding] ?? "现场检查"} · ${viewValue(row.inspector_name)} · ${place(row.location_id)}`;
        if (kind === "report") return `居民报事：${place(row.location_id)} · ${viewTime(row.observed_at)}`;
        if (kind === "search") return `查询范围：${queryLabels[row.source] ?? "业务记录"}`;
        if (kind === "context") return "核查说明";
        return `${typeLabels[kind] ?? "记录"}：${row.title ?? row.display_path ?? row.id ?? ref.split(":").slice(1).join(":")}`;
    };
    for (const [ref, row] of Object.entries(records)) {
        const details = viewNode("details");
        details.id = `agent-record-${run.id}-${ref}`;
        details.className = "agent-record";
        details.append(viewNode("summary", label(ref, row)));
        registry.set(ref, details);
    }
    const citations = refs => {
        const line = viewNode("p"); line.className = "evidence-citations";
        line.append(viewNode("span", "依据："));
        for (const ref of refs) {
            const link = viewNode("a", label(ref, records[ref]));
            link.href = "#" + encodeURIComponent(registry.get(ref).id);
            link.addEventListener("click", () => {
                registry.get(ref).open = true;
                if (groupedLocationRefs.has(ref)) locationGroup.open = true;
                if (ref.startsWith("search:") || ref.startsWith("context:") || ref.startsWith("location:")) target("#agent-query-details").open = true;
                else target("#agent-original-details").open = true;
            });
            line.append(link);
        }
        return line;
    };
    const originalFields = (ref, row) => {
        const kind = ref.split(":")[0];
        const values = [["记录编号", row.id ?? ref.split(":").slice(1).join(":")], ["记录版本", row.version]];
        if (row.service_category !== undefined) values.push(["服务类别", categoryLabels[row.service_category] ?? row.service_category]);
        if (["report", "inspection"].includes(kind)) values.push(["地点", place(row.location_id)], ["观察时间（北京时间）", viewTime(row.observed_at)]);
        if (kind === "report") values.push(["接收时间（北京时间）", viewTime(row.received_at)], ["人员声明的风险", {unknown: "信息不足，风险待核实", urgent: "已报告紧急风险", no_immediate_risk_reported: "未报告即时风险"}[row.risk_status] ?? viewValue(row.risk_status)]);
        if (kind === "plan") values.push(["服务地点", places(row.location_ids)], ["负责人", row.owner_name], ["计划开始（北京时间）", viewTime(row.planned_start_at)], ["计划结束（北京时间）", viewTime(row.planned_end_at)]);
        if (kind === "execution") values.push(["执行地点", places(row.location_ids)], ["执行人员", row.executor_name], ["完成填报", claimLabels[row.completion_claim] ?? viewValue(row.completion_claim)], ["工作开始时间（北京时间）", viewTime(row.claimed_started_at)], ["工作完成时间（北京时间）", viewTime(row.claimed_finished_at)], ["录入时间（北京时间）", viewTime(row.recorded_at)]);
        if (kind === "inspection") values.push(["检查人员", row.inspector_name], ["检查所见", findingLabels[row.finding] ?? viewValue(row.finding)], ["录入时间（北京时间）", viewTime(row.recorded_at)]);
        if (["execution", "inspection"].includes(kind)) values.push(["所属计划", records[row.plan_ref]?.title ?? row.plan_id ?? row.plan_ref]);
        if (row.location_relation) values.push(["适用范围", scopeLabels[row.location_relation] ?? row.location_relation]);
        if (kind === "link") values.push(["填报人", row.linked_by], ["选择时间（北京时间）", viewTime(row.linked_at)], ["选择理由", row.reason]);
        const details = registry.get(ref);
        details.append(viewFields(values));
        if (row.description !== undefined || row.requirements !== undefined) details.append(viewNode("h4", kind === "plan" ? "工作要求原文" : "记录原文"), viewNode("pre", row.description ?? row.requirements));
        if (row.discovery) {
            const reasons = row.discovery.reason_codes ?? [];
            details.append(viewNode("p", "发现理由：" + reasons.map(code => reasonLabels[code] ?? "其他检索线索").join("；")));
            if (row.discovery.manual_link_ids?.length) details.append(viewNode("p", row.discovery.automatic ? "系统发现，工作人员也曾补充此计划。" : "由工作人员补充此计划。"));
            for (const warning of row.discovery.warnings ?? []) details.append(viewNode("p", warning));
        }
        const relation = row.report_comparison?.relation;
        if (relation) details.append(viewNode("p", {after: "居民观察晚于执行人员填报的完成时间；可能再次出现，也需核实此前效果，不能直接认定未完成或虚报。", before: "居民观察早于执行人员填报的完成时间，不能据此判断之后没有完成。", same_time: "居民观察与填报完成时间相同，仍需核实现场。", unknown: "地点、类别或实际时间尚未明确，不能判断本报事与完成填报的先后。"}[relation] ?? "时间关系待核实"));
        if (row.observation_relation_to_report) details.append(viewNode("p", {after: "这次现场检查晚于居民观察。", before: "这次现场检查早于居民观察，不能证明之后仍然正常。", same_time: "现场检查与居民观察时间相同。", unknown: "地点或实际时间尚未明确，不能把这次检查作为本报事的时间对照。"}[row.observation_relation_to_report]));
        if (kind === "plan") {
            for (const [path, text] of [["executions", "新增或查看执行记录"], ["inspections", "新增或查看现场检查"]]) {
                const link = viewNode("a", text); link.href = `/${path}?plan_id=${encodeURIComponent(row.id ?? ref.slice(5))}&report_id=${encodeURIComponent(reportId)}`;
                details.append(link, viewNode("span", " "));
            }
            if (row.plan_checks?.findings?.length) {
                const checks = viewNode("details"); checks.append(viewNode("summary", "程序核对提示"));
                for (const finding of row.plan_checks.findings) checks.append(viewNode("p", `${finding.location_id ? place(finding.location_id) + "：" : ""}${finding.message}`));
                details.append(checks);
            }
        }
    };
    const business = [], queries = [], locations = [], contexts = [];
    for (const [ref, row] of Object.entries(records)) {
        const kind = ref.split(":")[0];
        const details = registry.get(ref);
        if (["report", "plan", "execution", "inspection", "link"].includes(kind)) {
            originalFields(ref, row);
            if (ref === evidence.report_ref) details.open = false;
            business.push(details);
        } else if (kind === "search") {
            details.children[0].textContent = `${queryLabels[row.source] ?? "业务记录"}：${queryStatuses[row.status] ?? "状态待核实"}${row.count === null || row.count === undefined ? "" : `（${row.count}${row.source === "locations" ? "项" : "条"}）`}`;
            const scope = row.scope ?? {};
            const values = [];
            if (scope.window_start) values.push(["查找时间范围（北京时间）", `${viewTime(scope.window_start)} 至 ${viewTime(scope.window_end)}`]);
            if (scope.time_basis) values.push(["时间依据", scope.time_basis === "observed_at" ? "居民观察时间" : "接收时间仅用于找线索，实际发生时间未知"]);
            if (scope.location_scope) values.push(["地点范围", {same_building: "同一楼栋", same_location: "同一台账地点", unknown: "地点待核实，未按地点查询其他报事"}[scope.location_scope] ?? scope.location_scope]);
            if (scope.report_location_id !== undefined) values.push(["起始报事地点", place(scope.report_location_id)]);
            if (scope.category) values.push(["服务类别", categoryLabels[scope.category] ?? scope.category]);
            if (scope.max_reports || scope.max_candidates) values.push(["候选数量上限", scope.max_reports ?? scope.max_candidates]);
            if (scope.location_rule) values.push(["自动查找计划的地点范围", "同一明确点位；报事地点未知时仅按类别和时间查找"]);
            if (scope.record_scope) values.push(["记录范围", "候选计划下全部已保存执行和现场检查，包括明确标注的其他地点背景"]);
            if (scope.max_total_child_records) values.push(["执行与检查记录总数上限", scope.max_total_child_records]);
            if (scope.plan_ref) values.push(["对应计划", records[scope.plan_ref]?.title ?? scope.plan_ref]);
            if (scope.manual_supplements) values.push(["人员补充范围", "保留工作人员指定的计划，可能超出自动查找范围"]);
            if (scope.policy_version) values.push(["检索规则版本", scope.policy_version]);
            details.append(viewFields(values), viewNode("p", row.note ?? ""));
            if (row.error_code) details.append(viewNode("p", `读取失败（${row.error_code}），没有把失败结果当作查无记录。`));
            if (row.status === "failed" || row.status === "unsupported") details.open = true;
            queries.push({ref, row, details});
        } else if (kind === "location") {
            details.append(viewFields([["生成时地点路径", row.display_path], ["地点编号", row.id], ["台账版本", row.version]]));
            locations.push(details);
            groupedLocationRefs.add(ref);
        } else if (kind === "context") {
            details.append(viewNode("p", row.candidate_meaning ?? "本次查询未能完成，以下仅保留查询轨迹。"));
            for (const warning of row.warnings ?? []) details.append(viewNode("p", warning));
            contexts.push(details);
        }
    }
    const queryOrder = Object.keys(queryLabels);
    const rank = source => queryOrder.includes(source) ? queryOrder.indexOf(source) : queryOrder.length;
    queries.sort((a, b) => Number(b.row.status === "failed") - Number(a.row.status === "failed") || rank(a.row.source) - rank(b.row.source));
    const sources = [];
    const locationQueries = [];
    for (const query of queries) {
        // Keep failures visible; successful ledger query details belong with the ledger nodes.
        if (locations.length && query.row.source === "locations" && query.row.status !== "failed") {
            locationQueries.push(query.details);
            groupedLocationRefs.add(query.ref);
        } else sources.push(query.details);
    }
    if (locations.length) {
        locationGroup = viewNode("details");
        locationGroup.className = "agent-record";
        locationGroup.append(viewNode("summary", `地点依据（${locations.length}项）`),
            viewNode("p", "用于核对地点层级、路径和台账版本；这些点位不代表新增报事，也不能证明共同设备或故障原因。"),
            ...locationQueries, ...locations);
        sources.push(locationGroup);
    }
    sources.push(...contexts);
    target("#agent-run-records").replaceChildren(...(business.length ? business : [viewNode("p", failureTrace ? "本次取证未完成，未生成正常分析；可展开下方查询范围查看失败位置。" : "本次尚无可显示的证据快照。") ]));
    target("#agent-run-scope").replaceChildren(...(sources.length ? sources : [viewNode("p", "旧版记录未保存完整查询范围；不能据此认定其他来源已查过。") ]));
    if (run.status === "failed") target("#agent-query-details").open = true;
    if (!automatic && !failureTrace && evidence) {
        target("#agent-run-legacy-note").hidden = false;
        target("#agent-run-legacy-note").textContent = "这是旧版建议，保留原生成内容；未保存自动候选判断与完整查询范围。可发起新核查读取当前记录。";
    }
    if (run.status !== "pending_review") return;
    target("#agent-run-draft").hidden = false;
    target("#agent-fact-details").hidden = false;
    const appendObservations = (selector, rows, assess = false) => {
        const items = rows.map(row => { const item = viewNode("li"); item.append(viewNode("p", (assess ? dispositions[row.disposition] + "：" : "") + row.text), citations(row.evidence_refs)); return item; });
        target(selector).replaceChildren(...items);
    };
    appendObservations("#agent-run-observations", run.suggestion.observations);
    if (automatic) {
        appendObservations("#agent-run-assessments", run.suggestion.candidate_assessments, true);
        if (!run.suggestion.candidate_assessments.length) target("#agent-run-assessments").append(viewNode("li", "本次范围内未找到其他候选记录。请结合下方信息缺口继续核实，不代表没有开展工作。"));
        appendObservations("#agent-run-uncertainties", run.suggestion.uncertainties);
    } else {
        target("#agent-run-assessments").append(viewNode("li", "旧版建议未单独保存候选判断。"));
        target("#agent-run-uncertainties").append(viewNode("li", "旧版建议未单独保存不确定事项，请查看原建议并复核。"));
    }
    target("#agent-run-verification").replaceChildren(...run.suggestion.verification_items.map(text => viewNode("li", text)));
}
