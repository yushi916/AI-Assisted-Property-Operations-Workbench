function createLocationPicker(root, canonical, isLocked) {
    let byId = new Map();
    let children = new Map();
    let trail = [];
    let ready = false;

    const levels = document.createElement("div");
    const candidate = document.createElement("p");
    const selected = document.createElement("p");
    const use = document.createElement("button");
    const unknown = document.createElement("button");

    use.type = unknown.type = "button";
    use.textContent = "确认使用此地点";
    unknown.textContent = "地点待核实";
    selected.setAttribute("aria-live", "polite");
    root.replaceChildren(levels, candidate, use, unknown, selected);

    const kinds = {
        community: "小区",
        area: "区域",
        building: "楼栋",
        unit: "单元",
        floor: "楼层",
        room: "房间",
        public_space: "公共点位",
        unknown: "未分类",
    };

    function option(value, label) {
        const node = document.createElement("option");
        node.value = value;
        node.textContent = label;
        return node;
    }

    function render() {
        if (!ready) return;

        levels.replaceChildren();
        let parent = null;

        for (let depth = 0; ; depth++) {
            const items = children.get(parent) ?? [];
            if (!items.length) break;

            const label = document.createElement("label");
            label.textContent = depth === 0
                ? "从小区或未分类地点开始"
                : "继续选择下一级";

            const select = document.createElement("select");
            select.append(option(
                "", "请选择（可以停在已确认的层级）",
            ));

            for (const item of items) {
                select.append(option(
                    item.id,
                    item.name + "（" + kinds[item.kind] + "）",
                ));
            }

            select.value = trail[depth] ?? "";

            select.addEventListener("change", () => {
                if (isLocked()) return;
                trail = trail.slice(0, depth);
                if (select.value) trail.push(select.value);
                render();
            });

            label.append(select);
            levels.append(label);

            if (!trail[depth]) break;
            parent = trail[depth];
        }

        const target = byId.get(trail.at(-1));
        candidate.textContent = target
            ? "正在查找：" + target.display_path
                + "。点击确认后才用于提交。"
            : "请逐级查找地点，或保留地点待核实。";

        use.disabled = !target;

        selected.textContent = "本次提交地点：" + (
            byId.get(canonical.value)?.display_path
            ?? (
                canonical.value
                    ? canonical.value + "（台账暂未查到）"
                    : "地点待核实"
            )
        );
    }

    function sync() {
        if (!ready) return;

        trail = [];
        let item = byId.get(canonical.value);

        while (item) {
            trail.unshift(item.id);
            item = byId.get(item.parent_id);
        }

        render();
    }

    use.addEventListener("click", () => {
        if (isLocked() || !trail.length) return;
        canonical.value = trail.at(-1);
        sync();
    });

    unknown.addEventListener("click", () => {
        if (isLocked()) return;
        canonical.value = "";
        sync();
    });

    canonical.addEventListener("change", sync);

    return {
        sync,

        hasDraft: () => (
            ready && (trail.at(-1) ?? "") !== canonical.value
        ),

        setLocations(locations) {
            const next = new Map();

            for (const item of locations) {
                if (
                    !item
                    || typeof item.id !== "string"
                    || !item.id
                    || typeof item.name !== "string"
                    || typeof item.display_path !== "string"
                    || !Object.hasOwn(kinds, item.kind)
                    || (
                        item.parent_id !== null
                        && typeof item.parent_id !== "string"
                    )
                    || next.has(item.id)
                ) {
                    throw Error("地点层级格式异常");
                }

                next.set(item.id, item);
            }

            const groups = new Map();

            for (const item of locations) {
                const seen = new Set();
                let current = item;

                while (current) {
                    if (seen.has(current.id)) {
                        throw Error("地点层级循环");
                    }
                    seen.add(current.id);

                    if (current.parent_id === null) break;

                    current = next.get(current.parent_id);
                    if (!current) throw Error("父地点缺失");
                }

                if (!groups.has(item.parent_id)) {
                    groups.set(item.parent_id, []);
                }
                groups.get(item.parent_id).push(item);
            }

            for (const items of groups.values()) {
                items.sort((a, b) => (
                    a.name.localeCompare(b.name, "zh-CN")
                    || a.id.localeCompare(b.id)
                ));
            }

            byId = next;
            children = groups;
            ready = true;
            sync();

            root.hidden = false;
            canonical.parentElement.hidden = true;
        },
    };
}