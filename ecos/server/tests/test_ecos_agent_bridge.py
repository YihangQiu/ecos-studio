from __future__ import annotations

import json
import time
from pathlib import Path

import ecos_server.ecc.services.ecc as ecc_service_module
from ecos_server.ecc.schemas import ECCRequest, ECCResponse, ResponseEnum
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
    extract = service.extract_foundation_data(
        ECCRequest(cmd="extract_foundation_data", data={"directory": str(ws)})
    )
    assert extract.response == ResponseEnum.success.value
    manifest = Path(extract.data["manifest_path"])
    assert manifest.exists()
    assert extract.data["stale"] is False

    time.sleep(0.01)
    (ws / "home" / "flow.json").write_text(json.dumps({"steps": []}), encoding="utf-8")
    status = service.get_foundation_data(
        ECCRequest(cmd="get_foundation_data", data={"directory": str(ws)})
    )
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
            data={
                "directory": str(ws),
                "step": "place",
                "config": {"PL.GP.Density.target_density": 0.6},
            },
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
            data={
                "directory": str(ws),
                "step": "place",
                "config": {"note": "not a real ECOS config path"},
            },
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


def test_clone_workspace_rewrites_embedded_workspace_paths(tmp_path: Path):
    source = tmp_path / "source"
    target = tmp_path / "target"
    (source / "home").mkdir(parents=True)
    (source / "route_ecc" / "config").mkdir(parents=True)
    (source / "route_ecc" / "subflow.json").write_text(
        json.dumps({"path": str(source / "route_ecc" / "subflow.json")}),
        encoding="utf-8",
    )
    (source / "route_ecc" / "config" / "db_default_config.json").write_text(
        json.dumps(
            {
                "INPUT": {
                    "def_path": str(source / "legalization_dreamplace" / "output" / "gcd.def.gz")
                }
            }
        ),
        encoding="utf-8",
    )
    (source / "home" / "home.json").write_text(
        json.dumps({"flow": str(source / "home" / "flow.json")}),
        encoding="utf-8",
    )

    service = ECCService()
    response = service.clone_workspace(
        ECCRequest(
            cmd="clone_workspace",
            data={"directory": str(source), "target_directory": str(target)},
        )
    )

    assert response.response == ResponseEnum.success.value
    target_db = json.loads(
        (target / "route_ecc" / "config" / "db_default_config.json").read_text(encoding="utf-8")
    )
    target_subflow = json.loads((target / "route_ecc" / "subflow.json").read_text(encoding="utf-8"))
    target_home = json.loads((target / "home" / "home.json").read_text(encoding="utf-8"))
    combined = json.dumps([target_db, target_subflow, target_home])
    assert str(source) not in combined
    assert str(target) in combined


def test_prepare_rerun_rebuilds_cloned_step_config_paths(tmp_path: Path):
    source = tmp_path / "source"
    target = tmp_path / "target"
    route_dir = target / "route_ecc"
    (source / "home").mkdir(parents=True)
    (target / "home").mkdir(parents=True)
    (route_dir / "config").mkdir(parents=True)
    (target / "legalization_dreamplace" / "output").mkdir(parents=True)
    (target / "origin").mkdir(parents=True)
    (target / "home" / "flow.json").write_text(
        json.dumps(
            {
                "steps": [
                    {"name": "legalization", "tool": "dreamplace", "state": "Success"},
                    {"name": "route", "tool": "ecc", "state": "Success"},
                ]
            }
        ),
        encoding="utf-8",
    )
    (route_dir / "config" / "db_default_config.json").write_text(
        json.dumps({"INPUT": {"def_path": str(source / "old.def.gz")}}),
        encoding="utf-8",
    )
    (route_dir / "config" / "rt_default_config.json").write_text(
        json.dumps({"RT": {"-temp_directory_path": str(source / "route_ecc" / "data" / "rt")}}),
        encoding="utf-8",
    )

    service = ECCService()
    rebuilt = service._prepare_rerun_step_configs(target, "route")

    assert "route_ecc/config/db_default_config.json" in rebuilt
    assert "route_ecc/config/rt_default_config.json" in rebuilt
    db_config = json.loads(
        (route_dir / "config" / "db_default_config.json").read_text(encoding="utf-8")
    )
    rt_config = json.loads(
        (route_dir / "config" / "rt_default_config.json").read_text(encoding="utf-8")
    )
    assert str(source) not in json.dumps([db_config, rt_config])
    assert db_config["INPUT"]["def_path"] == str(
        target / "legalization_dreamplace" / "output" / "gcd_legalization.def.gz"
    )
    assert rt_config["RT"]["-temp_directory_path"] == str(route_dir / "data" / "rt")


