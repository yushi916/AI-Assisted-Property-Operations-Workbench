const test = require("node:test");
const assert = require("node:assert/strict");
const vm = require("node:vm");
const {readFileSync} = require("node:fs");
const {join} = require("node:path");
const source = ["location-picker.js", "reports.js"].map(name =>
    readFileSync(join(__dirname, "..", "static", name), "utf8")
).join("\n");
const key = "property-v2-intake-v1";

class Element {
    constructor() {
        this.children = []; this.handlers = {}; this.value = "";
        this.disabled = false; this.hidden = false; this._text = "";
    }
    set textContent(value) { this._text = String(value); this.children = []; }
    get textContent() { return this._text + this.children.map(x => x.textContent).join(" "); }
    set innerHTML(value) { throw Error("地点名称必须按文本显示"); }
    get options() { return this.children; }
    append(...items) { for (const item of items) {item.parentElement = this; this.children.push(item);} }
    replaceChildren(...items) { this.children = []; this._text = ""; this.append(...items); }
    addEventListener(type, handler) { this.handlers[type] = handler; }
    emit(type) { return this.handlers[type]?.({preventDefault() {}}); }
    setAttribute() {}
    removeAttribute(name) { delete this[name]; }
    focus() {}
}

function catalog() {
    const rows = [
        ["C", "合成小区", "community", null], ["A", "A区", "area", "C"],
        ["B1", "1栋", "building", "A"], ["B2", "2栋", "building", "A"],
        ["U1", "1单元", "unit", "B1"], ["U2", "1单元", "unit", "B2"],
        ["F1", "3层", "floor", "U1"], ["F2", "3层", "floor", "U2"],
        ["R1", "302室", "room", "F1"], ["R2", "302室", "room", "F2"],
        ["P", "公共走廊", "public_space", "F1"],
    ];
    const paths = new Map();
    return rows.map(([id, name, kind, parent_id]) => {
        const display_path = (paths.has(parent_id) ? paths.get(parent_id) + " / " : "") + name;
        paths.set(id, display_path);
        return {id, name, kind, parent_id, display_path};
    });
}
const response = (status, body) => ({status, ok: status >= 200 && status < 300, json: async () => body});

async function page({storage = new Map(), post, locations = catalog(), failCatalog = false} = {}) {
    const nodes = new Map();
    const node = name => {
        if (!nodes.has(name)) nodes.set(name, new Element());
        return nodes.get(name);
    };
    const fields = Object.fromEntries([
        "service_category", "description", "risk_status", "observed_at", "location_id",
    ].map(name => [name, new Element()]));
    new Element().append(fields.location_id);
    fields.location_id.append(Object.assign(new Element(), {value: ""}));
    const form = node("#report-form");
    form.elements = {namedItem: name => fields[name]};
    form.reportValidity = () => true;
    form.reset = () => Object.values(fields).forEach(field => {field.value = "";});
    node("#location-picker").hidden = true;
    const posts = [];
    await vm.runInNewContext(source, {
        AbortController, setTimeout, clearTimeout,
        document: {querySelector: node, createElement: () => new Element()},
        crypto: {randomUUID: () => "11111111-1111-4111-8111-111111111111"},
        sessionStorage: {
            getItem: k => storage.get(k) ?? null,
            setItem: (k, value) => storage.set(k, value),
            removeItem: k => storage.delete(k),
        },
        async fetch(url, options) {
            if (url === "/api/locations") {
                assert.equal(options.cache, "no-store");
                if (failCatalog) throw Error("offline");
                return response(200, locations);
            }
            assert.equal(url, "/api/service-requests");
            const payload = JSON.parse(options.body);
            posts.push(payload);
            return post ? post(payload, posts.length) : response(201, {
                ...payload, id: "saved-report", received_at: "2026-11-11T01:00:00Z",
            });
        },
    });
    const root = node("#location-picker");
    const choose = (...ids) => {
        ids.forEach((id, index) => {
            const select = root.children[0].children[index].children[0];
            assert.ok(select.options.some(option => option.value === id), id);
            select.value = id; select.emit("change");
        });
    };
    Object.assign(fields.service_category, {value: "water_supply"});
    Object.assign(fields.description, {value: "  厨房没有水  "});
    Object.assign(fields.risk_status, {value: "unknown"});
    return {fields, storage, posts, root, node, choose,
        submit: () => form.emit("submit"),
        confirm: () => root.children[2].emit("click"),
        unknown: () => root.children[3].emit("click"),
    };
}

