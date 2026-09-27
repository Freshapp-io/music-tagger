"""One background job at a time (scan, batch fix, dedupe…). Every operation
that touches files also takes WRITE_LOCK so single edits never race a job."""
import threading
import time
import traceback
import uuid
from contextlib import contextmanager

WRITE_LOCK = threading.RLock()

_state = {"job": None}
_state_lock = threading.Lock()


class Busy(Exception):
    pass


class Job:
    def __init__(self, kind, label):
        self.id = uuid.uuid4().hex[:8]
        self.kind, self.label = kind, label
        self.total, self.done = 0, 0
        self.message = ""
        self.errors = []
        self.status = "running"
        self.started = time.time()
        self.finished = None
        self.result = None

    def step(self, message=None, n=1):
        self.done += n
        if message is not None:
            self.message = message

    def error(self, where, err):
        if len(self.errors) < 200:
            self.errors.append({"where": where, "error": str(err)[:300]})

    def as_dict(self):
        return {
            "id": self.id, "kind": self.kind, "label": self.label, "status": self.status,
            "total": self.total, "done": self.done, "message": self.message,
            "errors": self.errors[-50:], "n_errors": len(self.errors),
            "elapsed": round((self.finished or time.time()) - self.started, 1),
            "result": self.result,
        }


def current():
    return _state["job"]


def start(kind, label, fn, *args, **kwargs):
    with _state_lock:
        j = _state["job"]
        if j is not None and j.status == "running":
            raise Busy(f"Une tâche est déjà en cours : {j.label}")
        job = Job(kind, label)
        _state["job"] = job

    def run():
        with WRITE_LOCK:
            try:
                job.result = fn(job, *args, **kwargs)
                job.status = "done"
            except Exception as e:
                traceback.print_exc()
                job.error("job", e)
                job.status = "failed"
                job.message = str(e)
            finally:
                job.finished = time.time()

    threading.Thread(target=run, name=f"job-{kind}", daemon=True).start()
    return job


@contextmanager
def acquire_or_busy():
    if not WRITE_LOCK.acquire(timeout=5):
        raise Busy("Une tâche est en cours, réessayez quand elle sera terminée.")
    try:
        yield
    finally:
        WRITE_LOCK.release()
