const test = require("node:test");
const assert = require("node:assert/strict");
const vm = require("node:vm");
const {readFileSync} = require("node:fs");
const {join} = require("node:path");
const viewSource = readFileSync(join(__dirname, "..", "static", "agent-run-view.js"), "utf8").replace(/^export /gm, "");
const controllerSource = readFileSync(join(__dirname, "..", "static", "report-agent-runs.js"), "utf8").replace(/^import .*;\n/gm, "");
const reportId = "565eddc7-b40e-47b1-805f-cd89f8128380";
const runId = "00000000-0000-4000-8000-000000000001";
const otherId = "00000000-0000-4000-8000-000000000002";
const time = "2026-11-11T02:00:00Z";
const clone = value => JSON.parse(JSON.stringify(value));
const seed = `report:${reportId}`;

function sample(id = runId) {
    return {id, report_id: reportId, status: "pending_review", model_name: "test-model", created_at: time,
        evidence: {report_ref: seed, source_rule_version: "automatic-evidence-v1", checked_at: time, records: {
            [seed]: {id: reportId, version: 1, location_id: "ROOM", description: "厨房和卫生间都没水了。", observed_at: time, received_at: time, risk_status: "unknown", service_category: "water_supply"},
            "report:near": {id: "near", version: 1, location_id: "ROOM-301", description: "只有洗手盆没水，厨房正常。", discovery: {reason_codes: ["same_building", "within_report_search_window"]}},
            "plan:P1": {id: "P1", version: 1, title: "供水检查", requirements: "分别检查室内和走廊", location_ids: ["ROOM", "CORRIDOR"], owner_name: "维修班王师傅", planned_start_at: time, planned_end_at: time, discovery: {reason_codes: ["plan_period"], automatic: true, manual_link_ids: []}},
            "execution:E1": {id: "E1", version: 1, plan_id: "P1", plan_ref: "plan:P1", description: "本次只检查走廊，未进入室内。", executor_name: "维修班王师傅", completion_claim: "completed", location_ids: ["ROOM"], location_relation: "same_location", claimed_started_at: null, claimed_finished_at: time, recorded_at: time, report_comparison: {relation: "after"}},
            "inspection:I1": {id: "I1", version: 1, plan_ref: "plan:P1", inspector_name: "值班员陈女士", description: "走廊龙头出水正常。", finding: "no_issue_observed", location_id: "CORRIDOR", location_relation: "other_location", observed_at: time, recorded_at: time, observation_relation_to_report: "unknown"},
            "location:ROOM": {id: "ROOM", version: 1, display_path: "清和家园 / 1栋 / 302室"},
            "location:CORRIDOR": {id: "CORRIDOR", version: 1, display_path: "清和家园 / 1栋 / 公共走廊"},
            "context:investigation": {candidate_refs: ["report:near", "plan:P1"], warnings: ["同栋不等于同一故障"]},
            "search:related_reports": {source: "related_reports", status: "found", count: 1, scope: {window_start: time, window_end: time, time_basis: "observed_at", location_scope: "same_building", max_reports: 50}},
            "search:plans": {source: "plans", status: "found", count: 1, scope: {window_start: time, window_end: time, location_rule: "exact_point_or_unknown_report_fallback", max_candidates: 50, manual_supplements: "active_links_included_outside_automatic_window"}},
            "search:assets": {source: "assets", status: "unsupported", count: null, note: "尚未接入设备及管网资料，不能确认共同设备。"},
        }}, suggestion: {schema_version: "automatic-suggestion-v1",
            observations: [{text: "居民反映室内无水。", evidence_refs: [seed]}],
            candidate_assessments: [
                {candidate_ref: "report:near", disposition: "consider_separately", text: "另一户仅局部用水异常，先分别核实。", evidence_refs: [seed, "report:near"]},
                {candidate_ref: "plan:P1", disposition: "check_together", text: "核对室内和走廊记录的范围。", evidence_refs: [seed, "plan:P1", "execution:E1"]},
            ],
            uncertainties: [{text: "不能确认共同设备。", evidence_refs: ["search:assets"]}],
            verification_items: ["请维修值班人员到302室分别检查厨房和卫生间，并记录现场时间。"],
        }, failure_stage: null, error_code: null};
}

