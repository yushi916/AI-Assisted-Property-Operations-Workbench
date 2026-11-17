import unittest

from location_hierarchy import build_location_paths
from models import Location


class LocationHierarchyTests(unittest.TestCase):
    def node(self, location_id, name, kind, parent_id=None):
        return Location(
            id=location_id, name=name, kind=kind,
            parent_id=parent_id, version=1,
        )

    def test_same_room_names_have_different_full_paths(self):
        locations = [
            self.node("C", "合成小区", "community"),
            self.node("A", "A区", "area", "C"),
            self.node("B1", "1栋", "building", "A"),
            self.node("B2", "2栋", "building", "A"),
            self.node("U1", "1单元", "unit", "B1"),
            self.node("U2", "1单元", "unit", "B2"),
            self.node("R1", "302室", "room", "U1"),
            self.node("R2", "302室", "room", "U2"),
        ]
        paths = build_location_paths(locations)
        self.assertEqual(paths["R1"], "合成小区 / A区 / 1栋 / 1单元 / 302室")
        self.assertEqual(paths["R2"], "合成小区 / A区 / 2栋 / 1单元 / 302室")

    def test_optional_levels_and_public_space_parents(self):
        locations = [
            self.node("C", "合成小区", "community"),
            self.node("A", "A区", "area", "C"),
            self.node("B", "1栋", "building", "C"),
            self.node("U", "1单元", "unit", "B"),
            self.node("R", "302室", "room", "B"),
        ]
        self.assertEqual(
            build_location_paths(locations)["R"],
            "合成小区 / 1栋 / 302室",
        )
        for parent_id, prefix in (
            ("C", "合成小区"),
            ("A", "合成小区 / A区"),
            ("B", "合成小区 / 1栋"),
            ("U", "合成小区 / 1栋 / 1单元"),
        ):
            with self.subTest(parent_id=parent_id):
                point = self.node("P", "公共点位", "public_space", parent_id)
                paths = build_location_paths(locations + [point])
                self.assertEqual(paths["P"], prefix + " / 公共点位")

    def test_empty_and_unclassified_catalogs_keep_explicit_information(self):
        self.assertEqual(build_location_paths([]), {})
        location = self.node("LEGACY", "A区大堂", "unknown")
        self.assertEqual(build_location_paths([location]), {"LEGACY": "A区大堂"})
        self.assertIsNone(location.parent_id)

    def test_duplicate_ids_are_rejected(self):
        locations = [
            self.node("C", "合成小区一", "community"),
            self.node("C", "合成小区二", "community"),
        ]
        with self.assertRaisesRegex(ValueError, "地点编号重复"):
            build_location_paths(locations)

    def test_missing_parent_is_rejected(self):
        room = self.node("R", "302室", "room", "MISSING")
        with self.assertRaisesRegex(ValueError, "父地点不存在"):
            build_location_paths([room])

    def test_self_and_multiple_node_cycles_are_rejected(self):
        for parents in (
            {"A": "A"},
            {"A": "B", "B": "A"},
            {"A": "B", "B": "C", "C": "A"},
        ):
            with self.subTest(parents=parents):
                locations = []
                for location_id, parent_id in parents.items():
                    locations.append(
                        self.node(location_id, "合成楼栋", "building", parent_id)
                    )
                with self.assertRaisesRegex(ValueError, "存在循环"):
                    build_location_paths(locations)

    def test_wrong_parent_kinds_are_rejected(self):
        locations = [
            self.node("C", "合成小区", "community"),
            self.node("B", "1栋", "building", "C"),
            self.node("R", "301室", "room", "B"),
            self.node("P", "大堂", "public_space", "B"),
            self.node("UNKNOWN", "未分类点位", "unknown"),
        ]
        for kind, parent_id in (
            ("room", "R"), ("room", "P"),
            ("building", "R"), ("unit", "C"),
            ("area", "B"), ("public_space", "UNKNOWN"),
        ):
            with self.subTest(kind=kind, parent_id=parent_id):
                invalid = self.node("BAD", "合成错误地点", kind, parent_id)
                with self.assertRaisesRegex(ValueError, "父地点类型不适用"):
                    build_location_paths(locations + [invalid])

    def test_kind_and_parent_presence_rules_are_enforced(self):
        locations = [
            self.node("C", "合成小区", "community"),
            self.node("B", "1栋", "building", "C"),
        ]
        for kind, parent_id in (
            ("invalid", None), ("room", None),
            ("community", "B"), ("unknown", "B"),
        ):
            with self.subTest(kind=kind, parent_id=parent_id):
                invalid = self.node("BAD", "合成错误地点", kind, parent_id)
                with self.assertRaises(ValueError):
                    build_location_paths(locations + [invalid])


if __name__ == "__main__":
    unittest.main()