"""Shared PIL helpers: JPEG encode, scaling, and frame-diff motion detection."""
import unittest

from PIL import Image

from backends.imageops import (
    DIFF_GRID_COLS,
    DIFF_GRID_ROWS,
    encode_jpeg,
    frame_diff,
    scale_image,
)


class TestEncodeJpeg(unittest.TestCase):
    def test_encode_jpeg_returns_jpeg_bytes(self):
        solid = Image.new("RGB", (16, 16), (255, 0, 0))
        data = encode_jpeg(solid, 70)
        self.assertTrue(data.startswith(b"\xff\xd8"))
        self.assertTrue(data)


class TestScaleImage(unittest.TestCase):
    def test_scale_image_shrinks_and_grayscales(self):
        img = Image.new("RGB", (100, 100), (10, 20, 30))
        small = scale_image(img, 0.5, True)
        self.assertEqual(small.size, (50, 50))
        self.assertEqual(small.mode, "L")
        same = scale_image(img, 1.0, False)
        self.assertEqual(same.size, (100, 100))


class TestFrameDiff(unittest.TestCase):
    def test_frame_diff_identical_frames(self):
        prev = Image.new("L", (64, 48), 0)
        cur = Image.new("L", (64, 48), 0)
        result = frame_diff(prev, cur)
        self.assertIs(result["changed"], False)
        self.assertEqual(result["changed_pct"], 0.0)
        self.assertEqual(result["tiles"], [])

    def test_frame_diff_changed_tile_shape(self):
        prev = Image.new("L", (64, 48), 0)
        cur = Image.new("L", (64, 48), 0)
        for x in range(5, 15):
            for y in range(5, 15):
                cur.putpixel((x, y), 255)
        result = frame_diff(prev, cur)
        self.assertIs(result["changed"], True)
        bbox = result["bbox"]
        self.assertIsInstance(bbox, list)
        self.assertEqual(len(bbox), 4)
        self.assertTrue(result["tiles"])
        for tile in result["tiles"]:
            self.assertEqual(set(tile), {"row", "col", "pct", "center"})
            self.assertGreaterEqual(tile["row"], 0)
            self.assertLess(tile["row"], DIFF_GRID_ROWS)
            self.assertGreaterEqual(tile["col"], 0)
            self.assertLess(tile["col"], DIFF_GRID_COLS)

    def test_frame_diff_size_mismatch_resizes(self):
        prev = Image.new("L", (64, 48), 0)
        cur = Image.new("L", (32, 24), 0)
        result = frame_diff(prev, cur)
        self.assertEqual(
            set(result), {"changed", "bbox", "changed_pct", "tiles"})


if __name__ == "__main__":
    unittest.main()
