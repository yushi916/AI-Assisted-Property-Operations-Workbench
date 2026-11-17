import unittest

from pydantic import ValidationError

from location_hierarchy import build_location_paths
from location_schemas import LocationCreate


class LocationFloorRuleTests(unittest.TestCase):
    def node(self, location_id, name, kind, parent_id=None):
        return LocationCreate(
            id=location_id, name=name, kind=kind, parent_id=parent_id,
        )

    def base_locations(self):
        return [
            self.node("C", "锦园小区", "community"),
            self.node("A", "A区", "area", "C"),
            self.node("B", "1栋", "building", "A"),
            self.node("U", "1单元", "unit", "B"),
        ]

    def test_six_level_room_and_floor_public_space_paths(self):
        locations = self.base_locations() + [
            self.node("F", "3层", "floor", "U"),
            self.node("R", "302室", "room", "F"),
            self.node("P", "公共走廊", "public_space", "F"),
        ]
        paths = build_location_paths(locations)
        prefix = "锦园小区 / A区 / 1栋 / 1单元 / 3层 / "
        self.assertEqual(paths["R"], prefix + "302室")
        self.assertEqual(paths["P"], prefix + "公共走廊")

    def test_floor_requires_a_unit_parent(self):
        with self.assertRaises(ValidationError):
            self.node("F", "3层", "floor")

        for parent_id in ("C", "A", "B"):
            with self.subTest(parent_id=parent_id):
                locations = self.base_locations() + [
                    self.node("F", "3层", "floor", parent_id),
                ]
                with self.assertRaisesRegex(ValueError, "父地点类型不适用"):
                    build_location_paths(locations)

    def test_existing_room_and_area_public_space_paths_remain_valid(self):
        locations = self.base_locations() + [
            self.node("R1", "302室", "room", "U"),
            self.node("R2", "502室", "room", "B"),
            self.node("P", "A区大堂", "public_space", "A"),
        ]
        paths = build_location_paths(locations)
        self.assertEqual(
            paths["R1"], "锦园小区 / A区 / 1栋 / 1单元 / 302室",
        )
        self.assertEqual(
            paths["R2"], "锦园小区 / A区 / 1栋 / 502室",
        )
        self.assertEqual(
            paths["P"], "锦园小区 / A区 / A区大堂",
        )

    def test_same_room_and_floor_names_in_different_units_stay_distinct(self):
        locations = self.base_locations() + [
            self.node("U2", "2单元", "unit", "B"),
            self.node("F1", "3层", "floor", "U"),
            self.node("F2", "3层", "floor", "U2"),
            self.node("R1", "302室", "room", "F1"),
            self.node("R2", "302室", "room", "F2"),
        ]
        paths = build_location_paths(locations)
        self.assertEqual(
            paths["R1"], "锦园小区 / A区 / 1栋 / 1单元 / 3层 / 302室",
        )
        self.assertEqual(
            paths["R2"], "锦园小区 / A区 / 1栋 / 2单元 / 3层 / 302室",
        )


if __name__ == "__main__":
    unittest.main()