test("same room names send the confirmed ID from the chosen building", async () => {
    const p = await page();
    p.choose("C", "A", "B2", "U2", "F2", "R2");
    assert.equal(p.fields.location_id.value, "");
    p.confirm();
    assert.match(p.root.children[4].textContent, /2栋 \/ 1单元 \/ 3层 \/ 302室/);
    await p.submit();
    assert.equal(p.posts[0].location_id, "R2");
    assert.equal(p.posts[0].description, "  厨房没有水  ");
});

test("the saved-report link uses the receipt ID, survives reload, and clears for new intake", async () => {
    const storage = new Map();
    const p = await page({storage});
    assert.equal(p.node("#view-saved-report").hidden, true);
    await p.submit();
    assert.equal(p.node("#view-saved-report").href, "/report-evidence?report_id=saved-report");
    assert.equal(p.node("#view-saved-report").hidden, false);
    const restored = await page({storage});
    assert.equal(restored.posts.length, 0);
    assert.equal(restored.node("#view-saved-report").href, p.node("#view-saved-report").href);
    restored.node("#new-button").emit("click");
    assert.equal(restored.node("#view-saved-report").hidden, true);
    assert.equal(restored.node("#view-saved-report").href, undefined);
});

test("pending and rejected submissions never expose a saved-report navigation link", async () => {
    for (const post of [() => { throw Error("lost response"); }, () => response(422, {detail: "invalid"})]) {
        const p = await page({post});
        await p.submit();
        assert.equal(p.node("#view-saved-report").hidden, true);
        assert.equal(p.node("#view-saved-report").href, undefined);
    }
});

test("report list links each row directly to its own evidence without a new model or detail request", async () => {
    const nodes = new Map();
    const node = name => { if (!nodes.has(name)) nodes.set(name, new Element()); return nodes.get(name); };
    const calls = [];
    const ids = ["09cece20-85a5-460b-a916-eba61d2809a7", "758b541a-f8a0-4f8a-ad06-91a5d12e832d"];
    await vm.runInNewContext(readFileSync(join(__dirname, "..", "static", "report-list.js"), "utf8"), {
        AbortController, setTimeout, clearTimeout,
        document: {querySelector: node, createElement: () => new Element()},
        async fetch(url) {
            calls.push(url);
            assert.equal(url, "/api/service-requests?limit=11&offset=0");
            return response(200, ids.map(id => ({id, description: "厨房冷水异常", service_category: "water_supply",
                                                risk_status: "unknown", received_at: "2026-11-16T01:00:00Z"})));
        },
    });
    assert.equal(calls.length, 1);
    const links = node("#report-rows").children.map(row => row.children[4].children[0].children[0]);
    assert.deepEqual(links.map(link => link.href), ids.map(id => "/report-evidence?report_id=" + id));
    assert.equal(node("#report-rows").children[0].children[2].textContent, "厨房冷水异常");
});

test("navigation without confirmation blocks submission", async () => {
    const p = await page(); p.choose("C", "A", "B1");
    await p.submit();
    assert.equal(p.posts.length, 0);
    assert.match(p.node("#message").textContent, /尚未确认/);
    p.unknown(); await p.submit();
    assert.equal(p.posts[0].location_id, null);
});

