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

            def fake_run(in_path, out_path, workdir, shadow_path=None, detail_path=None):
                seen["paths"] = (in_path, shadow_path, detail_path)
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


if __name__ == "__main__":
    unittest.main()
