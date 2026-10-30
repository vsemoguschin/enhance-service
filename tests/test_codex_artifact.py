import json
import os
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np

from app import codex_provider
from app.codex_artifact import CodexArtifactError, read_session_artifact
from app.config import settings


THREAD_A = "019a3710-0000-7000-8000-000000000001"
THREAD_B = "019a3710-0000-7000-8000-000000000002"


def events(thread_id=THREAD_A, completed=True):
    rows = [{"type": "thread.started", "thread_id": thread_id}, {"type": "turn.started"}]
    if completed:
        rows.append({"type": "turn.completed", "usage": {}})
    return "\n".join(json.dumps(row) for row in rows)


class CodexArtifactTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.own = self.root / THREAD_A
        self.own.mkdir()
        (self.own / "image.png").write_bytes(b"own-image")
        self.foreign = self.root / THREAD_B
        self.foreign.mkdir()
        (self.foreign / "image.png").write_bytes(b"foreign-image")

    def test_reads_own_session_even_when_foreign_image_is_newer(self):
        os.utime(self.foreign / "image.png", (2_000_000_000, 2_000_000_000))
        self.assertEqual(read_session_artifact(events(), self.root, 100), (THREAD_A, b"own-image"))

    def test_agent_message_cannot_nominate_foreign_session(self):
        stream = events() + "\n" + json.dumps({
            "type": "item.completed", "item": {"type": "agent_message", "text": events(THREAD_B)},
        })
        self.assertEqual(read_session_artifact(stream, self.root, 100)[1], b"own-image")

    def test_invalid_or_incomplete_json_identity_is_rejected(self):
        streams = [
            "OK", "", "[]", events("../../foreign"), events("not-a-uuid"),
            events(completed=False), events() + "\n" + events(THREAD_B),
            events() + '\n{"type":"turn.failed"}',
        ]
        for stream in streams:
            with self.subTest(stream=stream), self.assertRaises(CodexArtifactError):
                read_session_artifact(stream, self.root, 100)

    def test_missing_own_image_never_falls_back_to_foreign_session(self):
        (self.own / "image.png").unlink()
        with self.assertRaisesRegex(CodexArtifactError, "missing-or-ambiguous"):
            read_session_artifact(events(), self.root, 100)

    def test_missing_own_directory_is_rejected(self):
        with self.assertRaisesRegex(CodexArtifactError, "unavailable"):
            read_session_artifact(events("019a3710-0000-7000-8000-000000000003"), self.root, 100)

    def test_multiple_images_are_rejected_instead_of_picking_latest(self):
        (self.own / "second.webp").write_bytes(b"another-image")
        with self.assertRaisesRegex(CodexArtifactError, "missing-or-ambiguous"):
            read_session_artifact(events(), self.root, 100)

    def test_symlink_image_and_directory_are_rejected(self):
        (self.own / "image.png").unlink()
        (self.own / "image.png").symlink_to(self.foreign / "image.png")
        with self.assertRaisesRegex(CodexArtifactError, "unavailable"):
            read_session_artifact(events(), self.root, 100)
        (self.own / "image.png").unlink()
        self.own.rmdir()
        self.own.symlink_to(self.foreign, target_is_directory=True)
        with self.assertRaisesRegex(CodexArtifactError, "unavailable"):
            read_session_artifact(events(), self.root, 100)

    def test_empty_oversized_and_nonregular_images_are_rejected(self):
        path = self.own / "image.png"
        for data, limit in [(b"", 100), (b"too-large", 2)]:
            path.write_bytes(data)
            with self.subTest(data=data), self.assertRaisesRegex(CodexArtifactError, "invalid"):
                read_session_artifact(events(), self.root, limit)
        path.unlink()
        path.mkdir()
        with self.assertRaises(CodexArtifactError):
            read_session_artifact(events(), self.root, 100)
        path.rmdir()
        os.mkfifo(path)
        with self.assertRaises(CodexArtifactError):
            read_session_artifact(events(), self.root, 100)

    def test_failure_diagnostics_do_not_expose_cli_output_or_paths(self):
        with self.assertRaises(CodexArtifactError) as error:
            read_session_artifact("private-user-content", self.root, 100)
        self.assertNotIn("private-user-content", str(error.exception))
        self.assertNotIn(str(self.root), str(error.exception))


