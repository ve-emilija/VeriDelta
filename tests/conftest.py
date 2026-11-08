import json
import os
import subprocess

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture
def adjacency():
    """A small diamond-free hierarchy:

    dv_base_env_cfg -> cip_base_env_cfg -> uart_env_cfg, spi_env_cfg
                    -> dv_base_agent_cfg -> uart_agent_cfg
    """
    return {
        "dv_base_env_cfg": ["cip_base_env_cfg", "dv_base_agent_cfg"],
        "cip_base_env_cfg": ["spi_env_cfg", "uart_env_cfg"],
        "dv_base_agent_cfg": ["uart_agent_cfg"],
        "uart_env_cfg": [], "spi_env_cfg": [], "uart_agent_cfg": [],
    }


@pytest.fixture
def node_types():
    t = {"dv_base_env_cfg": "CFG", "cip_base_env_cfg": "CFG", "dv_base_agent_cfg": "CFG",
         "uart_env_cfg": "CFG", "spi_env_cfg": "CFG", "uart_agent_cfg": "CFG"}
    return {n: {"type": v} for n, v in t.items()}


@pytest.fixture
def history():
    return {
        "aaaaaaaa11": {"date": "2024-01-01T00:00:00", "subject": "uart cfg", "ip_block": "uart",
                       "changed_nodes": ["uart_env_cfg"]},
        "bbbbbbbb22": {"date": "2024-03-01T00:00:00", "subject": "spi cfg", "ip_block": "spi",
                       "changed_nodes": ["spi_env_cfg"]},
        "cccccccc33": {"date": "2024-05-01T00:00:00", "subject": "base cfg", "ip_block": "dv",
                       "changed_nodes": ["dv_base_env_cfg"]},
        "dddddddd44": {"date": "2024-07-01T00:00:00", "subject": "unrelated", "ip_block": "xxx",
                       "changed_nodes": ["zzz_unknown"]},
    }


@pytest.fixture
def paper_data():
    """Shipped paper data; tests that need it are skipped if the checkout is partial."""
    def load(*parts):
        path = os.path.join(REPO, *parts)
        if not os.path.exists(path):
            pytest.skip(f"{path} not present")
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return load


@pytest.fixture
def repo(tmp_path):
    """A fresh, real, empty git repo for test_git_diff.py -- made with real
    `git` subprocess calls so method_body_diff() is verified against git's
    own actual diff output, not a hand-rolled fake."""
    d = tmp_path / "repo"
    d.mkdir()
    repo_dir = str(d)
    subprocess.run(["git", "init", "-q"], cwd=repo_dir, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=repo_dir, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo_dir, check=True)
    return repo_dir
