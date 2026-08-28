import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np

from app import codex_provider, finish
from app.config import settings


class CodexMultiReferenceTest(unittest.TestCase):
    @staticmethod
    def _sample() -> np.ndarray:
        x = np.linspace(15, 235, 96, dtype=np.uint8)
        luma = np.tile(x, (64, 1))
        return cv2.merge((luma // 2, luma, 255 - luma // 2))

    def test_reference_variants_keep_shape_and_change_pixels(self) -> None:
        source = self._sample()
        shadow, detail = finish.codex_reference_variants(source)

        self.assertEqual(shadow.shape, source.shape)
        self.assertEqual(detail.shape, source.shape)
        self.assertEqual(shadow.dtype, source.dtype)
        self.assertEqual(detail.dtype, source.dtype)
        self.assertFalse(np.array_equal(shadow, source))
        self.assertFalse(np.array_equal(detail, source))

    def test_provider_passes_three_existing_references_to_codex(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source_path = root / "source.png"
            output_path = root / "result.jpg"
            cv2.imwrite(str(source_path), self._sample())
            seen = {}

            def fake_run(
                in_path,
                out_path,
                workdir,
                shadow_path=None,
                detail_path=None,
                identity_path=None,
                identity_shadow_path=None,
            ):
                seen["paths"] = (in_path, shadow_path, detail_path)
                self.assertIsNone(identity_path)
                self.assertIsNone(identity_shadow_path)
                for path in seen["paths"]:
                    self.assertIsNotNone(path)
                    self.assertTrue(path.is_file())
                seen["shapes"] = tuple(
                    cv2.imread(str(path), cv2.IMREAD_COLOR).shape[:2]
                    for path in seen["paths"]
                )
                cv2.imwrite(str(out_path), cv2.imread(str(in_path), cv2.IMREAD_COLOR))

            with (
                patch.object(settings, "work_dir", root),
                patch.object(settings, "codex_multi_reference", True),
                patch.object(settings, "codex_face_reference", False),
                patch.object(settings, "codex_reference_max_px", 32),
                patch.object(settings, "finish_detail", False),
                patch.object(codex_provider, "_run_codex", fake_run),
            ):
                width, height = codex_provider.enhance(str(source_path), str(output_path))

            self.assertEqual((width, height), (96, 64))
            self.assertTrue(output_path.is_file())
            self.assertEqual(seen["shapes"][0], (64, 96))
            self.assertEqual(seen["shapes"][1:], ((21, 32), (21, 32)))
            prompt = codex_provider._build_prompt(
                Path("in.png"), Path("out.png"), Path("shadow.png"), Path("detail.png")
            )
            self.assertIn("authoritative original", prompt)
            self.assertIn("shadow-detail reference only", prompt)
            self.assertIn("edge-and-texture reference only", prompt)

    def test_adaptive_input_resizes_only_above_threshold_and_never_upscales(self) -> None:
        source = np.zeros((300, 400, 3), dtype=np.uint8)
        with (
            patch.object(settings, "codex_adaptive_input", True),
            patch.object(settings, "codex_input_downscale_threshold_px", 400),
            patch.object(settings, "codex_input_max_px", 200),
        ):
            unchanged = codex_provider._primary_input(source)
            self.assertIs(unchanged, source)

            large = np.zeros((600, 800, 3), dtype=np.uint8)
            resized = codex_provider._primary_input(large)
            self.assertEqual(resized.shape[:2], (150, 200))

        small = np.zeros((30, 40, 3), dtype=np.uint8)
        self.assertIs(codex_provider._resize_down_to_long_side(small, 200), small)

    def test_identity_crop_is_one_continuous_original_resolution_region(self) -> None:
        source = np.zeros((600, 1000, 3), dtype=np.uint8)
        source[:, :500] = (10, 20, 30)
        source[:, 500:] = (100, 110, 120)
        faces = [(120, 100, 80, 80), (700, 120, 80, 80)]
        with patch.object(settings, "codex_face_reference_max_px", 2000):
            crop = codex_provider._identity_context_crop(source, faces)

        self.assertIsNotNone(crop)
        self.assertGreater(crop.shape[1], 500)
        # В crop присутствуют обе стороны исходного кадра, а не сетка карточек лиц.
        self.assertTrue(np.any(crop[:, :, 0] == 10))
        self.assertTrue(np.any(crop[:, :, 0] == 100))

    def test_missing_face_model_falls_back_without_detection(self) -> None:
        with patch.object(settings, "codex_face_model", Path("/missing/yunet.onnx")):
            self.assertEqual(codex_provider._detect_faces(self._sample()), [])

    def test_primary_face_filter_removes_small_background_people(self) -> None:
        foreground = [(float(x), 100.0, 70.0, 90.0) for x in range(0, 600, 100)]
        background = [(700.0, 100.0, 25.0, 30.0), (760.0, 100.0, 30.0, 35.0)]
        with (
            patch.object(settings, "codex_face_relative_min_scale", 0.5),
            patch.object(settings, "codex_face_max_count", 16),
        ):
            kept = codex_provider._keep_primary_faces(foreground + background)

        self.assertEqual(kept, foreground)

    def test_provider_passes_identity_and_dark_face_as_fourth_and_fifth_refs(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = np.full((120, 160, 3), 20, dtype=np.uint8)
            source_path = root / "source.png"
            output_path = root / "result.jpg"
            cv2.imwrite(str(source_path), source)
            seen = {}

            def fake_run(
                in_path,
                out_path,
                workdir,
                shadow_path=None,
                detail_path=None,
                identity_path=None,
                identity_shadow_path=None,
            ):
                seen["paths"] = (
                    in_path,
                    shadow_path,
                    detail_path,
                    identity_path,
                    identity_shadow_path,
                )
                for path in seen["paths"]:
                    self.assertIsNotNone(path)
                    self.assertTrue(path.is_file())
                seen["input_shape"] = cv2.imread(str(in_path)).shape[:2]
                cv2.imwrite(str(out_path), cv2.imread(str(in_path), cv2.IMREAD_COLOR))

            with (
                patch.object(settings, "work_dir", root),
                patch.object(settings, "codex_multi_reference", True),
                patch.object(settings, "codex_face_reference", True),
                patch.object(settings, "codex_adaptive_input", True),
                patch.object(settings, "codex_input_downscale_threshold_px", 100),
                patch.object(settings, "codex_input_max_px", 80),
                patch.object(settings, "codex_reference_max_px", 80),
                patch.object(settings, "codex_face_reference_max_px", 80),
                patch.object(settings, "codex_face_shadow_max_luma", 255),
                patch.object(codex_provider, "_detect_faces", return_value=[(50, 20, 24, 24)]),
                patch.object(settings, "finish_detail", False),
                patch.object(codex_provider, "_run_codex", fake_run),
            ):
                width, height = codex_provider.enhance(str(source_path), str(output_path))

            self.assertEqual((width, height), (80, 60))
            self.assertEqual(seen["input_shape"], (60, 80))
            prompt = codex_provider._build_prompt(
                Path("in.png"),
                Path("out.png"),
                Path("shadow.png"),
                Path("detail.png"),
                Path("identity.png"),
                Path("identity-shadow.png"),
            )
            self.assertIn("4. identity.png", prompt)
            self.assertIn("5. identity-shadow.png", prompt)
            self.assertIn("Never swap, merge", prompt)
            self.assertIn("naturally soft", prompt)

    def test_identity_shadow_requires_identity(self) -> None:
        with self.assertRaises(ValueError):
            codex_provider._build_prompt(
                Path("in.png"),
                Path("out.png"),
                Path("shadow.png"),
                Path("detail.png"),
                identity_shadow_path=Path("identity-shadow.png"),
            )


if __name__ == "__main__":
    unittest.main()
