"""Private, persistent Node worker for GTM inference; OCR never imports TensorFlow."""
import atexit
import json
import queue
import subprocess
import threading
from pathlib import Path


_workers = {}
_workers_lock = threading.Lock()


class GTMWorkerError(RuntimeError):
    pass


class GTMWorker:
    def __init__(self, node, model_path, timeout):
        self.timeout = timeout
        self.lock = threading.Lock()
        script = Path(__file__).resolve().parents[2] / "gtm_model" / "predict.mjs"
        self.process = subprocess.Popen(
            [node, str(script), str(model_path)], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1,
        )
        self.responses = queue.Queue()
        threading.Thread(target=self._read, daemon=True).start()
        try:
            ready = self._receive()
            if ready.get("ready") is not True:
                raise GTMWorkerError("GTM worker did not initialize.")
        except Exception:
            self.close()
            raise

    def _read(self):
        for line in self.process.stdout:
            try:
                self.responses.put(json.loads(line))
            except json.JSONDecodeError:
                self.responses.put({"error": "Invalid GTM worker response."})
        self.responses.put({"error": "GTM worker exited."})

    def _receive(self):
        try:
            response = self.responses.get(timeout=self.timeout)
        except queue.Empty as exc:
            self.close()
            raise GTMWorkerError("GTM worker timed out.") from exc
        if response.get("error"):
            raise GTMWorkerError(response["error"])
        return response

    def predict(self, image_path):
        with self.lock:
            if self.process.poll() is not None:
                raise GTMWorkerError("GTM worker is unavailable.")
            try:
                self.process.stdin.write(json.dumps({"imagePath": str(image_path)}) + "\n")
                self.process.stdin.flush()
            except OSError as exc:
                raise GTMWorkerError("GTM worker input failed.") from exc
            result = self._receive()
            if not isinstance(result.get("scores"), list):
                raise GTMWorkerError("GTM worker returned no scores.")
            return result["scores"]

    def close(self):
        if self.process.poll() is None:
            self.process.kill()


def worker(node, model_path, version, timeout):
    key = (node, str(Path(model_path).resolve()), version)
    with _workers_lock:
        current = _workers.get(key)
        if current is None or current.process.poll() is not None:
            current = GTMWorker(node, model_path, timeout)
            _workers[key] = current
        return current


@atexit.register
def _close_workers():
    for current in _workers.values():
        current.close()
