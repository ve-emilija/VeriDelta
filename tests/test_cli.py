import json

import pytest

from veridelta.cli import main

pytest.importorskip("networkx")


@pytest.fixture
def sv_tree(tmp_path):
    d = tmp_path / "dv"
    d.mkdir()
    (d / "dv_base_env_cfg.sv").write_text("class dv_base_env_cfg extends uvm_object;\nendclass\n")
    (d / "uart_env_cfg.sv").write_text(
        "class uart_env_cfg #(type T = int) extends dv_base_env_cfg #(T);\nendclass\n")
    (d / "uart_agent_cfg.sv").write_text("class uart_agent_cfg extends dv_base_env_cfg;\nendclass\n")
    return d


@pytest.fixture
def graph_dir(tmp_path, sv_tree):
    out = tmp_path / "graph"
    assert main(["build-graph", str(sv_tree), "--out", str(out)]) == 0
    return out


def test_version(capsys):
    with pytest.raises(SystemExit) as e:
        main(["--version"])
    assert e.value.code == 0
    assert "veridelta" in capsys.readouterr().out


def test_build_graph_writes_both_files(graph_dir):
    adj = json.loads((graph_dir / "adjacency.json").read_text())
    types = json.loads((graph_dir / "node_types.json").read_text())
    assert adj["dv_base_env_cfg"] == ["uart_agent_cfg", "uart_env_cfg"]
    assert types["uart_env_cfg"]["type"] == "CFG"


def test_blast_radius_json(graph_dir, capsys):
    assert main(["blast-radius", "dv_base_env_cfg", "--graph", str(graph_dir), "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert [it["node"] for it in out["blast_radius"]] == ["uart_agent_cfg", "uart_env_cfg"]
    assert out["truncated"] is False


def test_blast_radius_table(graph_dir, capsys):
    assert main(["blast-radius", "dv_base_env_cfg", "--graph", str(graph_dir)]) == 0
    assert "2 downstream classes" in capsys.readouterr().out


def test_unknown_class_suggests_a_close_match(graph_dir, capsys):
    assert main(["blast-radius", "dv_base_env_cf", "--graph", str(graph_dir)]) == 2
    assert "dv_base_env_cfg" in capsys.readouterr().err


def test_missing_graph_directory_is_a_clean_error(tmp_path, capsys):
    assert main(["blast-radius", "x", "--graph", str(tmp_path / "none")]) == 2
    assert "build-graph" in capsys.readouterr().err


def test_analyze_mock_runs_offline(graph_dir, capsys):
    assert main(["analyze", "dv_base_env_cfg", "--graph", str(graph_dir), "--mock", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert set(out["predictions"]) == {"uart_agent_cfg", "uart_env_cfg"}


def test_analyze_without_key_explains_how_to_continue(graph_dir, monkeypatch, capsys):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert main(["analyze", "dv_base_env_cfg", "--graph", str(graph_dir)]) == 2
    err = capsys.readouterr().err
    assert "ANTHROPIC_API_KEY" in err and "--mock" in err


def test_build_graph_on_empty_directory_is_a_clean_error(tmp_path, capsys):
    assert main(["build-graph", str(tmp_path), "--out", str(tmp_path / "g")]) == 2
