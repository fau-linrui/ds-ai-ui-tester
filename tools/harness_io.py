"""Small, vendor-neutral process and checkpoint primitives for the harness."""
from __future__ import annotations

import contextlib
import hashlib
import json
import math
import os
import signal
import subprocess
import time
from pathlib import Path
from threading import Event


class HarnessError(ValueError):
    pass


class Cancelled(Exception):
    pass


def json_text(value) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + "\n"


def parse_json(text: str):
    def constant(value):
        raise HarnessError(f"non-finite JSON number: {value}")

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise HarnessError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    def number(value):
        result = float(value)
        if not math.isfinite(result):
            raise HarnessError("JSON number overflow")
        return result

    try:
        return json.loads(text, parse_constant=constant, parse_float=number, object_pairs_hook=pairs)
    except (ValueError, TypeError, RecursionError) as exc:
        raise HarnessError(f"invalid JSON: {exc}") from exc


def read_json(path: Path):
    return parse_json(path.read_text(encoding="utf-8"))


def atomic_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(json_text(value))
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inside(root: Path, value: str) -> Path:
    path = (root / value).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError as exc:
        raise HarnessError(f"path escapes project/run directory: {value}") from exc
    return path


@contextlib.contextmanager
def run_lock(directory: Path):
    """OS-owned lock: a crashed process does not leave an unbreakable lease."""
    with (directory / "run.lock").open("a+b") as handle:
        try:
            handle.seek(0)
            if not handle.read(1):
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise HarnessError("this run is already owned by another scheduler") from exc
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def terminate_tree(process) -> None:
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False, timeout=10,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    if process.poll() is None:
        process.kill()
    process.wait()


def run_process(command: list[str], *, cwd: Path, env: dict, request: Path,
                prefix: Path, timeout: float, limit: int, stop: Event) -> dict:
    """File-backed streams avoid pipe deadlocks; poll bounds output and wall time."""
    stdout, stderr = prefix.with_suffix(".stdout"), prefix.with_suffix(".stderr")
    started = time.monotonic()
    reason = None
    process = None
    flags = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
    try:
        with request.open("rb") as stdin, stdout.open("wb") as out, stderr.open("wb") as err:
            if stop.is_set():
                raise Cancelled()
            process = subprocess.Popen(command, cwd=cwd, env=env, stdin=stdin, stdout=out, stderr=err, shell=False, **flags)
            while process.poll() is None:
                if stop.is_set():
                    reason = "cancelled"
                elif time.monotonic() - started > timeout:
                    reason = "timeout"
                elif stdout.stat().st_size + stderr.stat().st_size > limit:
                    reason = "output_limit"
                if reason:
                    terminate_tree(process)
                    break
                stop.wait(0.05)
            if stdout.stat().st_size + stderr.stat().st_size > limit:
                reason = "output_limit"
            return {"returncode": process.returncode, "reason": reason, "seconds": round(time.monotonic() - started, 3)}
    except OSError as exc:
        return {"returncode": None, "reason": f"launch_error: {exc}", "seconds": round(time.monotonic() - started, 3)}
    finally:
        if process is not None and process.poll() is None:
            terminate_tree(process)


def agent_result(raw: str, protocol: str):
    if protocol == "json":
        return parse_json(raw)
    # OpenCode --format json is JSONL, not a single assistant JSON document.
    parts = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        event = parse_json(line)
        if not isinstance(event, dict):
            raise HarnessError("OpenCode event must be an object")
        if event.get("type") == "error":
            raise HarnessError(f"OpenCode returned an error: {event.get('error')}")
        if event.get("type") == "text":
            part = event.get("part", {})
            if not isinstance(part, dict) or part.get("synthetic"):
                raise HarnessError("unexpected OpenCode synthetic/text event")
            value = part.get("text")
            if not isinstance(value, str):
                raise HarnessError("OpenCode text event has no part.text")
            parts.append(value)
    if not parts:
        raise HarnessError("OpenCode exited without assistant text")
    return parse_json("".join(parts))
