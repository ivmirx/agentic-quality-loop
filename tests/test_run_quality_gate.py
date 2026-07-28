#!/usr/bin/env python3
"""Adversarial tests for the common runner and its invocation contract."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = REPOSITORY_ROOT / "skills" / "agentic-quality-loop"
SCRIPT = SKILL_ROOT / "scripts" / "run_quality_gate.py"
SPEC = importlib.util.spec_from_file_location("run_quality_gate", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
RUNNER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RUNNER)


class RunnerTests(unittest.TestCase):
    def make_repo(self) -> Path:
        root = Path(tempfile.mkdtemp(prefix="agentic-quality-runner-test-"))
        self.addCleanup(shutil.rmtree, root, True)
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        return root.resolve()

    def write_gate(self, root: Path, relative: str, source: str = "exit 0\n") -> Path:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("#!/usr/bin/env bash\n" + source, encoding="utf-8")
        return path

    def make_lock_path(self) -> Path:
        root = Path(tempfile.mkdtemp(prefix="agentic-quality-lock-test-"))
        self.addCleanup(shutil.rmtree, root, True)
        return root / "machine-gate.lock"

    def runner_command(self, lock_path: Path, *arguments: str) -> list[str]:
        source = "\n".join(
            (
                "import pathlib",
                "import runpy",
                f"module = runpy.run_path({str(SCRIPT)!r}, "
                "run_name='agentic_quality_runner_test')",
                "module['main'].__globals__['default_machine_lock_path'] = "
                f"lambda: pathlib.Path({str(lock_path)!r})",
                "raise SystemExit(module['main']())",
            )
        )
        return [sys.executable, "-c", source, *arguments]

    @staticmethod
    def _process_exists(pid: int) -> bool:
        try:
            os.kill(pid, 0)
        except (ProcessLookupError, PermissionError):
            return False
        return True

    @staticmethod
    def _process_group_exists(process_group: int) -> bool:
        try:
            os.killpg(process_group, 0)
        except (ProcessLookupError, PermissionError):
            return False
        return True

    def assert_signal_kills_descendant(
        self,
        sent_signal: signal.Signals,
        expected_exit_code: int,
    ) -> None:
        root = self.make_repo()
        child_pid = root / "child.pid"
        child_program = (
            "import os,signal,time; "
            "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
            f"open({str(child_pid)!r}, 'w').write(str(os.getpid())); "
            "time.sleep(30)"
        )
        self.write_gate(
            root,
            "quality/gate.sh",
            f"{shlex.quote(sys.executable)} -c {shlex.quote(child_program)} &\nwait\n",
        )
        process = subprocess.Popen(
            self.runner_command(
                self.make_lock_path(),
                "--repo",
                str(root),
                "--profile",
                "fast",
                "--json",
            ),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.addCleanup(
            lambda: process.kill() if process.poll() is None else None,
        )
        for _ in range(100):
            if child_pid.is_file():
                break
            time.sleep(0.02)
        else:
            self.fail("quality-gate descendant did not start")

        os.kill(process.pid, sent_signal)
        stdout, stderr = process.communicate(timeout=10)
        self.assertEqual(process.returncode, expected_exit_code, stderr)
        summary = json.loads(stdout)
        self.assertEqual(summary["status"], "INTERRUPTED")
        self.assertEqual(summary["exit_code"], expected_exit_code)

        pid = int(child_pid.read_text(encoding="utf-8"))
        for _ in range(20):
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.05)
        else:
            self.fail(
                f"descendant process {pid} survived {sent_signal.name}"
            )

    def test_discovers_each_supported_uniform_quality_gate(self) -> None:
        for relative in RUNNER.CANDIDATES:
            with self.subTest(relative=relative):
                root = self.make_repo()
                expected = self.write_gate(root, relative)
                self.assertEqual(RUNNER.discover(root), expected)

    def test_rejects_ambiguous_language_variants(self) -> None:
        root = self.make_repo()
        self.write_gate(root, "quality/gate.sh")
        self.write_gate(root, "quality/gate.py")
        with self.assertRaisesRegex(ValueError, "keep exactly one"):
            RUNNER.discover(root)

    def test_unsupported_quality_gate_variant_is_not_discovered(self) -> None:
        root = self.make_repo()
        self.write_gate(root, "quality/gate.rb")
        with self.assertRaisesRegex(FileNotFoundError, "no quality gate found"):
            RUNNER.discover(root)

    def test_legacy_script_location_is_not_discovered(self) -> None:
        root = self.make_repo()
        self.write_gate(root, "scripts/quality-gate.sh")
        with self.assertRaisesRegex(FileNotFoundError, "no quality gate found"):
            RUNNER.discover(root)

    def test_nonzero_exit_is_propagated_with_bounded_diagnostics(self) -> None:
        root = self.make_repo()
        self.write_gate(
            root,
            "quality/gate.sh",
            "printf 'first\\nsecond\\nthird\\n'\nexit 7\n",
        )
        result = subprocess.run(
            self.runner_command(
                self.make_lock_path(),
                "--repo",
                str(root),
                "--profile",
                "fast",
                "--show-lines",
                "2",
                "--json",
            ),
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 7, result.stderr)
        summary = json.loads(result.stdout)
        self.assertEqual(summary["status"], "FAIL")
        self.assertEqual(summary["exit_code"], 7)
        self.assertEqual(summary["diagnostics"], ["second", "third"])
        self.assertTrue(Path(summary["log"]).is_file())

    def test_log_is_byte_bounded_and_keeps_actionable_head_and_tail(self) -> None:
        root = self.make_repo()
        payload_size = RUNNER.MAX_LOG_BYTES + (1024 * 1024)
        program = (
            "import sys; "
            f"sys.stdout.write('HEAD_SENTINEL' + 'x' * {payload_size} "
            "+ 'TAIL_SENTINEL')"
        )
        self.write_gate(
            root,
            "quality/gate.sh",
            f"{shlex.quote(sys.executable)} -c {shlex.quote(program)}\nexit 9\n",
        )
        result = subprocess.run(
            self.runner_command(
                self.make_lock_path(),
                "--repo",
                str(root),
                "--profile",
                "fast",
                "--json",
            ),
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )

        self.assertEqual(result.returncode, 9, result.stderr)
        summary = json.loads(result.stdout)
        log_path = Path(summary["log"])
        content = log_path.read_bytes()
        self.assertLessEqual(len(content), RUNNER.MAX_LOG_BYTES)
        self.assertTrue(content.startswith(b"HEAD_SENTINEL"))
        self.assertIn(b"...[output truncated; kept beginning and end]...", content)
        self.assertTrue(content.endswith(b"TAIL_SENTINEL"))
        self.assertIn("TAIL_SENTINEL", "\n".join(summary["diagnostics"]))
        self.assertLessEqual(
            len("\n".join(summary["diagnostics"]).encode("utf-8")),
            RUNNER.MAX_DIAGNOSTIC_BYTES + 128,
        )

    def test_runner_logs_keep_current_plus_one_previous_and_prune_incomplete(self) -> None:
        root = self.make_repo()
        self.write_gate(root, "quality/gate.sh")
        lock_path = self.make_lock_path()
        log_root = lock_path.parent / "runner-logs"
        log_root.mkdir(mode=0o700)
        incomplete = log_root / "run-20000101T000000.000000Z-1-deadbeef"
        incomplete.mkdir(mode=0o700)

        observed_logs = []
        for _ in range(3):
            result = subprocess.run(
                self.runner_command(
                    lock_path,
                    "--repo",
                    str(root),
                    "--profile",
                    "fast",
                    "--json",
                ),
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            observed_logs.append(Path(json.loads(result.stdout)["log"]).parent)

        retained = sorted(
            path for path in log_root.iterdir()
            if RUNNER.RUN_LOG_NAME.fullmatch(path.name)
        )
        self.assertEqual(set(retained), set(observed_logs[-2:]))
        self.assertFalse(incomplete.exists())
        self.assertFalse(observed_logs[0].exists())
        for run_root in retained:
            self.assertTrue((run_root / ".complete").is_file())
            self.assertTrue((run_root / "gate.log").is_file())

    def test_repeated_crash_allocations_keep_only_one_incomplete_log(self) -> None:
        lock_path = self.make_lock_path()
        observed = []
        for index in range(5):
            store = RUNNER.RunnerLogStore(
                lock_path.parent,
                RUNNER.DEFAULT_PREVIOUS_RUNS,
            )
            log_path = store.begin()
            log_path.write_text(f"crash {index}\n", encoding="utf-8")
            observed.append(log_path.parent)
            candidates = [
                path
                for path in store.root.iterdir()
                if RUNNER.RUN_LOG_NAME.fullmatch(path.name)
            ]
            self.assertEqual(candidates, [log_path.parent])

        for stale in observed[:-1]:
            self.assertFalse(stale.exists())
        self.assertTrue(observed[-1].exists())
        self.assertFalse((observed[-1] / ".complete").exists())

    def test_invalid_timeout_and_diagnostic_limits_are_rejected(self) -> None:
        root = self.make_repo()
        self.write_gate(root, "quality/gate.sh")
        for option, value in (("--timeout", "0"), ("--show-lines", "-1")):
            with self.subTest(option=option):
                result = subprocess.run(
                    self.runner_command(
                        self.make_lock_path(),
                        "--repo",
                        str(root),
                        option,
                        value,
                        "--dry-run",
                    ),
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(result.returncode, 2)
                self.assertIn("error:", result.stderr)

    def test_symlinked_runner_log_root_fails_closed_before_gate_start(self) -> None:
        root = self.make_repo()
        started = root / "started"
        self.write_gate(
            root,
            "quality/gate.sh",
            f"printf started > {shlex.quote(str(started))}\n",
        )
        lock_path = self.make_lock_path()
        foreign_root = lock_path.parent / "foreign"
        foreign_root.mkdir(mode=0o700)
        (lock_path.parent / "runner-logs").symlink_to(foreign_root)

        result = subprocess.run(
            self.runner_command(
                lock_path,
                "--repo",
                str(root),
                "--json",
            ),
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 2, result.stderr)
        summary = json.loads(result.stdout)
        self.assertEqual(summary["status"], "ERROR")
        self.assertIn("runner-log storage unavailable", summary["diagnostics"][0])
        self.assertFalse(started.exists())

    def test_optional_retention_argument_is_forwarded(self) -> None:
        root = self.make_repo()
        self.write_gate(root, "quality/gate.sh")
        result = subprocess.run(
            self.runner_command(
                self.make_lock_path(),
                "--repo",
                str(root),
                "--profile",
                "fast",
                "--keep-previous-runs",
                "2",
                "--dry-run",
                "--json",
            ),
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        summary = json.loads(result.stdout)
        self.assertEqual(
            summary["command"][-2:],
            ["--keep-previous-runs", "2"],
        )

    @unittest.skipUnless(
        os.name == "posix" and hasattr(signal, "pthread_sigmask"),
        "signal-mask assertion is POSIX-specific",
    )
    def test_repository_gate_inherits_unblocked_control_signals(self) -> None:
        root = self.make_repo()
        signal_names = ("SIGINT", "SIGTERM", "SIGHUP")
        available_signals = tuple(
            int(getattr(signal, name))
            for name in signal_names
            if hasattr(signal, name)
        )
        probe = (
            "import signal,sys; "
            f"expected={available_signals!r}; "
            "blocked=signal.pthread_sigmask(signal.SIG_BLOCK, []); "
            "unexpected=blocked.intersection(expected); "
            "print(','.join(sorted(item.name for item in unexpected))); "
            "sys.exit(bool(unexpected))"
        )
        self.write_gate(
            root,
            "quality/gate.sh",
            f"exec {shlex.quote(sys.executable)} -c {shlex.quote(probe)}\n",
        )

        result = subprocess.run(
            self.runner_command(
                self.make_lock_path(),
                "--repo",
                str(root),
                "--profile",
                "fast",
                "--json",
            ),
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["status"], "PASS")

    @unittest.skipUnless(os.name == "posix", "flock assertion is POSIX-specific")
    def test_cross_repository_contention_fails_fast_with_owner_metadata(self) -> None:
        first_root = self.make_repo()
        second_root = self.make_repo()
        first_started = first_root / "gate-started"
        second_started = second_root / "gate-started"
        self.write_gate(
            first_root,
            "quality/gate.sh",
            f"printf 'started\\n' > {shlex.quote(str(first_started))}\nsleep 30\n",
        )
        self.write_gate(
            second_root,
            "quality/gate.sh",
            f"printf 'started\\n' > {shlex.quote(str(second_started))}\n",
        )
        lock_path = self.make_lock_path()
        first = subprocess.Popen(
            self.runner_command(
                lock_path,
                "--repo",
                str(first_root),
                "--profile",
                "full",
                "--json",
            ),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.addCleanup(lambda: first.kill() if first.poll() is None else None)
        for _ in range(100):
            if first_started.is_file():
                break
            time.sleep(0.02)
        else:
            self.fail("first repository gate did not start")

        contention_started = time.monotonic()
        second = subprocess.run(
            self.runner_command(
                lock_path,
                "--repo",
                str(second_root),
                "--profile",
                "fast",
                "--json",
            ),
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
        contention_elapsed = time.monotonic() - contention_started
        self.assertEqual(second.returncode, RUNNER.BUSY_EXIT_CODE, second.stderr)
        self.assertLess(contention_elapsed, 2)
        summary = json.loads(second.stdout)
        self.assertEqual(summary["status"], "BUSY")
        self.assertEqual(summary["exit_code"], RUNNER.BUSY_EXIT_CODE)
        self.assertEqual(summary["owner"]["repo"], str(first_root))
        self.assertEqual(summary["owner"]["pid"], first.pid)
        self.assertEqual(summary["owner"]["profile"], "full")
        self.assertTrue(summary["owner"]["started_at"])
        self.assertIn(str(first_root), summary["diagnostics"][0])
        self.assertFalse(second_started.exists())

        first.send_signal(signal.SIGTERM)
        stdout, stderr = first.communicate(timeout=10)
        self.assertEqual(first.returncode, 143, stderr)
        self.assertEqual(json.loads(stdout)["status"], "INTERRUPTED")

    @unittest.skipUnless(os.name == "posix", "flock assertion is POSIX-specific")
    def test_guard_holds_lock_after_runner_sigkill_until_gate_exits(self) -> None:
        lock_path = self.make_lock_path()
        owner_root = self.make_repo()
        next_root = self.make_repo()
        marker = owner_root / "gate-started"
        gate_pid_path = owner_root / "gate.pid"
        self.write_gate(
            owner_root,
            "quality/gate.sh",
            f"printf '%s' \"$$\" > {shlex.quote(str(gate_pid_path))}\n"
            f"printf 'started\\n' > {shlex.quote(str(marker))}\n"
            "sleep 30\n",
        )
        self.write_gate(next_root, "quality/gate.sh")
        runner = subprocess.Popen(
            self.runner_command(
                lock_path,
                "--repo",
                str(owner_root),
                "--profile",
                "native",
                "--json",
            ),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.addCleanup(lambda: runner.kill() if runner.poll() is None else None)
        for _ in range(100):
            if marker.is_file():
                break
            time.sleep(0.02)
        else:
            self.fail("repository gate did not start")

        gate_pid = int(gate_pid_path.read_text(encoding="utf-8"))
        gate_process_group = os.getpgid(gate_pid)
        self.addCleanup(
            lambda: os.killpg(gate_process_group, signal.SIGKILL)
            if self._process_group_exists(gate_process_group)
            else None
        )
        runner.kill()
        runner.communicate(timeout=5)
        self.assertEqual(runner.returncode, -signal.SIGKILL)
        os.kill(gate_pid, 0)

        while_orphaned = subprocess.run(
            self.runner_command(
                lock_path,
                "--repo",
                str(next_root),
                "--profile",
                "fast",
                "--json",
            ),
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
        self.assertEqual(
            while_orphaned.returncode,
            RUNNER.BUSY_EXIT_CODE,
            while_orphaned.stderr,
        )
        summary = json.loads(while_orphaned.stdout)
        self.assertEqual(summary["status"], "BUSY")
        self.assertEqual(summary["owner"]["pid"], runner.pid)
        self.assertEqual(summary["owner"]["guard_pid"], gate_process_group)
        self.assertIn(
            f"owner_guard_pid={gate_process_group}",
            summary["diagnostics"][0],
        )

        os.killpg(gate_process_group, signal.SIGKILL)
        for _ in range(100):
            probe = RUNNER.MachineGateLock(
                lock_path,
                root=next_root,
                profile="fast",
            )
            try:
                probe.acquire()
            except RUNNER.MachineLockBusy:
                time.sleep(0.02)
                continue
            else:
                probe.release()
                break
        else:
            self.fail("guard did not release the lock after its gate was killed")

        recovered = subprocess.run(
            self.runner_command(
                lock_path,
                "--repo",
                str(next_root),
                "--profile",
                "fast",
                "--json",
            ),
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
        self.assertEqual(recovered.returncode, 0, recovered.stderr)
        self.assertEqual(json.loads(recovered.stdout)["status"], "PASS")
        owner_path = lock_path.with_name(lock_path.name + ".owner.json")
        self.assertFalse(owner_path.exists())

    @unittest.skipUnless(os.name == "posix", "fork assertion is POSIX-specific")
    def test_detached_gate_descendant_does_not_inherit_machine_lock(self) -> None:
        first_root = self.make_repo()
        second_root = self.make_repo()
        lock_path = self.make_lock_path()
        helper_pid_path = first_root / "detached-helper.pid"
        detached_program = (
            "import os,pathlib,time; "
            "pid=os.fork(); "
            f"path=pathlib.Path({str(helper_pid_path)!r}); "
            "(os.setsid(),path.write_text(str(os.getpid())),time.sleep(30),"
            "os._exit(0)) if pid == 0 else os._exit(0)"
        )
        self.write_gate(
            first_root,
            "quality/gate.sh",
            f"exec {shlex.quote(sys.executable)} -c "
            f"{shlex.quote(detached_program)}\n",
        )
        self.write_gate(second_root, "quality/gate.sh")

        first = subprocess.run(
            self.runner_command(
                lock_path,
                "--repo",
                str(first_root),
                "--profile",
                "fast",
                "--json",
            ),
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
        self.assertEqual(first.returncode, 0, first.stderr)
        for _ in range(100):
            if helper_pid_path.is_file():
                break
            time.sleep(0.02)
        else:
            self.fail("detached gate descendant did not start")
        helper_pid = int(helper_pid_path.read_text(encoding="utf-8"))
        self.addCleanup(
            lambda: os.kill(helper_pid, signal.SIGKILL)
            if self._process_exists(helper_pid)
            else None
        )
        os.kill(helper_pid, 0)

        second = subprocess.run(
            self.runner_command(
                lock_path,
                "--repo",
                str(second_root),
                "--profile",
                "fast",
                "--json",
            ),
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(json.loads(second.stdout)["status"], "PASS")
        os.kill(helper_pid, signal.SIGKILL)

    @unittest.skipUnless(os.name == "posix", "flock assertion is POSIX-specific")
    def test_lock_is_held_until_interrupted_gate_cleanup_finishes(self) -> None:
        first_root = self.make_repo()
        second_root = self.make_repo()
        lock_path = self.make_lock_path()
        marker = first_root / "gate-started"
        ignoring_gate = (
            "import pathlib,signal,time; "
            "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
            f"pathlib.Path({str(marker)!r}).write_text('started'); "
            "time.sleep(30)"
        )
        self.write_gate(
            first_root,
            "quality/gate.sh",
            f"exec {shlex.quote(sys.executable)} -c "
            f"{shlex.quote(ignoring_gate)}\n",
        )
        self.write_gate(second_root, "quality/gate.sh")
        first = subprocess.Popen(
            self.runner_command(
                lock_path,
                "--repo",
                str(first_root),
                "--profile",
                "native",
                "--json",
            ),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.addCleanup(lambda: first.kill() if first.poll() is None else None)
        for _ in range(100):
            if marker.is_file():
                break
            time.sleep(0.02)
        else:
            self.fail("signal-resistant repository gate did not start")

        time.sleep(0.05)
        first.send_signal(signal.SIGTERM)
        time.sleep(0.05)
        during_cleanup = subprocess.run(
            self.runner_command(
                lock_path,
                "--repo",
                str(second_root),
                "--profile",
                "fast",
                "--json",
            ),
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
        self.assertEqual(
            during_cleanup.returncode,
            RUNNER.BUSY_EXIT_CODE,
            during_cleanup.stderr,
        )

        stdout, stderr = first.communicate(timeout=10)
        self.assertEqual(first.returncode, 143, stderr)
        self.assertEqual(json.loads(stdout)["status"], "INTERRUPTED")
        after_cleanup = subprocess.run(
            self.runner_command(
                lock_path,
                "--repo",
                str(second_root),
                "--profile",
                "fast",
                "--json",
            ),
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
        self.assertEqual(after_cleanup.returncode, 0, after_cleanup.stderr)
        self.assertEqual(json.loads(after_cleanup.stdout)["status"], "PASS")

    def test_unsupported_host_fails_closed_instead_of_running_unlocked(self) -> None:
        lock = RUNNER.MachineGateLock(
            self.make_lock_path(),
            root=self.make_repo(),
            profile="fast",
        )
        with mock.patch.object(RUNNER, "fcntl", None):
            with self.assertRaisesRegex(
                RUNNER.MachineLockUnavailable,
                "gate was not started unlocked",
            ):
                lock.acquire()

    def test_unsafe_lock_directory_and_symlink_fail_closed(self) -> None:
        lock_path = self.make_lock_path()
        os.chmod(lock_path.parent, 0o755)
        unsafe_directory_lock = RUNNER.MachineGateLock(
            lock_path,
            root=self.make_repo(),
            profile="fast",
        )
        with self.assertRaisesRegex(
            RUNNER.MachineLockUnavailable,
            "private 0700 directory",
        ):
            unsafe_directory_lock.acquire()

        os.chmod(lock_path.parent, 0o700)
        target = lock_path.parent / "other-inode"
        target.write_text("", encoding="utf-8")
        lock_path.symlink_to(target)
        symlink_lock = RUNNER.MachineGateLock(
            lock_path,
            root=self.make_repo(),
            profile="fast",
        )
        with self.assertRaisesRegex(
            RUNNER.MachineLockUnavailable,
            "cannot open machine lock",
        ):
            symlink_lock.acquire()

    def test_malformed_or_oversized_owner_metadata_is_unknown(self) -> None:
        lock_path = self.make_lock_path()
        lock = RUNNER.MachineGateLock(
            lock_path,
            root=self.make_repo(),
            profile="fast",
        )
        lock.owner_path.write_bytes(b"\xff")
        self.assertEqual(lock._read_owner(), {})
        lock.owner_path.write_bytes(b"x" * (RUNNER.MAX_OWNER_METADATA_BYTES + 1))
        self.assertEqual(lock._read_owner(), {})

    @unittest.skipUnless(os.name == "posix", "process-group assertion is POSIX-specific")
    def test_timeout_kills_descendant_and_reports_124(self) -> None:
        root = self.make_repo()
        child_pid = root / "child.pid"
        child_program = (
            "import os,signal,time; "
            "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
            f"open({str(child_pid)!r}, 'w').write(str(os.getpid())); "
            "time.sleep(30)"
        )
        self.write_gate(
            root,
            "quality/gate.sh",
            f"{shlex.quote(sys.executable)} -c {shlex.quote(child_program)} &\nwait\n",
        )
        result = subprocess.run(
            self.runner_command(
                self.make_lock_path(),
                "--repo",
                str(root),
                "--profile",
                "fast",
                "--timeout",
                "1",
                "--json",
            ),
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 124, result.stderr)
        summary = json.loads(result.stdout)
        self.assertEqual(summary["status"], "TIMEOUT")
        self.assertTrue(Path(summary["log"]).is_file())
        pid = int(child_pid.read_text(encoding="utf-8"))
        for _ in range(20):
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.05)
        else:
            self.fail(f"descendant process {pid} survived timeout")

    @unittest.skipUnless(os.name == "posix", "signal assertion is POSIX-specific")
    def test_interrupt_kills_descendant_and_reports_130(self) -> None:
        self.assert_signal_kills_descendant(signal.SIGINT, 130)

    @unittest.skipUnless(os.name == "posix", "signal assertion is POSIX-specific")
    def test_sigterm_kills_descendant_and_reports_143(self) -> None:
        self.assert_signal_kills_descendant(signal.SIGTERM, 143)

    @unittest.skipUnless(
        os.name == "posix" and hasattr(signal, "SIGHUP"),
        "SIGHUP assertion is POSIX-specific",
    )
    def test_sighup_kills_descendant_and_reports_129(self) -> None:
        self.assert_signal_kills_descendant(signal.SIGHUP, 129)


class SkillDocumentationTests(unittest.TestCase):
    def test_examples_invoke_the_runner_executable_directly(self) -> None:
        expected = "<absolute-skill-root>/scripts/run_quality_gate.py \\\n"
        interpreter_prefix = (
            r"python(?:3)?\s+[^\n]*/scripts/run_quality_gate\.py"
        )

        for path in (REPOSITORY_ROOT / "README.md", SKILL_ROOT / "SKILL.md"):
            with self.subTest(path=path):
                content = path.read_text(encoding="utf-8")
                self.assertIn(expected, content)
                self.assertNotRegex(content, interpreter_prefix)

    @unittest.skipUnless(os.name == "posix", "executable mode is POSIX-specific")
    def test_documented_runner_is_executable(self) -> None:
        self.assertTrue(os.access(SCRIPT, os.X_OK))


if __name__ == "__main__":
    unittest.main()