class Element {
    constructor(tag = "div") { this.tag = tag; this.children = []; this.handlers = {}; this._text = ""; this.hidden = false; this.disabled = false; this.open = false; this.value = ""; }
    set textContent(value) { this._text = String(value); this.children = []; }
    get textContent() { return this._text + this.children.map(child => child.textContent).join(" "); }
    set innerHTML(value) { throw Error("禁止将原文或模型输出拼接成HTML"); }
    append(...items) { this.children.push(...items); }
    replaceChildren(...items) { this._text = ""; this.children = items; }
    addEventListener(type, handler) { this.handlers[type] = handler; }
    async emit(type) { if (!this.disabled) return this.handlers[type]?.(); }
}
const flatten = element => [element, ...element.children.flatMap(flatten)];
const response = (status, body) => ({status, ok: status >= 200 && status < 300, json: async () => body});

function page({runs = [], storage = new Map(), get, post, nextId = runId, controller = true} = {}) {
    const nodes = new Map(), calls = [];
    const node = selector => { if (!nodes.has(selector)) nodes.set(selector, new Element()); return nodes.get(selector); };
    node("#report-evidence").dataset = {reportId};
    const context = vm.createContext({AbortController, setTimeout, clearTimeout,
        crypto: {randomUUID: () => nextId},
        sessionStorage: {getItem: key => storage.get(key) ?? null, setItem: (key, value) => storage.set(key, value), removeItem: key => storage.delete(key)},
        document: {querySelector: node, createElement: tag => new Element(tag)},
        async fetch(path, options) {
            calls.push({path, options});
            assert.equal(path, `/api/service-requests/${reportId}/agent-runs`);
            return options.method === "POST" ? post ? post(JSON.parse(options.body)) : response(201, sample(nextId)) : get ? get() : response(200, runs);
        },
    });
    vm.runInContext(viewSource, context);
    if (controller) vm.runInContext(controllerSource, context);
    return {node, calls, storage, context,
        render: run => context.renderAgentRun(run, reportId),
        validate: run => context.validateAgentRun(run, reportId),
        async ready() { for (let i = 0; i < 5; i++) await new Promise(resolve => setImmediate(resolve)); },
        create: () => node("#generate-agent-run").emit("click"), refresh: () => node("#refresh-agent-runs").emit("click"),
    };
}

module.exports = {Element, sample, page, flatten, response, reportId, runId, otherId, seed};

test("first use enables AI without reading manual links, plans or location APIs", async () => {
    const p = page(); await p.ready();
    assert.equal(p.node("#generate-agent-run").disabled, false);
    assert.match(p.node("#agent-run-message").textContent, /还没有核查记录/);
    await p.create();
    assert.equal(p.calls.filter(x => x.options.method === "POST").length, 1);
    assert.equal(p.calls.length, 2);
    assert.equal(p.node("#agent-run-draft").hidden, false);
    assert.equal(p.node("#agent-run-message").hidden, true);
});

test("saved snapshot renders originals, candidate judgments, uncertainty and specific next step", () => {
    const p = page({controller: false}); p.render(sample());
    const text = p.node("#agent-run-records").textContent;
    for (const phrase of ["厨房和卫生间", "只有洗手盆", "维修班王师傅", "值班员陈女士", "记录版本", "观察时间", "未进入室内", "清和家园 / 1栋 / 302室", "同一楼栋", "所属计划", "其他地点，仅作背景", "不能把这次检查", "不能直接认定未完成或虚报"]) assert.ok(text.includes(phrase), phrase);
    assert.match(p.node("#agent-run-assessments").textContent, /暂时分开核查/);
    assert.match(p.node("#agent-run-uncertainties").textContent, /不能确认共同设备/);
    assert.match(p.node("#agent-run-verification").textContent, /302室分别检查厨房和卫生间/);
    assert.match(p.node("#agent-run-scope").textContent, /尚未接入/);
    assert.match(p.node("#agent-run-scope").textContent, /人员补充范围/);
});

