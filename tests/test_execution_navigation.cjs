const test = require("node:test");
const assert = require("node:assert/strict");
const vm = require("node:vm");
const {readFileSync} = require("node:fs");
const {join} = require("node:path");
const source = readFileSync(join(__dirname, "..", "static", "execution-list.js"), "utf8").replace(/^import .*;\s*/m, "");
const planId = "23832b15-693f-4ff8-a563-4a60fa7d42be";
const reportId = "565eddc7-b40e-47b1-805f-cd89f8128380";
class Element {
    constructor() { this.children = []; this.handlers = {}; this.hidden = true; this.textContent = ""; }
    replaceChildren(...items) { this.children = items; }
    append(...items) { this.children.push(...items); }
    addEventListener(kind, fn) { this.handlers[kind] = fn; }
    emit(kind) { return this.handlers[kind]?.(); }
    focus() {}
}
async function page(report) {
    const nodes = new Map(), calls = [];
    const node = key => {
        if (!nodes.has(key)) nodes.set(key, new Element());
        return nodes.get(key);
    };
    const search = "?plan_id=" + planId + (report === undefined ? "" : "&report_id=" + encodeURIComponent(report));
    await vm.runInNewContext(source, {
        window: {location: {search}}, URLSearchParams, AbortController, setTimeout, clearTimeout,
        document: {querySelector: node, createElement: () => new Element()},
        fetch: async path => {
            calls.push(path);
            const body = path.startsWith("/api/plans/") ? {
                id: planId, title: "保洁安排", owner_name: "林悦", location_ids: ["A"],
                planned_start_at: "2026-11-11T01:00:00Z", planned_end_at: "2026-11-11T02:00:00Z",
                requirements: "清洁入口", version: 1,
            } : path === "/api/locations" ? [{id: "A", display_path: "A区大堂"}] : [];
            return {ok: true, status: 200, json: async () => body};
        },
    });
    return {node, calls};
}
test("execution view carries report context only in internal return links", async () => {
    const p = await page(reportId);
    assert.equal(p.node("#execution-return-report").href, "/report-evidence?report_id=" + reportId);
    assert.equal(p.node("#execution-return-report").hidden, false);
    assert.equal(p.node("#execution-inspections-link").href, "/inspections?plan_id=" + planId + "&report_id=" + reportId);
    assert.ok(p.calls.every(path => !path.includes("report_id=")));
});
test("missing or invalid return context does not invent a report or external destination", async () => {
    for (const report of [undefined, "https://outside.example/", "bad-id"]) {
        const p = await page(report);
        assert.equal(p.node("#execution-return-report").hidden, true);
        assert.equal(p.node("#execution-evidence-link").href, "/plan-evidence?plan_id=" + planId);
    }
});
test("view saved records refreshes the current plan from the first page", async () => {
    const p = await page(reportId);
    const before = p.calls.filter(path => path.startsWith("/api/executions?")).length;
    await p.node("#execution-view-saved").emit("click");
    const reads = p.calls.filter(path => path.startsWith("/api/executions?"));
    assert.equal(reads.length, before + 1);
    const query = new URLSearchParams(reads.at(-1).split("?")[1]);
    assert.equal(query.get("plan_id"), planId);
    assert.equal(query.get("offset"), "0");
});
