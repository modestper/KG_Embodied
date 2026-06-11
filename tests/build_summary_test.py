import tempfile
import unittest
import math
from collections import Counter, defaultdict
from pathlib import Path

from scripts.build_summary import normalize_coordinate, scan_roads, scan_tasks


class BuildSummaryTest(unittest.TestCase):
    def test_scan_roads_reads_tab_separated_geometry(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "roads.csv"
            path.write_text(
                "osm_id\tcode\tfclass\tname\tref\toneway\tmaxspeed\tlayer\tbridge\ttunnel\tcity\tgeometry\n"
                "1\t5114\tsecondary\t测试路\t\tF\t0\t0\tF\tF\t杭州市\t"
                "LINESTRING (1 2, 3 4, 5 6)\n",
                encoding="utf-8",
            )

            roads = scan_roads(path)

        self.assertEqual(roads["杭州市"][0]["name"], "测试路")
        self.assertEqual(roads["杭州市"][0]["points"], [[1.0, 2.0], [3.0, 4.0], [5.0, 6.0]])

    def test_scan_tasks_reads_split_delivery_schema(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "delivery_jl.csv"
            path.write_text(
                "order_id,region_id,city,courier_id,lng,lat,aoi_id,aoi_type,"
                "accept_time,accept_gps_time,accept_gps_lng,accept_gps_lat,"
                "delivery_time,delivery_gps_time,delivery_gps_lng,delivery_gps_lat,ds\n"
                "1,31,Jilin,4849,126.56526,43.84112,94,14,"
                "09-25 08:08:00,09-25 08:08:00,126.56614,43.87092,"
                "09-25 11:32:00,09-25 11:32:00,126.56919,43.84248,0925\n",
                encoding="utf-8",
            )
            cities = defaultdict(
                lambda: {
                    "delivery": 0,
                    "pickup": 0,
                    "couriers": set(),
                    "aoi": set(),
                    "typecodes": {"delivery": Counter(), "pickup": Counter()},
                    "delivery_duration_total": 0,
                    "delivery_duration_count": 0,
                    "pickup_duration_total": 0,
                    "pickup_duration_count": 0,
                }
            )
            hourly = {"delivery": Counter(), "pickup": Counter()}
            samples = {"delivery": defaultdict(list), "pickup": defaultdict(list)}

            scan_tasks(path, "delivery", cities, hourly, samples)

        self.assertEqual(cities["吉林市"]["delivery"], 1)
        self.assertEqual(cities["吉林市"]["delivery_duration_total"], 204)
        self.assertEqual(hourly["delivery"][8], 1)
        expected_lng = round(6378137 * math.radians(126.56526), 3)
        expected_lat = round(
            6378137 * math.log(math.tan(math.pi / 4 + math.radians(43.84112) / 2)),
            3,
        )
        self.assertEqual(samples["delivery"]["吉林市"][0]["lng"], expected_lng)
        self.assertEqual(samples["delivery"]["吉林市"][0]["lat"], expected_lat)

    def test_normalize_coordinate_keeps_projected_values(self):
        self.assertEqual(
            normalize_coordinate(13373499.140, 3538916.295),
            (13373499.14, 3538916.295),
        )


if __name__ == "__main__":
    unittest.main()
