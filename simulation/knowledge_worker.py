"""Private JSON-lines protocol for ProcessKnowledgeWriter (no credentials in argv)."""

import json
import sys
from contextlib import redirect_stdout

from simulation.knowledge_writer import KnowledgeWriteExecutor, KnowledgeWriterConfig


def main():
    executor = None
    try:
        for line in sys.stdin:
            request = json.loads(line)
            operation = request["operation"]
            try:
                with redirect_stdout(sys.stderr):
                    if operation == "init":
                        if executor is not None:
                            raise ValueError("knowledge worker is already initialized")
                        executor = KnowledgeWriteExecutor(KnowledgeWriterConfig(**request["payload"]))
                        result = executor.status()
                    elif executor is None:
                        raise ValueError("knowledge worker is not initialized")
                    elif operation == "write":
                        executor.apply(request["payload"])
                        result = None
                    elif operation in {"flush", "status"}:
                        result = executor.status()
                    elif operation == "close":
                        result = executor.status()
                        executor.close()
                        executor = None
                    else:
                        raise ValueError(f"Unknown knowledge worker operation: {operation}")
                response = {"id": request["id"], "ok": True, "result": result}
            except Exception as error:
                response = {"id": request["id"], "ok": False,
                            "error": f"{type(error).__name__}: {error}"}
            print(json.dumps(response, ensure_ascii=False, allow_nan=False), flush=True)
            if operation == "close" or not response["ok"]:
                break
    finally:
        if executor is not None:
            with redirect_stdout(sys.stderr):
                executor.close()


if __name__ == "__main__":
    main()
