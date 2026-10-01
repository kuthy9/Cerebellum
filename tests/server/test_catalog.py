import json
import os
import shutil

import pytest

from cerebellum.server import catalog as catalog_module
from cerebellum.server.catalog import Catalog
from cerebellum.server.serialize import graph_json, workflow_detail, workflow_summary
from cerebellum.spec import load_workflow, parse_workflow
from cerebellum.templates import template_path

FLOW = """
name: tiny
description: A tiny flow
input: {who: {type: string, required: true}}
steps:
  - {id: hello, type: task, title: "Hello {{ input.who }}"}
"""


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def rewrite(path, text):
    """Change a file and move its mtime on, even on file systems with coarse timestamps."""
    before = path.stat().st_mtime_ns
    write(path, text)
    os.utime(path, ns=(before + 2_000_000_000, before + 2_000_000_000))


def test_scan_skips_non_workflows_hidden_dirs_and_deep_files(store, tmp_path):
    root = tmp_path / "project"
    write(root / "flows" / "tiny" / "workflow.yaml", FLOW)
    write(root / "flows" / "tiny" / "inputs" / "alice.json", json.dumps({"who": "alice"}))
    write(root / "flows" / "tiny" / "inputs" / "broken.json", "{not json")
    write(root / "docker-compose.yml", "services: {db: {image: postgres}}\n")
    write(root / "broken.yaml", "name: [unclosed\n")
    write(root / ".hidden" / "w.yaml", FLOW.replace("tiny", "hidden"))
    write(root / "node_modules" / "pkg" / "w.yaml", FLOW.replace("tiny", "vendored"))
    write(root / "a" / "b" / "c" / "w.yaml", FLOW.replace("tiny", "depth_three"))
    write(root / "a" / "b" / "c" / "d" / "w.yaml", FLOW.replace("tiny", "depth_four"))

    entries = {e.id: e for e in Catalog(store, root).entries()}

    assert sorted(entries) == ["a/b/c/w.yaml", "flows/tiny/workflow.yaml"]
    tiny = entries["flows/tiny/workflow.yaml"]
    assert tiny.source == "file" and tiny.workflow.name == "tiny" and tiny.runs == 0
    assert tiny.samples == {"alice": {"who": "alice"}}
    assert tiny.path == str((root / "flows" / "tiny" / "workflow.yaml").resolve())


def test_history_snapshots_are_listed_and_merged_with_files(store, tmp_path):
    root = tmp_path / "project"
    on_disk = load_workflow(write(root / "tiny.yaml", FLOW))
    store.save_workflow(on_disk)
    store.create_run("r_cat00001", on_disk, {"who": "a"}, {}, mock=True)
    elsewhere = tmp_path / "elsewhere"
    shutil.copytree(template_path("refund"), elsewhere)
    refund = load_workflow(elsewhere / "workflow.yaml", env={})
    store.save_workflow(refund)

    entries = {e.id: e for e in Catalog(store, root).entries()}

    assert entries["tiny.yaml"].runs == 1  # merged with its snapshot, not listed twice
    history = entries[refund.digest]
    assert history.source == "history" and history.path is None
    assert history.workflow.name == "refund_request"
    assert set(history.samples) == {"small", "large", "flaky", "outage", "fraud"}
    assert len(entries) == 2


def test_snapshots_that_need_missing_env_vars_are_skipped(store, tmp_path):
    needs_env = """
name: needs_env
connectors: {api: {type: rest, base_url: "${SOME_UNSET_BASE_URL}"}}
steps:
  - {id: call, type: http, connector: api, path: /x}
"""
    store.save_workflow(
        parse_workflow(needs_env, base_dir=tmp_path, env={"SOME_UNSET_BASE_URL": "http://x"})
    )
    assert Catalog(store, tmp_path / "empty").entries() == []


def test_new_changed_and_deleted_files_are_picked_up(store, tmp_path):
    root = tmp_path / "project"
    tiny = write(root / "tiny" / "workflow.yaml", FLOW)
    sample = write(root / "tiny" / "inputs" / "alice.json", json.dumps({"who": "alice"}))
    catalog = Catalog(store, root)
    assert [e.workflow.description for e in catalog.entries()] == ["A tiny flow"]

    rewrite(tiny, FLOW.replace("A tiny flow", "A changed flow"))
    rewrite(sample, json.dumps({"who": "alice smith"}))
    write(root / "tiny" / "inputs" / "bob.json", json.dumps({"who": "bob"}))
    write(root / "other.yaml", FLOW.replace("tiny", "other"))
    entries = {e.id: e for e in catalog.entries()}
    assert sorted(entries) == ["other.yaml", "tiny/workflow.yaml"]
    changed = entries["tiny/workflow.yaml"]
    assert changed.workflow.description == "A changed flow"
    assert changed.samples == {"alice": {"who": "alice smith"}, "bob": {"who": "bob"}}

    tiny.unlink()
    assert [e.id for e in catalog.entries()] == ["other.yaml"]


def test_unchanged_files_are_not_parsed_again(store, tmp_path, monkeypatch):
    root = tmp_path / "project"
    write(root / "tiny.yaml", FLOW)
    write(root / "notes.yaml", "just: notes\n")  # not a workflow: its failure is remembered too
    on_disk = load_workflow(write(tmp_path / "gone" / "old.yaml", FLOW.replace("tiny", "old")))
    store.save_workflow(on_disk)  # a history snapshot whose file is not under the root
    loads, parses = [], []

    def counting_load(path, **kwargs):
        loads.append(path)
        return load_workflow(path, **kwargs)

    def counting_parse(text, **kwargs):
        parses.append(text)
        return parse_workflow(text, **kwargs)

    monkeypatch.setattr(catalog_module, "load_workflow", counting_load)
    monkeypatch.setattr(catalog_module, "parse_workflow", counting_parse)
    catalog = Catalog(store, root)
    first = catalog.entries()
    second = catalog.entries()

    assert sorted(e.id for e in second) == sorted([on_disk.digest, "tiny.yaml"])
    assert [e.workflow for e in second] == [e.workflow for e in first]
    assert len(loads) == 2 and len(parses) == 1


def test_get_and_serialisation(store, tmp_path):
    root = tmp_path / "project"
    write(root / "tiny.yaml", FLOW)
    catalog = Catalog(store, root)
    entry = catalog.get("tiny.yaml")
    summary = workflow_summary(entry)
    assert summary == {
        "id": "tiny.yaml",
        "name": "tiny",
        "version": 1,
        "description": "A tiny flow",
        "source": "file",
        "path": entry.path,
        "digest": entry.workflow.digest,
        "steps": 1,
        "runs": 0,
        "last_run_at": None,
    }
    detail = workflow_detail(entry)
    assert detail["yaml"] == FLOW and detail["params"] == {} and detail["samples"] == {}
    assert detail["input"]["who"]["required"] is True
    assert detail["graph"] == graph_json(entry.workflow)
    with pytest.raises(KeyError):
        catalog.get("missing.yaml")


def test_graph_json_lists_steps_and_fallback_links():
    refund = load_workflow(template_path("refund") / "workflow.yaml", env={})
    graph = graph_json(refund)
    assert [s["id"] for s in graph["steps"]][:2] == ["fetch_order", "policy_check"]
    issue = next(s for s in graph["steps"] if s["id"] == "issue_refund")
    assert issue["needs"] == ["manager_approval"] and issue["fallback"] == "open_manual_case"
    assert graph["fallbacks"] == [
        {
            "id": "open_manual_case",
            "type": "task",
            "description": "",
            "fallback_for": "issue_refund",
        }
    ]
