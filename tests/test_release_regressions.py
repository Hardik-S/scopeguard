from pathlib import Path
import os
import sys
import unittest
from unittest.mock import patch

from scopeguard import engine
from test_engine import GitRepo


class ReleaseRegressions(GitRepo):
    def test_existing_unsupported_node_cannot_receive_missing_file_fingerprint(self):
        target = self.write("owned.txt", "changed")
        original = Path.is_file

        def is_file(path):
            if path.samefile(target):
                return False
            return original(path)

        with patch.object(Path, "is_file", is_file):
            with self.assertRaisesRegex(RuntimeError, "Unsupported special filesystem node"):
                self.evaluate(run_checks=True)

    @unittest.skipIf(os.name == "nt", "Windows does not expose POSIX executable mode")
    def test_untracked_executable_mode_mutation_is_detected(self):
        self.contract["allowed_paths"].append("tool.sh")
        self.contract["checks"] = [{
            "id": "chmod", "argv": [sys.executable, "-c",
                                   "from pathlib import Path; Path('tool.sh').chmod(0o755)"],
            "timeout_seconds": 5,
        }]
        self.write_contract()
        self.commit("configure executable-mode check")
        self.baseline = engine.freeze(self.repo, self.contract_path)
        target = self.write("tool.sh", "#!/bin/sh")
        target.chmod(0o644)
        report = self.evaluate(run_checks=True)
        self.assertEqual(report["verdict"], "FAIL", report)
        self.assertTrue(report["state_changed_during_checks"])

    def test_staged_blob_mutation_with_unchanged_worktree_is_detected(self):
        command = (
            "import subprocess; "
            "blob=subprocess.run(['git','hash-object','-w','--stdin'], "
            "input=b'stage-after', capture_output=True, check=True).stdout.decode().strip(); "
            "subprocess.run(['git','update-index','--cacheinfo','100644',blob,'owned.txt'], check=True)"
        )
        self.contract["checks"] = [{
            "id": "index-mutation", "argv": [sys.executable, "-c", command],
            "timeout_seconds": 5,
        }]
        self.write_contract()
        self.commit("configure index mutation")
        self.baseline = engine.freeze(self.repo, self.contract_path)
        self.write("owned.txt", "stage-before")
        self.git("add", "owned.txt")
        self.write("owned.txt", "working")
        report = self.evaluate(run_checks=True)
        self.assertEqual(report["verdict"], "FAIL", report)
        self.assertTrue(report["state_changed_during_checks"])

    def test_content_records_have_unambiguous_boundaries(self):
        self.write("first.txt", "base")
        self.write("second.txt", "base")
        self.contract["allowed_paths"].extend(["first.txt", "second.txt"])
        command = (
            "from pathlib import Path; name=b'second.txt'; "
            "prefix=len(name).to_bytes(8, 'big')+name+b'F'; "
            "Path('first.txt').write_bytes(b'X'+prefix); "
            "Path('second.txt').write_bytes(b'Y')"
        )
        self.contract["checks"] = [{
            "id": "move-record-prefix", "argv": [sys.executable, "-c", command],
            "timeout_seconds": 5,
        }]
        self.write_contract()
        self.commit("configure boundary-collision reproduction")
        self.baseline = engine.freeze(self.repo, self.contract_path)
        name = b"second.txt"
        prefix = len(name).to_bytes(8, "big") + name + b"F"
        (self.repo / "first.txt").write_bytes(b"X")
        (self.repo / "second.txt").write_bytes(prefix + b"Y")
        report = self.evaluate(run_checks=True)
        self.assertEqual(report["verdict"], "FAIL", report)
        self.assertTrue(report["state_changed_during_checks"])

    def test_output_directory_write_does_not_count_as_source_mutation(self):
        self.contract["checks"] = [{
            "id": "receipt",
            "argv": [sys.executable, "-c",
                     "from pathlib import Path; "
                     "Path('.scopeguard').mkdir(exist_ok=True); "
                     "Path('.scopeguard/log.txt').write_text('receipt')"],
            "timeout_seconds": 5,
        }]
        self.write_contract()
        self.commit("configure receipt-only check")
        self.baseline = engine.freeze(self.repo, self.contract_path)
        report = self.evaluate(run_checks=True)
        self.assertEqual(report["verdict"], "PASS", report)
        self.assertFalse(report["state_changed_during_checks"])
        self.assertEqual(report["changed_paths"], [])

    def test_unreadable_changed_source_cannot_pass(self):
        target = self.write("owned.txt", "changed")
        original = Path.read_bytes

        def read_bytes(path):
            if path.samefile(target):
                raise PermissionError("synthetic unreadable source")
            return original(path)

        with patch.object(Path, "read_bytes", read_bytes):
            with self.assertRaisesRegex(RuntimeError, "Cannot fingerprint"):
                self.evaluate(run_checks=True)
