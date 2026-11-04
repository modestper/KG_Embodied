"""Keep the latest finished run in memory and save it only on explicit request."""

import json
import os
import re
import tempfile
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from urllib.parse import urlencode

from scripts.build_trace_index import index_lock, trace_entry, write_index


class TraceStorageError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


class TraceStorage:
    def __init__(self, data_dir):
        self.data_dir = Path(data_dir).resolve()
        self.root = self.data_dir / "traces"
        self._lock = threading.Lock()
        self._completed = None

    def retain(self, env, config, depots, summary, regions):
        """Detach trace data before closing actors/stores, without writing files."""
        trace = env.to_trace()
        run_id = uuid.uuid4().hex
        finished_at = datetime.now(timezone.utc).isoformat()
        trace.update({
            "scenario": f"{config.get('city', '')} 多无人机配送模拟",
            "config": dict(config), "depots": depots, "regions": regions,
            "run_id": run_id, "finished_at": finished_at,
            "reason": summary["reason"], "steps_run": summary["steps_run"],
            "metrics": {**trace["metrics"], **summary["metrics"]},
            "frame_stride": 1,
        })
        # Frames are snapshots; only copy their small outer dict to add the true graph Step.
        trace["frames"] = [dict(frame, step_index=index + 1)
                           for index, frame in enumerate(trace["frames"])]
        city = re.sub(r"[^\w-]", "_", str(config.get("city", "simulation")))[:40]
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        descriptor = {"run_id": run_id,
                      "filename": f"{city}_{stamp}_{run_id[:8]}.json",
                      "frame_count": len(trace["frames"])}
        with self._lock:
            self._completed = (descriptor, trace)
        return descriptor

    def _directory(self, value):
        if not isinstance(value, str) or len(value) > 500 or "\\" in value:
            raise TraceStorageError("文件夹路径不合法。")
        parts = value.split("/") if value else []
        if any(not self._valid_name(part) for part in parts):
            raise TraceStorageError("请选择轨迹目录内的文件夹，不能使用绝对路径或 ..。")
        path = self.root.joinpath(*parts)
        # Reject symlinks, including the root, to keep browsing/writes in this tree.
        for candidate in [self.root, *[self.root.joinpath(*parts[:i + 1]) for i in range(len(parts))]]:
            if candidate.is_symlink():
                raise TraceStorageError("轨迹目录不能使用符号链接。")
        if not path.resolve().is_relative_to(self.data_dir / "traces"):
            raise TraceStorageError("目标文件夹不在允许的轨迹目录内。")
        return path

    @staticmethod
    def _valid_name(value):
        return (isinstance(value, str) and 0 < len(value) <= 160
                and value not in {".", ".."} and not value.startswith(".")
                and value == value.strip()
                and not re.search(r'[\x00-\x1f\x7f/\\:*?"<>|]', value))

    def directories(self, relative=""):
        path = self._directory(relative)
        if path.exists() and not path.is_dir():
            raise TraceStorageError("所选路径不是文件夹。")
        if relative and not path.exists():
            raise TraceStorageError("文件夹不存在。", 404)
        children = []
        if path.exists():
            for child in sorted(path.iterdir(), key=lambda item: item.name.casefold()):
                if child.is_dir() and not child.is_symlink() and self._valid_name(child.name):
                    children.append({"name": child.name, "path": child.relative_to(self.root).as_posix()})
        return {"ok": True, "root": "data/traces", "path": relative,
                "parent": str(PurePosixPath(relative).parent) if "/" in relative else "",
                "directories": children}

    def save(self, payload):
        if not isinstance(payload, dict):
            raise TraceStorageError("保存请求必须是 JSON 对象。")
        directory = self._directory(payload.get("directory", ""))
        filename = payload.get("filename")
        if not self._valid_name(filename) or not filename.endswith(".json"):
            raise TraceStorageError("请输入合法的 .json 文件名。")
        target = directory / filename
        with self._lock:
            if self._completed is None or payload.get("run_id") != self._completed[0]["run_id"]:
                raise TraceStorageError("本次轨迹已失效，请保存最近一次完成的模拟。", 410)
            trace = {**self._completed[1], "saved_at": datetime.now(timezone.utc).isoformat()}
            if not trace["frames"]:
                raise TraceStorageError("本次模拟没有可保存的轨迹帧。")
            temporary = None
            committed = False
            try:
                directory.mkdir(parents=True, exist_ok=True)
                self._directory(payload.get("directory", ""))
                with index_lock(self.data_dir):
                    index_path = self.data_dir / "traces.json"
                    if index_path.exists():
                        index = json.loads(index_path.read_text(encoding="utf-8"))
                        if not isinstance(index, dict) or not isinstance(index.get("traces"), list):
                            raise TraceStorageError("轨迹索引格式不正确，请先修复索引。", 500)
                    else:
                        index = {"traces": []}
                    if target.exists() or target.is_symlink():
                        raise TraceStorageError("同名轨迹已存在，请更换文件名。", 409)
                    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=directory,
                                                     suffix=".tmp", delete=False) as handle:
                        temporary = Path(handle.name)
                        json.dump(trace, handle, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
                        handle.flush()
                        os.fsync(handle.fileno())
                    # Atomic creation without overwriting a concurrently created file.
                    os.link(temporary, target)
                    committed = True
                    entry = trace_entry(target, trace, self.data_dir)
                    index["traces"] = [entry, *[item for item in index["traces"]
                                                if item.get("file") != entry["file"]]]
                    write_index(index, self.data_dir)
            except Exception:
                if committed:
                    target.unlink(missing_ok=True)
                raise
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
        return {"ok": True, "file": entry["file"], "path": f"data/{entry['file']}",
                "frame_count": len(trace["frames"]), "steps_run": trace["steps_run"],
                "replay_url": "./simulation.html?" + urlencode({"trace": entry["file"]})}
