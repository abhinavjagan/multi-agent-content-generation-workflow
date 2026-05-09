"""Command-line interface for the x-agent."""

from __future__ import annotations

import logging
import os
import sys
import uuid
from typing import Optional

import typer
from langgraph.types import Command
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table
from rich.text import Text

from .config import get_settings
from .graph import build_graph
from .interview_graph import build_interview_graph, initial_interview_state
from .persona.store import PersonaNotFoundError, get_default_store


def _check_ollama_model(model: str) -> tuple[bool, str]:
    """Verify the configured Ollama model is actually pulled.

    Returns ``(ok, message)``. We do this at the top of long-running commands
    so the user fails fast with a copy-pasteable fix instead of crashing five
    minutes into an interview.
    """
    settings = get_settings()
    try:
        import httpx  # already a tweepy/langchain transitive dep
        resp = httpx.get(
            f"{settings.ollama_base_url.rstrip('/')}/api/tags", timeout=3.0
        )
        resp.raise_for_status()
        names = {m.get("name", "") for m in resp.json().get("models", [])}
    except Exception as exc:  # noqa: BLE001 - any failure -> degraded warning
        return False, (
            f"could not reach Ollama at {settings.ollama_base_url}: {exc}. "
            "Start it with `ollama serve` (or the desktop app)."
        )
    if model in names or any(n.startswith(f"{model}:") for n in names):
        return True, ""
    return False, (
        f"Ollama model {model!r} is not pulled. Available: "
        f"{sorted(names) or '(none)'}. Either:\n"
        f"  1) pull it:        ollama pull {model}\n"
        f"  2) override once:  OLLAMA_MODEL=<one-of-the-available> "
        f"x-agent <command> ...\n"
        f"  3) edit .env:      OLLAMA_MODEL=<model>"
    )

app = typer.Typer(
    add_completion=False,
    help="Draft a short blog with a local Ollama LLM and post it to X.",
)
persona_app = typer.Typer(
    add_completion=False,
    help="Manage cloned-persona profiles used to condition drafts.",
)
app.add_typer(persona_app, name="persona")
console = Console()


def _print_draft(posts: list[str], topic: str, mode: str) -> None:
    console.print()
    console.rule(f"[bold]Draft for[/]: {topic}  [dim]({mode}, {len(posts)} tweet(s))[/]")
    for i, body in enumerate(posts, start=1):
        header = Text(f"  Tweet {i}/{len(posts)}  ", style="bold cyan")
        header.append(f"[{len(body)} chars]", style="dim")
        console.print(Panel(body, title=header, border_style="cyan"))
    console.rule()


def _print_sources(web_results: list[dict] | None) -> None:
    """Render fetched/searched sources next to the draft for human review."""
    if not web_results:
        return
    console.print()
    console.print(
        f"[bold]Sources[/] [dim]({len(web_results)} grounded source(s))[/]"
    )
    for i, raw in enumerate(web_results, start=1):
        if not isinstance(raw, dict):
            continue
        url = str(raw.get("url", ""))
        title = str(raw.get("title", "") or "(untitled)")
        snippet = str(raw.get("snippet", "") or raw.get("content", "") or "")
        snippet = snippet.replace("\n", " ").strip()
        if len(snippet) > 200:
            snippet = snippet[:199] + "\u2026"
        console.print(
            f"  [cyan]{i}.[/] [bold]{title}[/]\n"
            f"     [dim]{url}[/]"
            + (f"\n     {snippet}" if snippet else "")
        )


def _capture_edit(initial: list[str]) -> str:
    """Open an editor (or fall back to multi-line stdin) for the user to edit."""
    editor = os.environ.get("EDITOR")
    initial_text = "\n\n".join(initial)
    if editor:
        edited = typer.edit(initial_text, extension=".md")
        return (edited or initial_text).strip()
    console.print(
        "[yellow]No $EDITOR set.[/] Paste your edited draft below, "
        "then end with a single line containing only [bold].[/] :"
    )
    lines: list[str] = []
    for raw in sys.stdin:
        if raw.rstrip("\n") == ".":
            break
        lines.append(raw.rstrip("\n"))
    return "\n".join(lines).strip() or initial_text