test("citation opens the exact saved original instead of fetching current records", async () => {
    const p = page({controller: false}); p.render(sample());
    const links = flatten(p.node("#agent-run-assessments")).filter(x => x.tag === "a");
    const record = flatten(p.node("#agent-run-records")).find(x => x.id?.endsWith("execution:E1"));
    assert.equal(record.open, false);
    await links.find(x => decodeURIComponent(x.href).endsWith("execution:E1")).emit("click");
    assert.equal(record.open, true);
    assert.equal(p.node("#agent-original-details").open, true);
    assert.equal(p.calls.length, 0);
});

test("summary keeps state and time while full metadata and unchanged facts remain available in collapsed supplements", () => {
    const run = sample(), before = JSON.stringify(run);
    const p = page({controller: false}); p.render(run);
    const summary = p.node("#agent-run-summary").textContent;
    assert.match(summary, /待审核/);
    assert.match(summary, /2026/);
    assert.equal(summary.includes(run.id), false);
    assert.equal(summary.includes(run.model_name), false);
    assert.ok(p.node("#agent-run-meta").textContent.includes(run.id));
    assert.ok(p.node("#agent-run-meta").textContent.includes(run.model_name));
    assert.match(p.node("#agent-run-meta").textContent, /依据截至/);
    assert.equal(p.node("#agent-fact-details").hidden, false);
    assert.ok(p.node("#agent-run-observations").textContent.includes(run.suggestion.observations[0].text));
    for (const name of ["agent-fact-details", "agent-original-details", "agent-runtime-details", "agent-query-details"]) assert.equal(p.node(`#${name}`).open, false);
    assert.equal(JSON.stringify(run), before);
    assert.equal(p.calls.length, 0);
});

test("citations for report, plan, execution and inspection open the enclosing saved-evidence section", async () => {
    const run = sample();
    const refs = [seed, "plan:P1", "execution:E1", "inspection:I1"];
    run.suggestion.uncertainties[0].evidence_refs = refs;
    const p = page({controller: false}); p.render(run);
    const links = flatten(p.node("#agent-run-uncertainties")).filter(x => x.tag === "a");
    for (const ref of refs) {
        p.node("#agent-original-details").open = false;
        const record = flatten(p.node("#agent-run-records")).find(x => x.id?.endsWith(ref));
        record.open = false;
        await links.find(x => decodeURIComponent(x.href).endsWith(ref)).emit("click");
        assert.equal(p.node("#agent-original-details").open, true);
        assert.equal(record.open, true);
        assert.equal(p.node("#agent-query-details").open, false);
    }
    assert.equal(p.calls.length, 0);
});

test("switching or clearing a result closes supplements and removes stale successful facts and metadata", () => {
    const p = page({controller: false}); p.render(sample());
    const folds = ["agent-fact-details", "agent-original-details", "agent-runtime-details", "agent-query-details"];
    for (const id of folds) p.node(`#${id}`).open = true;
    const failed = {...sample(otherId), status: "failed", suggestion: null};
    p.render(failed);
    assert.equal(p.node("#agent-fact-details").hidden, true);
    assert.equal(p.node("#agent-run-observations").children.length, 0);
    assert.equal(p.node("#agent-runtime-details").open, false);
    assert.equal(p.node("#agent-query-details").open, true);
    assert.match(p.node("#agent-run-summary").textContent, /核查失败/);
    assert.equal(p.node("#agent-run-meta").textContent.includes(runId), false);
    p.render(null);
    for (const id of folds) assert.equal(p.node(`#${id}`).open, false);
    assert.equal(p.node("#agent-run-result").hidden, true);
    assert.equal(p.node("#agent-run-summary").textContent, "");
    assert.equal(p.node("#agent-run-meta").textContent, "");
});

