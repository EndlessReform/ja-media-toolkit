#!/usr/bin/env -S uv run --with rich --with textual --with tiktoken --no-sync
# /// script
# requires-python = ">=3.13"
# dependencies = [
#     "rich",
#     "textual",
#     "tiktoken",
# ]
# ///
"""OpenAI-compatible batch helpers.

This is intentionally a quick single-file script while the rollout workflow is
still settling. Current scope: inspect an OpenAI batch JSONL file and estimate
token volume, rate-limit pacing, and rough cost. Future scope: execute the same
JSONL as dumb rollout units against an OpenAI-compatible endpoint.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from rich.console import Console
from rich.table import Table


DEFAULT_OUTPUT_MULTIPLIER = 1.5


@dataclass(frozen=True)
class PromptTokens:
    system_tokens: int
    user_tokens: int
    body_tokens: int
    total_input_tokens: int


@dataclass(frozen=True)
class BatchStats:
    path: Path
    request_count: int
    tokenizer: str
    approximate_tokens: bool
    unique_system_prompts: int
    system_tokens_per_request: int
    system_tokens_total: int
    body_tokens_per_request: int
    body_tokens_total: int
    user_tokens_total: int
    user_min: int
    user_p50: int
    user_p95: int
    user_max: int
    input_tokens_total: int


@dataclass(frozen=True)
class EstimateInputs:
    rate_limit_ktpm: float | None
    rate_limit_rpm: float | None
    safety_margin: float
    output_multiplier: float
    input_price_per_mtok: float | None
    cached_input_price_per_mtok: float | None
    output_price_per_mtok: float | None


@dataclass(frozen=True)
class Estimate:
    full_rate_input_tokens_with_cache: int
    cached_discount_input_tokens: int
    uncached_input_tokens: int
    output_tokens: int
    safe_ktpm: float | None
    safe_rpm: float | None
    token_bound_rpm: float | None
    estimated_rpm: float | None
    estimated_rps: float | None
    suggested_concurrency: int | None
    uncached_cost: float | None
    cached_cost: float | None


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if args.command == "stats":
        stats = analyze_batch(args.input, args.tokenizer)
        estimate = build_estimate(
            stats,
            EstimateInputs(
                rate_limit_ktpm=args.rate_limit_ktpm,
                rate_limit_rpm=args.rate_limit_rpm,
                safety_margin=args.safety_margin,
                output_multiplier=args.output_multiplier,
                input_price_per_mtok=args.input_price_per_mtok,
                cached_input_price_per_mtok=args.cached_input_price_per_mtok,
                output_price_per_mtok=args.output_price_per_mtok,
            ),
        )
        Console().print(render_stats_table(stats, estimate))
        return
    if args.command == "tui":
        run_tui(args.input, args.tokenizer)
        return
    parser.print_help()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Helpers for OpenAI-compatible batch JSONL rollouts."
    )
    subcommands = parser.add_subparsers(dest="command")

    stats = subcommands.add_parser("stats", help="summarize token/cost estimates")
    stats.add_argument("--input", type=Path, required=True, help="batch JSONL file")
    stats.add_argument(
        "--tokenizer",
        default="o200k_base",
        help="tiktoken encoding name, with approximate fallback if unavailable",
    )
    add_estimate_args(stats)

    tui = subcommands.add_parser("tui", help="interactive rollout cost calculator")
    tui.add_argument("--input", type=Path, required=True, help="batch JSONL file")
    tui.add_argument(
        "--tokenizer",
        default="o200k_base",
        help="tiktoken encoding name, with approximate fallback if unavailable",
    )
    return parser


def add_estimate_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--rate-limit-ktpm",
        type=float,
        help="provider input-token limit in thousands of tokens per minute",
    )
    parser.add_argument(
        "--rate-limit-rpm",
        type=float,
        help="provider request limit per minute; without KTPM this is concurrency",
    )
    parser.add_argument(
        "--safety-margin",
        type=float,
        default=0.8,
        help="fraction of provider limits to target",
    )
    parser.add_argument(
        "--output-multiplier",
        type=float,
        default=DEFAULT_OUTPUT_MULTIPLIER,
        help="estimated output tokens as a multiple of input tokens",
    )
    parser.add_argument("--input-price-per-mtok", type=float)
    parser.add_argument("--cached-input-price-per-mtok", type=float)
    parser.add_argument("--output-price-per-mtok", type=float)


def analyze_batch(path: Path, tokenizer_name: str) -> BatchStats:
    counter = TokenCounter(tokenizer_name)
    rows = list(read_jsonl(path))
    prompt_tokens = [count_prompt_tokens(row, counter) for row in rows]
    user_values = [item.user_tokens for item in prompt_tokens]
    system_values = [item.system_tokens for item in prompt_tokens]
    body_values = [item.body_tokens for item in prompt_tokens]
    system_prompts = {extract_system_prompt(row) for row in rows}
    stable_system_tokens = max(system_values) if system_values else 0
    stable_body_tokens = max(body_values) if body_values else 0
    return BatchStats(
        path=path,
        request_count=len(prompt_tokens),
        tokenizer=tokenizer_name,
        approximate_tokens=counter.approximate,
        unique_system_prompts=len(system_prompts),
        system_tokens_per_request=stable_system_tokens,
        system_tokens_total=sum(system_values),
        body_tokens_per_request=stable_body_tokens,
        body_tokens_total=sum(body_values),
        user_tokens_total=sum(user_values),
        user_min=min(user_values, default=0),
        user_p50=percentile(user_values, 50),
        user_p95=percentile(user_values, 95),
        user_max=max(user_values, default=0),
        input_tokens_total=sum(item.total_input_tokens for item in prompt_tokens),
    )


def build_estimate(stats: BatchStats, inputs: EstimateInputs) -> Estimate:
    margin = clamp(inputs.safety_margin, 0.0, 1.0)
    output_tokens = math.ceil(stats.input_tokens_total * inputs.output_multiplier)
    stable_cacheable_per_request = stats.system_tokens_per_request + stats.body_tokens_per_request
    cached_discount_input_tokens = stable_cacheable_per_request * max(stats.request_count - 1, 0)
    full_rate_input_tokens_with_cache = stats.user_tokens_total + stable_cacheable_per_request
    uncached_input_tokens = stats.input_tokens_total

    safe_ktpm = inputs.rate_limit_ktpm * margin if inputs.rate_limit_ktpm else None
    rpm_as_concurrency = bool(inputs.rate_limit_rpm and not inputs.rate_limit_ktpm)
    safe_rpm = (
        inputs.rate_limit_rpm * margin
        if inputs.rate_limit_rpm and not rpm_as_concurrency
        else None
    )
    avg_input = stats.input_tokens_total / stats.request_count if stats.request_count else 0
    token_bound_rpm = (safe_ktpm * 1000 / avg_input) if safe_ktpm and avg_input else None
    candidates = [value for value in (safe_rpm, token_bound_rpm) if value is not None]
    estimated_rpm = min(candidates) if candidates else None
    estimated_rps = estimated_rpm / 60 if estimated_rpm else None
    if rpm_as_concurrency and inputs.rate_limit_rpm:
        suggested_concurrency = math.ceil(inputs.rate_limit_rpm * margin)
    elif safe_rpm:
        suggested_concurrency = math.ceil(safe_rpm / 60)
    else:
        suggested_concurrency = None

    output_cost = price(output_tokens, inputs.output_price_per_mtok)
    uncached_cost = add_costs(price(uncached_input_tokens, inputs.input_price_per_mtok), output_cost)
    cached_cost = add_costs(
        price(full_rate_input_tokens_with_cache, inputs.input_price_per_mtok),
        price(cached_discount_input_tokens, inputs.cached_input_price_per_mtok),
        output_cost,
    )
    return Estimate(
        full_rate_input_tokens_with_cache=full_rate_input_tokens_with_cache,
        cached_discount_input_tokens=cached_discount_input_tokens,
        uncached_input_tokens=uncached_input_tokens,
        output_tokens=output_tokens,
        safe_ktpm=safe_ktpm,
        safe_rpm=safe_rpm,
        token_bound_rpm=token_bound_rpm,
        estimated_rpm=estimated_rpm,
        estimated_rps=estimated_rps,
        suggested_concurrency=suggested_concurrency,
        uncached_cost=uncached_cost,
        cached_cost=cached_cost,
    )


def render_stats_table(stats: BatchStats, estimate: Estimate) -> Table:
    title = "Batch Rollout Estimate"
    if stats.approximate_tokens:
        title += " (approx tokens)"
    table = Table(title=title)
    table.add_column("Metric", style="cyan", no_wrap=True)
    table.add_column("Value", justify="right")
    table.add_row("Shard", str(stats.path))
    table.add_row("Requests", f"{stats.request_count:,}")
    table.add_row("Tokenizer", stats.tokenizer)
    table.add_row("Unique system prompts", f"{stats.unique_system_prompts:,}")
    table.add_row("Stable system tokens/request", f"{stats.system_tokens_per_request:,}")
    table.add_row("System tokens total", f"{stats.system_tokens_total:,}")
    table.add_row("Body/schema tokens/request", f"{stats.body_tokens_per_request:,}")
    table.add_row("Body/schema tokens total", f"{stats.body_tokens_total:,}")
    table.add_row("User tokens min/p50/p95/max", token_spread(stats))
    table.add_row("User tokens total", f"{stats.user_tokens_total:,}")
    table.add_row("Input tokens uncached", f"{estimate.uncached_input_tokens:,}")
    table.add_row("Full-rate input with cache", f"{estimate.full_rate_input_tokens_with_cache:,}")
    table.add_row("Discounted cached input", f"{estimate.cached_discount_input_tokens:,}")
    table.add_row("Output tokens estimate", f"{estimate.output_tokens:,}")
    table.add_row("Safe KTPM", format_optional(estimate.safe_ktpm))
    table.add_row("Safe RPM", format_optional(estimate.safe_rpm))
    table.add_row("Token-bound RPM", format_optional(estimate.token_bound_rpm))
    table.add_row("Estimated RPM/RPS", format_rpm_rps(estimate))
    table.add_row("Suggested concurrency", format_optional(estimate.suggested_concurrency))
    table.add_row("Cost uncached", format_money(estimate.uncached_cost))
    table.add_row("Cost cached upper bound", format_money(estimate.cached_cost))
    return table


def count_prompt_tokens(row: dict[str, Any], counter: "TokenCounter") -> PromptTokens:
    system_tokens = 0
    user_tokens = 0
    other_tokens = 0
    for message in extract_messages(row):
        content = message.get("content", "")
        tokens = counter.count(message_content_to_text(content))
        role = message.get("role")
        if role == "system":
            system_tokens += tokens
        elif role == "user":
            user_tokens += tokens
        else:
            other_tokens += tokens
    body_tokens = counter.count(extract_body_control_text(row))
    return PromptTokens(
        system_tokens=system_tokens,
        user_tokens=user_tokens,
        body_tokens=body_tokens,
        total_input_tokens=system_tokens + user_tokens + other_tokens + body_tokens,
    )


def extract_messages(row: dict[str, Any]) -> list[dict[str, Any]]:
    body = row.get("body")
    if not isinstance(body, dict):
        return []
    messages = body.get("messages", [])
    return [message for message in messages if isinstance(message, dict)]


def extract_system_prompt(row: dict[str, Any]) -> str:
    parts = []
    for message in extract_messages(row):
        if message.get("role") == "system":
            parts.append(message_content_to_text(message.get("content", "")))
    return "\n".join(parts)


def extract_body_control_text(row: dict[str, Any]) -> str:
    body = row.get("body")
    if not isinstance(body, dict):
        return ""
    control_body = {key: value for key, value in body.items() if key != "messages"}
    return json.dumps(control_body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def message_content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                value = item.get("text") or item.get("input_text") or ""
                parts.append(str(value))
            else:
                parts.append(str(item))
        return "\n".join(parts)
    return str(content)


def read_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                row = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise SystemExit(f"{path}:{line_number}: invalid JSON: {exc}") from exc
            if not isinstance(row, dict):
                raise SystemExit(f"{path}:{line_number}: expected JSON object")
            yield row


class TokenCounter:
    def __init__(self, tokenizer_name: str) -> None:
        self.tokenizer_name = tokenizer_name
        self.approximate = False
        try:
            import tiktoken  # type: ignore[import-not-found]
        except ModuleNotFoundError:
            self._encoding = None
            self.approximate = True
        else:
            try:
                self._encoding = tiktoken.get_encoding(tokenizer_name)
            except Exception:
                self._encoding = None
                self.approximate = True

    def count(self, text: str) -> int:
        if self._encoding is not None:
            return len(self._encoding.encode(text))
        return max(1, math.ceil(len(text) / 4)) if text else 0


def run_tui(path: Path, tokenizer_name: str) -> None:
    from textual.app import App, ComposeResult
    from textual.containers import Grid, Vertical
    from textual.widgets import DataTable, Footer, Header, Input, Label, Static

    stats = analyze_batch(path, tokenizer_name)

    class CalculatorApp(App[None]):
        TITLE = "Rollout Cost Calculator"
        CSS = """
        Screen { layout: vertical; }
        #summary {
            height: 3;
            padding: 0 2;
            content-align: left middle;
            background: $surface;
            color: $text-muted;
        }
        #inputs {
            height: 11;
            grid-size: 4 2;
            grid-gutter: 1 2;
            padding: 1 2;
        }
        .fieldbox { height: 4; }
        .fieldlabel { height: 1; color: $text-muted; }
        .field { height: 3; width: 100%; }
        DataTable { height: 1fr; margin: 0 2 1 2; }
        """
        BINDINGS = [("escape", "quit", "Quit"), ("ctrl+q", "quit", "Quit")]

        def compose(self) -> ComposeResult:
            yield Header(show_clock=True)
            yield Static(self.summary_text(), id="summary")
            with Grid(id="inputs"):
                yield self.field("KTPM", "ktpm", "30_000")
                yield self.field("RPM / concurrency", "rpm", "10_000")
                yield self.field("Safety margin", "margin", "0.80")
                yield self.field("Output x input", "output", "1.5")
                yield self.field("Input $/MTok", "input_price", "")
                yield self.field("Cached $/MTok", "cached_price", "")
                yield self.field("Output $/MTok", "output_price", "")
            yield DataTable(id="table")
            yield Footer()

        def field(self, label: str, widget_id: str, value: str) -> Vertical:
            return Vertical(
                Label(label, classes="fieldlabel"),
                Input(value, id=widget_id, classes="field"),
                classes="fieldbox",
            )

        def on_mount(self) -> None:
            table = self.query_one(DataTable)
            table.add_columns("Metric", "Value")
            self.refresh_table()

        def on_input_changed(self, event: Input.Changed) -> None:
            self.refresh_table()

        def refresh_table(self) -> None:
            table = self.query_one(DataTable)
            table.clear()
            estimate = build_estimate(stats, self.inputs())
            for label, value in table_rows(stats, estimate):
                table.add_row(label, value)

        def inputs(self) -> EstimateInputs:
            return EstimateInputs(
                rate_limit_ktpm=parse_float(self.input_value("ktpm")),
                rate_limit_rpm=parse_float(self.input_value("rpm")),
                safety_margin=parse_float(self.input_value("margin"), 0.8) or 0.8,
                output_multiplier=parse_float(self.input_value("output"), 1.5) or 1.5,
                input_price_per_mtok=parse_float(self.input_value("input_price")),
                cached_input_price_per_mtok=parse_float(self.input_value("cached_price")),
                output_price_per_mtok=parse_float(self.input_value("output_price")),
            )

        def input_value(self, widget_id: str) -> str:
            return self.query_one(f"#{widget_id}", Input).value

        def summary_text(self) -> str:
            mode = "approx tokens" if stats.approximate_tokens else stats.tokenizer
            return (
                f"{stats.path} | {stats.request_count:,} requests | {mode} | "
                "blank KTPM means RPM is treated as concurrency"
            )

    CalculatorApp().run()


def table_rows(stats: BatchStats, estimate: Estimate) -> list[tuple[str, str]]:
    return [
        ("Shard", str(stats.path)),
        ("Requests", f"{stats.request_count:,}"),
        ("Tokenizer", f"{stats.tokenizer}{' (approx)' if stats.approximate_tokens else ''}"),
        ("Unique system prompts", f"{stats.unique_system_prompts:,}"),
        ("Stable system tokens/request", f"{stats.system_tokens_per_request:,}"),
        ("System tokens total", f"{stats.system_tokens_total:,}"),
        ("Body/schema tokens/request", f"{stats.body_tokens_per_request:,}"),
        ("Body/schema tokens total", f"{stats.body_tokens_total:,}"),
        ("User tokens min/p50/p95/max", token_spread(stats)),
        ("User tokens total", f"{stats.user_tokens_total:,}"),
        ("Input tokens uncached", f"{estimate.uncached_input_tokens:,}"),
        ("Full-rate input with cache", f"{estimate.full_rate_input_tokens_with_cache:,}"),
        ("Discounted cached input", f"{estimate.cached_discount_input_tokens:,}"),
        ("Output tokens estimate", f"{estimate.output_tokens:,}"),
        ("Safe KTPM", format_optional(estimate.safe_ktpm)),
        ("Safe RPM", format_optional(estimate.safe_rpm)),
        ("Token-bound RPM", format_optional(estimate.token_bound_rpm)),
        ("Estimated RPM/RPS", format_rpm_rps(estimate)),
        ("Suggested concurrency", format_optional(estimate.suggested_concurrency)),
        ("Cost uncached", format_money(estimate.uncached_cost)),
        ("Cost cached upper bound", format_money(estimate.cached_cost)),
    ]


def percentile(values: list[int], percent: int) -> int:
    if not values:
        return 0
    if len(values) == 1:
        return values[0]
    return math.ceil(statistics.quantiles(values, n=100, method="inclusive")[percent - 1])


def token_spread(stats: BatchStats) -> str:
    return f"{stats.user_min:,} / {stats.user_p50:,} / {stats.user_p95:,} / {stats.user_max:,}"


def price(tokens: int, dollars_per_mtok: float | None) -> float | None:
    if dollars_per_mtok is None:
        return None
    return tokens / 1_000_000 * dollars_per_mtok


def add_costs(*costs: float | None) -> float | None:
    if any(cost is None for cost in costs):
        return None
    return sum(cost for cost in costs if cost is not None)


def parse_float(value: str, default: float | None = None) -> float | None:
    normalized = value.strip().replace("_", "").replace(",", "")
    if not normalized:
        return default
    try:
        return float(normalized)
    except ValueError:
        return default


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def format_optional(value: float | int | None) -> str:
    if value is None:
        return "-"
    if isinstance(value, int):
        return f"{value:,}"
    return f"{value:,.2f}"


def format_rpm_rps(estimate: Estimate) -> str:
    if estimate.estimated_rpm is None or estimate.estimated_rps is None:
        return "-"
    return f"{estimate.estimated_rpm:,.2f} / {estimate.estimated_rps:,.2f}"


def format_money(value: float | None) -> str:
    if value is None:
        return "-"
    return f"${value:,.4f}"


if __name__ == "__main__":
    main()
