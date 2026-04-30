#!/usr/bin/env python
import json
import logging
import os
import re
import shutil
import sys
import threading
import time
import uuid
import contextlib
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

from ..schemas import (
    CMDEnum,
    ECCRequest,
    ECCResponse,
    ResponseEnum,
)
from ..sse import server_notify

gui_notify = server_notify()

logger = logging.getLogger(__name__)

_TEXT_ARTIFACT_LIMIT = 120_000
_FOUNDATION_DIR = Path("foundation_data") / "ecc"
_FOUNDATION_KINDS = {
    "manifest",
    "summary",
    "stage_index",
    "canonical_grid",
    "quality",
    "ml_view",
    "agent_view",
    "vectors",
    "maps",
}
_FOUNDATION_VECTOR_ENTITIES = {
    "instances",
    "nets",
    "pins",
    "wires",
    "routing_graphs",
    "timing_paths",
    "patches",
}
_FOUNDATION_MAP_ENTITIES = {"density", "egr_overflow", "rudy", "margin", "other"}
_FOUNDATION_TOKEN_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
_TASKS: dict[str, dict] = {}
_TASKS_LOCK = threading.Lock()
_WORKSPACE_LOCKS: dict[str, threading.Lock] = {}
_WORKSPACE_LOCKS_LOCK = threading.Lock()
_SECRET_PATTERNS = [
    re.compile(r"(?i)(api[_-]?key|token|password|secret)\s*[:=]\s*([^\s,;]+)"),
]
_ALLOWED_PARAMETER_PATHS = {
    "Core.Utilitization",
    "Core.Margin",
    "Core.Aspect ratio",
    "Target density",
    "Target overflow",
    "Max fanout",
    "Global right padding",
    "Cell padding x",
    "Routability opt flag",
    "Frequency max [MHz]",
    "Bottom layer",
    "Top layer",
}
_PARAMETER_ALIASES = {
    "Core.Utilization": "Core.Utilitization",
}
_ALLOWED_STEP_CONFIG_PATHS = {
    "place": {
        "PL.is_timing_effort",
        "PL.is_congestion_effort",
        "PL.GP.global_right_padding",
        "PL.GP.Density.target_density",
        "PL.GP.Density.bin_cnt_x",
        "PL.GP.Density.bin_cnt_y",
        "PL.GP.Nesterov.target_overflow",
        "PL.GP.Nesterov.max_iter",
        "PL.BUFFER.max_buffer_num",
        "PL.LG.global_right_padding",
        "PL.DP.global_right_padding",
    },
    "CTS": {
        "skew_bound",
        "max_buf_tran",
        "max_sink_tran",
        "max_fanout",
        "cluster_size",
        "routing_layer",
        "root_buffer_required",
        "break_long_wire",
    },
    "route": {
        "RT.-thread_number",
        "RT.-enable_timing",
        "RT.-bottom_routing_layer",
    },
}
_STALE_STEP_DIR_NAMES = (
    "output",
    "log",
    "report",
    "analysis",
    "feature",
)
_STALE_STEP_FILE_NAMES = (
    "subflow.json",
    "checklist.json",
)


def _summarize_request(data: object) -> dict:
    """Extract key fields from request data for logging."""
    if not isinstance(data, dict):
        return {}
    summary = {}
    for key in ("directory", "step", "id", "pdk", "pdk_root", "rerun"):
        if key in data:
            summary[key] = data[key]
    if "parameters" in data:
        summary["parameters_keys"] = len(data["parameters"])
    if "rtl_list" in data:
        rtl = data["rtl_list"]
        summary["rtl_count"] = len(rtl.splitlines() if isinstance(rtl, str) else rtl)
    return summary


def _foundation_extractor_class():
    try:
        from chipcompiler.data.foundation import FoundationExtractor
        return FoundationExtractor
    except ModuleNotFoundError:
        local_ecc = Path(__file__).resolve().parents[5] / "ecc"
        local_pkg = local_ecc / "chipcompiler"
        if local_pkg.exists():
            sys.path.insert(0, str(local_ecc))
            import chipcompiler

            pkg_path = str(local_pkg)
            if pkg_path not in getattr(chipcompiler, "__path__", []):
                chipcompiler.__path__.append(pkg_path)
            if "chipcompiler.data" in sys.modules:
                data_pkg_path = str(local_pkg / "data")
                data_pkg = sys.modules["chipcompiler.data"]
                if data_pkg_path not in getattr(data_pkg, "__path__", []):
                    data_pkg.__path__.append(data_pkg_path)
            from chipcompiler.data.foundation import FoundationExtractor
            return FoundationExtractor
        raise


def _jsonl_record_count(path: Path) -> int:
    if not path.exists() or not path.is_file():
        return 0
    with path.open("r", encoding="utf-8") as handle:
        return sum(1 for line in handle if line.strip())


def _validate_foundation_token(name: str, value: str) -> None:
    if value in {".", ".."} or "/" in value or "\\" in value or not _FOUNDATION_TOKEN_RE.fullmatch(value):
        raise ValueError(f"invalid foundation data {name}: {value}")


def _parse_bool(value: object, *, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "t", "yes", "y", "on"}:
            return True
        if normalized in {"0", "false", "f", "no", "n", "off", ""}:
            return False
    raise ValueError(f"invalid boolean value: {value}")


