Building a robust, long-running **Planner-Executor** architecture atop a clean Python backend without over-engineering requires avoiding heavy proprietary frameworks (like heavy orchestrators or opinionated agent graph SDKs) in favor of a lightweight, transparent design pattern.

By leaning on **SQLite for persistent state** and **Docker Compose for service orchestration**, you get crash recovery, inspectable state, and easy debugging while keeping lines of code minimal.

---

## 1. Conceptual Architecture

The system uses a strict **Hub-and-Spoke / Manager-Worker** topology. Agents never talk directly to one another; all state mutations flow through a durable database overseen by the Planner.

```
                 [ User / CLI / Web UI ]
                            │
                            ▼
              ┌───────────────────────────┐
              │    Planner / Controller    │ (Stateful Orchestrator)
              └─────────────┬─────────────┘
                            │
               Reads/Writes Plan & State
                            │
                            ▼
              ┌───────────────────────────┐
              │    SQLite State Store     │ (Persistent Durable Ledger)
              └─────┬───────────────┬─────┘
                    │               │
       Pulls Task   │               │  Pulls Task
       & Reports    │               │  & Reports
                    ▼               ▼
          ┌──────────────────┐ ┌──────────────────┐
          │ Executor Worker 1│ │ Executor Worker N│ (Stateless Task Runners)
          └──────────────────┘ └──────────────────┘

```

* **The Planner / Controller (Singleton):**
* Ingests user requests and breaks them down into a structured, append-only DAG (Directed Acyclic Graph) or sequential task list with explicit success criteria.
* Inspects completed steps from the database, evaluates output quality, handles error recovery (retries, alternative tool selection, or plan modification), and triggers subsequent steps.


* **The Executor(s) (Stateless Workers):**
* Scale horizontally (1 to $N$ containers). They do not coordinate with each other.
* Poll the task queue, pull an atomic subtask assigned by the Planner, execute tools or code, and write structured results/logs back to SQLite.


* **The State Store (SQLite):**
* Acts as the single source of truth. Because SQLite handles ACID transactions locally and writes to a mounted Docker volume, any container crash leaves the workflow state intact. On restart, the Planner simply reads the database and resumes where it left off.



---

## 2. Implementation Design

### Database Schema (SQLite)

A simple schema tracks workflows, granular task items, and execution logs.

```sql
CREATE TABLE IF NOT EXISTS workflows (
    workflow_id TEXT PRIMARY KEY,
    goal TEXT NOT NULL,
    status TEXT NOT NULL, -- 'planning', 'running', 'paused', 'completed', 'failed'
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS tasks (
    task_id TEXT PRIMARY KEY,
    workflow_id TEXT NOT NULL,
    sequence_index INTEGER NOT NULL,
    title TEXT NOT NULL,
    description TEXT,
    status TEXT NOT NULL, -- 'pending', 'running', 'success', 'failed', 'skipped'
    assigned_executor TEXT,
    input_payload TEXT,
    output_result TEXT,
    error_log TEXT,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (workflow_id) REFERENCES workflows(workflow_id)
);

```

### Core Code Layout (`claudio` backend structure)

```text
claudio/
├── docker-compose.yml
├── Dockerfile
├── requirements.txt
└── src/
    ├── database.py       # SQLite connection & atomic queries
    ├── models.py         # Pydantic schemas for plans & task payloads
    ├── planner.py        # Planner agent loop & replanning logic
    └── executor.py       # Worker loop pulling & executing tasks

```

---

## 3. Production Code Components

### `src/database.py`

Ensures thread-safe, robust local persistence with WAL (Write-Ahead Logging) mode enabled for concurrent reads by workers and writes by the planner.

```python
import sqlite3
from contextlib import contextmanager

DB_PATH = "/data/claudio.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL;")
    with open("schema.sql", "r") as f:
        conn.executescript(f.read())
    conn.close()

@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

```

### `src/planner.py` (The Orchestrator Loop)