test("same-time history entries stay distinguishable without exposing full IDs and failures remain visible", async () => {
    const failed = {...sample(otherId), status: "failed", suggestion: null, error_code: "TimeoutError", failure_stage: "suggestion_request"};
    const p = page({runs: [sample(), failed]}); await p.ready();
    const options = p.node("#agent-run-history").children;
    assert.notEqual(options[0].textContent, options[1].textContent);
    assert.ok(options.every(x => !x.textContent.includes(x.value)));
    assert.deepEqual(options.map(x => x.value), [runId, otherId]);
    assert.equal(p.node("#agent-run-message").hidden, true);
    p.node("#agent-run-history").value = otherId;
    await p.node("#agent-run-history").emit("change");
    assert.equal(p.node("#agent-run-message").hidden, false);
    assert.match(p.node("#agent-run-message").textContent, /TimeoutError/);
    assert.ok(p.node("#agent-run-meta").textContent.includes(otherId));
    p.node("#agent-run-history").value = runId;
    await p.node("#agent-run-history").emit("change");
    assert.equal(p.node("#agent-run-message").hidden, true);
    assert.equal(p.calls.length, 1);
});

test("query citation opens both source details and enclosing query section", async () => {
    const p = page({controller: false}); p.render(sample());
    await flatten(p.node("#agent-run-uncertainties")).find(x => x.tag === "a").emit("click");
    assert.equal(p.node("#agent-query-details").open, true);
    const assets = flatten(p.node("#agent-run-scope")).find(x => x.id?.endsWith("search:assets"));
    assert.equal(assets.open, true);
});

test("eight ledger nodes are grouped after query statuses without changing the saved snapshot", () => {
    const run = sample();
    for (const id of ["SITE", "AREA", "BUILDING", "UNIT", "FLOOR", "ROOM-301"]) {
        run.evidence.records[`location:${id}`] = {id, version: 3, display_path: `清和家园 / ${id}`};
    }
    run.evidence.records["search:locations"] = {source: "locations", status: "found", count: 8, note: "按编号去重的地点台账。"};
    run.evidence.records["search:manual_links"] = {source: "manual_links", status: "empty", count: 0};
    run.evidence.records["search:plan_records"] = {source: "plan_records", status: "not_queried", count: null};
    const before = JSON.stringify(run);
    const p = page({controller: false}); p.render(run);
    const scope = p.node("#agent-run-scope");
    const group = scope.children.find(x => x.children[0]?.textContent === "地点依据（8项）");
    assert.ok(group);
    assert.equal(group.open, false);
    const queryCards = scope.children.filter(x => x.id?.includes("search:"));
    assert.ok(queryCards.every(x => scope.children.indexOf(x) < scope.children.indexOf(group)));
    assert.ok(queryCards[0].id.endsWith("search:related_reports"));
    for (const text of ["已找到记录", "本次范围内未找到记录", "本次尚未查询", "尚未接入", "查找时间范围", "居民观察时间"]) assert.ok(scope.textContent.includes(text), text);
    assert.equal(flatten(group).filter(x => x.id?.includes("location:")).length, 8);
    for (const [ref, row] of Object.entries(run.evidence.records).filter(([ref]) => ref.startsWith("location:"))) {
        const detail = flatten(group).find(x => x.id?.endsWith(ref));
        assert.ok(detail.textContent.includes(row.display_path));
        assert.ok(detail.textContent.includes(String(row.version)));
    }
    assert.ok(flatten(group).some(x => x.id?.endsWith("search:locations")));
    assert.equal(scope.children.some(x => x.id?.endsWith("search:locations")), false);
    assert.equal(scope.children.at(-1).children[0].textContent, "核查说明");
    assert.equal(JSON.stringify(run), before);
    assert.equal(p.calls.length, 0);
});