class ECCService:
    # Logger subtree that receives workspace-level file logging
    _WS_LOGGER_NAME = "ecos_server.ecc"

    # All CMDEnum values except "notify" (which is SSE-only, not dispatched).
    _COMMANDS = frozenset(e.value for e in CMDEnum if e is not CMDEnum.notify)

    def __init__(self):
        self.workspace = None
        self.engine_flow = None
        self._workspace_log_handler = None

    def dispatch(self, request: ECCRequest) -> ECCResponse:
        """Route request to the matching handler method and log execution."""
        cmd = request.cmd
        if cmd not in self._COMMANDS:
            return ECCResponse(
                cmd=cmd,
                response=ResponseEnum.error.value,
                data={},
                message=[f"unknown command: {cmd}"],
            )

        handler = getattr(self, cmd)
        logger.info("[CMD:start] cmd=%s %s", cmd, _summarize_request(request.data))

        start = time.time()
        try:
            response = handler(request)
        except Exception:
            elapsed_ms = (time.time() - start) * 1000
            logger.exception("[CMD:error] cmd=%s elapsed=%.0fms", cmd, elapsed_ms)
            raise

        elapsed_ms = (time.time() - start) * 1000
        result = getattr(response, "response", type(response).__name__)
        logger.info("[CMD:done] cmd=%s result=%s elapsed=%.0fms", cmd, result, elapsed_ms)
        return response

    def _attach_workspace_log(self, workspace_dir: str):
        """Attach a rotating file handler that mirrors API logs into {workspace}/log/server.log."""
        self._detach_workspace_log()
        log_dir = os.path.join(os.path.abspath(workspace_dir), "log")
        os.makedirs(log_dir, exist_ok=True)
        log_path = os.path.join(log_dir, "server.log")
        handler = RotatingFileHandler(log_path, maxBytes=10 * 1024 * 1024, backupCount=5)
        handler.setFormatter(
            logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
        )
        logging.getLogger(self._WS_LOGGER_NAME).addHandler(handler)
        self._workspace_log_handler = handler
        logger.info("Server API logs -> %s", log_path)

    def _detach_workspace_log(self):
        """Remove the previous workspace file handler, if any."""
        if self._workspace_log_handler:
            logging.getLogger(self._WS_LOGGER_NAME).removeHandler(self._workspace_log_handler)
            self._workspace_log_handler.close()
            self._workspace_log_handler = None

    @staticmethod
    def _normalize_rtl_list(rtl_list) -> list[str]:
        if not rtl_list:
            return []
        if isinstance(rtl_list, list):
            items = rtl_list
        elif isinstance(rtl_list, str):
            items = rtl_list.splitlines()
        else:
            items = [rtl_list]

        result = []
        seen = set()
        for item in items:
            path = str(item).strip()
            if not path or path in seen:
                continue
            seen.add(path)
            result.append(path)
        return result

    @staticmethod
    def _write_filelist(directory: str, rtl_paths: list[str]) -> str:
        os.makedirs(directory, exist_ok=True)
        filelist_path = os.path.join(directory, "filelist")
        with open(filelist_path, "w", encoding="utf-8") as f:
            for path in rtl_paths:
                if any(ch.isspace() for ch in path):
                    f.write(f'"{path}"\n')
                else:
                    f.write(f"{path}\n")
        return filelist_path

    def __build_flow(self):
        from chipcompiler.engine import EngineFlow
        from chipcompiler.rtl2gds import build_rtl2gds_flow

        engine_flow = EngineFlow(workspace=self.workspace)
        if not engine_flow.has_init():
            steps = build_rtl2gds_flow()
            for step, tool, state in steps:
                engine_flow.add_step(step=step, tool=tool, state=state)
        else:
            engine_flow.create_step_workspaces()

        self.engine_flow = engine_flow

        if engine_flow.is_flow_success():
            self._dedupe_workspace_steps()
            return
        engine_flow.create_step_workspaces()
        self._dedupe_workspace_steps()

    def _dedupe_workspace_steps(self) -> None:
        """Defensively remove duplicate workspace step entries after flow rebuild."""
        if self.engine_flow is None or not hasattr(self.engine_flow, "workspace_steps"):
            return
        deduped = []
        seen = set()
        for workspace_step in self.engine_flow.workspace_steps:
            key = getattr(workspace_step, "name", None) or id(workspace_step)
            if key in seen:
                continue
            seen.add(key)
            deduped.append(workspace_step)
        self.engine_flow.workspace_steps = deduped

    def _workspace_dir_from_request(self, data: dict) -> Path:
        directory = str(data.get("directory") or data.get("workspace_id") or "").strip()
        if directory:
            return Path(directory).expanduser().resolve()
        if self.workspace is not None:
            return Path(self.workspace.directory).expanduser().resolve()
        raise ValueError("missing workspace directory")

    @staticmethod
    def _ensure_workspace_file(workspace_dir: Path, relative_path: str) -> Path:
        rel = str(relative_path or "").strip()
        if not rel:
            raise ValueError("missing artifact path")
        candidate = Path(rel).expanduser()
        path = candidate.resolve() if candidate.is_absolute() else (workspace_dir / candidate).resolve()
        if not path.is_relative_to(workspace_dir):
            raise ValueError(f"path is outside workspace: {relative_path}")
        return path

    @staticmethod
    def _read_json(path: Path) -> dict:
        if not path.exists():
            return {}
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}
        return raw if isinstance(raw, dict) else {}

    @staticmethod
    def _write_json(path: Path, payload: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    @staticmethod
    def _redact_text(content: str) -> str:
        redacted = content
        for pattern in _SECRET_PATTERNS:
            redacted = pattern.sub(lambda m: f"{m.group(1)}=<redacted>", redacted)
        return redacted

    @staticmethod
    def _manifest_path(workspace_dir: Path) -> Path:
        return workspace_dir / _FOUNDATION_DIR / "manifest.json"

    @staticmethod
    def _source_signature(paths: list[Path]) -> dict[str, float]:
        signature: dict[str, float] = {}
        for path in paths:
            if path.exists():
                signature[str(path)] = path.stat().st_mtime
        return signature

    def _foundation_sources(self, workspace_dir: Path) -> list[Path]:
        sources = [workspace_dir / "home" / "flow.json", workspace_dir / "home" / "parameters.json"]
        sources.extend(sorted(workspace_dir.glob("*/analysis/*.json")))
        sources.extend(sorted(workspace_dir.glob("*/checklist.json")))
        return sources

    def _foundation_payload(self, workspace_dir: Path) -> dict:
        flow = self._read_json(workspace_dir / "home" / "flow.json")
        parameters = self._read_json(workspace_dir / "home" / "parameters.json")
        metrics: dict[str, dict] = {}
        for metric_path in sorted(workspace_dir.glob("*/analysis/*.json")):
            step = metric_path.parent.parent.name
            metrics[step] = self._read_json(metric_path)
        checklist: dict[str, dict] = {}
        for checklist_path in sorted(workspace_dir.glob("*/checklist.json")):
            checklist[checklist_path.parent.name] = self._read_json(checklist_path)
        return {
            "workspace": str(workspace_dir),
            "flow": flow,
            "parameters": parameters,
            "metrics": metrics,
            "checklist": checklist,
        }

    def _foundation_is_stale(self, workspace_dir: Path, manifest: dict) -> bool:
        recorded = manifest.get("sources", {}) if isinstance(manifest, dict) else {}
        if manifest.get("profile") == "iccd_full_v1" or int(manifest.get("version", 0) or 0) >= 2:
            return recorded != self._foundation_v2_source_signature(workspace_dir)
        return recorded != self._source_signature(self._foundation_sources(workspace_dir))

    def _foundation_v2_source_signature(self, workspace_dir: Path) -> dict[str, float]:
        paths = [workspace_dir / "home" / "flow.json", workspace_dir / "home" / "parameters.json"]
        for stage_dir in workspace_dir.glob("*_*"):
            if not stage_dir.is_dir():
                continue
            for folder in ("output", "feature", "analysis", "report", "data"):
                root = stage_dir / folder
                if root.exists():
                    paths.extend(path for path in root.rglob("*") if path.is_file())
        return self._source_signature(paths)

    def _task_snapshot(self, workspace_dir: Path | None = None) -> list[dict]:
        with _TASKS_LOCK:
            tasks = [dict(task) for task in _TASKS.values()]
        if workspace_dir is None:
            return tasks
        return [task for task in tasks if task.get("workspace") == str(workspace_dir)]

    @staticmethod
    def _workspace_lock(workspace_dir: Path) -> threading.Lock:
        key = str(workspace_dir)
        with _WORKSPACE_LOCKS_LOCK:
            if key not in _WORKSPACE_LOCKS:
                _WORKSPACE_LOCKS[key] = threading.Lock()
            return _WORKSPACE_LOCKS[key]

    @staticmethod
    def _set_nested_parameter(parameters: dict, dotted_path: str, value) -> None:
        parts = dotted_path.split(".")
        cursor = parameters
        for part in parts[:-1]:
            child = cursor.get(part)
            if not isinstance(child, dict):
                child = {}
                cursor[part] = child
            cursor = child
        cursor[parts[-1]] = value

    def _flatten_parameter_updates(self, payload: dict, prefix: str = "") -> dict[str, object]:
        flattened: dict[str, object] = {}
        for key, value in payload.items():
            full_key = f"{prefix}.{key}" if prefix else str(key)
            if isinstance(value, dict):
                flattened.update(self._flatten_parameter_updates(value, full_key))
            else:
                flattened[full_key] = value
        return flattened

    @staticmethod
    def _allowed_step_config_paths(step: str) -> set[str]:
        if step in _ALLOWED_STEP_CONFIG_PATHS:
            return _ALLOWED_STEP_CONFIG_PATHS[step]
        lowered = step.lower()
        for name, paths in _ALLOWED_STEP_CONFIG_PATHS.items():
            if name.lower() == lowered:
                return paths
        return set()

    @staticmethod
    def _step_workspace_dir(workspace_dir: Path, name: str, tool: str) -> Path:
        suffix = "yosys" if tool == "yosys" else tool
        return workspace_dir / f"{name}_{suffix}"

    def _flow_step_dirs_from(self, workspace_dir: Path, start_step: str) -> list[Path]:
        flow = self._read_json(workspace_dir / "home" / "flow.json")
        steps = flow.get("steps", [])
        if not isinstance(steps, list):
            return []
        start_index = None
        for index, step in enumerate(steps):
            if isinstance(step, dict) and str(step.get("name", "")) == start_step:
                start_index = index
                break
        if start_index is None:
            return []
        dirs = []
        for step in steps[start_index:]:
            if not isinstance(step, dict):
                continue
            name = str(step.get("name", "")).strip()
            tool = str(step.get("tool", "")).strip()
            if name and tool:
                dirs.append(self._step_workspace_dir(workspace_dir, name, tool))
        return dirs

    def _cleanup_stale_step_artifacts(self, workspace_dir: Path, start_step: str) -> list[str]:
        """Remove stale outputs/logs for target and downstream steps before rerun.

        Configuration directories are intentionally preserved; this only clears
        generated artifacts that could otherwise make an old result look fresh.
        """
        removed: list[str] = []
        for step_dir in self._flow_step_dirs_from(workspace_dir, start_step):
            if not step_dir.exists() or not step_dir.is_dir():
                continue
            for dirname in _STALE_STEP_DIR_NAMES:
                target = step_dir / dirname
                if target.exists():
                    for path in sorted(target.rglob("*"), reverse=True):
                        if path.is_file() or path.is_symlink():
                            removed.append(str(path.relative_to(workspace_dir)))
                            path.unlink(missing_ok=True)
                        elif path.is_dir():
                            with contextlib.suppress(OSError):
                                path.rmdir()
                target.mkdir(parents=True, exist_ok=True)
            for filename in _STALE_STEP_FILE_NAMES:
                target = step_dir / filename
                if target.exists() and target.is_file():
                    removed.append(str(target.relative_to(workspace_dir)))
                    target.unlink(missing_ok=True)
        return removed

    def get_flow_status(self, request: ECCRequest) -> ECCResponse:
        data = request.data
        try:
            workspace_dir = self._workspace_dir_from_request(data)
            flow = self._read_json(workspace_dir / "home" / "flow.json")
            manifest = self._read_json(self._manifest_path(workspace_dir))
            response_data = {
                "directory": str(workspace_dir),
                "flow": flow,
                "steps": flow.get("steps", []),
                "foundation_stale": self._foundation_is_stale(workspace_dir, manifest) if manifest else True,
                "tasks": self._task_snapshot(workspace_dir),
            }
        except Exception as e:
            return ECCResponse(cmd=request.cmd, response=ResponseEnum.error.value, data={}, message=[str(e)])
        return ECCResponse(
            cmd=request.cmd,
            response=ResponseEnum.success.value,
            data=response_data,
            message=[f"flow status loaded: {workspace_dir}"],
        )

    def get_artifact(self, request: ECCRequest) -> ECCResponse:
        data = request.data
        try:
            workspace_dir = self._workspace_dir_from_request(data)
            path = self._ensure_workspace_file(workspace_dir, str(data.get("path", "")))
            if not path.exists() or not path.is_file():
                return ECCResponse(
                    cmd=request.cmd,
                    response=ResponseEnum.failed.value,
                    data={"path": str(path)},
                    message=[f"artifact not found: {path}"],
                )
            raw = path.read_bytes()
            try:
                content = raw[: _TEXT_ARTIFACT_LIMIT + 1].decode("utf-8")
                binary = False
            except UnicodeDecodeError:
                content = ""
                binary = True
            truncated = len(raw) > _TEXT_ARTIFACT_LIMIT
            if truncated and content:
                content = content[:_TEXT_ARTIFACT_LIMIT]
            response_data = {
                "path": str(path),
                "relative_path": str(path.relative_to(workspace_dir)),
                "binary": binary,
                "truncated": truncated,
                "size_bytes": len(raw),
                "content": self._redact_text(content),
            }
        except Exception as e:
            return ECCResponse(cmd=request.cmd, response=ResponseEnum.error.value, data={}, message=[str(e)])
        return ECCResponse(
            cmd=request.cmd,
            response=ResponseEnum.success.value,
            data=response_data,
            message=[f"artifact loaded: {response_data['relative_path']}"],
        )

    def extract_foundation_data(self, request: ECCRequest) -> ECCResponse:
        try:
            workspace_dir = self._workspace_dir_from_request(request.data)
            foundation_dir = workspace_dir / _FOUNDATION_DIR
            profile = str(request.data.get("profile", "summary_v1")).strip() or "summary_v1"
            if profile == "iccd_full_v1":
                extractor_cls = _foundation_extractor_class()
                result = extractor_cls(workspace_dir, profile=profile).extract(force=_parse_bool(request.data.get("force", False)))
                manifest = result.manifest
                data = {
                    "directory": str(workspace_dir),
                    "foundation_dir": str(result.foundation_dir),
                    "manifest_path": str(self._manifest_path(workspace_dir)),
                    "profile": profile,
                    "stale": False,
                    "manifest": manifest,
                    "summary": result.summary,
                }
            else:
                payload = self._foundation_payload(workspace_dir)
                sources = self._foundation_sources(workspace_dir)
                manifest = {
                    "version": 1,
                    "workspace": str(workspace_dir),
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "sources": self._source_signature(sources),
                    "artifacts": {
                        "summary": str(foundation_dir / "summary.json"),
                        "metrics": str(foundation_dir / "metrics.json"),
                    },
                }
                self._write_json(foundation_dir / "summary.json", payload)
                self._write_json(foundation_dir / "metrics.json", {"metrics": payload["metrics"]})
                self._write_json(foundation_dir / "parameters.json", {"parameters": payload["parameters"]})
                self._write_json(self._manifest_path(workspace_dir), manifest)
                data = {
                    "directory": str(workspace_dir),
                    "foundation_dir": str(foundation_dir),
                    "manifest_path": str(self._manifest_path(workspace_dir)),
                    "profile": profile,
                    "stale": False,
                    "summary": payload,
                }
        except Exception as e:
            return ECCResponse(cmd=request.cmd, response=ResponseEnum.error.value, data={}, message=[str(e)])
        return ECCResponse(
            cmd=request.cmd,
            response=ResponseEnum.success.value,
            data=data,
            message=[f"foundation data extracted: {workspace_dir}"],
        )

    def get_foundation_data(self, request: ECCRequest) -> ECCResponse:
        try:
            workspace_dir = self._workspace_dir_from_request(request.data)
            manifest_path = self._manifest_path(workspace_dir)
            manifest = self._read_json(manifest_path)
            kind = str(request.data.get("kind", "summary")).strip().lower() or "summary"
            entity = str(request.data.get("entity", "")).strip()
            stage = str(request.data.get("stage", "")).strip()
            index_only = _parse_bool(request.data.get("index_only", False))
            foundation_dir = workspace_dir / _FOUNDATION_DIR
            target = self._foundation_kind_path(foundation_dir, kind, entity=entity, stage=stage)
            foundation_root = foundation_dir.resolve()
            target_resolved = target.resolve(strict=False)
            if not target_resolved.is_relative_to(foundation_root):
                raise ValueError("foundation data path escapes foundation directory")
            if target.suffix == ".jsonl":
                content = {
                    "path": str(target),
                    "relative_path": str(target.relative_to(foundation_dir)),
                    "record_count": _jsonl_record_count(target),
                }
                if not index_only and target.exists():
                    content["records"] = [json.loads(line) for line in target.read_text(encoding="utf-8").splitlines() if line.strip()]
            else:
                content = self._read_json(target)
            stale = self._foundation_is_stale(workspace_dir, manifest) if manifest else True
            data = {
                "directory": str(workspace_dir),
                "kind": kind,
                "entity": entity or None,
                "stage": stage or None,
                "manifest": manifest,
                "manifest_path": str(manifest_path),
                "stale": stale,
                "content": content,
            }
        except Exception as e:
            return ECCResponse(cmd=request.cmd, response=ResponseEnum.error.value, data={}, message=[str(e)])
        return ECCResponse(
            cmd=request.cmd,
            response=ResponseEnum.warning.value if data["stale"] else ResponseEnum.success.value,
            data=data,
            message=[f"foundation data {'stale' if data['stale'] else 'current'}: {workspace_dir}"],
        )

    @staticmethod
    def _foundation_kind_path(foundation_dir: Path, kind: str, *, entity: str = "", stage: str = "") -> Path:
        if kind not in _FOUNDATION_KINDS:
            raise ValueError(f"unsupported foundation data kind: {kind}")
        if kind == "manifest":
            return foundation_dir / "manifest.json"
        if kind in {"summary", "stage_index", "canonical_grid", "quality"}:
            return foundation_dir / f"{kind}.json"
        if kind == "ml_view":
            return foundation_dir / "views" / "ml" / "dataset_index.json"
        if kind == "agent_view":
            return foundation_dir / "views" / "agent" / "run_summary.json"
        if kind == "vectors":
            if not entity or not stage:
                raise ValueError("kind=vectors requires entity and stage")
            _validate_foundation_token("entity", entity)
            _validate_foundation_token("stage", stage)
            if entity not in _FOUNDATION_VECTOR_ENTITIES:
                raise ValueError(f"invalid foundation data entity: {entity}")
            return foundation_dir / "vectors" / entity / f"{stage}-00000.jsonl"
        if kind == "maps":
            if not entity or not stage:
                raise ValueError("kind=maps requires entity and stage")
            _validate_foundation_token("entity", entity)
            _validate_foundation_token("stage", stage)
            if entity not in _FOUNDATION_MAP_ENTITIES:
                raise ValueError(f"invalid foundation data entity: {entity}")
            return foundation_dir / "maps" / "canonical" / stage / f"{entity}.json"
        raise ValueError(f"unsupported foundation data kind: {kind}")

    def clone_workspace(self, request: ECCRequest) -> ECCResponse:
        data = request.data
        try:
            source_dir = self._workspace_dir_from_request(data)
            target = str(data.get("target_directory") or data.get("target") or "").strip()
            if not target:
                target_dir = source_dir.parent / f"{source_dir.name}-agent-{uuid.uuid4().hex[:8]}"
            else:
                target_dir = Path(target).expanduser().resolve()
            if target_dir.exists():
                return ECCResponse(
                    cmd=request.cmd,
                    response=ResponseEnum.failed.value,
                    data={"source": str(source_dir), "target": str(target_dir)},
                    message=[f"target workspace already exists: {target_dir}"],
                )
            shutil.copytree(source_dir, target_dir, ignore=shutil.ignore_patterns("foundation_data/ecc"))
        except Exception as e:
            return ECCResponse(cmd=request.cmd, response=ResponseEnum.error.value, data={}, message=[str(e)])
        return ECCResponse(
            cmd=request.cmd,
            response=ResponseEnum.success.value,
            data={"source": str(source_dir), "target": str(target_dir), "workspace_id": str(target_dir)},
            message=[f"workspace cloned: {target_dir}"],
        )

    def _run_from_step_worker(self, task_id: str, workspace_dir: Path, start_step: str, rerun: bool) -> None:
        def update(**fields) -> None:
            with _TASKS_LOCK:
                _TASKS[task_id] = {**_TASKS.get(task_id, {}), **fields, "updated_at": datetime.now(timezone.utc).isoformat()}

        lock = self._workspace_lock(workspace_dir)
        if not lock.acquire(blocking=False):
            update(status="failed", error="workspace is locked by another run")
            return
        try:
            update(status="running")
            service = ECCService()
            load = service.load_workspace(ECCRequest(cmd="load_workspace", data={"directory": str(workspace_dir)}))
            if load.response != ResponseEnum.success.value:
                update(status="failed", error="; ".join(load.message))
                return
            steps = [s.name for s in service.engine_flow.workspace_steps]
            if start_step not in steps:
                update(status="failed", error=f"unknown step: {start_step}")
                return
            removed_artifacts = service._cleanup_stale_step_artifacts(workspace_dir, start_step)
            update(cleaned_artifacts=removed_artifacts)
            for step in steps[steps.index(start_step) :]:
                update(current_step=step)
                gui_notify.notify_to(
                    str(workspace_dir),
                    ECCResponse(
                        cmd=CMDEnum.notify.value,
                        response=ResponseEnum.success.value,
                        data={"type": "flow_progress", "task_id": task_id, "step": step, "status": "running"},
                        message=[f"running {step}"],
                    ),
                )
                result = service.run_step(ECCRequest(cmd="run_step", data={"step": step, "rerun": rerun}))
                if result.response != ResponseEnum.success.value:
                    update(status="failed", current_step=step, result=result.model_dump(), error="; ".join(result.message))
                    return
            update(status="success", current_step="", error="", completed_at=datetime.now(timezone.utc).isoformat())
        except Exception as exc:
            logger.exception("run_from_step: background worker failed")
            update(status="error", error=str(exc))
        finally:
            lock.release()

    def run_from_step(self, request: ECCRequest) -> ECCResponse:
        try:
            workspace_dir = self._workspace_dir_from_request(request.data)
            step = str(request.data.get("step") or "").strip()
            if not step:
                raise ValueError("missing step")
            task_id = uuid.uuid4().hex
            task = {
                "task_id": task_id,
                "workspace": str(workspace_dir),
                "start_step": step,
                "current_step": "",
                "status": "queued",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "error": "",
            }
            with _TASKS_LOCK:
                _TASKS[task_id] = task
            thread = threading.Thread(
                target=self._run_from_step_worker,
                args=(task_id, workspace_dir, step, bool(request.data.get("rerun", True))),
                daemon=True,
            )
            thread.start()
        except Exception as e:
            return ECCResponse(cmd=request.cmd, response=ResponseEnum.error.value, data={}, message=[str(e)])
        return ECCResponse(
            cmd=request.cmd,
            response=ResponseEnum.success.value,
            data=task,
            message=[f"run_from_step queued: {task_id}"],
        )

    def update_parameters(self, request: ECCRequest) -> ECCResponse:
        try:
            workspace_dir = self._workspace_dir_from_request(request.data)
            raw_updates = request.data.get("parameters", {})
            if not isinstance(raw_updates, dict):
                raise ValueError("parameters must be a dict")
            flattened = self._flatten_parameter_updates(raw_updates)
            accepted: dict[str, object] = {}
            rejected: dict[str, object] = {}
            for key, value in flattened.items():
                canonical = _PARAMETER_ALIASES.get(key, key)
                if canonical in _ALLOWED_PARAMETER_PATHS:
                    accepted[canonical] = value
                else:
                    rejected[key] = value
            params_path = workspace_dir / "home" / "parameters.json"
            params = self._read_json(params_path)
            for key, value in accepted.items():
                self._set_nested_parameter(params, key, value)
            write = bool(request.data.get("write", True))
            if write and accepted:
                self._write_json(params_path, params)
            data = {
                "directory": str(workspace_dir),
                "updated": accepted,
                "rejected": rejected,
                "write": write,
                "parameters_path": str(params_path),
            }
        except Exception as e:
            return ECCResponse(cmd=request.cmd, response=ResponseEnum.error.value, data={}, message=[str(e)])
        return ECCResponse(
            cmd=request.cmd,
            response=ResponseEnum.warning.value if data["rejected"] else ResponseEnum.success.value,
            data=data,
            message=[f"updated parameters: {len(data['updated'])}; rejected: {len(data['rejected'])}"],
        )

    def update_step_config(self, request: ECCRequest) -> ECCResponse:
        try:
            workspace_dir = self._workspace_dir_from_request(request.data)
            step = str(request.data.get("step") or "").strip()
            config = request.data.get("config", request.data.get("patch", {}))
            if not step:
                raise ValueError("missing step")
            if not isinstance(config, dict):
                raise ValueError("config must be a dict")
            flattened = self._flatten_parameter_updates(config)
            allowed_paths = self._allowed_step_config_paths(step)
            accepted = {key: value for key, value in flattened.items() if key in allowed_paths}
            rejected = {key: value for key, value in flattened.items() if key not in allowed_paths}
            if rejected:
                return ECCResponse(
                    cmd=request.cmd,
                    response=ResponseEnum.failed.value,
                    data={
                        "directory": str(workspace_dir),
                        "step": step,
                        "updated": {},
                        "rejected": rejected,
                        "effective_on_next_run": False,
                    },
                    message=[f"rejected non-whitelisted step config paths: {', '.join(sorted(rejected))}"],
                )
            audit_path = workspace_dir / "home" / "strategy_overrides.json"
            audit = self._read_json(audit_path) or {"version": 1, "overrides": []}
            overrides = audit.get("overrides", [])
            if not isinstance(overrides, list):
                overrides = []
            reason = str(request.data.get("reason", "")).strip()
            created_at = datetime.now(timezone.utc).isoformat()
            for path, value in accepted.items():
                entry = {
                    "step": step,
                    "tool": "ecc",
                    "path": path,
                    "value": value,
                    "created_at": created_at,
                    "effective_on_next_run": False,
                }
                if reason:
                    entry["reason"] = reason
                overrides.append(entry)
            audit["version"] = audit.get("version", 1)
            audit["overrides"] = overrides
            if accepted:
                self._write_json(audit_path, audit)
            data = {
                "directory": str(workspace_dir),
                "step": step,
                "updated": accepted,
                "rejected": rejected,
                "audit_path": str(audit_path),
                "effective_on_next_run": False,
            }
        except Exception as e:
            return ECCResponse(cmd=request.cmd, response=ResponseEnum.error.value, data={}, message=[str(e)])
        return ECCResponse(
            cmd=request.cmd,
            response=ResponseEnum.success.value,
            data=data,
            message=[f"step config override recorded for audit only: {step}"],
        )

    def create_workspace(self, request: ECCRequest) -> ECCResponse:
        """
        "request" : {
            "directory" : "",
            "pdk" : "",
            "pdk_root" : "",
            "parameters" : {},
            "origin_def" : "",
            "origin_verilog" : "",
            "filelist" : "",
            "rtl_list" : ""
        },
        "response" : {
            "directory" : ""
        }
        """
        from chipcompiler.data import create_workspace as _create_workspace

        # get data
        data = request.data

        # check data

        # process cmd
        input_filelist = data.get("filelist", "")
        if not input_filelist:
            rtl_list = data.get("rtl_list", "")
            rtl_paths = self._normalize_rtl_list(rtl_list)
            if rtl_paths:
                try:
                    input_filelist = self._write_filelist(
                        directory=data.get("directory", ""), rtl_paths=rtl_paths
                    )
                except Exception as e:
                    logger.exception("create_workspace: failed to write filelist from rtl_list")
                    return ECCResponse(
                        cmd=request.cmd,
                        response=ResponseEnum.error.value,
                        data={},
                        message=[f"failed to create filelist from rtl_list: {e}"],
                    )

        try:
            workspace = _create_workspace(
                directory=data.get("directory", ""),
                pdk=data.get("pdk", ""),
                parameters=data.get("parameters", {}),
                origin_def=data.get("origin_def", ""),
                origin_verilog=data.get("origin_verilog", ""),
                input_filelist=input_filelist,
                pdk_root=data.get("pdk_root", ""),
            )
        except Exception as e:
            logger.exception("create_workspace: create_workspace() raised exception")
            return ECCResponse(
                cmd=request.cmd,
                response=ResponseEnum.error.value,
                data={},
                message=[
                    f"create workspace failed : {data.get('directory', '')}, error info is {e}"
                ],
            )

        if workspace is None:
            return ECCResponse(
                cmd=request.cmd,
                response=ResponseEnum.failed.value,
                data={},
                message=[f"create workspace failed : {data.get('directory', '')}"],
            )
        else:
            self.workspace = workspace
            self.__build_flow()

            # 设置 gui_notify 的 workspace_id
            gui_notify.set_workspace(workspace.directory)

            # Attach workspace-level log handler
            self._attach_workspace_log(workspace.directory)

            response_data = {
                "directory": data.get("directory", ""),
                "workspace_id": workspace.directory,  # 前端用于订阅 SSE
            }
            return ECCResponse(
                cmd=request.cmd,
                response=ResponseEnum.success.value,
                data=response_data,
                message=[f"create workspace success : {data.get('directory', '')}"],
            )

    def set_pdk_root(self, request: ECCRequest) -> ECCResponse:
        """
        "request" : {
            "pdk" : "ics55",
            "pdk_root" : "/abs/path/to/pdk"
        },
        "response" : {
            "pdk" : "ics55",
            "pdk_root" : "/abs/path/to/pdk",
            "env_key" : "CHIPCOMPILER_ICS55_PDK_ROOT"
        }
        """
        from chipcompiler.data import get_pdk

        data = request.data
        pdk_name = str(data.get("pdk", "")).strip().lower()
        pdk_root = str(data.get("pdk_root", "")).strip()

        env_key = f"CHIPCOMPILER_{pdk_name.upper()}_PDK_ROOT" if pdk_name else ""
        response_data = {
            "pdk": pdk_name,
            "pdk_root": pdk_root,
            "env_key": env_key,
        }

        # Validate inputs
        error = None
        if not pdk_name:
            error = "missing pdk name"
        elif not pdk_root:
            error = "missing pdk_root"
        elif pdk_name not in {"ics55"}:
            error = f"unsupported pdk '{pdk_name}'"
        elif not os.path.isdir(pdk_root):
            error = f"pdk_root is not a directory: {pdk_root}"

        if error:
            return ECCResponse(
                cmd=request.cmd,
                response=ResponseEnum.failed.value,
                data=response_data,
                message=[f"set pdk root failed: {error}"],
            )

        try:
            pdk = get_pdk(pdk_name=pdk_name, pdk_root=pdk_root)
            resolved_root = pdk.root or pdk_root
            os.environ[env_key] = resolved_root

            response_data["pdk_root"] = resolved_root

            if self.workspace is not None and self.workspace.pdk.name.lower() == pdk_name:
                self.workspace.pdk = pdk
                self.workspace.parameters.data["PDK Root"] = resolved_root
        except Exception as e:
            logger.exception("set_pdk_root: get_pdk() or env update failed")
            return ECCResponse(
                cmd=request.cmd,
                response=ResponseEnum.error.value,
                data=response_data,
                message=[f"set pdk root error: {e}"],
            )

        return ECCResponse(
            cmd=request.cmd,
            response=ResponseEnum.success.value,
            data=response_data,
            message=[f"set pdk root success: {pdk_name} -> {response_data['pdk_root']}"],
        )

    def load_workspace(self, request: ECCRequest) -> ECCResponse:
        """
        "request" : {
            "directory" : ""
        },
        "response" : {
            "directory" : ""
        }
        """
        from chipcompiler.data import load_workspace as _load_workspace

        # get data
        data = request.data

        # check data

        # process cmd
        try:
            workspace = _load_workspace(directory=data.get("directory", ""))
        except Exception as e:
            logger.exception("load_workspace: load_workspace() raised exception")
            return ECCResponse(
                cmd=request.cmd,
                response=ResponseEnum.failed.value,
                data={},
                message=[f"load workspace failed : {data.get('directory', '')}, error info is {e}"],
            )

        if workspace is None:
            return ECCResponse(
                cmd=request.cmd,
                response=ResponseEnum.failed.value,
                data={},
                message=[f"load workspace failed : {data.get('directory', '')}"],
            )
        else:
            self.workspace = workspace
            self.__build_flow()

            # 设置 gui_notify 的 workspace_id
            gui_notify.set_workspace(workspace.directory)

            # Attach workspace-level log handler
            self._attach_workspace_log(workspace.directory)

            response_data = {
                "directory": data.get("directory", ""),
                "workspace_id": workspace.directory,  # 前端用于订阅 SSE
            }
            return ECCResponse(
                cmd=request.cmd,
                response=ResponseEnum.success.value,
                data=response_data,
                message=[f"load workspace success : {data.get('directory', '')}"],
            )

    def delete_workspace(self, request: ECCRequest) -> ECCResponse:
        """
        "request" : {
            "directory" : ""
        },
        "response" : {
            "directory" : ""
        }
        """
        # get data
        data = request.data
        directory = data.get("directory", "")

        # check data
        if (
            self.workspace is None
            or self.workspace.directory != directory
            or not os.path.exists(directory)
        ):
            return ECCResponse(
                cmd=request.cmd,
                response=ResponseEnum.error.value,
                data={},
                message=[f"workspace not exist : {directory}"],
            )

        # process cmd
        self._detach_workspace_log()
        self.engine_flow = None
        self.workspace = None

        # 清除 gui_notify 的 workspace_id
        gui_notify.clear_workspace()

        try:
            import shutil

            shutil.rmtree(directory)
        except Exception:
            logger.exception("delete_workspace: failed to remove workspace directory")

        response_data = {"directory": directory}
        return ECCResponse(
            cmd=request.cmd,
            response=ResponseEnum.success.value,
            data=response_data,
            message=[f"delete workspace success : {directory}"],
        )

    def rtl2gds(self, request: ECCRequest) -> ECCResponse:
        """
        "request" : {
            "rerun" : False
        },
        "response" : {
            "rerun" : False
        }
        """
        # get data
        data = request.data

        response_data = {"rerun": data.get("rerun", False)}

        # check data
        if self.workspace is None or not os.path.exists(self.workspace.directory):
            return ECCResponse(
                cmd=request.cmd,
                response=ResponseEnum.error.value,
                data=response_data,
                message=[f"workspace not exist : {self.workspace.directory}"],
            )

        if self.engine_flow is None:
            return ECCResponse(
                cmd=request.cmd,
                response=ResponseEnum.error.value,
                data=response_data,
                message=[f"rtl2gds flow not exist : {self.workspace.directory}"],
            )

        # process cmd
        failed_step = None
        try:
            if data.get("rerun", False):
                self.engine_flow.clear_states()

            for workspace_step in self.engine_flow.workspace_steps:
                ecc_req = ECCRequest(
                    cmd="run_step",
                    data={"step": workspace_step.name, "rerun": data.get("rerun", False)},
                )
                # get response for each step
                # TBD, need to send response back to gui
                step_response = self.run_step(ecc_req)
                if step_response.response != ResponseEnum.success.value:
                    failed_step = workspace_step.name
                    break
                else:
                    log_file = workspace_step.log.get("file", "")
                    gui_notify.notify_step(
                        step=workspace_step.name,
                        step_path=self.workspace.flow.path,
                        home_page=self.workspace.home.path,
                        log_file=os.path.abspath(log_file) if log_file else "",
                    )
            # self.engine_flow.run_steps()
        except Exception as e:
            logger.exception("rtl2gds: execution failed")
            return ECCResponse(
                cmd=request.cmd,
                response=ResponseEnum.error.value,
                data=response_data,
                message=[f"run rtl2gds failed : {e}"],
            )

        if failed_step is None:
            return ECCResponse(
                cmd=request.cmd,
                response=ResponseEnum.success.value,
                data=response_data,
                message=[f"run rtl2gds success : {self.workspace.directory}"],
            )
        else:
            return ECCResponse(
                cmd=request.cmd,
                response=ResponseEnum.failed.value,
                data=response_data,
                message=[f"run rtl2gds failed in step : {failed_step}"],
            )

    def run_step(self, request: ECCRequest) -> ECCResponse:
        """
        "request" : {
            "step" : "",
            "rerun" : False
        },
        "response" : {
            "step" : "",
            "state" : "Unstart"
        }
        """
        from chipcompiler.data import StateEnum

        # get data
        data = request.data
        step = data.get("step", "")
        rerun = data.get("rerun", "")

        response_data = {"step": step, "state": "Unstart"}

        # check data
        if self.workspace is None or not os.path.exists(self.workspace.directory):
            return ECCResponse(
                cmd=request.cmd,
                response=ResponseEnum.error.value,
                data=response_data,
                message=[f"workspace not exist : {self.workspace.directory}"],
            )

        # process cmd
        state = StateEnum.Unstart
        try:
            state = self.engine_flow.run_step(step, rerun)
        except Exception:
            state = StateEnum.Imcomplete
            logger.exception("run_step: engine_flow.run_step() raised exception")

        response_data["state"] = state.value

        if StateEnum.Success == state:
            return ECCResponse(
                cmd=request.cmd,
                response=ResponseEnum.success.value,
                data=response_data,
                message=[f"run step {step} success : {self.workspace.directory}"],
            )
        else:
            return ECCResponse(
                cmd=request.cmd,
                response=ResponseEnum.failed.value,
                data=response_data,
                message=[
                    f"run step {step} failed with state {state.value} : {self.workspace.directory}"
                ],
            )

    def get_info(self, request: ECCRequest) -> ECCResponse:
        """
        get information by step (defined by StepEnum) and id (defined by InfoEnum)
        "request" : {
            "step" : "",
            "id" : ""
        },
        "response" : {
            "step" : "",
            "id" : "",
            "info" : {}
        }
        """
        # get data
        data = request.data
        step = data.get("step", "")
        id = data.get("id", "")

        response_data = {"step": step, "id": id, "info": {}}

        # check data
        if self.workspace is None or not os.path.exists(self.workspace.directory):
            return ECCResponse(
                cmd=request.cmd,
                response=ResponseEnum.error.value,
                data=response_data,
                message=[f"workspace not exist : {self.workspace.directory}"],
            )

        # process cmd
        try:
            # build information
            from .info import get_step_info

            info = get_step_info(
                workspace=self.workspace, step=self.engine_flow.get_workspace_step(step), id=id
            )

            if len(info) == 0:
                return ECCResponse(
                    cmd=request.cmd,
                    response=ResponseEnum.warning.value,
                    data=response_data,
                    message=[f"no information for step {step} : {self.workspace.directory}"],
                )
            else:
                response_data["info"] = info
        except Exception as e:
            logger.exception("get_info: get_step_info() raised exception")
            return ECCResponse(
                cmd=request.cmd,
                response=ResponseEnum.error.value,
                data=response_data,
                message=[f"get information error for step {step} : {e}"],
            )

        return ECCResponse(
            cmd=request.cmd,
            response=ResponseEnum.success.value,
            data=response_data,
            message=[f"get information success : {step} - {id}"],
        )

    def home_page(self, request: ECCRequest) -> ECCResponse:
        """
        "request" : {},
        "response" : {
            "path" : ""
        }
        """
        if os.path.exists(self.workspace.home.path):
            response_data = {"path": self.workspace.home.path}
            return ECCResponse(
                cmd=request.cmd,
                response=ResponseEnum.success.value,
                data=response_data,
                message=[f"build home page success : {self.workspace.home.path}"],
            )
        else:
            return ECCResponse(
                cmd=request.cmd,
                response=ResponseEnum.failed.value,
                data={},
                message=[f"build home page failed : {self.workspace.home.path}"],
            )