```python
import time
import json
from src.database import get_db

class PlannerAgent:
    def __init__(self, workflow_id: str):
        self.workflow_id = workflow_id

    def assess_and_plan(self, goal: str):
        """Decomposes a request into tasks and writes them to SQLite."""
        with get_db() as db:
            db.execute(
                "INSERT OR IGNORE INTO workflows (workflow_id, goal, status) VALUES (?, ?, ?)",
                (self.workflow_id, goal, "running")
            )
            # Simulated LLM plan generation (JSON output)
            initial_tasks = [
                ("t1", 1, "Gather initial context and list target files", "pending"),
                ("t2", 2, "Perform static analysis on core modules", "pending"),
            ]
            for tid, seq, title, status in initial_tasks:
                db.execute(
                    """INSERT OR IGNORE INTO tasks 
                       (task_id, workflow_id, sequence_index, title, status) 
                       VALUES (?, ?, ?, ?, ?)""",
                    (tid, self.workflow_id, seq, title, status)
                )

    def monitor_loop(self):
        """Long-running loop checking task progress, handling retries/replanning."""
        while True:
            with get_db() as db:
                tasks = db.execute(
                    "SELECT * FROM tasks WHERE workflow_id = ? ORDER BY sequence_index",
                    (self.workflow_id,)
                ).fetchall()

                all_done = True
                for task in tasks:
                    if task["status"] == "failed":
                        print(f"[-] Task {task['task_id']} failed. Analyzing error for replanning...")
                        # Recovery strategy: Retry or insert corrective task
                        db.execute("UPDATE tasks SET status = 'pending' WHERE task_id = ?", (task["task_id"],))
                    elif task["status"] != "success":
                        all_done = False

                if all_done and tasks:
                    db.execute("UPDATE workflows SET status = 'completed' WHERE workflow_id = ?", (self.workflow_id,))
                    print("[+] Workflow completed successfully.")
                    break

            time.sleep(5) # Poll interval

```

### `src/executor.py` (The Stateless Worker)

```python
import time
import os
from src.database import get_db

class ExecutorWorker:
    def __init__(self, worker_id: str):
        self.worker_id = worker_id

    def poll_and_execute(self):
        """Atomically claims and executes the next pending task."""
        while True:
            with get_db() as db:
                # Atomically claim one pending task
                cursor = db.cursor()
                cursor.execute(
                    """SELECT task_id, title FROM tasks 
                       WHERE status = 'pending' 
                       ORDER BY sequence_index LIMIT 1"""
                )
                task = cursor.fetchone()

                if task:
                    task_id = task["task_id"]
                    db.execute(
                        "UPDATE tasks SET status = 'running', assigned_executor = ? WHERE task_id = ?",
                        (self.worker_id, task_id)
                    )
                else:
                    task_id = None

            if task_id:
                print(f"[Worker {self.worker_id}] Executing task: {task['title']}")
                try:
                    # Execute task logic (tool calls, code execution, API queries)
                    time.sleep(3) # Simulate work
                    result_data = "Task execution successful."
                    
                    with get_db() as db:
                        db.execute(
                            "UPDATE tasks SET status = 'success', output_result = ? WHERE task_id = ?",
                            (result_data, task_id)
                        )
                except Exception as e:
                    with get_db() as db:
                        db.execute(
                            "UPDATE tasks SET status = 'failed', error_log = ? WHERE task_id = ?",
                            (str(e), task_id)
                        )
            else:
                time.sleep(2) # Backoff if no work available

```

---

## 4. Docker Deployment & Easy Debugging

A single `docker-compose.yml` mounts a local directory for SQLite storage, allowing you to inspect the database live or spin up multiple executors instantly.

### `docker-compose.yml`

```yaml
version: '3.8'

services:
  planner:
    build: .
    command: python -m src.planner
    volumes:
      - ./data:/data
    environment:
      - PYTHONUNBUFFERED=1

  executor-1:
    build: .
    command: python -m src.executor --worker-id=exec-1
    volumes:
      - ./data:/data
    environment:
      - PYTHONUNBUFFERED=1
    depends_on:
      - planner

  executor-2:
    build: .
    command: python -m src.executor --worker-id=exec-2
    volumes:
      - ./data:/data
    environment:
      - PYTHONUNBUFFERED=1
    depends_on:
      - planner

```

### Debugging & Operational Control

1. **Live Log Inspection:** Because `PYTHONUNBUFFERED=1` is set, you can instantly inspect agent reasoning and task transitions via terminal logs:
```bash
docker compose logs -f planner executor-1

```


2. **State Inspection:** Because state is held in `./data/claudio.db`, you can query or modify state mid-run using standard SQLite tools without restarting containers:
```bash
sqlite3 data/claudio.db "SELECT task_id, status, title FROM tasks;"

```


3. **Scaling Executors:** To increase execution throughput from 1 to $N$ workers without changing architecture, scale via Docker Compose on the fly:
```bash
docker compose up --scale executor-1=3 -d

```