#!/usr/bin/env python3
"""Run a repository-owned quality gate while keeping successful output terse."""

from __future__ import annotations

import argparse
from collections import deque
from datetime import datetime, timezone
import errno
import json
import os
from pathlib import Path
import re
import secrets
import signal
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from typing import TextIO

try:
    import fcntl
except ImportError:  # pragma: no cover - exercised only on non-POSIX hosts.
    fcntl = None


CANDIDATES = (
    "quality/gate",
    "quality/gate.sh",
    "quality/gate.mjs",
    "quality/gate.py",
    "quality/gate.ps1",
)
BUSY_EXIT_CODE = 75
MAX_OWNER_METADATA_BYTES = 16_384
MAX_LOG_BYTES = 8 * 1024 * 1024
MAX_DIAGNOSTIC_BYTES = 32 * 1024
DEFAULT_PREVIOUS_RUNS = 1
TERMINATION_GRACE_SECONDS = 2.0
GUARD_SCRIPT = Path(__file__).with_name("_quality_gate_guard.py")
RUN_LOG_NAME = re.compile(
    r"^run-\d{8}T\d{6}\.\d{6}Z-\d+-[0-9a-f]{8}$"
)


class MachineLockBusy(Exception):
    """Raised when another universal quality-gate runner owns the machine lock."""

    def __init__(self, owner: dict[str, object]) -> None:
        super().__init__("another quality gate is already running")
        self.owner = owner


class MachineLockUnavailable(Exception):
    """Raised when this host cannot provide the required kernel-held lock."""


class RunnerLogUnavailable(Exception):
    """Raised when bounded runner-log storage cannot be used safely."""


