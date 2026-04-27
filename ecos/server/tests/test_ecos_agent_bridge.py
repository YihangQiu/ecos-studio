from __future__ import annotations

import json
import time
from pathlib import Path

from ecos_server.ecc.schemas import ECCRequest, ResponseEnum
from ecos_server.ecc.services.ecc import ECCService


def _workspace(tmp_path: Path) -> Path:
    root = tmp_path / "ws"
    (root / "home").mkdir(parents=True)
    (root / "log").mkdir()
    (root / "place_dreamplace" / "analysis").mkdir(parents=True)
    (root / "place_dreamplace" / "log").mkdir(parents=True)
    (root / "home" / "flow.json").write_text(
        json.dumps({"steps": [{"name": "place", "tool": "dreamplace", "state": "Success"}]}),
        encoding="utf-8",
    )
    (root / "home" / "parameters.json").write_text(
        json.dumps({"Core": {"Utilitization": 0.4}, "Target density": 0.3}),
        encoding="utf-8",
    )
    (root / "place_dreamplace" / "analysis" / "place_metrics.json").write_text(
        json.dumps({"wns": -0.1}),
        encoding="utf-8",
    )
    (root / "log" / "secret.log").write_text("api_key=abc123\nnormal line", encoding="utf-8")
    return root


def test_get_artifact_rejects_workspace_escape(tmp_path: Path):
    ws = _workspace(tmp_path)
    service = ECCService()
    response = service.get_artifact(
        ECCRequest(cmd="get_artifact", data={"directory": str(ws), "path": "../outside.txt"})
    )
    assert response.response == ResponseEnum.error.value
    assert "outside workspace" in response.message[0]


def test_get_artifact_reads_text_with_redaction(tmp_path: Path):
    ws = _workspace(tmp_path)
    service = ECCService()
    response = service.get_artifact(
        ECCRequest(cmd="get_artifact", data={"directory": str(ws), "path": "log/secret.log"})
    )
    assert response.response == ResponseEnum.success.value
    assert "api_key=<redacted>" in response.data["content"]
    assert "abc123" not in response.data["content"]


def test_extract_foundation_data_writes_manifest_and_stale_detection(tmp_path: Path):
    ws = _workspace(tmp_path)
    service = ECCService()
    extract = service.extract_foundation_data(ECCRequest(cmd="extract_foundation_data", data={"directory": str(ws)}))
    assert extract.response == ResponseEnum.success.value
    manifest = Path(extract.data["manifest_path"])
    assert manifest.exists()
    assert extract.data["stale"] is False

    time.sleep(0.01)
    (ws / "home" / "flow.json").write_text(json.dumps({"steps": []}), encoding="utf-8")
    status = service.get_foundation_data(ECCRequest(cmd="get_foundation_data", data={"directory": str(ws)}))
    assert status.response == ResponseEnum.warning.value
    assert status.data["stale"] is True


def test_update_parameters_allows_whitelist_and_utilization_alias(tmp_path: Path):
    ws = _workspace(tmp_path)
    service = ECCService()
    response = service.update_parameters(
        ECCRequest(
            cmd="update_parameters",
            data={
                "directory": str(ws),
                "parameters": {"Core.Utilization": 0.55, "PDK Root": "/secret/pdk"},
                "write": True,
            },
        )
    )
    assert response.response == ResponseEnum.warning.value
    assert response.data["updated"] == {"Core.Utilitization": 0.55}
    assert "PDK Root" in response.data["rejected"]
    params = json.loads((ws / "home" / "parameters.json").read_text(encoding="utf-8"))
    assert params["Core"]["Utilitization"] == 0.55


def test_update_step_config_writes_audit_only(tmp_path: Path):
    ws = _workspace(tmp_path)
    service = ECCService()
    response = service.update_step_config(
        ECCRequest(
            cmd="update_step_config",
            data={"directory": str(ws), "step": "place", "config": {"PL.GP.Density.target_density": 0.6}},
        )
    )
    assert response.response == ResponseEnum.success.value
    assert response.data["effective_on_next_run"] is False
    audit = json.loads((ws / "home" / "strategy_overrides.json").read_text(encoding="utf-8"))
    assert audit["overrides"][-1]["step"] == "place"
    assert audit["overrides"][-1]["path"] == "PL.GP.Density.target_density"


def test_update_step_config_rejects_non_whitelisted_paths(tmp_path: Path):
    ws = _workspace(tmp_path)
    service = ECCService()
    response = service.update_step_config(
        ECCRequest(
            cmd="update_step_config",
            data={"directory": str(ws), "step": "place", "config": {"note": "not a real ECOS config path"}},
        )
    )
    assert response.response == ResponseEnum.failed.value
    assert response.data["rejected"] == {"note": "not a real ECOS config path"}
    assert not (ws / "home" / "strategy_overrides.json").exists()


def test_cleanup_stale_outputs_removes_step_and_downstream_artifacts(tmp_path: Path):
    ws = _workspace(tmp_path)
    (ws / "CTS_ecc" / "output").mkdir(parents=True)
    (ws / "CTS_ecc" / "log").mkdir(parents=True)
    (ws / "place_dreamplace" / "output").mkdir(parents=True)
    (ws / "place_dreamplace" / "config").mkdir(parents=True)
    (ws / "place_dreamplace" / "output" / "old.def").write_text("old", encoding="utf-8")
    (ws / "place_dreamplace" / "analysis" / "old.json").write_text("{}", encoding="utf-8")
    (ws / "place_dreamplace" / "config" / "keep.json").write_text("{}", encoding="utf-8")
    (ws / "CTS_ecc" / "output" / "old.def").write_text("old", encoding="utf-8")
    (ws / "CTS_ecc" / "log" / "old.log").write_text("old", encoding="utf-8")
    (ws / "home" / "flow.json").write_text(
        json.dumps(
            {
                "steps": [
                    {"name": "place", "tool": "dreamplace", "state": "Success"},
                    {"name": "CTS", "tool": "ecc", "state": "Success"},
                ]
            }
        ),
        encoding="utf-8",
    )

    service = ECCService()
    removed = service._cleanup_stale_step_artifacts(ws, "place")

    assert "place_dreamplace/output/old.def" in removed
    assert "place_dreamplace/analysis/old.json" in removed
    assert "CTS_ecc/output/old.def" in removed
    assert "CTS_ecc/log/old.log" in removed
    assert (ws / "place_dreamplace" / "output").is_dir()
    assert (ws / "place_dreamplace" / "analysis").is_dir()
    assert (ws / "CTS_ecc" / "output").is_dir()
    assert (ws / "CTS_ecc" / "log").is_dir()
    assert (ws / "place_dreamplace" / "config" / "keep.json").exists()
