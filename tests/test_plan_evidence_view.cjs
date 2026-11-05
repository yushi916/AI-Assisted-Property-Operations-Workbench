const test = require("node:test");
const assert = require("node:assert/strict");
const vm = require("node:vm");
const {readFileSync} = require("node:fs");
const {join} = require("node:path");
const source = readFileSync(join(__dirname, "..", "static", "plan-evidence.js"), "utf8");
const planId = "23832b15-693f-4ff8-a563-4a60fa7d42be";
const ref = (kind, id) => ({kind, id, version: 1});

function sample() {
    return {
        plan_id: planId, plan_version: 1, rule_version: "plan-evidence-v1",
        checked_at: "2026-11-05T10:00:00Z", due: true,
        coverage: [
            {location_id: "A", execution_ids: ["E1", "E2"], inspection_ids: ["I1"]},
            {location_id: "B", execution_ids: [], inspection_ids: []},
        ],
        findings: [
            {location_id: "B", message: "未查到明确执行记录", evidence: [ref("plan", planId)]},
            {location_id: null, message: "观察时间待核实", evidence: [ref("inspection", "I2")]},
        ],
        comparisons: [
            {location_id: "A", execution_id: "E1", inspection_id: "I1", finding: "issue_observed", relation: "after"},
            {location_id: "A", execution_id: "E2", inspection_id: "I1", finding: "issue_observed", relation: "unknown"},
        ],
        evidence: [ref("plan", planId), ref("execution", "E1"), ref("execution", "E2"),
            ref("inspection", "I1"), ref("inspection", "I2")],
    };
}

class Element {
    constructor() { this.children = []; this.handlers = {}; this._text = ""; }
    set textContent(value) { this._text = String(value); this.children = []; }
    get textContent() { return this._text + this.children.map(item => item.textContent).join(" "); }
    set innerHTML(value) { throw Error("不得将证据拼接成HTML"); }
    replaceChildren(...items) { this._text = ""; this.children = items; }
    append(...items) { this.children.push(...items); }
    addEventListener(type, handler) { this.handlers[type] = handler; }
    async emit(type) { return this.handlers[type]?.(); }
}

function response(status, body) {
    return {status, ok: status >= 200 && status < 300, json: async () => body};
}

function page({data = sample(), evidence, catalog} = {}) {
    const nodes = new Map();
    const node = selector => {
        if (!nodes.has(selector)) nodes.set(selector, new Element());
        return nodes.get(selector);
    };
    node("#plan-evidence").dataset = {planId};
    node("#evidence-result").hidden = true;
    const calls = [];
    const ready = vm.runInNewContext(source, {
        AbortController, setTimeout, clearTimeout,
        document: {querySelector: node, createElement: () => new Element()},
        async fetch(path, options) {
            calls.push({path, options});
            if (path === "/api/locations") {
                return catalog ? catalog() : response(200, [
                    {id: "A", display_path: "合成小区 / A区 / A区大堂"},
                    {id: "B", display_path: "合成小区 / B区 / B区大堂"},
                ]);
            }
            assert.equal(path, `/api/plans/${planId}/evidence-check`);
            return evidence ? evidence() : response(200, data);
        },
    });
    return {node, calls, ready, refresh: () => node("#refresh-evidence").emit("click")};
}

test("shows coverage, Chinese comparisons and all evidence including unknown scope", async () => {
    const p = page(); await p.ready;
    assert.equal(p.node("#evidence-result").hidden, false);
    const coverage = p.node("#evidence-coverage").textContent;
    assert.match(coverage, /A区大堂/); assert.match(coverage, /2条：E1、E2/);
    assert.match(coverage, /0条明确记录/); assert.doesNotMatch(coverage, /I2/);
    assert.match(p.node("#evidence-references").textContent, /巡查记录 I2/);
    assert.equal(p.node("#evidence-references").children.length, 5);
    assert.match(p.node("#evidence-summary").textContent, /已到达计划截止时间/);
    assert.match(p.node("#evidence-comparisons").textContent, /晚于声称完成时间/);
    assert.match(p.node("#evidence-comparisons").textContent, /时间信息不足/);
    assert.ok(p.calls.every(call => call.options.cache === "no-store" && !call.options.method));
});

