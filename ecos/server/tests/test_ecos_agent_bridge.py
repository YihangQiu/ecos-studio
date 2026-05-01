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


def test_extract_foundation_data_iccd_full_profile_and_indexed_kinds(tmp_path: Path):
    ws = _workspace(tmp_path)
    stage = ws / "place_dreamplace"
    (stage / "output").mkdir(parents=True)
    (stage / "feature" / "density_map").mkdir(parents=True)
    (stage / "feature" / "egr_congestion_map").mkdir(parents=True)
    (stage / "output" / "gcd_place.json").write_text(
        json.dumps(
            {
                "design name": "gcd",
                "diearea": {"path": [[0, 0], [20, 0], [20, 20], [0, 20], [0, 0]]},
                "data": [
                    {
                        "type": "group",
                        "struct name": "Instance_U1",
                        "children": [
                            {"type": "box", "layer": 0, "path": [[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]]}
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (stage / "feature" / "density_map" / "place_allcell_density.csv").write_text("1,2\n3,4\n", encoding="utf-8")
    (stage / "feature" / "egr_congestion_map" / "place_egr_horizontal_overflow.csv").write_text("5\n", encoding="utf-8")
    (stage / "feature" / "egr_congestion_map" / "place_egr_vertical_overflow.csv").write_text("7\n", encoding="utf-8")

    service = ECCService()
    extract = service.extract_foundation_data(
        ECCRequest(
            cmd="extract_foundation_data",
            data={"directory": str(ws), "profile": "iccd_full_v1", "include_raw_refs": True},
        )
    )
    assert extract.response == ResponseEnum.success.value
    assert extract.data["profile"] == "iccd_full_v1"
    assert "canonical_grid" in extract.data["manifest"]["artifacts"]

    indexed = service.get_foundation_data(
        ECCRequest(
            cmd="get_foundation_data",
            data={"directory": str(ws), "kind": "vectors", "entity": "instances", "stage": "place", "index_only": True},
        )
    )
    assert indexed.response == ResponseEnum.success.value
    assert indexed.data["kind"] == "vectors"
    assert indexed.data["entity"] == "instances"
    assert indexed.data["stage"] == "place"
    assert indexed.data["content"]["path"].endswith("vectors/instances/place-00000.jsonl")
    assert indexed.data["content"]["record_count"] == 1

    grid = service.get_foundation_data(
        ECCRequest(cmd="get_foundation_data", data={"directory": str(ws), "kind": "canonical_grid"})
    )
    assert grid.response == ResponseEnum.success.value
    assert grid.data["content"]["rows"] == 2

    for kind in ("summary", "quality", "agent_view", "ml_view"):
        response = service.get_foundation_data(ECCRequest(cmd="get_foundation_data", data={"directory": str(ws), "kind": kind}))
        assert response.response == ResponseEnum.success.value
        assert response.data["kind"] == kind

    maps = service.get_foundation_data(
        ECCRequest(cmd="get_foundation_data", data={"directory": str(ws), "kind": "maps", "entity": "density", "stage": "place"})
    )
    assert maps.response == ResponseEnum.success.value
    assert maps.data["content"]["place_allcell_density"] == [[1.0, 2.0], [3.0, 4.0]]


def test_get_foundation_data_rejects_path_traversal(tmp_path: Path):
    ws = _workspace(tmp_path)
    service = ECCService()
    service.extract_foundation_data(
        ECCRequest(cmd="extract_foundation_data", data={"directory": str(ws), "profile": "iccd_full_v1"})
    )

    bad_kind = service.get_foundation_data(
        ECCRequest(cmd="get_foundation_data", data={"directory": str(ws), "kind": "../../home/flow"})
    )
    bad_entity = service.get_foundation_data(
        ECCRequest(
            cmd="get_foundation_data",
            data={"directory": str(ws), "kind": "vectors", "entity": "../instances", "stage": "place"},
        )
    )
    bad_stage_parent = service.get_foundation_data(
        ECCRequest(
            cmd="get_foundation_data",
            data={"directory": str(ws), "kind": "vectors", "entity": "instances", "stage": ".."},
        )
    )
    bad_stage_abs = service.get_foundation_data(
        ECCRequest(
            cmd="get_foundation_data",
            data={"directory": str(ws), "kind": "vectors", "entity": "instances", "stage": "/tmp/place"},
        )
    )
    bad_stage_token = service.get_foundation_data(
        ECCRequest(
            cmd="get_foundation_data",
            data={"directory": str(ws), "kind": "vectors", "entity": "instances", "stage": "place/../../x"},
        )
    )

    assert bad_kind.response == ResponseEnum.error.value
    assert "unsupported foundation data kind" in bad_kind.message[0]
    assert bad_entity.response == ResponseEnum.error.value
    assert "invalid foundation data entity" in bad_entity.message[0]
    assert bad_stage_parent.response == ResponseEnum.error.value
    assert "invalid foundation data stage" in bad_stage_parent.message[0]
    assert bad_stage_abs.response == ResponseEnum.error.value
    assert "invalid foundation data stage" in bad_stage_abs.message[0]
    assert bad_stage_token.response == ResponseEnum.error.value
    assert "invalid foundation data stage" in bad_stage_token.message[0]


def test_foundation_bool_options_parse_explicit_false_strings(tmp_path: Path):
    ws = _workspace(tmp_path)
    service = ECCService()
    service.extract_foundation_data(
        ECCRequest(cmd="extract_foundation_data", data={"directory": str(ws), "profile": "iccd_full_v1", "force": "false"})
    )

    response = service.get_foundation_data(
        ECCRequest(
            cmd="get_foundation_data",
            data={"directory": str(ws), "kind": "vectors", "entity": "instances", "stage": "place", "index_only": "false"},
        )
    )

    assert response.response == ResponseEnum.success.value
    assert "records" in response.data["content"]


def test_iccd_foundation_stale_detects_new_source_files(tmp_path: Path):
    ws = _workspace(tmp_path)
    service = ECCService()
    service.extract_foundation_data(
        ECCRequest(cmd="extract_foundation_data", data={"directory": str(ws), "profile": "iccd_full_v1"})
    )

    time.sleep(0.01)
    new_csv = ws / "place_dreamplace" / "feature" / "density_map" / "new_density.csv"
    new_csv.parent.mkdir(parents=True, exist_ok=True)
    new_csv.write_text("1,2\n", encoding="utf-8")

    status = service.get_foundation_data(ECCRequest(cmd="get_foundation_data", data={"directory": str(ws)}))

    assert status.response == ResponseEnum.warning.value
    assert status.data["stale"] is True


def test_extract_foundation_data_forwards_stage_filter_and_raw_refs_option(tmp_path: Path):
    ws = _workspace(tmp_path)
    stage = ws / "place_dreamplace"
    (stage / "output").mkdir(parents=True)
    (stage / "output" / "gcd_place.json").write_text(
        json.dumps(
            {
                "design name": "gcd",
                "diearea": {"path": [[0, 0], [20, 0], [20, 20], [0, 20], [0, 0]]},
                "data": [
                    {
                        "type": "group",
                        "struct name": "Instance_U1",
                        "children": [
                            {"type": "box", "layer": 0, "path": [[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]]}
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    service = ECCService()
    response = service.extract_foundation_data(
        ECCRequest(
            cmd="extract_foundation_data",
            data={
                "directory": str(ws),
                "profile": "iccd_full_v1",
                "stages": ["place"],
                "include_raw_refs": "false",
            },
        )
    )

    assert response.response == ResponseEnum.success.value
    manifest = response.data["manifest"]
    assert manifest["options"] == {"stages": ["place"], "include_raw_refs": False}
    assert "raw_refs" not in manifest["artifacts"]
    assert [item["name"] for item in response.data["summary"]["stages"]] == ["place"]
    assert not (ws / "foundation_data" / "ecc" / "raw_refs" / "artifacts.json").exists()


def test_extract_foundation_data_rejects_unknown_stage_filter(tmp_path: Path):
    ws = _workspace(tmp_path)
    service = ECCService()
    response = service.extract_foundation_data(
        ECCRequest(
            cmd="extract_foundation_data",
            data={"directory": str(ws), "profile": "iccd_full_v1", "stages": ["route"]},
        )
    )

    assert response.response == ResponseEnum.error.value
    assert "unknown foundation extraction stage" in response.message[0]