test("location and ledger-query citations reveal every enclosing fold and use readable source names", async () => {
    const run = sample();
    run.evidence.records["search:locations"] = {source: "locations", status: "found", count: 2};
    for (const source of ["work_orders", "notices"]) run.evidence.records[`search:${source}`] = {source, status: "unsupported", count: null};
    const refs = ["location:ROOM", "search:locations", "search:assets", "search:work_orders", "search:notices", "context:investigation"];
    run.suggestion.uncertainties[0].evidence_refs = refs;
    const p = page({controller: false}); p.render(run);
    const scope = p.node("#agent-run-scope");
    const group = scope.children.find(x => x.children[0]?.textContent === "地点依据（2项）");
    const links = flatten(p.node("#agent-run-uncertainties")).filter(x => x.tag === "a");
    for (const ref of ["location:ROOM", "search:locations"]) {
        group.open = false;
        p.node("#agent-query-details").open = false;
        const record = flatten(group).find(x => x.id?.endsWith(ref));
        record.open = false;
        await links.find(x => decodeURIComponent(x.href).endsWith(ref)).emit("click");
        assert.equal(record.open, true);
        assert.equal(group.open, true);
        assert.equal(p.node("#agent-query-details").open, true);
    }
    for (const label of ["查询范围：设备及管网资料", "查询范围：维修工单", "查询范围：通知", "核查说明"]) assert.ok(links.some(x => x.textContent === label), label);
    assert.ok(links.every(x => !/assets|work_orders|notices|investigation/.test(x.textContent)));
    assert.equal(p.calls.length, 0);
});

test("ledger read failure stays visible before folded ledger details", () => {
    const run = sample(); run.status = "failed"; run.suggestion = null;
    run.evidence.records["search:locations"] = {source: "locations", status: "failed", error_code: "OperationalError", note: "读取地点时失败。"};
    const p = page({controller: false}); p.render(run);
    const scope = p.node("#agent-run-scope");
    const failed = scope.children[0];
    assert.ok(failed.id.endsWith("search:locations"));
    assert.equal(failed.open, true);
    assert.match(failed.textContent, /读取失败/);
    assert.match(failed.textContent, /没有把失败结果当作查无记录/);
    assert.equal(p.node("#agent-query-details").open, true);
    assert.equal(scope.children.find(x => x.children[0]?.textContent === "地点依据（2项）").open, false);
});

test("history switching rebuilds the location fold and keeps snapshots without ledger nodes readable", () => {
    const p = page({controller: false}); p.render(sample());
    const group = p.node("#agent-run-scope").children.find(x => x.children[0]?.textContent === "地点依据（2项）");
    group.open = true;
    p.render(sample(otherId));
    const fresh = p.node("#agent-run-scope").children.find(x => x.children[0]?.textContent === "地点依据（2项）");
    assert.notEqual(fresh, group);
    assert.equal(fresh.open, false);
    assert.equal(flatten(fresh).some(x => x.id?.includes(runId)), false);
    const run = sample();
    delete run.evidence.records["location:ROOM"];
    delete run.evidence.records["location:CORRIDOR"];
    run.evidence.records["search:locations"] = {source: "locations", status: "empty", count: 0};
    p.render(run);
    const scope = p.node("#agent-run-scope");
    assert.equal(scope.textContent.includes("地点依据（"), false);
    assert.match(scope.textContent, /地点台账：本次范围内未找到记录（0项）/);
    assert.equal(flatten(scope).some(x => x.id?.includes(otherId)), false);
    assert.equal(p.calls.length, 0);
});

