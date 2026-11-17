import unittest

from pydantic import ValidationError

from location_schemas import LocationCreate


class LocationSchemaTests(unittest.TestCase):
    def room_data(self):
        return {
            "id": "LOC-A-B1-U1-302",
            "name": "302室",
            "kind": "room",
            "parent_id": "LOC-A-B1-U1",
        }

    def test_community_and_unclassified_location_can_have_no_parent(self):
        for kind in ("community", "unknown"):
            with self.subTest(kind=kind):
                location = LocationCreate(
                    id="LOC-ROOT", name="合成地点", kind=kind
                )
                self.assertIsNone(location.parent_id)

    def test_valid_room_preserves_explicit_id_name_and_parent(self):
        data = self.room_data()
        self.assertEqual(LocationCreate(**data).model_dump(), data)

    def test_child_locations_require_a_parent(self):
        for kind in ("area", "building", "unit", "room", "public_space"):
            with self.subTest(kind=kind):
                data = self.room_data()
                data["kind"] = kind
                data.pop("parent_id")
                with self.assertRaisesRegex(ValidationError, "必须填写父地点"):
                    LocationCreate(**data)

    def test_root_and_unclassified_locations_reject_a_parent(self):
        for kind in ("community", "unknown"):
            with self.subTest(kind=kind):
                data = self.room_data()
                data["kind"] = kind
                with self.assertRaisesRegex(ValidationError, "不能填写父地点"):
                    LocationCreate(**data)

    def test_location_cannot_be_its_own_parent(self):
        data = self.room_data()
        data["parent_id"] = data["id"]
        with self.assertRaisesRegex(ValidationError, "自身作为父地点"):
            LocationCreate(**data)

    def test_blank_and_overlong_fields_are_rejected(self):
        for field, limit in (("id", 64), ("name", 120), ("parent_id", 64)):
            for value in ("", "   ", "x" * (limit + 1)):
                with self.subTest(field=field, value=value):
                    data = self.room_data()
                    data[field] = value
                    with self.assertRaises(ValidationError):
                        LocationCreate(**data)

    def test_unsupported_location_kind_is_rejected(self):
        data = self.room_data()
        data["kind"] = "cleaning"
        with self.assertRaises(ValidationError):
            LocationCreate(**data)

    def test_client_cannot_supply_the_server_version(self):
        data = self.room_data()
        data["version"] = 99
        with self.assertRaises(ValidationError):
            LocationCreate(**data)


if __name__ == "__main__":
    unittest.main()