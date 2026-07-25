#!/usr/bin/env python3
"""Hold the machine lease while supervising one repository quality gate."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import select
import signal
import stat
import subprocess
import sys


TRUNCATION_MARKER = b"\n...[output truncated; kept beginning and end]...\n"


def positive_integer(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return parsed


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--lock-fd", required=True, type=int)
    parser.add_argument("--start-fd", required=True, type=int)
    parser.add_argument("--log-path", required=True)
    parser.add_argument("--max-log-bytes", required=True, type=positive_integer)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.command[:1] == ["--"]:
        args.command = args.command[1:]
    if not args.command:
        parser.error("missing repository gate command")
    return args


def retain_lease(_signum: int, _frame: object) -> None:
    """Let the gate exit, or let the runner kill the whole process group."""


def open_safe_log(path: Path) -> int:
    flags = os.O_WRONLY | os.O_TRUNC
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        descriptor_status = os.fstat(descriptor)
        path_status = path.lstat()
        effective_uid = os.geteuid()
        if (
            not stat.S_ISREG(descriptor_status.st_mode)
            or descriptor_status.st_uid != effective_uid
            or descriptor_status.st_nlink != 1
            or stat.S_IMODE(descriptor_status.st_mode) != 0o600
            or (descriptor_status.st_dev, descriptor_status.st_ino)
            != (path_status.st_dev, path_status.st_ino)
        ):
            raise OSError(
                "log must be one private regular, non-symlink inode "
                f"owned by euid {effective_uid}: {path}"
            )
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def write_all(descriptor: int, content: bytes) -> None:
    remaining = memoryview(content)
    while remaining:
        written = os.write(descriptor, remaining)
        remaining = remaining[written:]


def capture_bounded(
    gate: subprocess.Popen[bytes],
    descriptor: int,
    max_bytes: int,
) -> None:
    if gate.stdout is None:
        raise OSError("repository gate output pipe was not created")

    marker = TRUNCATION_MARKER
    if max_bytes <= len(marker):
        marker = marker[:max_bytes]
    payload_limit = max_bytes - len(marker)
    head_limit = payload_limit // 2
    tail_limit = payload_limit - head_limit
    head_written = 0
    tail = bytearray()
    total = 0

    def retain(chunk: bytes) -> None:
        nonlocal head_written, total
        total += len(chunk)
        head_part = min(len(chunk), head_limit - head_written)
        if head_part > 0:
            write_all(descriptor, chunk[:head_part])
            head_written += head_part
        remainder = chunk[head_part:]
        if tail_limit and remainder:
            tail.extend(remainder)
            if len(tail) > tail_limit:
                del tail[: len(tail) - tail_limit]

    output_fd = gate.stdout.fileno()
    os.set_blocking(output_fd, False)
    output_closed = False
    while True:
        if output_closed:
            gate.wait()
            break

        direct_gate_finished = gate.poll() is not None
        readable, _, _ = select.select(
            [output_fd],
            [],
            [],
            0 if direct_gate_finished else 0.1,
        )
        if readable:
            while True:
                try:
                    chunk = os.read(output_fd, 64 * 1024)
                except BlockingIOError:
                    break
                if not chunk:
                    output_closed = True
                    break
                retain(chunk)

        # A deliberately detached descendant may keep the pipe open after the
        # repository gate exits. Drain bytes already available, then close our
        # read end rather than keeping the machine lease for that descendant.
        if direct_gate_finished:
            break

    truncated = total > payload_limit
    if truncated:
        write_all(descriptor, marker)
    write_all(descriptor, tail)
    os.ftruncate(descriptor, min(total if not truncated else max_bytes, max_bytes))


def main() -> int:
    args = parse_args()
    try:
        os.fstat(args.lock_fd)
        os.fstat(args.start_fd)
        os.set_inheritable(args.lock_fd, False)
        os.set_inheritable(args.start_fd, False)
    except OSError as error:
        print(f"quality-gate guard has an invalid control fd: {error}", file=sys.stderr)
        return 125

    try:
        start_token = os.read(args.start_fd, 1)
    except OSError as error:
        print(f"quality-gate guard could not read startup token: {error}", file=sys.stderr)
        return 125
    finally:
        os.close(args.start_fd)
    if start_token != b"1":
        return 125

    handled_signals = tuple(
        signum
        for signum in (
            signal.SIGINT,
            signal.SIGTERM,
            getattr(signal, "SIGHUP", None),
        )
        if signum is not None
    )
    for signum in handled_signals:
        signal.signal(signum, retain_lease)
    signal.pthread_sigmask(signal.SIG_UNBLOCK, handled_signals)

    try:
        log_descriptor = open_safe_log(Path(args.log_path))
    except OSError as error:
        print(f"quality-gate guard could not open bounded log: {error}", file=sys.stderr)
        return 125

    try:
        gate = subprocess.Popen(
            args.command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            close_fds=True,
        )
    except OSError as error:
        write_all(
            log_descriptor,
            f"failed to start repository quality gate: {error}\n".encode(
                "utf-8",
                errors="replace",
            )[: args.max_log_bytes],
        )
        os.close(log_descriptor)
        return 127

    try:
        capture_bounded(gate, log_descriptor, args.max_log_bytes)
    finally:
        gate.stdout.close() if gate.stdout is not None else None
        os.close(log_descriptor)
    return_code = gate.wait()
    return return_code if return_code >= 0 else 128 - return_code


if __name__ == "__main__":
    raise SystemExit(main())