test("failed refresh hides previous result and a later refresh recovers", async () => {
    let status = 200;
    const p = page({evidence: () => response(status, sample())}); await p.ready;
    status = 503; await p.refresh();
    assert.equal(p.node("#evidence-result").hidden, true);
    assert.match(p.node("#evidence-message").textContent, /未完成/);
    assert.equal(p.node("#refresh-evidence").disabled, false);
    status = 200; await p.refresh();
    assert.equal(p.node("#evidence-result").hidden, false);
});

test("network failure is not presented as empty evidence", async () => {
    const p = page({evidence: () => { throw new TypeError("offline"); }}); await p.ready;
    assert.equal(p.node("#evidence-result").hidden, true);
    assert.match(p.node("#evidence-message").textContent, /未完成/);
    assert.equal(p.node("#refresh-evidence").disabled, false);
});

test("timeout gives retry guidance", async () => {
    const p = page({evidence: () => { const error = Error("timeout"); error.name = "AbortError"; throw error; }});
    await p.ready;
    assert.match(p.node("#evidence-message").textContent, /超时/);
    assert.equal(p.node("#evidence-result").hidden, true);
});

test("missing plan is identified", async () => {
    const p = page({evidence: () => response(404)}); await p.ready;
    assert.match(p.node("#evidence-message").textContent, /计划不存在/);
    assert.equal(p.node("#evidence-result").hidden, true);
});

test("wrong plan, invalid arrays and missing plan reference are rejected", async () => {
    for (const change of [data => { data.plan_id = "other"; },
        data => { data.coverage = null; }, data => { data.evidence = []; }]) {
        const data = sample(); change(data);
        const p = page({data}); await p.ready;
        assert.equal(p.node("#evidence-result").hidden, true);
        assert.match(p.node("#evidence-message").textContent, /未完成/);
    }
});

test("catalog failure preserves result with original location IDs", async () => {
    const p = page({catalog: () => { throw Error("catalog offline"); }}); await p.ready;
    assert.equal(p.node("#evidence-result").hidden, false);
    assert.match(p.node("#evidence-coverage").textContent, /A（地点名称暂无法读取）/);
    assert.match(p.node("#evidence-message").textContent, /已显示原地点编号/);
});

test("missing catalog entry preserves the evidence point", async () => {
    const p = page({catalog: () => response(200, [])}); await p.ready;
    assert.match(p.node("#evidence-coverage").textContent, /B（当前台账未查到）/);
    assert.equal(p.node("#evidence-result").hidden, false);
});

test("evidence messages are rendered as text", async () => {
    const data = sample(); data.findings[0].message = '<img src=x onerror="alert(1)">';
    const p = page({data}); await p.ready;
    assert.equal(p.node("#evidence-result").hidden, false);
    assert.match(p.node("#evidence-findings").textContent, /<img src=x/);
});

test("refresh while busy cannot start another query", async () => {
    let release;
    const p = page({evidence: () => new Promise(resolve => { release = resolve; })});
    assert.equal(p.node("#refresh-evidence").disabled, true);
    await p.refresh(); assert.equal(p.calls.length, 1);
    release(response(200, sample())); await p.ready;
    assert.equal(p.node("#refresh-evidence").disabled, false);
});

test("all time relations have distinct Chinese explanations", async () => {
    const data = sample();
    data.comparisons = ["before", "same_time", "after", "unknown"].map(relation => ({
        ...data.comparisons[0], relation,
    }));
    const p = page({data}); await p.ready;
    const text = p.node("#evidence-comparisons").textContent;
    for (const phrase of ["早于", "两个时间相同", "晚于", "时间信息不足"]) assert.match(text, new RegExp(phrase));
});

test("empty findings and comparisons do not declare fulfillment", async () => {
    const data = sample(); data.due = false; data.findings = []; data.comparisons = [];
    const p = page({data}); await p.ready;
    assert.match(p.node("#evidence-summary").textContent, /尚未到达/);
    assert.match(p.node("#evidence-findings").textContent, /仍需人员复核/);
    assert.match(p.node("#evidence-comparisons").textContent, /暂无可进行时间比较/);
});