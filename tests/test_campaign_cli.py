"""Typer wiring and literal approval display for the local campaign queue."""

from __future__ import annotations

import ast
import importlib
import json
import sys
import types
from pathlib import Path

from typer.testing import CliRunner


ROOT = Path(__file__).resolve().parents[1]


def _load_cli_without_drafting_stack(monkeypatch):
    """Campaign commands do not need LangGraph/Ollama; stub those imports."""
    langgraph_types = types.ModuleType("langgraph.types")
    langgraph_types.Command = type("Command", (), {})
    monkeypatch.setitem(sys.modules, "langgraph", types.ModuleType("langgraph"))
    monkeypatch.setitem(sys.modules, "langgraph.types", langgraph_types)

    config = types.ModuleType("x_agent.config")
    config.get_settings = lambda: None
    graph = types.ModuleType("x_agent.graph")
    graph.build_graph = lambda: None
    interview = types.ModuleType("x_agent.interview_graph")
    interview.build_interview_graph = lambda: None
    interview.initial_interview_state = lambda **_kwargs: {}
    persona_store = types.ModuleType("x_agent.persona.store")
    persona_store.PersonaNotFoundError = type("PersonaNotFoundError", (KeyError,), {})
    persona_store.get_default_store = lambda: None
    monkeypatch.setitem(sys.modules, "x_agent.config", config)
    monkeypatch.setitem(sys.modules, "x_agent.graph", graph)
    monkeypatch.setitem(sys.modules, "x_agent.interview_graph", interview)
    monkeypatch.setitem(sys.modules, "x_agent.persona.store", persona_store)
    monkeypatch.delitem(sys.modules, "x_agent.cli", raising=False)
    module = importlib.import_module("x_agent.cli")
    monkeypatch.setitem(sys.modules, "x_agent.cli", module)
    return module


def test_campaign_cli_exact_copy_lifecycle(tmp_path: Path, monkeypatch) -> None:
    cli = _load_cli_without_drafting_stack(monkeypatch)
    runner = CliRunner()
    brief_path = ROOT / "campaigns/cramzz/packet-panic.example.json"
    item = json.loads((ROOT / "campaigns/cramzz/content-item.example.json").read_text())
    item["copy"] = "Literal [red]markup[/red] must remain visible."
    item_path = tmp_path / "item.json"
    item_path.write_text(json.dumps(item), encoding="utf-8")
    queue_dir = tmp_path / "private-queue"

    validate = runner.invoke(cli.app, ["campaign", "validate", str(brief_path), "--item", str(item_path)])
    assert validate.exit_code == 0, validate.output
    enqueue = runner.invoke(
        cli.app,
        ["campaign", "enqueue", "--brief", str(brief_path), "--item", str(item_path), "--directory", str(queue_dir)],
    )
    assert enqueue.exit_code == 0, enqueue.output
    submit = runner.invoke(
        cli.app,
        ["campaign", "submit", item["id"], "--directory", str(queue_dir)],
    )
    assert submit.exit_code == 0, submit.output
    approve = runner.invoke(
        cli.app,
        ["campaign", "approve", item["id"], "--approver", "Abhinav", "--directory", str(queue_dir)],
        input="y\n",
    )
    assert approve.exit_code == 0, approve.output
    assert "Literal [red]markup[/red] must remain visible." in approve.output
    assert 'experiment_slug: "packet-panic"' in approve.output
    assert 'channel: "x"' in approve.output
    assert 'content_type: "post"' in approve.output
    assert "context_url: null" in approve.output
    assert "media_url:" in approve.output
    assert "packet-panic-card.png" in approve.output
    assert "alt_text:" in approve.output
    assert "evidence_refs:" in approve.output
    assert "six-hop-ttl" in approve.output
    assert "sponsor_related: false" in approve.output
    assert "Nothing was sent." in approve.output
    published = runner.invoke(
        cli.app,
        ["campaign", "mark-published", item["id"], "--directory", str(queue_dir)],
    )
    assert published.exit_code == 0, published.output
    persisted = json.loads((queue_dir / "queue.json").read_text())
    assert persisted["items"][0]["status"] == "published"


def test_campaign_package_contains_no_network_or_browser_publisher() -> None:
    forbidden_modules = {"httpx", "requests", "tweepy", "selenium", "webbrowser"}
    imported: set[str] = set()
    for path in (ROOT / "src/x_agent/campaigns").glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
    assert imported.isdisjoint(forbidden_modules)