test("all untrusted original and model text is rendered as plain text", () => {
    const run = sample();
    run.evidence.records[seed].description = '<img src=x onerror="alert(1)">';
    run.suggestion.observations[0].text = '<script>alert(1)</script>';
    const p = page({controller: false}); p.render(run);
    assert.match(p.node("#agent-run-records").textContent, /<img/);
    assert.match(p.node("#agent-run-observations").textContent, /<script>/);
    assert.equal(flatten(p.node("#agent-run-result")).some(x => ["script", "img"].includes(x.tag)), false);
});

test("no plan and no other report remain analyzable with empty and unsupported separated", () => {
    const run = sample();
    run.evidence.records = {[seed]: run.evidence.records[seed], "context:investigation": {candidate_refs: []},
        "search:plans": {source: "plans", status: "empty", count: 0},
        "search:plan_records": {source: "plan_records", status: "not_queried", count: null},
        "search:assets": run.evidence.records["search:assets"]};
    run.suggestion.candidate_assessments = [];
    const p = page({controller: false}); p.render(run);
    assert.match(p.node("#agent-run-assessments").textContent, /不代表没有开展工作/);
    for (const phrase of ["本次范围内未找到记录", "本次尚未查询", "尚未接入"]) assert.ok(p.node("#agent-run-scope").textContent.includes(phrase));
});

test("unknown observation and unknown point are not rewritten using receipt time", () => {
    const run = sample(); run.evidence.records[seed].observed_at = null; run.evidence.records[seed].location_id = null;
    const p = page({controller: false}); p.render(run);
    assert.match(p.node("#agent-run-records").textContent, /未注明，待核实/);
    run.evidence.records["execution:E1"].report_comparison.relation = "unknown";
    p.render(run); assert.match(p.node("#agent-run-records").textContent, /不能判断本报事/);
});

test("legacy suggestions retain content and explicitly lack new scope and judgments", () => {
    const run = sample(); run.evidence.source_rule_version = "report-evidence-v1";
    run.evidence.records = {[seed]: {description: "旧版原文"}};
    run.suggestion = {observations: [{text: "旧版建议", evidence_refs: [seed]}], verification_items: ["旧版事项"]};
    const p = page({controller: false}); p.render(run);
    assert.equal(p.node("#agent-run-legacy-note").hidden, false);
    assert.match(p.node("#agent-run-observations").textContent, /旧版建议/);
    assert.match(p.node("#agent-run-scope").textContent, /不能据此认定其他来源已查过/);
});

test("read failure renders trace and never a normal partial suggestion", () => {
    const run = sample(); run.status = "failed"; run.suggestion = null;
    run.evidence.source_rule_version = "automatic-evidence-failure-v1";
    run.evidence.records = {"search:related_reports": {source: "related_reports", status: "failed", error_code: "OperationalError"}};
    const p = page({controller: false}); p.render(run);
    assert.equal(p.node("#agent-run-draft").hidden, true);
    assert.match(p.node("#agent-run-records").textContent, /取证未完成/);
    assert.match(p.node("#agent-run-scope").textContent, /读取失败/);
    assert.equal(p.node("#agent-query-details").open, true);
});

test("failed model retains readable evidence but hides any previous successful suggestion", () => {
    const p = page({controller: false}); p.render(sample());
    const run = sample(); run.status = "failed"; run.suggestion = null;
    p.render(run);
    assert.equal(p.node("#agent-run-draft").hidden, true);
    assert.equal(p.node("#agent-run-observations").children.length, 0);
    assert.match(p.node("#agent-run-records").textContent, /厨房和卫生间/);
});

test("wrong report, wrong references and omitted candidate judgments are rejected", () => {
    const p = page({controller: false});
    for (const mutate of [r => r.report_id = "other", r => r.evidence.report_ref = "report:other", r => r.suggestion.uncertainties[0].evidence_refs = ["unknown"], r => r.suggestion.candidate_assessments.pop()]) {
        const run = sample(); mutate(run); assert.throws(() => p.validate(run));
    }
});