test("changing building clears the candidate's unit floor and room", async () => {
    const p = await page();
    p.choose("C", "A", "B1", "U1", "F1", "R1"); p.confirm();
    p.choose("C", "A", "B2");
    assert.equal(p.root.children[0].children.length, 4);
    assert.equal(p.fields.location_id.value, "R1");
    await p.submit(); assert.equal(p.posts.length, 0);
    p.choose("C", "A", "B2", "U2", "F2", "R2"); p.confirm();
    await p.submit(); assert.equal(p.posts[0].location_id, "R2");
});

test("a corridor or explicitly confirmed parent remains one selected point", async () => {
    for (const [trail, expected] of [
        [["C", "A", "B1", "U1", "F1", "P"], "P"],
        [["C", "A", "B1"], "B1"],
    ]) {
        const p = await page(); p.choose(...trail); p.confirm(); await p.submit();
        assert.equal(p.posts[0].location_id, expected);
        assert.ok(!Object.hasOwn(p.posts[0], "location_ids"));
    }
});

test("pending reload with catalog failure retries the exact original payload", async () => {
    const storage = new Map();
    const p = await page({storage, post: () => {throw Error("lost receipt");}});
    p.choose("C", "A", "B1", "U1", "F1", "R1"); p.confirm(); await p.submit();
    const first = p.posts[0];
    const restored = await page({storage, failCatalog: true});
    await restored.submit();
    assert.deepEqual(restored.posts[0], first);
});

test("pending state prevents picker controls from changing the saved location", async () => {
    const p = await page({post: () => {throw Error("offline");}});
    p.choose("C", "A", "B1", "U1", "F1", "R1"); p.confirm(); await p.submit();
    p.unknown();
    assert.equal(p.fields.location_id.value, "R1");
    const rootSelect = p.root.children[0].children[0].children[0];
    rootSelect.value = ""; rootSelect.emit("change"); p.confirm();
    assert.equal(p.fields.location_id.value, "R1");
    await p.submit(); assert.deepEqual(p.posts[1], p.posts[0]);
});

test("422 allows a corrected location with the same request ID", async () => {
    const p = await page({post: (payload, n) => n === 1 ? response(422, {detail: "修正位置"})
        : response(201, {...payload, id: "saved", received_at: "2026-11-11T01:00:00Z"})});
    p.choose("C", "A", "B1", "U1", "F1", "R1"); p.confirm(); await p.submit();
    p.choose("C", "A", "B2", "U2", "F2", "R2"); p.confirm(); await p.submit();
    assert.equal(p.posts[0].request_id, p.posts[1].request_id);
    assert.equal(p.posts[1].location_id, "R2");
});

test("a new report resets both the confirmed point and the navigation", async () => {
    const p = await page();
    p.choose("C", "A", "B1", "U1", "F1", "P"); p.confirm(); await p.submit();
    p.node("#new-button").emit("click");
    assert.equal(p.fields.location_id.value, "");
    assert.equal(p.root.children[0].children.length, 1);
    assert.match(p.root.children[4].textContent, /地点待核实/);
});

test("missing saved point is preserved for pending retry", async () => {
    const payload = {request_id: "original-id", source: "web", service_category: "water_supply",
        risk_status: "unknown", description: " 原始内容 ", location_id: "REMOVED", observed_at: null};
    const storage = new Map([[key, JSON.stringify({phase: "pending", payload})]]);
    const p = await page({storage});
    assert.match(p.root.children[4].textContent, /REMOVED.*台账暂未查到/);
    await p.submit(); assert.deepEqual(p.posts[0], payload);
});

test("invalid hierarchy falls back to the original selector; labels stay text", async () => {
    const bad = catalog(); bad[0].parent_id = "MISSING";
    const p = await page({locations: bad});
    assert.equal(p.root.hidden, true);
    assert.equal(p.fields.location_id.parentElement.hidden, false);
    const safe = catalog(); safe[0].name = "<img src=x onerror=alert(1)>";
    const q = await page({locations: safe});
    assert.match(q.root.children[0].textContent, /<img src=x/);
});
