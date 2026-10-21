import tempfile
import unittest
from pathlib import Path

from scripts.build_fixed_region_depots import aggregate_city
from scripts.build_summary import normalize_coordinate


class FixedRegionDepotsTest(unittest.TestCase):
    def test_aggregate_city_uses_all_order_coordinates_per_region(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "delivery.csv"
            path.write_text(
                "order_id,region_id,city,lng,lat\n"
                "1,31,Jilin,126.5,43.8\n"
                "2,31,Jilin,126.7,44.0\n"
                "3,32,Jilin,126.9,44.2\n"
                "4,31,Yantai,121.4,37.3\n",
                encoding="utf-8",
            )

            result = aggregate_city(path, "Jilin")

        first = normalize_coordinate("126.5", "43.8")
        second = normalize_coordinate("126.7", "44.0")
        self.assertEqual(result["valid_coordinate_rows"], 3)
        self.assertEqual(result["region_count"], 2)
        self.assertEqual(result["regions"]["31"]["task_count"], 2)
        self.assertEqual(
            result["regions"]["31"]["x"],
            round((first[0] + second[0]) / 2, 3),
        )
        self.assertEqual(
            result["regions"]["31"]["y"],
            round((first[1] + second[1]) / 2, 3),
        )


if __name__ == "__main__":
    unittest.main()
