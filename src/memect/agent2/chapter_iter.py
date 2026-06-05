from __future__ import annotations
import json
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from memect.base.utils import console

from .evaluator import ChapterEvaluator
from .models import ChapterProposal, EvalResult, IterState
from .proposer import ChapterProposer


def _proposal_to_params(proposal: ChapterProposal) -> dict[str, Any]:
    return {
        "mode": "tree",
        "tree": {
            "backend": "default",
            "template": {"chapters": list(proposal.chapters)},
        },
    }


def _proposal_to_json(proposal: ChapterProposal) -> dict[str, Any]:
    return {"chapters": list(proposal.chapters)}


def _eval_to_json(result: EvalResult) -> dict[str, Any]:
    return {
        "score": result.score,
        "issues": [asdict(i) for i in result.issues],
    }


def _describe_rule(rule: dict[str, Any]) -> str:
    parts: list[str] = []
    if "title" in rule:
        parts.append(str(rule["title"]))
    if "titles" in rule:
        titles = rule["titles"]
        if isinstance(titles, list) and titles:
            head = str(titles[0])
            more = len(titles) - 1
            parts.append(f"titles=[{head}" + (f", +{more}" if more else "") + "]")
    extras: list[str] = []
    if "type" in rule:
        extras.append(f"type={rule['type']}")
    if "pages" in rule:
        extras.append(f"pages={rule['pages']}")
    if extras:
        parts.append("(" + ", ".join(extras) + ")")
    return " ".join(parts) if parts else json.dumps(rule, ensure_ascii=False)


class ChapterIterAgent:
    def __init__(
        self,
        out_dir: Path,
        agent_json: Path = Path("./agent.json"),
        max_iter: int = 4,
        score_threshold: int = 85,
    ):
        self._out_dir = Path(out_dir)
        self._agent_json = Path(agent_json)
        self._max_iter = max_iter
        self._score_threshold = score_threshold
        self._agent_dir = self._out_dir / "agent2"
        self._proposer = ChapterProposer(self._agent_json)
        self._evaluator = ChapterEvaluator()

    def run(self) -> Path:
        tree_md_path = self._out_dir / "tree.md"
        if not tree_md_path.is_file():
            raise FileNotFoundError(f"tree.md not found: {tree_md_path}")
        tree_md = tree_md_path.read_text("utf-8", errors="replace")

        self._agent_dir.mkdir(parents=True, exist_ok=True)
        state = IterState()
        prev_proposal: ChapterProposal | None = None
        prev_eval: EvalResult | None = None
        history: list[dict[str, Any]] = []

        console.rule("chapter iter agent")
        console.print(f"out_dir       : {self._out_dir}")
        console.print(f"tree.md       : {tree_md_path} ({len(tree_md)} chars)")
        console.print(f"agent.json    : {self._agent_json}")
        console.print(
            f"max_iter={self._max_iter}  threshold={self._score_threshold}"
        )

        stop_reason = "max_iter_reached"

        for n in range(1, self._max_iter + 1):
            state.iteration = n
            console.rule(f"iteration {n}/{self._max_iter}", style="bold cyan")

            t0 = time.monotonic()
            try:
                proposal = self._proposer.propose(tree_md, prev_proposal, prev_eval)
            except Exception as e:
                msg = f"{type(e).__name__}: {e}"
                console.print(f"  ✗ propose failed: {msg}", style="red")
                history.append({"iteration": n, "error": msg})
                stop_reason = "propose_failed"
                break
            llm_ms = int((time.monotonic() - t0) * 1000)
            console.print(
                f"  ✓ proposed {len(proposal.chapters)} rules (llm {llm_ms} ms)",
                style="green",
            )
            self._print_proposal(proposal)

            result = self._evaluator.evaluate(proposal, tree_md)
            self._write_iter(n, proposal, result)
            history.append(
                {"iteration": n, "score": result.score, "issue_count": len(result.issues)}
            )

            delta = result.score - state.best_score if state.best_score >= 0 else None
            self._print_eval(result, delta)

            if result.score > state.best_score:
                state.best_score = result.score
                state.best_proposal = proposal
                state.no_improve_count = 0
                console.print(f"  ✓ new best score: {result.score}", style="green")
            else:
                state.no_improve_count += 1
                console.print(
                    f"  · no improvement (streak={state.no_improve_count})",
                    style="yellow",
                )

            if result.score >= self._score_threshold:
                stop_reason = f"score>={self._score_threshold}"
                break
            if state.no_improve_count >= 2:
                stop_reason = "no_improvement"
                break

            prev_proposal = proposal
            prev_eval = result

        console.rule("done")
        console.print(
            f"stop_reason={stop_reason}  best_score={state.best_score}  "
            f"iterations={len(history)}"
        )
        params_file = self._write_final(state, history)
        console.print(f"params: {params_file}")
        console.print(f"summary: {self._agent_dir / 'summary.md'}")
        return params_file

    def _print_proposal(self, proposal: ChapterProposal) -> None:
        for i, rule in enumerate(proposal.chapters, 1):
            console.print(f"    {i:>2}. {_describe_rule(rule)}", style="dim")

    def _print_eval(self, result: EvalResult, delta: int | None) -> None:
        delta_str = ""
        if delta is not None:
            sign = "+" if delta >= 0 else ""
            delta_str = f" ({sign}{delta})"
        style = "green" if result.score >= self._score_threshold else "yellow"
        console.print(
            f"  ✓ score={result.score}{delta_str}  issues={len(result.issues)}",
            style=style,
        )
        for issue in result.issues[:5]:
            console.print(f"    - [{issue.kind}] {issue.advice}", style="dim")
        if len(result.issues) > 5:
            console.print(f"    ... +{len(result.issues) - 5} more", style="dim")

    def _write_iter(self, n: int, proposal: ChapterProposal, result: EvalResult) -> None:
        iter_dir = self._agent_dir / f"iter-{n}"
        iter_dir.mkdir(parents=True, exist_ok=True)
        (iter_dir / "proposal.json").write_text(
            json.dumps(_proposal_to_json(proposal), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (iter_dir / "evaluation.json").write_text(
            json.dumps(_eval_to_json(result), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _write_final(self, state: IterState, history: list[dict[str, Any]]) -> Path:
        params_file = self._agent_dir / "chapter-params.json"
        if state.best_proposal is None:
            params: dict[str, Any] = {
                "mode": "tree",
                "tree": {"backend": "default", "template": {"chapters": []}},
            }
        else:
            params = _proposal_to_params(state.best_proposal)
        params_file.write_text(
            json.dumps(params, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        summary_lines = [
            "# agent2 chapter iter summary",
            "",
            f"- best_score: {state.best_score}",
            f"- iterations: {len(history)}",
            f"- params_file: {params_file}",
            "",
            "## history",
            "",
            "| iter | score | issues |",
            "|---:|---:|---:|",
        ]
        for h in history:
            if "error" in h:
                summary_lines.append(f"| {h['iteration']} | - | error: {h['error']} |")
            else:
                summary_lines.append(
                    f"| {h['iteration']} | {h['score']} | {h['issue_count']} |"
                )
        (self._agent_dir / "summary.md").write_text(
            "\n".join(summary_lines) + "\n", encoding="utf-8",
        )
        return params_file
