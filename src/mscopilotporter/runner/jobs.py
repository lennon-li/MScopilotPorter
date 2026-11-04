import os
import time
import json
import sqlite3
import signal
import secrets
import threading
import subprocess
import logging
from typing import Optional, Any
from mscopilotporter import paths
from mscopilotporter.runner.sandbox import build_bwrap_args, sandbox_host_env

logger = logging.getLogger("mscopilotporter.jobs")

DB_PATH = str(paths.jobs_db())
RUNTIME_JOBS_DIR = str(paths.jobs_dir())

def get_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    os.makedirs(RUNTIME_JOBS_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

import re

def init_db():
    conn = get_db()
    with conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS jobs (
                job_id TEXT PRIMARY KEY,
                workspace_id TEXT,
                argv TEXT,
                cwd TEXT,
                pid INTEGER,
                pgid INTEGER,
                status TEXT,
                exit_code INTEGER,
                created_at REAL,
                started_at REAL,
                finished_at REAL,
                duration REAL,
                stdout_path TEXT,
                stderr_path TEXT,
                agent TEXT,
                provider TEXT,
                model TEXT,
                requested_model TEXT,
                actual_model TEXT,
                reasoning_level TEXT,
                cli_version TEXT
            )
        """)
        # Schema migration for existing tables
        cursor = conn.execute("PRAGMA table_info(jobs)")
        existing = {row[1] for row in cursor.fetchall()}
        for col in ["requested_model", "actual_model", "reasoning_level", "cli_version"]:
            if col not in existing:
                conn.execute(f"ALTER TABLE jobs ADD COLUMN {col} TEXT")
    conn.close()

init_db()

def extract_actual_model(stdout_path: str, stderr_path: str) -> str:
    """Extract actual model only if explicitly reported by CLI output. Otherwise 'unknown'."""
    try:
        combined = ""
        if os.path.exists(stdout_path):
            with open(stdout_path, "r", encoding="utf-8", errors="replace") as f:
                combined += f.read()
        if os.path.exists(stderr_path):
            with open(stderr_path, "r", encoding="utf-8", errors="replace") as f:
                combined += "\n" + f.read()

        for line in combined.splitlines():
            line = line.strip()
            if not line:
                continue
            # Check JSON output lines
            if line.startswith("{") and line.endswith("}"):
                try:
                    data = json.loads(line)
                    if isinstance(data, dict):
                        for k in ["actual_model", "resolved_model", "model", "model_name"]:
                            if k in data and isinstance(data[k], str) and data[k]:
                                return data[k]
                except Exception:
                    pass
            # Check explicit text patterns
            m = re.search(r'^(?:actual\s*model|resolved\s*model|using\s*model|model\s*used):\s*([a-zA-Z0-9\.\-\_\/]+)', line, re.I)
            if m:
                return m.group(1).strip()
    except Exception:
        pass
    return "unknown"

class JobManager:
    def __init__(self):
        self.lock = threading.Lock()
        self.active_processes: dict[str, subprocess.Popen] = {}

    def start_sandbox_job(
        self,
        workspace_id: str,
        workspace_path: str,
        argv: list[str],
        cwd: str = "",
        timeout_seconds: int = 300,
        writable: bool = True,
        network_access: bool = False
    ) -> str:
        """Start an async command inside the bubblewrap sandbox."""
        job_id = f"job_{secrets.token_hex(12)}"
        stdout_path = os.path.join(RUNTIME_JOBS_DIR, f"{job_id}.stdout.log")
        stderr_path = os.path.join(RUNTIME_JOBS_DIR, f"{job_id}.stderr.log")

        process_env = sandbox_host_env(network_access=network_access)
        bwrap_args = build_bwrap_args(workspace_path, writable=writable, sub_cwd=cwd, network_access=network_access)
        full_cmd = bwrap_args + ["--"] + argv

        stdout_f = open(stdout_path, "w")
        stderr_f = open(stderr_path, "w")

        now = time.time()
        proc = subprocess.Popen(
            full_cmd,
            env=process_env,
            stdout=stdout_f,
            stderr=stderr_f,
            preexec_fn=os.setsid
        )

        pgid = os.getpgid(proc.pid)
        with self.lock:
            self.active_processes[job_id] = proc

        conn = get_db()
        with conn:
            conn.execute(
                """
                INSERT INTO jobs (
                    job_id, workspace_id, argv, cwd, pid, pgid,
                    status, created_at, started_at, stdout_path, stderr_path
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id, workspace_id, json.dumps(argv), cwd, proc.pid, pgid,
                    "running", now, now, stdout_path, stderr_path
                )
            )
        conn.close()

        # Monitor in background thread
        thread = threading.Thread(
            target=self._monitor_proc,
            args=(job_id, proc, stdout_f, stderr_f, timeout_seconds, now),
            daemon=True
        )
        thread.start()

        return job_id

    def start_raw_job(
        self,
        workspace_id: str,
        workspace_path: str,
        cmd: list[str],
        env: Optional[dict] = None,
        agent: str = "",
        provider: str = "",
        model: str = "",
        requested_model: str = "",
        reasoning_level: str = "",
        cli_version: str = "",
        timeout_seconds: int = 600
    ) -> str:
        """Start a raw process (e.g. CLI agent worker) asynchronously."""
        job_id = f"job_{secrets.token_hex(12)}"
        stdout_path = os.path.join(RUNTIME_JOBS_DIR, f"{job_id}.stdout.log")
        stderr_path = os.path.join(RUNTIME_JOBS_DIR, f"{job_id}.stderr.log")

        stdout_f = open(stdout_path, "w")
        stderr_f = open(stderr_path, "w")

        now = time.time()
        proc = subprocess.Popen(
            cmd,
            cwd=workspace_path,
            env=env,
            stdout=stdout_f,
            stderr=stderr_f,
            preexec_fn=os.setsid
        )

        pgid = os.getpgid(proc.pid)
        with self.lock:
            self.active_processes[job_id] = proc

        req_model = requested_model or model
        conn = get_db()
        with conn:
            conn.execute(
                """
                INSERT INTO jobs (
                    job_id, workspace_id, argv, cwd, pid, pgid,
                    status, created_at, started_at, stdout_path, stderr_path,
                    agent, provider, model, requested_model, actual_model,
                    reasoning_level, cli_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id, workspace_id, json.dumps(cmd), workspace_path, proc.pid, pgid,
                    "running", now, now, stdout_path, stderr_path,
                    agent, provider, req_model, req_model, "unknown",
                    reasoning_level, cli_version
                )
            )
        conn.close()

        thread = threading.Thread(
            target=self._monitor_proc,
            args=(job_id, proc, stdout_f, stderr_f, timeout_seconds, now),
            daemon=True
        )
        thread.start()

        return job_id

    def _monitor_proc(self, job_id: str, proc: subprocess.Popen, stdout_f, stderr_f, timeout_seconds: int, start_time: float):
        status = "completed"
        exit_code = None
        timed_out = False

        try:
            exit_code = proc.wait(timeout=timeout_seconds)
            if exit_code != 0:
                status = "failed"
        except subprocess.TimeoutExpired:
            timed_out = True
            status = "timed_out"
            exit_code = 124
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except Exception:
                pass
            proc.wait()
        except Exception as e:
            status = "failed"
            exit_code = 1
            logger.error("Job %s monitor error: %s", job_id, e)
        finally:
            try:
                stdout_f.close()
                stderr_f.close()
            except Exception:
                pass

            finished_at = time.time()
            duration = finished_at - start_time
            stdout_path = os.path.join(RUNTIME_JOBS_DIR, f"{job_id}.stdout.log")
            stderr_path = os.path.join(RUNTIME_JOBS_DIR, f"{job_id}.stderr.log")
            actual_model = extract_actual_model(stdout_path, stderr_path)

            with self.lock:
                self.active_processes.pop(job_id, None)

            conn = get_db()
            with conn:
                row = conn.execute("SELECT status, actual_model FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
                existing_actual = row["actual_model"] if row and row["actual_model"] else "unknown"
                resolved_actual = actual_model if actual_model != "unknown" else existing_actual
                if row and row["status"] == "cancelled":
                    conn.execute(
                        "UPDATE jobs SET finished_at = ?, duration = ?, exit_code = ?, actual_model = ? WHERE job_id = ?",
                        (finished_at, duration, exit_code, resolved_actual, job_id)
                    )
                else:
                    conn.execute(
                        """
                        UPDATE jobs SET
                            status = ?, exit_code = ?, finished_at = ?, duration = ?, actual_model = ?
                        WHERE job_id = ?
                        """,
                        (status, exit_code, finished_at, duration, resolved_actual, job_id)
                    )
            conn.close()

    def get_status(self, job_id: str) -> dict:
        conn = get_db()
        row = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        conn.close()
        if not row:
            raise KeyError(f"Job not found: {job_id}")

        row_keys = row.keys()
        actual_model = row["actual_model"] if "actual_model" in row_keys and row["actual_model"] else "unknown"
        requested_model = row["requested_model"] if "requested_model" in row_keys and row["requested_model"] else row["model"]
        reasoning_level = row["reasoning_level"] if "reasoning_level" in row_keys else ""
        cli_version = row["cli_version"] if "cli_version" in row_keys else ""
        exit_code = row["exit_code"]

        from mscopilotporter.agents.registry import AGENT_REGISTRY

        worker_cli = AGENT_REGISTRY.get(row["agent"], {}).get("worker_cli", row["agent"])
        return {
            "job_id": row["job_id"],
            "workspace_id": row["workspace_id"],
            "worker_cli": worker_cli,
            "agent": row["agent"],
            "provider": row["provider"],
            "requested_model": requested_model,
            "actual_model": actual_model,
            "reasoning_level": reasoning_level,
            "cli_version": cli_version,
            "status": row["status"],
            "exit_status": exit_code,
            "exit_code": exit_code,
            "created_at": row["created_at"],
            "started_at": row["started_at"],
            "finished_at": row["finished_at"],
            "duration": row["duration"] if row["duration"] is not None else (time.time() - row["started_at"] if row["started_at"] else 0.0),
            "model": requested_model,
        }

    def get_output(self, job_id: str, cursor: int = 0, max_bytes: int = 65536) -> dict:
        conn = get_db()
        row = conn.execute("SELECT stdout_path, stderr_path, status, exit_code FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        conn.close()
        if not row:
            raise KeyError(f"Job not found: {job_id}")

        stdout_path = row["stdout_path"]
        stderr_path = row["stderr_path"]

        stdout_chunk = ""
        stderr_chunk = ""
        next_cursor = cursor

        if os.path.exists(stdout_path):
            with open(stdout_path, "rb") as f:
                f.seek(cursor)
                data = f.read(max_bytes)
                next_cursor = f.tell()
                stdout_chunk = data.decode("utf-8", errors="replace")

        if os.path.exists(stderr_path):
            with open(stderr_path, "rb") as f:
                stderr_chunk = f.read(max_bytes).decode("utf-8", errors="replace")

        return {
            "job_id": job_id,
            "status": row["status"],
            "exit_code": row["exit_code"],
            "cursor": cursor,
            "next_cursor": next_cursor,
            "stdout": stdout_chunk,
            "stderr": stderr_chunk,
        }

    def cancel(self, job_id: str) -> dict:
        conn = get_db()
        row = conn.execute("SELECT pid, pgid, status FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        if not row:
            conn.close()
            raise KeyError(f"Job not found: {job_id}")

        if row["status"] != "running":
            conn.close()
            return {"job_id": job_id, "status": row["status"], "message": "Job is not running"}

        pgid = row["pgid"]
        pid = row["pid"]

        try:
            if pgid:
                os.killpg(pgid, signal.SIGTERM)
                time.sleep(0.1)
                os.killpg(pgid, signal.SIGKILL)
            elif pid:
                os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except Exception as e:
            logger.warning("Error signalling job %s: %s", job_id, e)

        with conn:
            conn.execute("UPDATE jobs SET status = 'cancelled', exit_code = 130, finished_at = ? WHERE job_id = ?", (time.time(), job_id))
        conn.close()

        with self.lock:
            self.active_processes.pop(job_id, None)

        return {"job_id": job_id, "status": "cancelled", "message": "Job cancelled"}

job_manager = JobManager()