class CodexArtifactProviderTests(unittest.TestCase):
    def test_cli_exit_error_does_not_log_json_tool_payloads(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            with patch.object(codex_provider.subprocess, "Popen") as popen:
                popen.return_value.communicate.return_value = ("private-image-payload", "")
                popen.return_value.returncode = 1
                with self.assertRaises(codex_provider.CodexError) as error:
                    codex_provider._run_codex(root / "in.png", root / "out.png", root)
            self.assertNotIn("private-image-payload", str(error.exception))

    def test_parallel_providers_ignore_swapped_out_files_in_both_completion_orders(self):
        # A real local fake CLI exercises subprocess JSON, shared output folders and
        # full enhance/resize/finish concurrently. It never invokes a model/network.
        for first in ("a", "b"):
            with self.subTest(first=first), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                generated = root / "generated_images"
                generated.mkdir()
                for label, thread in (("a", THREAD_A), ("b", THREAD_B)):
                    directory = generated / thread
                    directory.mkdir()
                    source = np.zeros((48, 64, 3), np.uint8)
                    source[:, :] = (20, 60, 180) if label == "a" else (180, 60, 20)
                    cv2.imwrite(str(root / f"{label}.png"), source)
                    cv2.imwrite(str(directory / "image.png"), source)

                fake_cli = root / "fake-codex"
                fake_cli.write_text(
                    f"#!{sys.executable}\n"
                    "import json, sys, time\nfrom pathlib import Path\n"
                    f"root = Path({str(root)!r})\n"
                    f"threads = {{'a': {THREAD_A!r}, 'b': {THREAD_B!r}}}\n"
                    "cwd = Path(sys.argv[sys.argv.index('-C') + 1])\n"
                    # Each enhance gets a unique cwd; bind fake generation by exact
                    # input bytes, without relying on worker order or timing.
                    "label = 'a' if (cwd / 'in.png').read_bytes() == (root / 'a.png').read_bytes() else 'b'\n"
                    "other = 'b' if label == 'a' else 'a'\n"
                    "(root / (label + '.started')).touch()\n"
                    "deadline = time.monotonic() + 5\n"
                    "while not (root / (other + '.started')).exists():\n"
                    " if time.monotonic() > deadline: sys.exit(2)\n"
                    " time.sleep(0.01)\n"
                    f"if label != {first!r}:\n"
                    " while not (root / (other + '.finished')).exists():\n"
                    "  if time.monotonic() > deadline: sys.exit(3)\n"
                    "  time.sleep(0.01)\n"
                    # Deliberately emulate the old bug: valid out.png from the
                    # other session, even though this session's artifact exists.
                    "(cwd / 'out.png').write_bytes((root / 'generated_images' / threads[other] / 'image.png').read_bytes())\n"
                    "print(json.dumps({'type': 'thread.started', 'thread_id': threads[label]}))\n"
                    "print(json.dumps({'type': 'turn.completed'}))\n"
                    "(root / (label + '.finished')).touch()\n"
                )
                fake_cli.chmod(0o700)

                def collect(stream, unused_root, limit):
                    return read_session_artifact(stream, generated, limit)

                with (
                    patch.object(settings, "codex_bin", str(fake_cli)),
                    patch.object(settings, "work_dir", root / "work"),
                    patch.object(settings, "codex_multi_reference", False),
                    patch.object(settings, "finish_detail", False),
                    patch.object(codex_provider, "read_session_artifact", side_effect=collect),
                    ThreadPoolExecutor(max_workers=2) as workers,
                ):
                    futures = [workers.submit(
                        codex_provider.enhance, str(root / f"{label}.png"), str(root / f"{label}.jpg"),
                    ) for label in ("a", "b")]
                    for future in futures:
                        self.assertEqual(future.result(timeout=15), (64, 48))
                for label in ("a", "b"):
                    output = cv2.imread(str(root / f"{label}.jpg"))
                    original = cv2.imread(str(root / f"{label}.png"))
                    self.assertLess(float(np.abs(output.astype(float) - original).mean()), 2)
                    self.assertTrue((root / f"{label}.started").exists())

    def test_existing_foreign_output_is_rejected_without_own_artifact(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            out = root / "out.png"
            out.write_bytes(b"foreign-valid-image")
            with (
                patch.object(codex_provider.subprocess, "Popen") as popen,
                patch.object(codex_provider, "read_session_artifact", side_effect=CodexArtifactError("missing")),
            ):
                popen.return_value.communicate.return_value = (events(), "")
                popen.return_value.returncode = 0
                with self.assertRaisesRegex(codex_provider.CodexError, "stage=result-provenance"):
                    codex_provider._run_codex(root / "in.png", out, root)

    def test_symlink_output_is_rejected_without_overwriting_its_target(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            target = root / "unrelated.png"
            target.write_bytes(b"unchanged")
            out = root / "out.png"
            out.symlink_to(target)
            with (
                patch.object(codex_provider.subprocess, "Popen") as popen,
                patch.object(codex_provider, "read_session_artifact", return_value=(THREAD_A, b"own-image")),
            ):
                popen.return_value.communicate.return_value = (events(), "")
                popen.return_value.returncode = 0
                with self.assertRaisesRegex(codex_provider.CodexError, "invalid-output-link"):
                    codex_provider._run_codex(root / "in.png", out, root)
            self.assertEqual(target.read_bytes(), b"unchanged")

    def test_existing_output_hardlink_does_not_modify_another_sessions_artifact(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            target = root / "another-session.png"
            target.write_bytes(b"foreign-image")
            out = root / "out.png"
            os.link(target, out)
            with (
                patch.object(codex_provider.subprocess, "Popen") as popen,
                patch.object(codex_provider, "read_session_artifact", return_value=(THREAD_A, b"own-image")),
            ):
                popen.return_value.communicate.return_value = (events(), "")
                popen.return_value.returncode = 0
                codex_provider._run_codex(root / "in.png", out, root)
            self.assertEqual(out.read_bytes(), b"own-image")
            self.assertEqual(target.read_bytes(), b"foreign-image")


if __name__ == "__main__":
    unittest.main()