class MachineGateLock:
    """A non-waiting, kernel-held lock shared by this user's runner processes."""

    def __init__(
        self,
        path: Path,
        *,
        root: Path,
        profile: str,
    ) -> None:
        self.path = path
        self.owner_path = path.with_name(path.name + ".owner.json")
        self.owner = {
            "repo": str(root),
            "pid": os.getpid(),
            "profile": profile,
            "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        self._stream: TextIO | None = None

    def acquire(self) -> None:
        if (
            os.name != "posix"
            or fcntl is None
            or not hasattr(os, "geteuid")
        ):
            raise MachineLockUnavailable(
                "a POSIX fcntl.flock implementation is required; "
                "the gate was not started unlocked"
            )

        descriptor: int | None = None
        try:
            self.path.parent.mkdir(mode=0o700, exist_ok=True)
            directory_status = self.path.parent.lstat()
            if (
                not stat.S_ISDIR(directory_status.st_mode)
                or directory_status.st_uid != os.geteuid()
                or stat.S_IMODE(directory_status.st_mode) != 0o700
            ):
                raise MachineLockUnavailable(
                    "machine-lock directory must be a private 0700 directory "
                    f"owned by euid {os.geteuid()}: {self.path.parent}"
                )

            flags = os.O_RDWR | os.O_CREAT
            flags |= getattr(os, "O_CLOEXEC", 0)
            flags |= getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(self.path, flags, 0o600)
            lock_status = os.fstat(descriptor)
            path_status = self.path.lstat()
            if (
                not stat.S_ISREG(lock_status.st_mode)
                or lock_status.st_uid != os.geteuid()
                or lock_status.st_nlink != 1
                or (lock_status.st_dev, lock_status.st_ino)
                != (path_status.st_dev, path_status.st_ino)
            ):
                os.close(descriptor)
                descriptor = None
                raise MachineLockUnavailable(
                    "machine lock must be one regular, non-symlink inode owned "
                    f"by euid {os.geteuid()}: {self.path}"
                )
            os.fchmod(descriptor, 0o600)
            stream = os.fdopen(descriptor, "r+", encoding="utf-8")
            descriptor = None
        except MachineLockUnavailable:
            if descriptor is not None:
                os.close(descriptor)
            raise
        except OSError as error:
            if descriptor is not None:
                os.close(descriptor)
            raise MachineLockUnavailable(
                f"cannot open machine lock {self.path}: {error}"
            ) from error

        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            stream.close()
            if error.errno in (errno.EACCES, errno.EAGAIN):
                raise MachineLockBusy(self._read_owner()) from error
            raise MachineLockUnavailable(
                f"cannot acquire machine lock {self.path}: {error}"
            ) from error

        self._stream = stream
        try:
            self._write_owner()
        except OSError as error:
            self.release()
            raise MachineLockUnavailable(
                f"cannot write machine-lock owner metadata "
                f"{self.owner_path}: {error}"
            ) from error

    def _read_owner(self) -> dict[str, object]:
        try:
            with self.owner_path.open("rb") as stream:
                encoded = stream.read(MAX_OWNER_METADATA_BYTES + 1)
            if len(encoded) > MAX_OWNER_METADATA_BYTES:
                return {}
            value = json.loads(encoded.decode("utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}

    def _write_owner(self) -> None:
        descriptor, temporary_name = tempfile.mkstemp(
            dir=self.owner_path.parent,
            prefix=f".{self.owner_path.name}.",
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                os.fchmod(stream.fileno(), 0o600)
                json.dump(self.owner, stream, sort_keys=True)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_path, self.owner_path)
        finally:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass

    def record_guard(self, pid: int) -> None:
        self.owner["guard_pid"] = pid
        self._write_owner()

    def release(self) -> None:
        if self._stream is None:
            return
        try:
            try:
                self.owner_path.unlink()
            except FileNotFoundError:
                pass
        finally:
            stream = self._stream
            self._stream = None
            stream.close()

    def fileno(self) -> int:
        if self._stream is None:
            raise MachineLockUnavailable("machine lock has not been acquired")
        return self._stream.fileno()

    def __enter__(self) -> MachineGateLock:
        self.acquire()
        return self

    def __exit__(self, *unused: object) -> None:
        self.release()


class TerminationSignal(Exception):
    """Raised when the runner must stop and clean up its child process group."""

    def __init__(self, signum: int) -> None:
        super().__init__(signum)
        self.signum = signum


def nonnegative_integer(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be zero or greater")
    return parsed


def positive_integer(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return parsed


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=".", help="Repository path")
    parser.add_argument(
        "--profile",
        default="auto",
        choices=("auto", "fast", "full", "native"),
    )
    parser.add_argument("--base", help="Task base git ref")
    parser.add_argument("--timeout", type=positive_integer, default=3600)
    parser.add_argument("--show-lines", type=nonnegative_integer, default=20)
    parser.add_argument(
        "--keep-previous-runs",
        type=nonnegative_integer,
        help="Ask a supporting repository gate to retain this many earlier runs",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true", dest="json_output")
    return parser.parse_args()


def git_root(path: Path) -> Path:
    result = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "--show-toplevel"],
        check=True,
        capture_output=True,
        text=True,
    )
    return Path(result.stdout.strip()).resolve()


def discover(root: Path) -> Path:
    matches = [
        root / relative
        for relative in CANDIDATES
        if (root / relative).is_file()
    ]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        relative_matches = ", ".join(str(path.relative_to(root)) for path in matches)
        raise ValueError(
            "multiple quality gates found; keep exactly one supported entrypoint: "
            + relative_matches
        )
    raise FileNotFoundError(
        "no quality gate found; expected one of: " + ", ".join(CANDIDATES)
    )


def command_for(entrypoint: Path, root: Path, args: argparse.Namespace) -> list[str]:
    relative = entrypoint.relative_to(root)
    if entrypoint.suffix == ".mjs":
        command = ["node", str(relative), args.profile]
    elif entrypoint.suffix == ".sh":
        command = ["bash", str(relative), args.profile]
    elif entrypoint.suffix == ".py":
        command = [sys.executable, str(relative), args.profile]
    elif entrypoint.suffix == ".ps1":
        command = ["pwsh", "-File", str(relative), args.profile]
    else:
        command = [str(relative), args.profile]
    if args.base:
        command.extend(["--base", args.base])
    if args.keep_previous_runs is not None:
        command.extend(["--keep-previous-runs", str(args.keep_previous_runs)])
    return command


def tail_file(path: Path, count: int) -> list[str]:
    if count <= 0:
        return []
    with path.open("rb") as stream:
        stream.seek(0, os.SEEK_END)
        size = stream.tell()
        read_size = min(size, MAX_DIAGNOSTIC_BYTES)
        stream.seek(size - read_size)
        encoded = stream.read(read_size)
    lines: deque[str] = deque(maxlen=count)
    for line in encoded.decode("utf-8", errors="replace").splitlines():
        stripped = line.rstrip()
        if stripped:
            lines.append(stripped)
    selected = list(lines)
    if size > read_size and selected:
        selected[0] = "[earlier diagnostic bytes omitted] " + selected[0]
    return selected


def terminate_process_tree(process: subprocess.Popen[object]) -> None:
    if os.name == "posix":
        process_group = process.pid
        try:
            os.killpg(process_group, signal.SIGTERM)
        except ProcessLookupError:
            if process.poll() is None:
                process.wait()
            return
        deadline = time.monotonic() + TERMINATION_GRACE_SECONDS
        while time.monotonic() < deadline:
            process.poll()
            try:
                os.killpg(process_group, 0)
            except ProcessLookupError:
                break
            time.sleep(0.05)
        else:
            try:
                os.killpg(process_group, signal.SIGKILL)
            except ProcessLookupError:
                pass
    elif os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            check=False,
            capture_output=True,
        )
    else:
        process.kill()
    if process.poll() is None:
        process.wait()


def emit(summary: dict[str, object], as_json: bool) -> None:
    if as_json:
        print(json.dumps(summary, sort_keys=True))
        return
    status = summary["status"]
    print(
        f"QUALITY_GATE {status} profile={summary['profile']} "
        f"exit={summary['exit_code']} elapsed={summary['elapsed_seconds']}s "
        f"log={summary['log']}"
    )
    for line in summary.get("diagnostics", []):
        print(line)


def default_machine_lock_path() -> Path:
    user_identifier = os.geteuid() if hasattr(os, "geteuid") else "unsupported"
    return Path("/tmp") / f"agentic-quality-loop-{user_identifier}" / "machine-gate.lock"


def busy_diagnostic(owner: dict[str, object], lock_path: Path) -> str:
    return (
        "machine-wide quality gate busy: "
        f"owner_repo={owner.get('repo', 'unknown')} "
        f"owner_pid={owner.get('pid', 'unknown')} "
        f"owner_guard_pid={owner.get('guard_pid', 'unknown')} "
        f"owner_profile={owner.get('profile', 'unknown')} "
        f"owner_started={owner.get('started_at', 'unknown')} "
        f"lock={lock_path}"
    )


class RunnerLogStore:
    """Create and prune private, byte-bounded universal-runner logs."""

    def __init__(self, lock_parent: Path, keep_previous_runs: int) -> None:
        self.root = lock_parent / "runner-logs"
        self.keep_previous_runs = keep_previous_runs
        self.run_root: Path | None = None
        self.log_path: Path | None = None

    @staticmethod
    def _effective_uid() -> int:
        if not hasattr(os, "geteuid"):
            raise RunnerLogUnavailable(
                "a POSIX effective user id is required for safe log retention"
            )
        return os.geteuid()

    @classmethod
    def _validate_private_directory(
        cls,
        path: Path,
        *,
        direct_parent: Path | None = None,
    ) -> None:
        try:
            status = path.lstat()
        except OSError as error:
            raise RunnerLogUnavailable(
                f"cannot inspect runner-log directory {path}: {error}"
            ) from error
        if (
            not stat.S_ISDIR(status.st_mode)
            or status.st_uid != cls._effective_uid()
            or stat.S_IMODE(status.st_mode) != 0o700
            or (direct_parent is not None and path.parent != direct_parent)
        ):
            raise RunnerLogUnavailable(
                "runner-log directories must be direct, non-symlink 0700 "
                f"directories owned by euid {cls._effective_uid()}: {path}"
            )

    @classmethod
    def _create_private_directory(
        cls,
        path: Path,
        *,
        direct_parent: Path | None = None,
        exist_ok: bool,
    ) -> None:
        try:
            path.mkdir(mode=0o700, exist_ok=exist_ok)
        except OSError as error:
            raise RunnerLogUnavailable(
                f"cannot create runner-log directory {path}: {error}"
            ) from error
        cls._validate_private_directory(path, direct_parent=direct_parent)

    @classmethod
    def _create_private_file(cls, path: Path) -> None:
        descriptor: int | None = None
        try:
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            flags |= getattr(os, "O_CLOEXEC", 0)
            flags |= getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(path, flags, 0o600)
            descriptor_status = os.fstat(descriptor)
            path_status = path.lstat()
            if (
                not stat.S_ISREG(descriptor_status.st_mode)
                or descriptor_status.st_uid != cls._effective_uid()
                or descriptor_status.st_nlink != 1
                or (descriptor_status.st_dev, descriptor_status.st_ino)
                != (path_status.st_dev, path_status.st_ino)
            ):
                raise RunnerLogUnavailable(
                    "runner-log files must be one regular, non-symlink inode "
                    f"owned by euid {cls._effective_uid()}: {path}"
                )
            os.fchmod(descriptor, 0o600)
        except RunnerLogUnavailable:
            raise
        except OSError as error:
            raise RunnerLogUnavailable(
                f"cannot create runner-log file {path}: {error}"
            ) from error
        finally:
            if descriptor is not None:
                os.close(descriptor)

    def _candidates(self) -> list[Path]:
        candidates: list[Path] = []
        try:
            entries = list(self.root.iterdir())
        except OSError as error:
            raise RunnerLogUnavailable(
                f"cannot enumerate runner logs under {self.root}: {error}"
            ) from error
        for path in entries:
            if not RUN_LOG_NAME.fullmatch(path.name):
                continue
            self._validate_private_directory(path, direct_parent=self.root)
            candidates.append(path)
        return candidates

    @classmethod
    def _has_completion_marker(cls, run_root: Path) -> bool:
        marker = run_root / ".complete"
        try:
            status = marker.lstat()
        except FileNotFoundError:
            return False
        except OSError as error:
            raise RunnerLogUnavailable(
                f"cannot inspect runner-log completion marker {marker}: {error}"
            ) from error
        if (
            not stat.S_ISREG(status.st_mode)
            or status.st_uid != cls._effective_uid()
            or status.st_nlink != 1
            or stat.S_IMODE(status.st_mode) != 0o600
        ):
            raise RunnerLogUnavailable(
                "runner-log completion markers must be private regular files "
                f"owned by euid {cls._effective_uid()}: {marker}"
            )
        return True

    def _remove_candidate(self, path: Path) -> None:
        self._validate_private_directory(path, direct_parent=self.root)
        try:
            shutil.rmtree(path)
        except OSError as error:
            raise RunnerLogUnavailable(
                f"cannot prune runner-log directory {path}: {error}"
            ) from error

    def begin(self) -> Path:
        self._create_private_directory(
            self.root,
            direct_parent=self.root.parent,
            exist_ok=True,
        )
        # The caller already holds the machine lock, so no guard can still be
        # writing an incomplete run. Remove crash remnants before allocating
        # the next one; repeated uncatchable runner exits stay bounded.
        for path in self._candidates():
            if not self._has_completion_marker(path):
                self._remove_candidate(path)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        for _ in range(10):
            name = f"run-{timestamp}-{os.getpid()}-{secrets.token_hex(4)}"
            candidate = self.root / name
            try:
                self._create_private_directory(
                    candidate,
                    direct_parent=self.root,
                    exist_ok=False,
                )
            except RunnerLogUnavailable as error:
                if candidate.exists():
                    continue
                raise error
            self.run_root = candidate
            self.log_path = candidate / "gate.log"
            self._create_private_file(self.log_path)
            return self.log_path
        raise RunnerLogUnavailable("could not allocate a unique runner-log directory")

    def complete_and_prune(self) -> None:
        if self.run_root is None:
            raise RunnerLogUnavailable("runner-log storage was not started")
        marker = self.run_root / ".complete"
        self._create_private_file(marker)

        candidates = self._candidates()
        completed = sorted(
            (
                path
                for path in candidates
                if path != self.run_root and self._has_completion_marker(path)
            ),
            key=lambda path: path.name,
            reverse=True,
        )
        retained = {self.run_root}
        retained.update(completed[: self.keep_previous_runs])
        for path in candidates:
            if path in retained:
                continue
            self._remove_candidate(path)


def run_locked_gate(
    args: argparse.Namespace,
    root: Path,
    entrypoint: Path,
    command: list[str],
    machine_lock: MachineGateLock,
    log_store: RunnerLogStore,
    log_path: Path,
) -> int:
    started = time.monotonic()
    environment = os.environ.copy()
    environment["AGENTIC_QUALITY_GATE"] = "1"
    previous_signal_handlers: dict[int, object] = {}
    handled_signals = tuple(
        signum
        for signum in (
            signal.SIGINT,
            signal.SIGTERM,
            getattr(signal, "SIGHUP", None),
        )
        if signum is not None
    )

    def interrupt_for_signal(signum: int, _frame: object) -> None:
        raise TerminationSignal(signum)

    def ignore_cleanup_signals() -> None:
        for signum in handled_signals:
            signal.signal(signum, signal.SIG_IGN)

    synthetic_diagnostics: list[str] = []
    process: subprocess.Popen[bytes] | None = None
    previous_mask: set[signal.Signals] | None = None
    start_read_fd: int | None = None
    start_write_fd: int | None = None
    try:
        if not hasattr(signal, "pthread_sigmask"):
            raise OSError(
                "POSIX signal masking is unavailable; gate was not started"
            )
        previous_mask = signal.pthread_sigmask(
            signal.SIG_BLOCK,
            handled_signals,
        )
        try:
            for signum in handled_signals:
                previous_signal_handlers[signum] = signal.getsignal(signum)
                signal.signal(signum, interrupt_for_signal)
            lock_fd = machine_lock.fileno()
            start_read_fd, start_write_fd = os.pipe()
            try:
                process = subprocess.Popen(
                    [
                        sys.executable,
                        str(GUARD_SCRIPT),
                        "--lock-fd",
                        str(lock_fd),
                        "--start-fd",
                        str(start_read_fd),
                        "--log-path",
                        str(log_path),
                        "--max-log-bytes",
                        str(MAX_LOG_BYTES),
                        "--",
                        *command,
                    ],
                    cwd=root,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    env=environment,
                    start_new_session=True,
                    pass_fds=(lock_fd, start_read_fd),
                )
                machine_lock.record_guard(process.pid)
                os.write(start_write_fd, b"1")
            finally:
                for descriptor in (start_read_fd, start_write_fd):
                    if descriptor is not None:
                        try:
                            os.close(descriptor)
                        except OSError:
                            pass
                start_read_fd = None
                start_write_fd = None
        finally:
            if previous_mask is not None:
                signal.pthread_sigmask(signal.SIG_SETMASK, previous_mask)

        exit_code = process.wait(timeout=args.timeout)
        status = "PASS" if exit_code == 0 else "FAIL"
    except OSError as error:
        if process is not None and process.poll() is None:
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                terminate_process_tree(process)
        synthetic_diagnostics.append(f"failed to start quality gate: {error}")
        exit_code = 127
        status = "ERROR"
    except subprocess.TimeoutExpired:
        ignore_cleanup_signals()
        if process is not None:
            terminate_process_tree(process)
        exit_code = 124
        status = "TIMEOUT"
        synthetic_diagnostics.append(f"gate timed out after {args.timeout}s")
    except KeyboardInterrupt:
        ignore_cleanup_signals()
        if process is not None:
            terminate_process_tree(process)
        exit_code = 130
        status = "INTERRUPTED"
        synthetic_diagnostics.append(
            "gate interrupted; child process tree terminated"
        )
    except TerminationSignal as interruption:
        ignore_cleanup_signals()
        if process is not None:
            terminate_process_tree(process)
        exit_code = 128 + interruption.signum
        status = "INTERRUPTED"
        signal_name = signal.Signals(interruption.signum).name
        synthetic_diagnostics.append(
            f"gate interrupted by {signal_name}; child process tree terminated"
        )
    finally:
        for signum, previous_handler in previous_signal_handlers.items():
            signal.signal(signum, previous_handler)
    diagnostics = [] if exit_code == 0 else tail_file(log_path, args.show_lines)
    diagnostics = synthetic_diagnostics + [
        line for line in diagnostics if line not in synthetic_diagnostics
    ]
    if status == "TIMEOUT":
        timeout_line = f"gate timed out after {args.timeout}s"
        diagnostics = [timeout_line] + [
            line for line in diagnostics if line != timeout_line
        ]

    try:
        log_store.complete_and_prune()
    except RunnerLogUnavailable as error:
        exit_code = 2
        status = "ERROR"
        diagnostics = [f"runner-log retention failed: {error}"] + diagnostics

    elapsed = round(time.monotonic() - started, 2)
    emit(
        {
            "status": status,
            "profile": args.profile,
            "exit_code": exit_code,
            "elapsed_seconds": elapsed,
            "log": str(log_path),
            "command": command,
            "entrypoint": str(entrypoint),
            "diagnostics": diagnostics,
        },
        args.json_output,
    )
    return exit_code


def main() -> int:
    args = parse_args()
    try:
        root = git_root(Path(args.repo).resolve())
        entrypoint = discover(root)
        command = command_for(entrypoint, root, args)
    except (subprocess.CalledProcessError, OSError, ValueError) as error:
        print(f"QUALITY_GATE ERROR {error}", file=sys.stderr)
        return 2

    if args.dry_run:
        emit(
            {
                "status": "DRY_RUN",
                "profile": args.profile,
                "exit_code": 0,
                "elapsed_seconds": 0.0,
                "log": "",
                "command": command,
                "entrypoint": str(entrypoint),
            },
            args.json_output,
        )
        return 0

    selected_lock_path = default_machine_lock_path()
    machine_lock = MachineGateLock(
        selected_lock_path,
        root=root,
        profile=args.profile,
    )
    lock_started = time.monotonic()
    try:
        machine_lock.acquire()
    except MachineLockBusy as busy:
        emit(
            {
                "status": "BUSY",
                "profile": args.profile,
                "exit_code": BUSY_EXIT_CODE,
                "elapsed_seconds": round(time.monotonic() - lock_started, 2),
                "log": "",
                "command": command,
                "entrypoint": str(entrypoint),
                "lock": str(selected_lock_path),
                "owner": busy.owner,
                "diagnostics": [
                    busy_diagnostic(busy.owner, selected_lock_path),
                ],
            },
            args.json_output,
        )
        return BUSY_EXIT_CODE
    except MachineLockUnavailable as error:
        emit(
            {
                "status": "ERROR",
                "profile": args.profile,
                "exit_code": 2,
                "elapsed_seconds": round(time.monotonic() - lock_started, 2),
                "log": "",
                "command": command,
                "entrypoint": str(entrypoint),
                "lock": str(selected_lock_path),
                "diagnostics": [f"machine-wide quality gate lock unavailable: {error}"],
            },
            args.json_output,
        )
        return 2

    try:
        keep_previous_runs = (
            args.keep_previous_runs
            if args.keep_previous_runs is not None
            else DEFAULT_PREVIOUS_RUNS
        )
        log_store = RunnerLogStore(
            selected_lock_path.parent,
            keep_previous_runs,
        )
        try:
            log_path = log_store.begin()
        except RunnerLogUnavailable as error:
            emit(
                {
                    "status": "ERROR",
                    "profile": args.profile,
                    "exit_code": 2,
                    "elapsed_seconds": round(
                        time.monotonic() - lock_started,
                        2,
                    ),
                    "log": "",
                    "command": command,
                    "entrypoint": str(entrypoint),
                    "lock": str(selected_lock_path),
                    "diagnostics": [
                        f"runner-log storage unavailable: {error}",
                    ],
                },
                args.json_output,
            )
            return 2
        return run_locked_gate(
            args,
            root,
            entrypoint,
            command,
            machine_lock,
            log_store,
            log_path,
        )
    finally:
        machine_lock.release()


if __name__ == "__main__":
    raise SystemExit(main())