test("GET on refresh restores saved result without POST or re-analysis", async () => {
    const p = page({runs: [sample()]}); await p.ready();
    await p.refresh();
    assert.equal(p.calls.length, 2);
    assert.ok(p.calls.every(x => !x.options.method && x.options.cache === "no-store"));
    assert.match(p.node("#agent-run-observations").textContent, /室内无水/);
});

test("history switch uses saved snapshots and clears stale fields", async () => {
    const older = sample(otherId); older.suggestion.verification_items = ["旧结果的核实事项"];
    const p = page({runs: [sample(), older]}); await p.ready();
    p.node("#agent-run-history").value = otherId; await p.node("#agent-run-history").emit("change");
    assert.match(p.node("#agent-run-verification").textContent, /旧结果/);
    assert.equal(p.calls.length, 1);
    await p.refresh();
    assert.equal(p.node("#agent-run-history").value, otherId);
});

test("network uncertainty keeps original request id across retry", async () => {
    let first = true;
    const p = page({post: body => { if (first) { first = false; throw Error("网络中断"); } return response(200, sample(body.request_id)); }});
    await p.ready(); await p.create();
    assert.match(p.node("#generate-agent-run").textContent, /重试本次核查/);
    await p.create();
    const posts = p.calls.filter(x => x.options.method === "POST").map(x => JSON.parse(x.options.body));
    assert.equal(posts.length, 2); assert.equal(posts[0].request_id, posts[1].request_id);
    assert.equal(p.storage.size, 0);
});

test("running replay preserves pending id and blocks a fresh run until terminal read", async () => {
    let terminal = false;
    const storage = new Map([[`property-v2-agent-run:${reportId}`, runId]]);
    const p = page({storage, get: () => response(200, terminal ? [sample()] : [{...sample(), status: "running", evidence: null, suggestion: null}])});
    await p.ready();
    assert.equal(p.node("#generate-agent-run").disabled, true);
    assert.equal(storage.size, 1);
    terminal = true; await p.refresh();
    assert.equal(p.node("#generate-agent-run").disabled, false);
    assert.equal(storage.size, 0);
});

test("running POST response keeps retry id and blocks duplicate click", async () => {
    const p = page({post: () => response(200, {...sample(), status: "running", evidence: null, suggestion: null})});
    await p.ready(); await p.create(); await p.create();
    assert.equal(p.calls.filter(x => x.options.method === "POST").length, 1);
    assert.equal(p.storage.size, 1);
    assert.equal(p.node("#generate-agent-run").disabled, true);
});

test("read-list failure does not create a manual evidence prerequisite for AI", async () => {
    const p = page({get: () => response(503, {})}); await p.ready();
    assert.equal(p.node("#generate-agent-run").disabled, false);
    await p.create();
    assert.equal(p.node("#agent-run-draft").hidden, false);
});

test("failed record is distinct from never-run state and permits a new check", async () => {
    const failed = {...sample(), status: "failed", suggestion: null, error_code: "TimeoutError", failure_stage: "suggestion_request"};
    const p = page({runs: [failed], nextId: otherId}); await p.ready();
    assert.match(p.node("#agent-run-message").textContent, /请求AI分析/);
    assert.doesNotMatch(p.node("#agent-run-message").textContent, /还没有核查记录/);
    await p.create();
    assert.equal(JSON.parse(p.calls[1].options.body).request_id, otherId);
});

test("missing report disables generation and directs user back to report list", async () => {
    const p = page({get: () => response(404, {})}); await p.ready();
    assert.equal(p.node("#generate-agent-run").disabled, true);
    assert.match(p.node("#agent-run-message").textContent, /返回报事列表/);
});

test("invalid or cross-report list cannot replace a previously displayed valid result", async () => {
    let bad = false;
    const p = page({get: () => response(200, bad ? [{...sample(), report_id: "other"}] : [sample()])});
    await p.ready(); bad = true; await p.refresh();
    assert.match(p.node("#agent-run-message").textContent, /暂时无法读取/);
    assert.match(p.node("#agent-run-observations").textContent, /室内无水/);
});