@app.command()
def draft(
    topic: str = typer.Argument(..., help="What to write about."),
    mode: str = typer.Option("thread", "--mode", "-m", help="single | thread"),
    style: str = typer.Option(
        "punchy, technical, plain prose",
        "--style",
        "-s",
        help="Style hint passed to the writer prompt.",
    ),
    model: Optional[str] = typer.Option(
        None,
        "--model",
        help="Override OLLAMA_MODEL for this run.",
    ),
    persona: Optional[str] = typer.Option(
        None,
        "--persona",
        "-p",
        help="ID of a saved persona to write as. List with `x-agent persona list`.",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Skip the actual X API call; just print what would be posted.",
    ),
    research: bool = typer.Option(
        False,
        "--research/--no-research",
        help=(
            "Ground the draft in web sources. When --url is set, those URLs "
            "are fetched; otherwise the topic (or --query override) is "
            "searched via the configured provider (DuckDuckGo by default)."
        ),
    ),
    url: list[str] = typer.Option(
        [],
        "--url",
        help=(
            "URL to summarize. Repeatable, max 5. When set, takes precedence "
            "over the search query. Implies --research."
        ),
    ),
    query: Optional[str] = typer.Option(
        None,
        "--query",
        help="Override the search query (defaults to TOPIC). Implies --research.",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Draft a post about TOPIC, review interactively, and (optionally) publish."""
    logging.basicConfig(
        level=logging.INFO if verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if mode not in {"single", "thread"}:
        raise typer.BadParameter("--mode must be 'single' or 'thread'")

    settings = get_settings()
    if not dry_run and not settings.has_x_credentials:
        console.print(
            "[yellow]No X API credentials found in environment; "
            "switching to --dry-run.[/]"
        )
        dry_run = True
    # Propagate dry-run intent to the XClient via env so the node picks it up
    # without us having to thread an extra arg through the graph state.
    if dry_run:
        os.environ["X_AGENT_FORCE_DRY_RUN"] = "1"

    if persona:
        store = get_default_store()
        if not store.exists(persona):
            raise typer.BadParameter(f"unknown persona id: {persona}")

    effective_model = model or settings.ollama_model
    ok, msg = _check_ollama_model(effective_model)
    if not ok:
        console.print(f"[red]Cannot draft:[/] {msg}")
        raise typer.Exit(code=1)

    graph = build_graph()
    thread_id = uuid.uuid4().hex
    config = {"configurable": {"thread_id": thread_id}}

    initial: dict = {"topic": topic, "style": style, "mode": mode}
    if model:
        initial["model"] = model
    if persona:
        initial["persona_id"] = persona

    # --url / --query imply --research even if the user forgot the flag.
    research_enabled = research or bool(url) or bool(query)
    if research_enabled:
        if len(url) > 5:
            raise typer.BadParameter("--url accepts at most 5 entries")
        initial["research_enabled"] = True
        if url:
            initial["research_urls"] = list(url)
        if query:
            initial["research_query"] = query

    persona_label = f" as persona '{persona}'" if persona else ""
    research_label = ""
    if research_enabled:
        if url:
            research_label = f", grounded in {len(url)} URL(s)"
        else:
            research_label = ", web research on"
    console.print(
        f"[dim]Generating with Ollama model "
        f"{model or settings.ollama_model}{persona_label}{research_label}...[/]"
    )
    state = graph.invoke(initial, config=config)

    while True:
        # When the graph is paused on `interrupt()`, the returned state
        # contains a `__interrupt__` key with the payload list.
        interrupts = state.get("__interrupt__") if isinstance(state, dict) else None
        if not interrupts:
            break

        payload = interrupts[0].value if hasattr(interrupts[0], "value") else interrupts[0]
        posts = payload.get("posts", [])
        _print_draft(posts, payload.get("topic", topic), payload.get("mode", mode))
        _print_sources(state.get("web_results"))
        critic_score = payload.get("critic_score")
        if critic_score is not None:
            violations = payload.get("critic_violations") or []
            colour = "green" if critic_score >= 4 else ("yellow" if critic_score >= 2 else "red")
            console.print(
                f"[{colour}]Persona critic score: {critic_score}/5[/]"
                + (f"  violations: {'; '.join(violations[:5])}" if violations else "")
            )

        choice = Prompt.ask(
            "[bold]Action[/]",
            choices=["a", "e", "r", "q"],
            default="a",
            show_choices=False,
        )
        action_map = {"a": "approve", "e": "edit", "r": "regenerate", "q": "reject"}
        action = action_map[choice]

        resume: dict = {"action": action}
        if action == "edit":
            resume["edited"] = _capture_edit(posts)

        state = graph.invoke(Command(resume=resume), config=config)

    if state.get("rejected"):
        console.print("[red]Rejected. Nothing was posted.[/]")
        raise typer.Exit(code=1)

    tweet_url = state.get("tweet_url")
    tweet_ids = state.get("tweet_ids", [])
    error = state.get("error")
    if error:
        console.print(f"[red]Error:[/] {error}")
        raise typer.Exit(code=2)

    if dry_run:
        console.print(f"[green]Dry-run complete.[/] {len(tweet_ids)} tweet(s) simulated.")
    else:
        console.print(f"[green]Posted {len(tweet_ids)} tweet(s).[/]")
        if tweet_url:
            console.print(f"  -> {tweet_url}")


@app.command()
def version() -> None:
    """Print the package version."""
    from . import __version__
    console.print(__version__)


# ----------------------------------------------------------------- persona group


def _capture_answer_from_stdin(prompt_text: str) -> str:
    """Prompt for a multi-line answer, ended with a single '.' line."""
    console.print(prompt_text)
    console.print(
        "[dim](type your answer; finish with a single line containing only '.' "
        "to submit, or '/skip' to skip this question)[/]"
    )
    lines: list[str] = []
    for raw in sys.stdin:
        text = raw.rstrip("\n")
        if text == ".":
            break
        if text == "/skip" and not lines:
            return ""
        lines.append(text)
    return "\n".join(lines).strip()


@persona_app.command("list")
def persona_list() -> None:
    """List saved personas."""
    store = get_default_store()
    ids = store.list_ids()
    if not ids:
        console.print("[dim]No personas saved yet. Run `x-agent persona create`.[/]")
        return
    table = Table(title="Personas", show_lines=False)
    table.add_column("id", style="cyan", no_wrap=True)
    table.add_column("name")
    table.add_column("real")
    table.add_column("voice")
    table.add_column("updated")
    for pid in ids:
        try:
            spec = store.load(pid)
        except Exception:  # noqa: BLE001
            continue
        table.add_row(
            spec.id,
            spec.name,
            "yes" if spec.is_real_person else "no",
            f"f={spec.voice.formality} {spec.voice.brevity[:4]} {spec.voice.humor[:4]}",
            spec.updated_at.strftime("%Y-%m-%d"),
        )
    console.print(table)


@persona_app.command("show")
def persona_show(persona_id: str = typer.Argument(...)) -> None:
    """Print a saved persona spec."""
    try:
        spec = get_default_store().load(persona_id)
    except PersonaNotFoundError:
        console.print(f"[red]Unknown persona id: {persona_id}[/]")
        raise typer.Exit(code=1)
    console.print_json(spec.model_dump_json(indent=2))


@persona_app.command("delete")
def persona_delete(
    persona_id: str = typer.Argument(...),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Delete a saved persona (irreversible)."""
    store = get_default_store()
    if not store.exists(persona_id):
        console.print(f"[red]Unknown persona id: {persona_id}[/]")
        raise typer.Exit(code=1)
    if not yes and not Confirm.ask(
        f"Delete persona '{persona_id}'? This removes the spec, transcript, and embeddings.",
        default=False,
    ):
        raise typer.Exit(code=1)
    store.delete(persona_id)
    console.print(f"[green]Deleted {persona_id}.[/]")


@persona_app.command("create")
def persona_create(
    name: str = typer.Option(..., "--name", "-n", help="Display name of the subject."),
    real: bool = typer.Option(
        True,
        "--real/--fictional",
        help="Whether this represents a real person (requires consent + disclosure).",
    ),
    handle: Optional[str] = typer.Option(
        None,
        "--handle",
        help="Optional X handle used in the auto-disclosure tag for real personas.",
    ),
    quick: bool = typer.Option(
        False,
        "--quick",
        help="Use the short ~6-question set (style/humor/values/banned/two writing samples).",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Run the interactive interview and save a new persona."""
    logging.basicConfig(
        level=logging.INFO if verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    settings = get_settings()
    ok, msg = _check_ollama_model(settings.ollama_model)
    if not ok:
        console.print(f"[red]Cannot start interview:[/] {msg}")
        raise typer.Exit(code=1)

    disclosure = ""
    consent_ack = False
    if real:
        console.print(
            "\n[bold yellow]Consent[/]: this captures a real person's voice. "
            "By continuing you confirm the subject has agreed to participate, "
            "and that any generated posts will carry an AI-persona disclosure."
        )
        if not Confirm.ask("I confirm consent", default=False):
            console.print("[red]Aborted - consent not given.[/]")
            raise typer.Exit(code=1)
        consent_ack = True
        default_tag = (
            f"[AI persona of @{handle}]" if handle else f"[AI persona of {name}]"
        )
        disclosure = Prompt.ask(
            "Disclosure tag appended to every post",
            default=default_tag,
        )

    graph = build_interview_graph()
    cfg = {"configurable": {"thread_id": uuid.uuid4().hex}}
    state_in = initial_interview_state(
        name=name,
        is_real_person=real,
        disclosure_text=disclosure,
        consent_ack=consent_ack,
        quick=quick,
    )

    console.rule(f"[bold]Persona interview[/] - {name}")
    state = graph.invoke(dict(state_in), config=cfg)

    while True:
        interrupts = state.get("__interrupt__") if isinstance(state, dict) else None
        if not interrupts:
            break
        payload = interrupts[0].value if hasattr(interrupts[0], "value") else interrupts[0]
        if payload.get("kind") != "interview_question":
            break
        q = payload["question"]
        idx = int(payload.get("question_index", 0))
        total = int(payload.get("total", 0))
        suffix = " (follow-up)" if q.get("is_followup") else ""
        kind_tag = "[generative] " if q.get("kind") == "generative" else ""
        header = f"[bold]Q {idx + 1}/{total}{suffix}[/] [dim]({q['dimension']})[/]"
        console.print()
        console.print(header)
        answer = _capture_answer_from_stdin(f"{kind_tag}{q['prompt']}")
        state = graph.invoke(Command(resume={"answer": answer}), config=cfg)

    err = state.get("error")
    if err:
        console.print(f"[red]Interview failed:[/] {err}")
        raise typer.Exit(code=2)

    persona = state.get("persona") or {}
    pid = state.get("persona_id") or persona.get("id")
    if not state.get("saved"):
        console.print("[red]Persona was not saved.[/]")
        raise typer.Exit(code=2)
    console.print(f"\n[green]Persona saved:[/] {pid}")
    console.print(f"  Use it with: [cyan]x-agent draft \"<topic>\" --persona {pid}[/]")


@persona_app.command("resume-extract")
def persona_resume_extract(
    persona_id: str = typer.Argument(
        ..., help="Persona id whose saved transcript should be re-extracted."
    ),
) -> None:
    """Re-run extraction + embedding on an existing persona's saved transcript.

    Useful if the original `persona create` finished the interview but the
    extractor crashed (e.g. wrong Ollama model). The transcript is preserved
    on disk before extraction, so this recovers your interview answers
    instead of forcing you to redo the whole conversation.
    """
    from .persona.embedder import PersonaEmbedder
    from .persona.interview import extract_persona_spec

    settings = get_settings()
    ok, msg = _check_ollama_model(settings.ollama_model)
    if not ok:
        console.print(f"[red]Cannot extract:[/] {msg}")
        raise typer.Exit(code=1)

    store = get_default_store()
    try:
        spec = store.load(persona_id)
    except PersonaNotFoundError:
        console.print(f"[red]Unknown persona id: {persona_id}[/]")
        raise typer.Exit(code=1)

    transcript = store.read_transcript(persona_id)
    if not transcript:
        console.print(
            f"[red]No transcript found for {persona_id}. Cannot resume extraction.[/]"
        )
        raise typer.Exit(code=1)

    console.print(
        f"Re-running extraction on {len(transcript)} transcript entries "
        f"for [bold]{persona_id}[/]..."
    )
    new_spec = extract_persona_spec(
        name=spec.name,
        is_real_person=spec.is_real_person,
        disclosure_text=spec.disclosure_text,
        transcript=transcript,
        persona_id=spec.id,
    )
    store.save(new_spec)

    embedder = PersonaEmbedder()
    try:
        ids, _texts, vectors = embedder.build_index(transcript)
        if vectors.size > 0:
            store.save_embeddings(new_spec.id, ids, vectors)
    except Exception as exc:  # noqa: BLE001 - embedding is best-effort
        console.print(f"[yellow]Embedding step failed (continuing): {exc}[/]")

    console.print(f"[green]Persona refreshed:[/] {new_spec.id}")
    console.print(f"  Use it with: [cyan]x-agent draft \"<topic>\" --persona {new_spec.id}[/]")


@persona_app.command("eval")
def persona_eval(
    persona_id: str = typer.Argument(...),
    prompts: Optional[str] = typer.Option(
        None,
        "--prompts",
        help="Path to a text file with one topic per line. Defaults to a built-in set.",
    ),
    mode: str = typer.Option("single", "--mode", "-m", help="single | thread"),
) -> None:
    """Generate sample posts and score them with the persona critic."""
    from .persona.critic import score_against_persona
    from .persona.schema import PersonaSpec

    settings = get_settings()
    ok, msg = _check_ollama_model(settings.ollama_model)
    if not ok:
        console.print(f"[red]Cannot eval:[/] {msg}")
        raise typer.Exit(code=1)

    store = get_default_store()
    try:
        spec = store.load(persona_id)
    except PersonaNotFoundError:
        console.print(f"[red]Unknown persona id: {persona_id}[/]")
        raise typer.Exit(code=1)

    default_prompts = [
        "explain what idempotency means in API design",
        "share a hot take on remote work",
        "apologize for shipping a regression last week",
        "tell a small joke about deadlines",
        "disagree with the claim that microservices are always better",
        "explain why monitoring p99 latency matters",
        "share a quick tip on how to write better commit messages",
        "your view on rewriting legacy code vs. refactoring it",
    ]
    topics: list[str]
    if prompts:
        topics = [
            line.strip()
            for line in open(prompts, "r", encoding="utf-8").read().splitlines()
            if line.strip()
        ]
    else:
        topics = default_prompts

    os.environ["X_AGENT_FORCE_DRY_RUN"] = "1"
    graph = build_graph()
    cfg = {"configurable": {"thread_id": uuid.uuid4().hex}}

    table = Table(title=f"Persona eval: {persona_id}")
    table.add_column("topic", overflow="fold", max_width=40)
    table.add_column("score", justify="right")
    table.add_column("violations", overflow="fold", max_width=40)

    scores: list[int] = []
    spec_obj = PersonaSpec.model_validate(spec.model_dump())
    for topic in topics:
        cfg["configurable"]["thread_id"] = uuid.uuid4().hex
        state = graph.invoke(
            {"topic": topic, "mode": mode, "persona_id": persona_id},
            config=cfg,
        )
        interrupts = state.get("__interrupt__") if isinstance(state, dict) else None
        score = state.get("critic_score")
        violations = state.get("critic_violations") or []
        if interrupts:
            payload = interrupts[0].value
            score = payload.get("critic_score", score)
            violations = payload.get("critic_violations", violations)
        if score is None:
            posts = state.get("posts") or []
            result = score_against_persona(
                draft="\n\n".join(posts),
                persona=spec_obj,
                examples=state.get("retrieved_examples") or [],
            )
            score = result["score"]
            violations = result["violations"]
        scores.append(int(score))
        table.add_row(topic, f"{score}", "; ".join(violations[:3]))

    console.print(table)
    if scores:
        avg = sum(scores) / len(scores)
        console.print(f"[bold]Avg score:[/] {avg:.2f}/5  [dim](n={len(scores)})[/]")


@persona_app.command("refine")
def persona_refine(
    persona_id: str = typer.Argument(...),
    dimension: Optional[str] = typer.Option(
        None,
        "--dimension",
        "-d",
        help="Limit follow-up questions to this dimension (e.g. 'humor').",
    ),
) -> None:
    """Re-interview an existing persona to refine specific dimensions.

    v1: re-runs the full interview pipeline using the original spec as a seed
    (we keep the existing transcript on disk and append new entries).
    """
    settings = get_settings()
    ok, msg = _check_ollama_model(settings.ollama_model)
    if not ok:
        console.print(f"[red]Cannot refine:[/] {msg}")
        raise typer.Exit(code=1)

    store = get_default_store()
    try:
        spec = store.load(persona_id)
    except PersonaNotFoundError:
        console.print(f"[red]Unknown persona id: {persona_id}[/]")
        raise typer.Exit(code=1)

    if dimension:
        from .persona.questions import by_dimension

        questions = by_dimension(dimension)
        if not questions:
            console.print(f"[red]Unknown dimension: {dimension}[/]")
            raise typer.Exit(code=1)
    else:
        questions = []

    console.print(
        "[yellow]Refine flow is intentionally simple in v1:[/] it appends "
        "additional Q+A entries to the existing transcript, then re-extracts "
        "the persona spec. Use `persona create` if you want to start over."
    )
    if not Confirm.ask("Continue?", default=True):
        raise typer.Exit()

    from .persona.interview import extract_persona_spec
    from .persona.schema import TranscriptEntry, utcnow

    extra: list[TranscriptEntry] = []
    targets = questions or []
    if not targets:
        from .persona.questions import all_questions

        targets = all_questions()

    for q in targets:
        suffix = "[generative] " if q.kind == "generative" else ""
        console.print(
            f"\n[bold]({q.dimension})[/] {suffix}{q.prompt}"
        )
        answer = _capture_answer_from_stdin("")
        if not answer:
            continue
        extra.append(TranscriptEntry(
            dimension=q.dimension,
            question=q.prompt,
            answer=answer,
            is_followup=False,
            timestamp=utcnow(),
        ))

    if not extra:
        console.print("[dim]No new answers captured; nothing to do.[/]")
        return

    transcript = store.read_transcript(persona_id) + extra
    new_spec = extract_persona_spec(
        name=spec.name,
        is_real_person=spec.is_real_person,
        disclosure_text=spec.disclosure_text,
        transcript=transcript,
        persona_id=spec.id,
    )
    store.save(new_spec)
    store.overwrite_transcript(persona_id, transcript)

    from .persona.embedder import PersonaEmbedder

    embedder = PersonaEmbedder()
    try:
        ids, _texts, vectors = embedder.build_index(transcript)
        if vectors.size > 0:
            store.save_embeddings(persona_id, ids, vectors)
    except Exception as exc:  # noqa: BLE001
        console.print(f"[yellow]Embedding refresh failed (continuing): {exc}[/]")
    console.print(f"[green]Refined persona saved:[/] {persona_id}")


if __name__ == "__main__":
    app()
