import io
import unittest
from contextlib import redirect_stdout

from scripts.build_region_boundaries import build_voronoi_regions, polygon_point_count
from scripts.build_region_partition_preview import boundary_chains, classify_grid, majority_filter_cells, simplify_chain


class BuildRegionBoundariesTest(unittest.TestCase):
    def test_build_voronoi_regions_splits_city_bounds_without_overlap(self):
        grouped = {
            "left": [{"x": 0, "y": 0}, {"x": 0, "y": 100}],
            "right": [{"x": 100, "y": 0}, {"x": 100, "y": 100}],
        }

        regions = build_voronoi_regions(grouped, padding=0)

        self.assertEqual([region["region_id"] for region in regions], ["left", "right"])
        left_x = [point[0] for point in regions[0]["polygon"]]
        right_x = [point[0] for point in regions[1]["polygon"]]
        self.assertLessEqual(max(left_x), 50)
        self.assertGreaterEqual(min(right_x), 50)
        self.assertEqual(regions[0]["task_count"], 2)
        self.assertEqual(regions[1]["task_count"], 2)

    def test_build_voronoi_regions_simplifies_polygon_points_without_overlap(self):
        grouped = {
            "left": [{"x": 0, "y": 0}, {"x": 0, "y": 120}],
            "middle": [{"x": 120, "y": 0}, {"x": 120, "y": 120}],
            "right": [{"x": 240, "y": 0}, {"x": 240, "y": 120}],
        }

        unsimplified = build_voronoi_regions(grouped, padding=80, simplify_tolerance=0)
        simplified = build_voronoi_regions(grouped, padding=80, simplify_tolerance=50, overlap_threshold=0)

        self.assertEqual([region["region_id"] for region in simplified], ["left", "middle", "right"])
        self.assertLessEqual(polygon_point_count(simplified), polygon_point_count(unsimplified))
        self.assertTrue(all(len(region["polygon"]) >= 3 for region in simplified))

    def test_build_voronoi_regions_prints_point_count_stats(self):
        grouped = {
            "left": [{"x": 0, "y": 0}],
            "right": [{"x": 100, "y": 0}],
        }

        output = io.StringIO()
        with redirect_stdout(output):
            build_voronoi_regions(grouped, padding=0, simplify_tolerance=30, quiet=False)

        self.assertIn("Region boundary point counts:", output.getvalue())
        self.assertIn("left:", output.getvalue())

    def test_classify_grid_assigns_each_cell_to_one_region(self):
        points = {
            "left": [{"x": 0, "y": 0}],
            "right": [{"x": 100, "y": 0}],
        }

        grid = classify_grid(points, columns=4, rows=2, padding=0)

        assignments = [cell["region_id"] for cell in grid["cells"]]
        self.assertEqual(len(assignments), 8)
        self.assertTrue(all(region_id in {"left", "right"} for region_id in assignments))
        self.assertIn("left", assignments)
        self.assertIn("right", assignments)

    def test_boundary_chains_connect_adjacent_boundary_segments(self):
        segments = [
            ((0, 0), (1, 0)),
            ((1, 0), (2, 0)),
            ((2, 0), (3, 0)),
        ]

        chains = boundary_chains(segments)

        self.assertEqual(chains, [[(0, 0), (1, 0), (2, 0), (3, 0)]])

    def test_majority_filter_removes_isolated_cells(self):
        cells = [
            {"column": 0, "row": 0, "region_id": "a"},
            {"column": 1, "row": 0, "region_id": "a"},
            {"column": 2, "row": 0, "region_id": "a"},
            {"column": 0, "row": 1, "region_id": "a"},
            {"column": 1, "row": 1, "region_id": "b"},
            {"column": 2, "row": 1, "region_id": "a"},
            {"column": 0, "row": 2, "region_id": "a"},
            {"column": 1, "row": 2, "region_id": "a"},
            {"column": 2, "row": 2, "region_id": "a"},
        ]

        filtered = majority_filter_cells(cells, columns=3, rows=3, iterations=1)

        center = next(cell for cell in filtered if cell["column"] == 1 and cell["row"] == 1)
        self.assertEqual(center["region_id"], "a")

    def test_simplify_chain_removes_nearly_collinear_points(self):
        chain = [(0, 0), (1, 0.05), (2, -0.03), (3, 0)]

        simplified = simplify_chain(chain, tolerance=0.1)

        self.assertEqual(simplified, [(0, 0), (3, 0)])


if __name__ == "__main__":
    unittest.main()
