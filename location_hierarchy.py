ROOT_KINDS = {"community", "unknown"}

ALLOWED_PARENT_KINDS = {
    "community": set(),
    "unknown": set(),
    "area": {"community"},
    "building": {"community", "area"},
    "unit": {"building"},
    "room": {"building", "unit"},
    "public_space": {"community", "area", "building", "unit"},
}


def build_location_paths(locations):
    locations = list(locations)
    by_id = {}
    for location in locations:
        if location.id in by_id:
            raise ValueError(f"地点编号重复：{location.id}")
        by_id[location.id] = location

    paths = {}
    for location in locations:
        names = []
        seen = set()
        current = location
        while True:
            if current.id in seen:
                raise ValueError(f"地点父子关系存在循环：{current.id}")
            seen.add(current.id)
            names.append(current.name)
            if current.parent_id is None:
                break
            if current.parent_id not in by_id:
                raise ValueError(f"父地点不存在：{current.parent_id}")
            current = by_id[current.parent_id]
        paths[location.id] = " / ".join(reversed(names))

    for location in locations:
        allowed = ALLOWED_PARENT_KINDS.get(location.kind)
        if allowed is None:
            raise ValueError(f"地点类型无效：{location.id}")
        if location.parent_id is None:
            if location.kind not in ROOT_KINDS:
                raise ValueError(f"此类地点必须填写父地点：{location.id}")
        elif location.kind in ROOT_KINDS:
            raise ValueError(f"此类地点不能填写父地点：{location.id}")
        elif by_id[location.parent_id].kind not in allowed:
            raise ValueError(f"父地点类型不适用：{location.id}")

    return paths