def test_extract_foundation_data_iccd_full_profile_and_indexed_kinds(tmp_path: Path):
    ws = _workspace(tmp_path)
    stage = ws / "place_dreamplace"
    (stage / "output").mkdir(parents=True)
    (stage / "feature" / "density_map").mkdir(parents=True)
    (stage / "feature" / "gcell_patch_map" / "density_map").mkdir(parents=True)
    early_router = stage / "data" / "rt" / "rt_temp_directory" / "early_router"
    early_router.mkdir(parents=True)
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
                            {
                                "type": "box",
                                "layer": 0,
                                "path": [[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]],
                            }
                        ],
                    }
                ],
                "nets": [
                    {
                        "name": "n1",
                        "wires": [
                            {
                                "layer": "MET2",
                                "points": [[0, 0], [10, 0]],
                            }
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (stage / "feature" / "density_map" / "place_allcell_density.csv").write_text(
        "1,2\n3,4\n", encoding="utf-8"
    )
    (
        stage / "feature" / "gcell_patch_map" / "density_map" / "place_allcell_density.csv"
    ).write_text("1,2\n3,4\n", encoding="utf-8")
    (early_router / "gcell.info").write_text(
        "0,0,0,0,10,10\n0,1,0,10,10,20\n1,0,10,0,20,10\n1,1,10,10,20,20\n",
        encoding="utf-8",
    )
    (early_router / "route.guide").write_text(
        "\n".join(
            [
                "guide net_name",
                "pin grid_x grid_y real_x real_y layer energy name",
                "wire grid1_x grid1_y grid2_x grid2_y real1_x real1_y real2_x real2_y layer",
                "via grid_x grid_y real_x real_y layer1 layer2",
                "guide n1",
                "wire 0 0 1 0 0 0 10 0 MET2",
                "wire 0 0 0 1 0 0 0 10 MET3",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (early_router / "net_map_MET2.csv").write_text("8,0\n1,2\n", encoding="utf-8")
    (early_router / "supply_map_MET2.csv").write_text("5,1\n3,4\n", encoding="utf-8")
    (early_router / "net_map_MET3.csv").write_text("2,5\n4,8\n", encoding="utf-8")
    (early_router / "supply_map_MET3.csv").write_text("7,6\n9,10\n", encoding="utf-8")

    service = ECCService()
    extract = service.extract_foundation_data(
        ECCRequest(
            cmd="extract_foundation_data",
            data={"directory": str(ws), "profile": "iccd_full_v1", "include_raw_refs": True, "export_legacy_debug": True},
        )
    )
    assert extract.response == ResponseEnum.success.value
    assert extract.data["profile"] == "iccd_full_v1"
    manifest = extract.data["manifest"]
    assert manifest["contract_name"] == "foundation_data/ecc"
    assert manifest["storage_format"] == "parquet+json_views"
    assert "tables" in manifest
    assert "version" not in manifest
    assert "profile" not in manifest
    assert manifest["created_at"]
    assert manifest["generated_by"]["profile"] == "iccd_full_v1"
    assert "home/flow.json" in manifest["sources"]
    assert "place_dreamplace/analysis/place_metrics.json" in manifest["sources"]
    assert all(not Path(source).is_absolute() for source in manifest["sources"])
    assert manifest["artifacts"]["canonical_grid"] == "foundation_data/ecc/canonical_grid.json"

    indexed = service.get_foundation_data(
        ECCRequest(
            cmd="get_foundation_data",
            data={
                "directory": str(ws),
                "kind": "vectors",
                "entity": "instances",
                "stage": "place",
                "index_only": True,
            },
        )
    )
    assert indexed.response == ResponseEnum.success.value
    assert indexed.data["kind"] == "vectors"
    assert indexed.data["entity"] == "instances"
    assert indexed.data["stage"] == "place"
    assert indexed.data["content"]["path"].endswith("vectors/instances/place.jsonl")
    assert indexed.data["content"]["record_count"] == 1

    grid = service.get_foundation_data(
        ECCRequest(cmd="get_foundation_data", data={"directory": str(ws), "kind": "canonical_grid"})
    )
    assert grid.response == ResponseEnum.success.value
    assert grid.data["content"]["rows"] == 2

    for kind in ("summary", "quality", "agent_view", "ml_view"):
        response = service.get_foundation_data(
            ECCRequest(cmd="get_foundation_data", data={"directory": str(ws), "kind": kind})
        )
        assert response.response == ResponseEnum.success.value
        assert response.data["kind"] == kind

    maps = service.get_foundation_data(
        ECCRequest(
            cmd="get_foundation_data",
            data={"directory": str(ws), "kind": "maps", "entity": "density", "stage": "place"},
        )
    )
    assert maps.response == ResponseEnum.success.value
    assert maps.data["content"]["stage"] == "place"
    assert maps.data["content"]["category"] == "density"
    assert "place_allcell_density" not in maps.data["content"]["maps"]
    assert maps.data["content"]["maps"]["allcell_density"]["values"][0] == {
        "patch_id": 0,
        "row": 0,
        "col": 0,
        "value": 1.0,
    }

    congestion = service.get_foundation_data(
        ECCRequest(
            cmd="get_foundation_data",
            data={"directory": str(ws), "kind": "maps", "entity": "congestion", "stage": "place"},
        )
    )
    assert congestion.response == ResponseEnum.success.value
    assert congestion.data["content"]["category"] == "congestion"
    assert [
        item["value"] for item in congestion.data["content"]["maps"]["horizontal"]["values"]
    ] == [-2.0, -2.0, 3.0, -1.0]
    assert [item["value"] for item in congestion.data["content"]["maps"]["vertical"]["values"]] == [
        -5.0,
        -2.0,
        -5.0,
        -1.0,
    ]


def test_get_foundation_data_exposes_parquet_schema_table_index_and_query(tmp_path: Path):
    ws = _workspace(tmp_path)
    stage = ws / "place_dreamplace"
    (stage / "output").mkdir(parents=True)
    (stage / "feature" / "density_map").mkdir(parents=True)
    (stage / "feature" / "gcell_patch_map" / "density_map").mkdir(parents=True)
    early_router = stage / "data" / "rt" / "rt_temp_directory" / "early_router"
    early_router.mkdir(parents=True)
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
                            {
                                "type": "box",
                                "layer": 0,
                                "path": [[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]],
                            }
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (stage / "feature" / "density_map" / "place_allcell_density.csv").write_text(
        "1,2\n3,4\n", encoding="utf-8"
    )
    (
        stage / "feature" / "gcell_patch_map" / "density_map" / "place_allcell_density.csv"
    ).write_text("1,2\n3,4\n", encoding="utf-8")
    (early_router / "gcell.info").write_text(
        "0,0,0,0,10,10\n0,1,0,10,10,20\n1,0,10,0,20,10\n1,1,10,10,20,20\n",
        encoding="utf-8",
    )

    service = ECCService()
    extract = service.extract_foundation_data(
        ECCRequest(
            cmd="extract_foundation_data",
            data={"directory": str(ws), "profile": "iccd_full_v1", "include_raw_refs": True},
        )
    )
    assert extract.response == ResponseEnum.success.value

    schema = service.get_foundation_data(
        ECCRequest(cmd="get_foundation_data", data={"directory": str(ws), "kind": "schema"})
    )
    assert schema.response == ResponseEnum.success.value
    assert schema.data["content"]["storage_format"] == "parquet+json_views"
    assert "run_stage_patch_features" in schema.data["content"]["tables"]
    feature_columns = schema.data["content"]["tables"]["run_stage_patch_features"]["columns"]
    for column in (
        "instance_count_center",
        "macro_count",
        "cross_patch_net_count",
        "net_count_overlap",
        "rudy_horizontal",
        "margin_horizontal",
        "critical_path_count",
        "drc_count",
    ):
        assert column in feature_columns

    table_index = service.get_foundation_data(
        ECCRequest(cmd="get_foundation_data", data={"directory": str(ws), "kind": "table_index"})
    )
    assert table_index.response == ResponseEnum.success.value
    assert table_index.data["content"]["patches"]["row_count"] == 4

    task_view = service.get_foundation_data(
        ECCRequest(cmd="get_foundation_data", data={"directory": str(ws), "kind": "task_view"})
    )
    assert task_view.response == ResponseEnum.success.value
    assert "progressive_patch_route_demand_capacity" in task_view.data["content"]["tasks"]

    query = service.get_foundation_data(
        ECCRequest(
            cmd="get_foundation_data",
            data={
                "directory": str(ws),
                "kind": "query_table",
                "table": "run_stage_patch_features",
                "stage": "place",
                "patch_id": 0,
                "columns": ["stage_name", "patch_id", "cell_density"],
                "limit": 5,
            },
        )
    )
    assert query.response == ResponseEnum.success.value
    assert query.data["content"]["table"] == "run_stage_patch_features"
    assert query.data["content"]["row_count"] == 1
    assert query.data["content"]["records"] == [
        {"stage_name": "place", "patch_id": 0, "cell_density": 1.0}
    ]
    assert query.data["content"]["truncated"] is False

    filtered = service.get_foundation_data(
        ECCRequest(
            cmd="get_foundation_data",
            data={
                "directory": str(ws),
                "kind": "query_table",
                "table": "run_stage_patch_features",
                "filter": {"stage_name": "place", "patch_id": 0},
                "columns": ["stage_name", "patch_id"],
            },
        )
    )
    assert filtered.response == ResponseEnum.success.value
    assert filtered.data["content"]["filters"] == {"stage_name": "place", "patch_id": 0}
    assert filtered.data["content"]["records"] == [{"stage_name": "place", "patch_id": 0}]


def test_query_table_rejects_invalid_table_column_and_missing_legacy_vectors(tmp_path: Path):
    ws = _workspace(tmp_path)
    stage = ws / "place_dreamplace"
    (stage / "output").mkdir(parents=True)
    (stage / "feature" / "gcell_patch_map" / "density_map").mkdir(parents=True)
    early_router = stage / "data" / "rt" / "rt_temp_directory" / "early_router"
    early_router.mkdir(parents=True)
    (stage / "output" / "gcd_place.json").write_text(
        json.dumps(
            {
                "design name": "gcd",
                "diearea": {"path": [[0, 0], [20, 0], [20, 20], [0, 20], [0, 0]]},
                "data": [],
            }
        ),
        encoding="utf-8",
    )
    (
        stage / "feature" / "gcell_patch_map" / "density_map" / "place_allcell_density.csv"
    ).write_text("1,2\n3,4\n", encoding="utf-8")
    (early_router / "gcell.info").write_text(
        "0,0,0,0,10,10\n0,1,0,10,10,20\n1,0,10,0,20,10\n1,1,10,10,20,20\n",
        encoding="utf-8",
    )

    service = ECCService()
    service.extract_foundation_data(
        ECCRequest(cmd="extract_foundation_data", data={"directory": str(ws), "profile": "iccd_full_v1"})
    )

    bad_table = service.get_foundation_data(
        ECCRequest(
            cmd="get_foundation_data",
            data={"directory": str(ws), "kind": "query_table", "table": "../patches"},
        )
    )
    bad_column = service.get_foundation_data(
        ECCRequest(
            cmd="get_foundation_data",
            data={
                "directory": str(ws),
                "kind": "query_table",
                "table": "patches",
                "columns": ["patch_id", "../../secret"],
            },
        )
    )
    bad_bool_patch = service.get_foundation_data(
        ECCRequest(
            cmd="get_foundation_data",
            data={"directory": str(ws), "kind": "query_table", "table": "patches", "patch_id": True},
        )
    )
    bad_negative_patch = service.get_foundation_data(
        ECCRequest(
            cmd="get_foundation_data",
            data={"directory": str(ws), "kind": "query_table", "table": "patches", "patch_id": -1},
        )
    )
    bad_text_patch = service.get_foundation_data(
        ECCRequest(
            cmd="get_foundation_data",
            data={"directory": str(ws), "kind": "query_table", "table": "patches", "patch_id": "abc"},
        )
    )
    bad_filter_column = service.get_foundation_data(
        ECCRequest(
            cmd="get_foundation_data",
            data={
                "directory": str(ws),
                "kind": "query_table",
                "table": "patches",
                "filter": {"../../secret": 0},
            },
        )
    )
    ok_text_patch = service.get_foundation_data(
        ECCRequest(
            cmd="get_foundation_data",
            data={
                "directory": str(ws),
                "kind": "query_table",
                "table": "patches",
                "patch_id": "0",
                "columns": ["patch_id"],
                "limit": 1,
            },
        )
    )
    legacy_vectors = service.get_foundation_data(
        ECCRequest(
            cmd="get_foundation_data",
            data={"directory": str(ws), "kind": "vectors", "entity": "instances", "stage": "place"},
        )
    )

    assert bad_table.response == ResponseEnum.error.value
    assert "invalid foundation data table" in bad_table.message[0]
    assert bad_column.response == ResponseEnum.error.value
    assert "invalid foundation data column" in bad_column.message[0]
    assert bad_bool_patch.response == ResponseEnum.error.value
    assert "patch_id must be a non-negative integer" in bad_bool_patch.message[0]
    assert bad_negative_patch.response == ResponseEnum.error.value
    assert "patch_id must be a non-negative integer" in bad_negative_patch.message[0]
    assert bad_text_patch.response == ResponseEnum.error.value
    assert "patch_id must be a non-negative integer" in bad_text_patch.message[0]
    assert bad_filter_column.response == ResponseEnum.error.value
    assert "invalid foundation data column" in bad_filter_column.message[0]
    assert ok_text_patch.response == ResponseEnum.success.value
    assert ok_text_patch.data["content"]["records"] == [{"patch_id": 0}]
    assert legacy_vectors.response == ResponseEnum.error.value
    assert "legacy foundation data output is not available" in legacy_vectors.message[0]



def test_foundation_parquet_contract_has_joinable_provenance_and_artifacts(tmp_path: Path):
    ws = _workspace(tmp_path)
    stage = ws / "place_dreamplace"
    (stage / "output").mkdir(parents=True)
    (stage / "feature" / "gcell_patch_map" / "density_map").mkdir(parents=True)
    early_router = stage / "data" / "rt" / "rt_temp_directory" / "early_router"
    early_router.mkdir(parents=True)
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
                            {
                                "type": "box",
                                "layer": 0,
                                "path": [[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]],
                            }
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (stage / "output" / "gcd_place.def").write_text(
        "\n".join(
            [
                "VERSION 5.8 ;",
                "DESIGN gcd ;",
                "UNITS DISTANCE MICRONS 1000 ;",
                "DIEAREA ( 0 0 ) ( 20 20 ) ;",
                "COMPONENTS 1 ;",
                "- U1 BUF + PLACED ( 0 0 ) N ;",
                "END COMPONENTS",
                "PINS 1 ;",
                "- IN + NET n1 + DIRECTION INPUT + USE SIGNAL + PLACED ( 0 0 ) N ;",
                "END PINS",
                "NETS 1 ;",
                "- n1 ( PIN IN ) ( U1 A ) + ROUTED MET2 ( 0 0 ) ( 10 0 ) ;",
                "END NETS",
                "END DESIGN",
            ]
        ),
        encoding="utf-8",
    )
    (
        stage / "feature" / "gcell_patch_map" / "density_map" / "place_allcell_density.csv"
    ).write_text("1,2\n3,4\n", encoding="utf-8")
    (early_router / "gcell.info").write_text(
        "0,0,0,0,10,10\n0,1,0,10,10,20\n1,0,10,0,20,10\n1,1,10,10,20,20\n",
        encoding="utf-8",
    )
    (early_router / "route.guide").write_text(
        "\n".join(
            [
                "guide net_name",
                "pin grid_x grid_y real_x real_y layer energy name",
                "wire grid1_x grid1_y grid2_x grid2_y real1_x real1_y real2_x real2_y layer",
                "via grid_x grid_y real_x real_y layer1 layer2",
                "guide n1",
                "wire 0 0 1 0 0 0 10 0 MET2",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (early_router / "net_map_MET2.csv").write_text("8,0\n1,2\n", encoding="utf-8")
    (early_router / "supply_map_MET2.csv").write_text("5,1\n3,4\n", encoding="utf-8")

    service = ECCService()
    extract = service.extract_foundation_data(
        ECCRequest(
            cmd="extract_foundation_data",
            data={"directory": str(ws), "profile": "iccd_full_v1", "include_raw_refs": True},
        )
    )
    assert extract.response == ResponseEnum.success.value

    import pyarrow.parquet as pq

    foundation_dir = Path(extract.data["foundation_dir"])
    schema = json.loads((foundation_dir / "schema.json").read_text(encoding="utf-8"))
    route_label_columns = schema["tables"]["run_patch_route_labels"]["columns"]
    assert "horizontal_demand_capacity" in route_label_columns
    assert "vertical_demand_capacity" in route_label_columns
    assert "union_demand_capacity" in route_label_columns
    assert "horizontal_overflow" not in route_label_columns
    assert "vertical_overflow" not in route_label_columns
    assert "union_overflow" not in route_label_columns
    assert schema["tables"]["run_patch_route_label_layers"]["primary_key"] == [
        "design_id",
        "run_id",
        "patch_id",
        "layer_name",
        "direction",
    ]
    layer_label_columns = schema["tables"]["run_patch_route_label_layers"]["columns"]
    assert "demand_capacity" in layer_label_columns
    assert "overflow" not in layer_label_columns

    def table_rows(name: str, columns: list[str] | None = None) -> list[dict]:
        return pq.read_table(foundation_dir / schema["tables"][name]["path"], columns=columns).to_pylist()

    layer_pk = schema["tables"]["run_patch_route_label_layers"]["primary_key"]
    layer_rows = table_rows("run_patch_route_label_layers", layer_pk)
    assert len(layer_rows) == len({tuple(row[column] for column in layer_pk) for row in layer_rows})

    provenance_ids = {row["provenance_id"] for row in table_rows("provenance", ["provenance_id"])}
    for table_name in ("run_stage_patch_maps", "run_stage_patch_features", "stage_deltas"):
        refs = {
            row["provenance_id"]
            for row in table_rows(table_name, ["provenance_id"])
            if row["provenance_id"]
        }
        assert refs <= provenance_ids

    artifact_ids = {row["artifact_id"] for row in table_rows("artifacts", ["artifact_id"])}
    for table_name, column in (
        ("run_patch_route_labels", "label_source_artifact_id"),
        ("run_patch_route_label_layers", "source_artifact_id"),
        ("stage_metrics", "source_artifact_id"),
    ):
        refs = {row[column] for row in table_rows(table_name, [column]) if row[column]}
        assert refs <= artifact_ids

    semantic_rows = table_rows(
        "semantic_blocks",
        [
            "block_payload",
            "source_doc",
            "source_field_path",
            "preserved_reason",
            "future_normalization_plan",
        ],
    )
    assert semantic_rows
    assert all(row["source_doc"] for row in semantic_rows)
    assert all(row["source_field_path"] for row in semantic_rows)
    assert all(row["preserved_reason"] for row in semantic_rows)
    assert all(row["future_normalization_plan"] for row in semantic_rows)
    assert not any("vectors/" in row["block_payload"] or "maps/" in row["block_payload"] for row in semantic_rows)

    query_net = service.get_foundation_data(
        ECCRequest(
            cmd="get_foundation_data",
            data={
                "directory": str(ws),
                "kind": "query_table",
                "table": "nets",
                "entity_key": "n1",
                "columns": ["net_key"],
            },
        )
    )
    assert query_net.response == ResponseEnum.success.value
    assert query_net.data["content"]["filters"] == {"net_key": "n1"}
    assert query_net.data["content"]["records"] == [{"net_key": "n1"}]

    query_artifact = service.get_foundation_data(
        ECCRequest(
            cmd="get_foundation_data",
            data={
                "directory": str(ws),
                "kind": "query_table",
                "table": "artifacts",
                "filter": {"relative_path": "place_dreamplace/output/gcd_place.def"},
                "columns": ["relative_path", "artifact_type"],
            },
        )
    )
    assert query_artifact.response == ResponseEnum.success.value, query_artifact.message
    assert query_artifact.data["content"]["filters"] == {"relative_path": "place_dreamplace/output/gcd_place.def"}
    assert query_artifact.data["content"]["records"] == [
        {"relative_path": "place_dreamplace/output/gcd_place.def", "artifact_type": "def"}
    ]

def test_get_foundation_data_rejects_path_traversal(tmp_path: Path):
    ws = _workspace(tmp_path)
    service = ECCService()
    service.extract_foundation_data(
        ECCRequest(
            cmd="extract_foundation_data", data={"directory": str(ws), "profile": "iccd_full_v1"}
        )
    )

    bad_kind = service.get_foundation_data(
        ECCRequest(
            cmd="get_foundation_data", data={"directory": str(ws), "kind": "../../home/flow"}
        )
    )
    bad_entity = service.get_foundation_data(
        ECCRequest(
            cmd="get_foundation_data",
            data={
                "directory": str(ws),
                "kind": "vectors",
                "entity": "../instances",
                "stage": "place",
            },
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
            data={
                "directory": str(ws),
                "kind": "vectors",
                "entity": "instances",
                "stage": "/tmp/place",
            },
        )
    )
    bad_stage_token = service.get_foundation_data(
        ECCRequest(
            cmd="get_foundation_data",
            data={
                "directory": str(ws),
                "kind": "vectors",
                "entity": "instances",
                "stage": "place/../../x",
            },
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


def test_query_table_merges_base_delta_layout_sources(tmp_path: Path):
    import pyarrow as pa
    import pyarrow.parquet as pq

    ws = _workspace(tmp_path)
    base_root = tmp_path / "design_base" / "bench" / "design" / "foundation_data" / "ecc"
    variant_root = ws / "foundation_data" / "ecc"
    (base_root / "tables").mkdir(parents=True)
    (variant_root / "tables").mkdir(parents=True)
    pq.write_table(pa.Table.from_pylist([{"patch_id": 1, "x": 10.0, "y": 20.0}]), base_root / "tables" / "patches.parquet")
    pq.write_table(
        pa.Table.from_pylist([{"patch_id": 1, "stage_name": "route", "label": 0.25}]),
        variant_root / "tables" / "run_patch_route_labels.parquet",
    )
    schema = {
        "schema_version": "foundation-data-ecc-parquet-v1",
        "contract_name": "foundation_data/ecc",
        "storage_format": "parquet+json_views",
        "tables": {
            "patches": {"columns": ["patch_id", "x", "y"]},
            "run_patch_route_labels": {"columns": ["patch_id", "stage_name", "label"]},
        },
    }
    (variant_root / "schema.json").write_text(json.dumps(schema), encoding="utf-8")
    (variant_root / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "foundation-data-ecc-parquet-v1",
                "contract_name": "foundation_data/ecc",
                "storage_format": "parquet+json_views",
                "storage_layout": "base_delta_v1",
                "base_manifest_path": str(base_root / "manifest.json"),
                "tables": {
                    "patches": {
                        "path": "tables/patches.parquet",
                        "sources": [{"root": "design_base", "path": "tables/patches.parquet"}],
                    },
                    "run_patch_route_labels": {
                        "path": "tables/run_patch_route_labels.parquet",
                        "sources": [{"root": "variant_delta", "path": "tables/run_patch_route_labels.parquet"}],
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    (base_root / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "foundation-data-ecc-parquet-v1",
                "contract_name": "foundation_data/ecc",
                "storage_format": "parquet+json_views",
                "tables": {"patches": {"path": "tables/patches.parquet"}},
            }
        ),
        encoding="utf-8",
    )

    service = ECCService()
    response = service.get_foundation_data(
        ECCRequest(
            cmd="get_foundation_data",
            data={
                "directory": str(ws),
                "kind": "query_table",
                "table": "patches",
                "columns": ["patch_id", "x"],
                "patch_id": 1,
            },
        )
    )

    assert response.response in {ResponseEnum.success.value, ResponseEnum.warning.value}, response.message
    assert response.data["content"]["records"] == [{"patch_id": 1, "x": 10.0}]


def test_extract_foundation_data_forwards_base_delta_scope_options(tmp_path: Path, monkeypatch):
    captured: dict[str, object] = {}

    class _FakeExtractor:
        def __init__(self, workspace_dir: Path, *, profile: str) -> None:
            self.workspace_dir = Path(workspace_dir)
            self.profile = profile

        def extract(self, **kwargs):
            from types import SimpleNamespace

            captured.update(kwargs)
            foundation_dir = self.workspace_dir / "foundation_data" / "ecc"
            manifest = {
                "schema_version": "foundation-data-ecc-parquet-v1",
                "contract_name": "foundation_data/ecc",
                "storage_format": "parquet+json_views",
                "storage_layout": "base_delta_v1",
                "base_manifest_path": kwargs["base_manifest_path"],
                "tables": {},
            }
            return SimpleNamespace(
                foundation_dir=foundation_dir,
                manifest=manifest,
                summary={"ok": True},
            )

    monkeypatch.setattr("ecos_server.ecc.services.ecc._foundation_extractor_class", lambda: _FakeExtractor)
    ws = _workspace(tmp_path)
    base_manifest = tmp_path / "design_base" / "manifest.json"
    service = ECCService()

    response = service.extract_foundation_data(
        ECCRequest(
            cmd="extract_foundation_data",
            data={
                "directory": str(ws),
                "profile": "iccd_full_v1",
                "scope": "variant_delta",
                "base_manifest_path": str(base_manifest),
            },
        )
    )

    assert response.response == ResponseEnum.success.value
    assert captured["scope"] == "variant_delta"
    assert captured["base_manifest_path"] == str(base_manifest)
    assert response.data["manifest"]["storage_layout"] == "base_delta_v1"


def test_extract_foundation_data_timeout_terminates_worker(tmp_path: Path, monkeypatch):
    events: list[str] = []

    class _FakeQueue:
        def close(self) -> None:
            events.append("queue.close")

        def join_thread(self) -> None:
            events.append("queue.join_thread")

    class _HangingProcess:
        pid = 12345
        exitcode = None

        def __init__(self, *, target, args) -> None:
            self._alive_checks = 0

        def start(self) -> None:
            events.append("process.start")

        def join(self, timeout=None) -> None:
            events.append(f"process.join:{timeout}")

        def is_alive(self) -> bool:
            self._alive_checks += 1
            return self._alive_checks == 1

        def terminate(self) -> None:
            events.append("process.terminate")

        def kill(self) -> None:
            events.append("process.kill")

    class _FakeContext:
        def Queue(self, maxsize: int = 0):
            return _FakeQueue()

        def Process(self, *, target, args):
            return _HangingProcess(target=target, args=args)

    monkeypatch.setattr(
        ecc_service_module.multiprocessing,
        "get_context",
        lambda method: _FakeContext(),
    )
    ws = _workspace(tmp_path)
    service = ECCService()

    start = time.monotonic()
    response = service.extract_foundation_data(
        ECCRequest(
            cmd="extract_foundation_data",
            data={
                "directory": str(ws),
                "profile": "iccd_full_v1",
                "timeout_seconds": 0.1,
            },
        )
    )
    elapsed = time.monotonic() - start

    assert response.response == ResponseEnum.error.value
    assert "timed out" in response.message[0]
    assert elapsed < 2.0
    assert "process.terminate" in events
    assert "process.kill" not in events
    assert events[-2:] == ["queue.close", "queue.join_thread"]


def test_iccd_full_profile_timeout_uses_spawn_context(tmp_path: Path, monkeypatch):
    calls: list[str] = []

    class _FakeQueue:
        def __init__(self, maxsize: int = 0) -> None:
            self.maxsize = maxsize

        def get_nowait(self):
            return {"status": "success"}

        def close(self) -> None:
            calls.append("queue.close")

        def join_thread(self) -> None:
            calls.append("queue.join_thread")

    class _FakeProcess:
        pid = 23456
        exitcode = 0

        def __init__(self, *, target, args) -> None:
            calls.append("context.Process")
            self.target = target
            self.args = args
            self._alive_checks = 0

        def start(self) -> None:
            calls.append("process.start")

        def join(self, timeout=None) -> None:
            calls.append(f"process.join:{timeout}")

        def is_alive(self) -> bool:
            self._alive_checks += 1
            return self._alive_checks == 1

        def terminate(self) -> None:
            raise AssertionError("successful worker must not be terminated")

        def kill(self) -> None:
            raise AssertionError("successful worker must not be killed")

    class _FakeContext:
        def Queue(self, maxsize: int = 0):
            calls.append("context.Queue")
            return _FakeQueue(maxsize=maxsize)

        def Process(self, *, target, args):
            return _FakeProcess(target=target, args=args)

    def _fake_get_context(method: str):
        calls.append(f"get_context:{method}")
        return _FakeContext()

    def _forbidden_default_queue(*args, **kwargs):
        raise AssertionError("timeout worker must not use the default multiprocessing.Queue")

    def _forbidden_default_process(*args, **kwargs):
        raise AssertionError("timeout worker must not use the default multiprocessing.Process")

    monkeypatch.setattr(ecc_service_module.multiprocessing, "get_context", _fake_get_context)
    monkeypatch.setattr(ecc_service_module.multiprocessing, "Queue", _forbidden_default_queue)
    monkeypatch.setattr(ecc_service_module.multiprocessing, "Process", _forbidden_default_process)

    ecc_service_module._run_iccd_full_profile_with_timeout(
        tmp_path,
        "iccd_full_v1",
        {},
        timeout_seconds=1.0,
    )

    assert "get_context:spawn" in calls
    assert "context.Queue" in calls
    assert "context.Process" in calls
    join_timeouts = [
        float(call.removeprefix("process.join:"))
        for call in calls
        if call.startswith("process.join:")
    ]
    assert len(join_timeouts) == 1
    assert 0.0 < join_timeouts[0] <= 1.0


def test_iccd_full_profile_timeout_forwards_worker_progress_logs(tmp_path: Path, monkeypatch, caplog):
    class _FakeQueue:
        def __init__(self, maxsize: int = 0) -> None:
            self.items = [
                {"status": "progress", "message": "foundation_stage start name=write_vectors workspace=/tmp/ws"},
                {"status": "success"},
            ]

        def get_nowait(self):
            if not self.items:
                raise ecc_service_module.queue.Empty
            return self.items.pop(0)

        def close(self) -> None:
            pass

        def join_thread(self) -> None:
            pass

    class _FakeProcess:
        pid = 34567
        exitcode = 0

        def __init__(self, *, target, args) -> None:
            self._alive_checks = 0

        def start(self) -> None:
            pass

        def join(self, timeout=None) -> None:
            pass

        def is_alive(self) -> bool:
            self._alive_checks += 1
            return self._alive_checks == 1

        def terminate(self) -> None:
            raise AssertionError("successful worker must not be terminated")

        def kill(self) -> None:
            raise AssertionError("successful worker must not be killed")

    class _FakeContext:
        def Queue(self, maxsize: int = 0):
            return _FakeQueue(maxsize=maxsize)

        def Process(self, *, target, args):
            return _FakeProcess(target=target, args=args)

    monkeypatch.setattr(ecc_service_module.multiprocessing, "get_context", lambda method: _FakeContext())

    with caplog.at_level("INFO", logger="ecos.api"):
        ecc_service_module._run_iccd_full_profile_with_timeout(
            tmp_path,
            "iccd_full_v1",
            {},
            timeout_seconds=1.0,
        )

    messages = [record.getMessage() for record in caplog.records]
    assert any(
        "worker pid=34567 progress foundation_stage start name=write_vectors" in message
        for message in messages
    )


def test_foundation_bool_options_parse_explicit_false_strings(tmp_path: Path):
    ws = _workspace(tmp_path)
    service = ECCService()
    service.extract_foundation_data(
        ECCRequest(
            cmd="extract_foundation_data",
            data={"directory": str(ws), "profile": "iccd_full_v1", "force": "false", "export_legacy_debug": True},
        )
    )

    response = service.get_foundation_data(
        ECCRequest(
            cmd="get_foundation_data",
            data={
                "directory": str(ws),
                "kind": "vectors",
                "entity": "instances",
                "stage": "place",
                "index_only": "false",
            },
        )
    )

    assert response.response == ResponseEnum.success.value
    assert "records" in response.data["content"]


def test_iccd_foundation_stale_detects_new_source_files(tmp_path: Path):
    ws = _workspace(tmp_path)
    service = ECCService()
    service.extract_foundation_data(
        ECCRequest(
            cmd="extract_foundation_data", data={"directory": str(ws), "profile": "iccd_full_v1"}
        )
    )

    time.sleep(0.01)
    new_csv = ws / "place_dreamplace" / "feature" / "density_map" / "new_density.csv"
    new_csv.parent.mkdir(parents=True, exist_ok=True)
    new_csv.write_text("1,2\n", encoding="utf-8")

    status = service.get_foundation_data(
        ECCRequest(cmd="get_foundation_data", data={"directory": str(ws)})
    )

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
                            {
                                "type": "box",
                                "layer": 0,
                                "path": [[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]],
                            }
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
    assert manifest["options"] == {"stages": ["place"], "include_raw_refs": False, "export_legacy_debug": False}
    assert "raw_refs" not in manifest["artifacts"]
    assert [item["name"] for item in response.data["summary"]["flow"]["steps"]] == ["place"]
    assert not (ws / "foundation_data" / "ecc" / "raw_refs" / "artifacts.json").exists()


def test_run_from_step_forwards_timeout_seconds_to_worker(monkeypatch, tmp_path: Path):
    captured: dict[str, object] = {}

    def fake_worker(
        self,
        task_id,
        workspace_dir,
        start_step,
        rerun,
        timeout_seconds=None,
        stale_task_seconds=None,
        end_step=None,
    ):
        captured.update(
            {
                "task_id": task_id,
                "workspace_dir": workspace_dir,
                "start_step": start_step,
                "rerun": rerun,
                "timeout_seconds": timeout_seconds,
                "stale_task_seconds": stale_task_seconds,
                "end_step": end_step,
            }
        )

    class InlineThread:
        def __init__(self, target, args, daemon):
            self._target = target
            self._args = args
            self.daemon = daemon

        def start(self):
            self._target(*self._args)

    ws = _workspace(tmp_path)
    service = ECCService()
    monkeypatch.setattr(ECCService, "_run_from_step_worker", fake_worker)
    monkeypatch.setattr("ecos_server.ecc.services.ecc.threading.Thread", InlineThread)

    response = service.run_from_step(
        ECCRequest(
            cmd="run_from_step",
            data={
                "directory": str(ws),
                "step": "place",
                "rerun": True,
                "timeout_seconds": 5400,
                "stale_task_seconds": 900,
                "end_step": "route",
            },
        )
    )

    assert response.response == ResponseEnum.success.value
    assert captured["workspace_dir"] == ws
    assert captured["start_step"] == "place"
    assert captured["rerun"] is True
    assert captured["timeout_seconds"] == 5400.0
    assert captured["stale_task_seconds"] == 900.0
    assert captured["end_step"] == "route"
    assert response.data["timeout_seconds"] == 5400.0
    assert response.data["stale_task_seconds"] == 900.0
    assert response.data["end_step"] == "route"


def test_run_from_step_worker_stops_at_end_step_and_leaves_drc_filler_unrun(
    monkeypatch, tmp_path: Path
):
    ws = _workspace(tmp_path)
    flow_path = ws / "home" / "flow.json"
    flow_path.write_text(
        json.dumps(
            {
                "steps": [
                    {"name": "place", "tool": "dreamplace", "state": "Success"},
                    {"name": "CTS", "tool": "ecc", "state": "Success"},
                    {"name": "route", "tool": "ecc", "state": "Success"},
                    {"name": "drc", "tool": "ecc", "state": "Pending"},
                    {"name": "filler", "tool": "ecc", "state": "Pending"},
                ]
            }
        ),
        encoding="utf-8",
    )
    task_id = "task-route-slice"
    from ecos_server.ecc.services import ecc as ecc_module

    with ecc_module._TASKS_LOCK:
        ecc_module._TASKS[task_id] = {
            "task_id": task_id,
            "workspace": str(ws),
            "status": "queued",
        }

    run_steps: list[str] = []
    cleanup_args: list[tuple[str, str | None]] = []
    config_args: list[tuple[str, str | None]] = []

    def fake_load_workspace(self, request):
        self.workspace = type("Workspace", (), {"directory": str(ws)})()
        self.engine_flow = type(
            "EngineFlow",
            (),
            {
                "workspace_steps": [
                    type("Step", (), {"name": name})()
                    for name in ("place", "CTS", "route", "drc", "filler")
                ]
            },
        )()
        return ECCResponse(
            cmd="load_workspace",
            response=ResponseEnum.success.value,
            data={},
            message=["load workspace success"],
        )

    def fake_run_step(self, request):
        run_steps.append(str(request.data["step"]))
        return ECCResponse(
            cmd="run_step",
            response=ResponseEnum.success.value,
            data={"step": request.data["step"], "state": "Success"},
            message=[f"run step {request.data['step']} success"],
        )

    def fake_cleanup(self, workspace_dir, start_step, end_step=None):
        cleanup_args.append((start_step, end_step))
        return []

    def fake_prepare(self, workspace_dir, start_step, end_step=None):
        config_args.append((start_step, end_step))
        return []

    monkeypatch.setattr(ECCService, "load_workspace", fake_load_workspace)
    monkeypatch.setattr(ECCService, "_refresh_workspace_pdk_root", lambda self, workspace_dir: "")
    monkeypatch.setattr(ECCService, "_cleanup_stale_step_artifacts", fake_cleanup)
    monkeypatch.setattr(ECCService, "_prepare_rerun_step_configs", fake_prepare)
    monkeypatch.setattr(ECCService, "run_step", fake_run_step)
    monkeypatch.setattr("ecos_server.ecc.services.ecc.gui_notify.notify_to", lambda *args, **kwargs: None)

    service = ECCService()
    service._run_from_step_worker(task_id, ws, "place", True, timeout_seconds=10.0, end_step="route")

    tasks = {task["task_id"]: task for task in service._task_snapshot(ws)}
    assert tasks[task_id]["status"] == "success"
    assert tasks[task_id]["end_step"] == "route"
    assert run_steps == ["place", "CTS", "route"]
    assert cleanup_args == [("place", "route")]
    assert config_args == [("place", "route")]


def test_run_from_step_worker_records_runtime_on_unknown_step(tmp_path: Path):
    ws = _workspace(tmp_path)
    service = ECCService()
    task_id = "task-runtime"
    from ecos_server.ecc.services import ecc as ecc_module

    with ecc_module._TASKS_LOCK:
        ecc_module._TASKS[task_id] = {
            "task_id": task_id,
            "workspace": str(ws),
            "status": "queued",
        }

    service._run_from_step_worker(task_id, ws, "missing", True, timeout_seconds=1.0)

    tasks = {task["task_id"]: task for task in service._task_snapshot(ws)}
    assert tasks[task_id]["status"] == "failed"
    assert tasks[task_id]["error"]
    assert isinstance(tasks[task_id]["runtime_seconds"], float)


def test_run_from_step_worker_fails_when_step_result_leaves_flow_ongoing(
    monkeypatch, tmp_path: Path
):
    ws = _workspace(tmp_path)
    service = ECCService()
    flow_path = ws / "home" / "flow.json"
    flow_path.write_text(
        json.dumps({"steps": [{"name": "place", "tool": "dreamplace", "state": "Ongoing"}]}),
        encoding="utf-8",
    )
    task_id = "task-stale-step"
    from ecos_server.ecc.services import ecc as ecc_module

    with ecc_module._TASKS_LOCK:
        ecc_module._TASKS[task_id] = {
            "task_id": task_id,
            "workspace": str(ws),
            "status": "queued",
        }

    def fake_load_workspace(self, request):
        self.workspace = type(
            "Workspace",
            (),
            {
                "directory": str(ws),
                "flow": type(
                    "Flow",
                    (),
                    {
                        "data": {
                            "steps": [
                                {"name": "place", "tool": "dreamplace", "state": "Ongoing"}
                            ]
                        }
                    },
                )(),
            },
        )()
        self.engine_flow = type(
            "EngineFlow",
            (),
            {"workspace_steps": [type("Step", (), {"name": "place"})()]},
        )()
        return ECCResponse(
            cmd="load_workspace",
            response=ResponseEnum.success.value,
            data={},
            message=["load workspace success"],
        )

    def fake_run_step(self, request):
        return ECCResponse(
            cmd="run_step",
            response=ResponseEnum.success.value,
            data={"step": "place", "state": "Success"},
            message=["run step place success"],
        )

    monkeypatch.setattr(ECCService, "load_workspace", fake_load_workspace)
    monkeypatch.setattr(ECCService, "_refresh_workspace_pdk_root", lambda self, workspace_dir: "")
    monkeypatch.setattr(ECCService, "_cleanup_stale_step_artifacts", lambda self, workspace_dir, step: [])
    monkeypatch.setattr(ECCService, "_prepare_rerun_step_configs", lambda self, workspace_dir, step: [])
    monkeypatch.setattr(ECCService, "run_step", fake_run_step)
    monkeypatch.setattr("ecos_server.ecc.services.ecc.gui_notify.notify_to", lambda *args, **kwargs: None)

    service._run_from_step_worker(task_id, ws, "place", True, timeout_seconds=10.0)

    tasks = {task["task_id"]: task for task in service._task_snapshot(ws)}
    assert tasks[task_id]["status"] == "failed"
    assert "did not reach Success" in tasks[task_id]["error"]
    assert tasks[task_id]["current_step"] == "place"


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


def test_prepare_rerun_refreshes_dreamplace_config_parameters_and_paths(tmp_path: Path):
    source = tmp_path / "source"
    target = tmp_path / "target"
    place_dir = target / "place_dreamplace"
    (source / "home").mkdir(parents=True)
    (target / "home").mkdir(parents=True)
    (target / "origin").mkdir(parents=True)
    (place_dir / "config").mkdir(parents=True)
    (place_dir / "output").mkdir(parents=True)
    (target / "home" / "flow.json").write_text(
        json.dumps({"steps": [{"name": "place", "tool": "dreamplace", "state": "Success"}]}),
        encoding="utf-8",
    )
    (target / "home" / "parameters.json").write_text(
        json.dumps(
            {
                "Design": "ysyx_24070003",
                "Target density": 0.63,
                "Target overflow": 0.08,
                "Cell padding x": 700,
                "Routability opt flag": 1,
            }
        ),
        encoding="utf-8",
    )
    (target / "origin" / "ysyx_24070003.def").write_text("VERSION 5.8 ;", encoding="utf-8")
    (target / "origin" / "ysyx_24070003.v").write_text("module top; endmodule", encoding="utf-8")
    (place_dir / "config" / "dreamplace.json").write_text(
        json.dumps(
            {
                "def_input": str(source / "old.def"),
                "verilog_input": str(source / "old.v"),
                "result_dir": str(source / "place_dreamplace" / "output"),
                "base_design_name": "old",
                "target_density": 0.8,
                "stop_overflow": 0.1,
                "cell_padding_x": 600,
                "routability_opt_flag": 0,
            }
        ),
        encoding="utf-8",
    )

    service = ECCService()
    rebuilt = service._prepare_rerun_step_configs(target, "place")

    assert "place_dreamplace/config/dreamplace.json" in rebuilt
    dreamplace = json.loads((place_dir / "config" / "dreamplace.json").read_text(encoding="utf-8"))
    assert str(source) not in json.dumps(dreamplace)
    assert dreamplace["def_input"] == str(target / "origin" / "ysyx_24070003.def")
    assert dreamplace["verilog_input"] == str(target / "origin" / "ysyx_24070003.v")
    assert dreamplace["result_dir"] == str(place_dir / "output")
    assert dreamplace["base_design_name"] == "ysyx_24070003"
    assert dreamplace["target_density"] == 0.63
    assert dreamplace["stop_overflow"] == 0.08
    assert dreamplace["cell_padding_x"] == 700
    assert dreamplace["routability_opt_flag"] == 1


def test_refresh_workspace_pdk_root_uses_explicit_available_path(tmp_path: Path):
    ws = _workspace(tmp_path)
    pdk_root = tmp_path / "icsprout55-pdk"
    for rel_path in (
        "prtech/techLEF/N551P6M_ecos.lef",
        "IP/STD_cell/ics55_LLSC_H7C_V1p10C100/ics55_LLSC_H7CR/lef/ics55_LLSC_H7CR_ecos.lef",
        "IP/STD_cell/ics55_LLSC_H7C_V1p10C100/ics55_LLSC_H7CL/lef/ics55_LLSC_H7CL_ecos.lef",
    ):
        path = pdk_root / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("VERSION 5.8 ;\n", encoding="utf-8")

    service = ECCService()
    refreshed = service._refresh_workspace_pdk_root(ws, str(pdk_root))

    assert refreshed == str(pdk_root.resolve())
    params = json.loads((ws / "home" / "parameters.json").read_text(encoding="utf-8"))
    assert params["PDK Root"] == str(pdk_root.resolve())


def test_refresh_workspace_pdk_root_rejects_plain_directory(tmp_path: Path):
    ws = _workspace(tmp_path)
    pdk_root = tmp_path / "not-a-pdk"
    pdk_root.mkdir()

    service = ECCService()
    refreshed = service._refresh_workspace_pdk_root(ws, str(pdk_root))

    assert refreshed is None
    params = json.loads((ws / "home" / "parameters.json").read_text(encoding="utf-8"))
    assert "PDK Root" not in params


def test_clone_workspace_skips_stale_foundation_data(tmp_path: Path):
    source = tmp_path / "source"
    target = tmp_path / "target"
    (source / "home").mkdir(parents=True)
    (source / "foundation_data" / "ecc").mkdir(parents=True)
    (source / "foundation_data" / "ecc" / "manifest.json").write_text(
        json.dumps({"stale": True}), encoding="utf-8"
    )

    service = ECCService()
    response = service.clone_workspace(
        ECCRequest(
            cmd="clone_workspace",
            data={"directory": str(source), "target_directory": str(target)},
        )
    )

    assert response.response == ResponseEnum.success.value
    assert not (target / "foundation_data").exists()
