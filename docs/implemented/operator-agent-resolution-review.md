# Agent-assisted episode-resolution review

Status: proposal for review; no architecture is approved by this document.

## Recommendation

Add one agent loop to the existing operator backend. Do not add a service,
agent hierarchy, generic workflow engine, MCP layer, or model registry.

```text
resolution issue + capture
  ├── rejected-product reads
  ├── AniList search / metadata
  ├── bounded bronze subtitle reads
  └── save draft -> preview -> propose draft  [PAUSE]
        ├── reject(reason) -> agent continues
        └── accept -> transactional writeback
```

The OpenAI Agents SDK owns the model/tool loop and approval pause. The operator
application owns reads and writes. Dagster remains the pipeline UI, DuckLake the
replaceable automatic product, and PostgreSQL the human control plane.

## The product

**The downstream product is `canonical_episode_inputs`.** It is the crosswalk
from corrected episode identity to the physical media that bronze originally
captured. `canonical_subtitle_inputs` is the same contract for each subtitle
stream.

For every usable episode, downstream receives:

```text
(namespace, series_id, episode)
    -> audio_capture_id
    -> audio_object_bucket + audio_object_key + stream_index
    -> manifest_bucket + manifest_key + manifest_etag
```

Downstream discovers files by querying this table. It must not list bronze
folders and infer identity from their paths.

### Concrete example

Suppose bronze recorded this file under the season-one directory and manifest:

```text
That time I got reincarnated as an LLM 2: stuck in a black company!
bronze-declared AniList ID: 11111
correct AniList ID: 69420
episode: 3
capture: capture-abc
physical object: s3://bronze/.../11111/.../episode-03.mka
```

Accepting the review writes a human binding override:

```text
capture-abc -> anilist:69420 episode 3
```

The next `canonical_inputs` materialization emits:

```text
namespace       anilist
series_id       69420
episode         3
audio_capture_id capture-abc
binding_source  override
audio_object_*  s3://bronze/.../11111/.../episode-03.mka
```

The object stays physically under the old bronze prefix. That no longer
matters: corrected identity and physical location are separate columns in the
canonical product.

A file-discovery consumer asks DuckLake:

```sql
SELECT
    episode,
    audio_capture_id,
    audio_object_bucket,
    audio_object_key,
    audio_stream_index
FROM canonical_episode_inputs
WHERE namespace = 'anilist' AND series_id = '69420'
ORDER BY try_cast(episode AS INTEGER), episode;
```

For subtitle files it makes the equivalent query against
`canonical_subtitle_inputs`, which already carries corrected
`namespace/series_id/episode` plus `object_bucket/object_key/stream_index`.

Ordinary consumers need to know only these canonical table contracts. A
diagnostic consumer can follow `binding_id` and `binding_source` back to the
human decision, but discovery does not inspect the wrong bronze series ID.

### What acceptance writes

The agent proposal is temporary application state. Human acceptance creates one
of two PostgreSQL control records:

1. **Binding override:** `(capture_id -> anilist_id, episode)` in the existing
   `binding_overrides` history. This is an input to canonicalization.
2. **Capture disposition:** `(capture_id -> ignore)` in a new, equally small
   append/retire `capture_dispositions` history. This acknowledges and hides an
   unresolved capture from the operator review queue; it is not a canonical
   exclusion rule.

The binding head answers the durable downstream question:

> What human decision must take precedence over automatic episode resolution
> for this capture?

The disposition has narrower semantics. A capture offered for dismissal is
already an automatic resolution issue and therefore already absent from
canonical inputs. Dismissal saves the operator from repeatedly reviewing that
same issue. If a later resolver input or policy successfully proposes the
capture, ordinary automatic acceptance may put it into canonical inputs; that
is intentionally not vetoed by the old dismissal.

There is no accepted `rename` action in v1. “Rename” in the earlier taxonomy
usually meant that the filename used a valid alias; the durable remediation is
a binding to the current AniList ID and episode. A real source-file rename has
no downstream consumer while bronze is immutable, so recording it as though it
were an applied decision would be dishonest.

### Who reconciles

```text
bronze captures
  + automatic resolver products
  + binding_overrides revision
            │
            ▼
effective resolution
  ├── bind: mask automatic result for capture; inject human target
  └── undecided: retain automatic proposal or unresolved issue
            │
            ▼
canonical_episode_inputs + canonical_subtitle_inputs
            │
            ▼
subtitle LID and later media products
```

The existing Dagster `canonical_inputs` asset reconciles the system. Its
`compile_product()` calls `select_canonical_candidates()`, which already
overlays `binding_overrides` on `accepted_bindings_auto`, masks overridden
locators, and injects the human-selected capture. `build_canonical_rows()` then
dereferences that capture's ETag-pinned manifest and writes the exact audio and
subtitle object keys into the two canonical tables.

`capture_dispositions` are applied when the operator reads
`resolution_issues_auto`; `select_canonical_candidates()` deliberately does not
read them. Nothing downstream of the canonical tables needs to understand agent
reviews, dispositions, or bad bronze paths.

After accepting a binding, its control revision advances and the operator shows
`canonical_inputs` as stale. Until canonicalization runs, downstream still sees
the previous canonical head. The next explicit Dagster `canonical_inputs`
materialization publishes the corrected crosswalk atomically. Accepting only a
dismissal does not stale canonical inputs because it changes no compiler input.
The review page does not start Dagster runs in v1.

### Rematerialization and cache identity

Upstream rematerialization does **not** invalidate or delete an accepted human
decision. PostgreSQL control heads are independent of replaceable DuckLake
products and win again on every later compilation. A decision changes only
when a human retires or replaces it.

The durable pipeline key remains the existing canonical build key over upstream
materialization heads plus `binding_overrides.revision`. The resolution-review
projection cache uses:

```text
(DuckLake snapshot, binding revision, disposition revision)
```

Paused runs are not a durable cache. The one in-memory entry carries the SDK
`RunState` plus the exact source token `(resolution materialization ID, manifest
ETag, binding revision, disposition revision)`. Acceptance requires the token
still to match. If it does not, discard the state and rerun; this workload is
cheap.

## Problem and measured workload

`resolution_issues_auto` stops at quarantine. The operator needs to inspect one
failed capture, compare AniList entries, sample subtitles, and propose a
human-reviewed correction.

The measured 600-row snapshot is dominated by title aliases, wrong franchise
members, and extras. That supports a small review agent; it does not yet justify
batch autonomy.

Repository facts:

- The FastAPI/Jinja/HTMX operator already pages `resolution_issues_auto` joined
  to `bronze_captures`, 50 rows by default.
- `HttpAniListSearchClient` already has search and exact-ID metadata, including
  episodes, format, season/year, synonyms, and relations.
- `BronzeStore` and `subtitle_object_key()` already provide the safe primitives
  for manifest-derived subtitle reads.
- PostgreSQL `binding_overrides` already preserves human mapping history,
  retired heads, uniqueness, and a monotonic revision.
- It cannot represent a deliberate unbound extra.
- The data package installs the released `openai-agents==0.19.1` wheel from
  PyPI. The `docs/repo-symlinks/openai-agents-python` checkout is review-only;
  it is not an install source. The `v0.19.1` tag already contains the public
  surfaces used here: function tools, request-scoped providers, resumable
  `RunState`, HITL interruptions, semantic streaming events, and custom
  `tool_use_behavior`.

## Agent-facing draft

The agent reviews one bronze-declared series at a time. One draft may cover all
files in that series; the atomic unit is the complete draft, not one file.

Use a discriminated union so each decision exposes only fields that make sense:

```python
class FileEpisode(BaseModel):
    capture_id: str
    episode: str


class KeepInCurrentSeries(BaseModel):
    decision: Literal["keep_in_current_series"]
    files: list[FileEpisode]
    rationale: str


class MoveToAnotherSeries(BaseModel):
    decision: Literal["move_to_another_series"]
    destination_anilist_id: int
    files: list[FileEpisode]
    rationale: str


class LeaveOutOfEpisodeIndex(BaseModel):
    decision: Literal["leave_out_of_episode_index"]
    capture_ids: list[str]
    rationale: str


SeriesDecision = Annotated[
    KeepInCurrentSeries | MoveToAnotherSeries | LeaveOutOfEpisodeIndex,
    Field(discriminator="decision"),
]


class SeriesResolutionDraft(BaseModel):
    current_anilist_id: int
    summary: str
    decisions: list[SeriesDecision]
```

The names are literal:

- `current_anilist_id` is the AniList ID bronze currently claims.
- `destination_anilist_id` exists only for a move and is where the files go.
- `keep_in_current_series` means the current series is right; the accepted
  crosswalk admits the listed captures under that ID. This covers the common
  “filename alias/rename, but same show” case without pretending bronze moved.
- `move_to_another_series` moves all listed capture/episode pairs to one stated
  destination, so 15 season-two files are one decision, not 15 tool calls.
- `leave_out_of_episode_index` keeps bronze untouched and dismisses the current
  automatic issue from the operator queue. The capture already emits no
  canonical episode/subtitle row because it is unresolved; the disposition does
  not become a downstream veto if automatic resolution later succeeds.
- `rationale` is one short human-readable explanation for that grouped
  decision. It is not an identifier and is not a list.

If the agent cannot decide a file, it leaves it out of the draft and says so in
`summary`. “Unresolved” is not a write action. A genuinely multi-episode file
still needs a media-split design because one capture cannot currently occupy
several episode locators.

## Core registry

Use a typed context, six read tools, and three draft tools. No dynamic discovery:

```python
@dataclass
class ReviewContext:
    current_anilist_id: int
    toolbox: ResolutionToolbox


@function_tool
def list_current_series_files(
    ctx: RunContextWrapper[ReviewContext], offset: int = 0, limit: int = 50,
) -> list[dict]:
    return ctx.context.toolbox.issues(
        ctx.context.current_anilist_id, offset, min(limit, 50)
    )


@function_tool
def get_resolution_issue(
    ctx: RunContextWrapper[ReviewContext], issue_id: str,
) -> dict:
    return ctx.context.toolbox.issue(issue_id)


@function_tool
def search_anilist(
    ctx: RunContextWrapper[ReviewContext], query: str, limit: int = 5,
) -> list[dict]:
    return ctx.context.toolbox.search_anilist(query, min(limit, 10))


@function_tool
def get_anilist(ctx: RunContextWrapper[ReviewContext], anilist_id: int) -> dict:
    return ctx.context.toolbox.get_anilist(anilist_id)


@function_tool
def list_capture_subtitles(
    ctx: RunContextWrapper[ReviewContext], capture_id: str,
) -> list[dict]:
    return ctx.context.toolbox.list_subtitles(capture_id)


@function_tool
def read_capture_subtitle(
    ctx: RunContextWrapper[ReviewContext], capture_id: str, stream_index: int,
    start_line: int = 1, line_count: int = 80,
) -> str:
    return ctx.context.toolbox.read_subtitle(
        capture_id, stream_index, start_line, min(line_count, 200)
    )


@function_tool
def save_resolution_draft(
    ctx: RunContextWrapper[ReviewContext], draft: SeriesResolutionDraft,
) -> dict:
    """Replace the current draft and return its validated crosswalk preview."""
    return ctx.context.toolbox.save_draft(draft)


@function_tool
def preview_resolution_draft(ctx: RunContextWrapper[ReviewContext]) -> dict:
    """Return every capture's proposed final series, episode, and disposition."""
    return ctx.context.toolbox.preview_draft()


@function_tool(needs_approval=True)
def propose_resolution_draft(ctx: RunContextWrapper[ReviewContext]) -> dict:
    """Present the last validated draft for one all-or-nothing approval."""
    return ctx.context.toolbox.apply_approved_draft()


CORE_TOOLS = [
    list_current_series_files, get_resolution_issue,
    search_anilist, get_anilist,
    list_capture_subtitles, read_capture_subtitle,
    save_resolution_draft, preview_resolution_draft,
    propose_resolution_draft,
]
```

Saving and previewing mutate only process-local draft state. The only durable
write tool is `propose_resolution_draft`, and it always requires SDK approval.
The agent prompt requires a successful preview before proposal.

`save_resolution_draft` rejects duplicate captures, files outside the current
series, missing destination AniList records, missing episodes, and destination
locator conflicts. Its return value is the same flattened preview shown to the
human: one row per capture with filename, current AniList ID, decision,
destination AniList ID, destination episode, and outcome (`canonical` or
`left_out`). The agent can replace the entire draft and preview again.

`ResolutionToolbox` stays an operator application service:

- Issue reads are pinned to the current resolution materialization and bounded.
- AniList search uses `all_formats=True`; metadata uses a fixed field allowlist.
- Subtitle methods accept capture/stream IDs, never caller-supplied object keys.
  They read the ETag-pinned manifest, derive the object key, and return at most
  200 numbered lines and 20 KiB.
- Filenames, manifests, and subtitle text are untrusted data, never agent
  instructions.

## Request-scoped model construction

```python
def make_run_config(choice: ModelChoice) -> RunConfig:
    custom = choice.base_url is not None
    provider = OpenAIProvider(
        api_key=choice.api_key or ("not-used" if custom else None),
        base_url=choice.base_url,
        use_responses=not custom,
    )
    return RunConfig(
        model=choice.model_id,
        model_provider=provider,
        tracing_disabled=True,
        workflow_name="episode-resolution-review",
    )
```

First-party OpenAI uses Responses and the process key when the request omits
one. Custom OpenAI-compatible bases default to Chat Completions.

The browser stores `model_id` and `base_url` in `localStorage`. An entered key
stays in memory, is sent on run/resume, and is never logged or persisted.

There is no OAuth, application login, or TLS project in v1. Use either:

- `localhost`, where browser-to-operator traffic never leaves the machine; or
- the existing private Tailscale address, where Tailscale supplies device/user
  authentication, ACLs, and encrypted transport before the HTTP request reaches
  the operator.

Do not expose this page on the public internet or an untrusted LAN. If that ever
becomes a requirement, disable the page there until authentication is designed;
it is explicitly outside this proposal. Prefer the server's configured model
key. The ephemeral UI key remains optional for one-off provider testing.

## Agent lifecycle: the important part

### What owns state

The `Agent` owns no session. It is the reusable definition: instructions,
tools, and model defaults.

`Runner.run_streamed()` creates one live execution and returns a
`RunResultStreaming`. While its `stream_events()` iterator is running, that
result and the runner's background task own the mutable execution state.

A **turn** is one model invocation, including the tool calls the model emits.
If those tools run and their outputs go back to the model, that next model
invocation is the next turn. One series review is normally one run containing
several turns.

The SDK's optional `Session` API is a different concern: it stores conversation
history across separate user interactions. This v1 review flow does not need a
session. `RunState` is enough to resume the one run paused at approval.

When `propose_resolution_draft` requires approval, the stream finishes and
`result.interruptions` contains the blocked call. There is no model request or
agent task left running while the human thinks. `result.to_state()` produces
the passive SDK snapshot that resumes immediately before that tool executes.

The web application must keep that object because approval arrives in a later
HTTP request. Do not introduce a `PendingRunStore` abstraction in v1. Keep one
bounded application-lifespan dictionary:

```python
@dataclass
class PausedReview:
    state: RunState[ReviewContext]
    interruption: ToolApprovalItem
    source_token: SourceToken
    created_at: datetime


paused_reviews: dict[str, PausedReview] = {}
```

The random dictionary key is the browser's approval token. Cap the dictionary
at 128 entries and expire entries after 30 minutes. The `ReviewContext` held by
the state may reference the operator application service, but that service must
borrow database connections per tool call rather than hold a checked-out
connection across the pause. Do not put the model API key in the context.

This dictionary is not another state machine and does not move work out of
RAM. It is only the cross-request rendezvous for the SDK's `RunState`. Keeping
the SSE request open while awaiting approval would avoid the dictionary but
would strand an HTTP connection and task, fail awkwardly on refresh, and still
need a second request because SSE is server-to-browser only. A TUI could keep
`RunState` in a local variable while awaiting keyboard input; the web transport
is why the dictionary exists.

Process restart or expiry loses the paused review and the user reruns it. That
is deliberate. Do not serialize SDK state or create a pending-review table for
this cheap workflow.

### Start, pause, and resume

```python
agent = Agent[ReviewContext](
    name="Episode resolution reviewer",
    instructions=INSTRUCTIONS,
    tools=CORE_TOOLS,
)

result = Runner.run_streamed(
    agent,
    "Review every failed file in this series, preview one draft, then propose it.",
    context=context,
    run_config=make_run_config(choice),
    max_turns=12,
)
```

Drain `stream_events()` completely. If the run pauses, keep its live state:

```python
async for event in result.stream_events():
    if partial := render_agent_event(event):
        yield sse_html(partial)

if result.interruptions:
    interruption = result.interruptions[0]
    token = secrets.token_urlsafe(24)
    paused_reviews[token] = PausedReview(
        state=result.to_state(),
        interruption=interruption,
        source_token=current_source_token,
        created_at=utcnow(),
    )
    yield sse_html(render_proposal_partial(
        token,
        context.toolbox.preview_draft(),
    ))
else:
    yield sse_html(render_status_partial("finished", result.final_output))
```

Accept or reject consumes the token exactly once, checks that the input heads
have not changed, records the decision on the SDK state, and resumes the same
run:

```python
paused = paused_reviews.pop(token)
if paused.source_token != current_source_token:
    raise ProposalExpired("source changed; run again")

if accepted:
    paused.state.approve(paused.interruption)
else:
    paused.state.reject(paused.interruption, rejection_message=reason)

result = Runner.run_streamed(
    agent,
    paused.state,
    run_config=make_run_config(choice),
)
async for event in result.stream_events():
    if partial := render_agent_event(event):
        yield sse_html(partial)
```

Use the same drain-and-inspect helper after both the initial call and a resume.
That helper creates another paused entry if a rejected run later proposes a
revised draft; do not special-case the second pause.

The browser resends an ephemeral model key on resume so the server can create a
fresh request-scoped provider; the key is not retained in `RunState`. Accept
executes the blocked write tool and applies the whole draft. Reject sends the
human reason back to the model; the agent may replace, preview, and propose a
new draft, producing a new single-use token. Neither path writes pending state.

### SSE projection

Use `RunResultStreaming.stream_events()` as the UI event source. Do not build a
parallel callback bus. SDK `RunHooks` (`on_llm_start`, `on_tool_start`, and
friends) are useful for metrics or logging, but duplicative for this UI.

Project the SDK events into a deliberately small set of server-rendered UI
fragments. The browser should not learn an agent-event JSON API:

```text
#agent-status  replace: run/model/finished/failed status
#agent-events  append:  tool call, short tool result, reasoning summary,
                        or complete assistant message
#proposal      replace: approval card and its ordinary POST forms
```

The mapping is direct:

- raw `response.created` becomes `model_started`; ignore ordinary raw text
  deltas;
- `RunItemStreamEvent(name="tool_called")` exposes the completed tool call and
  arguments before local execution;
- `RunItemStreamEvent(name="tool_output")` exposes the completed result;
- `RunItemStreamEvent(name="reasoning_item_created")` may contain
  `item.raw_item.summary`; emit its summary strings as one completed event;
- `RunItemStreamEvent(name="message_output_created")` becomes one completed
  assistant message; and
- after the iterator ends, inspect `interruptions` and render the proposal
  card.

Reasoning summaries are provider/model-dependent. For first-party reasoning
models request a summary through `ModelSettings`; if no summary is returned,
show no reasoning row. Never label hidden chain-of-thought, raw unsupported
provider fields, or an invented status sentence as a reasoning summary. Custom
OpenAI-compatible bases may omit this feature entirely.

Tool outputs must be shaped for display rather than dumped wholesale. Show
AniList result counts/titles, subtitle line ranges, draft validation results,
and the flattened preview. Truncate long values and never send model keys,
headers, full manifests, or unbounded subtitle text to the browser event log.

## Writeback

Add only the missing control-head table:

```text
capture_dispositions:
disposition_id, capture_id, disposition, decision_note,
created_revision, retired_revision, created_at, retired_at
```

Applying a draft is all-or-nothing. Add one narrow repository method that takes
the validated draft, acquires the existing advisory lock, checks every capture
and destination locator for conflicts, then performs one transaction:

- `keep_in_current_series`: append one binding override per file using
  `current_anilist_id` and its stated episode;
- `move_to_another_series`: append one binding override per file using that
  decision's `destination_anilist_id` and stated episode; and
- `leave_out_of_episode_index`: append one capture-disposition head per file.

Advance each affected control revision once for the whole draft. Any conflict
rolls back every change. This batch method should reuse the SQL and uniqueness
rules behind `append_override()` rather than invoking its separate transaction
once per file.

Use `decision_method = 'agent-human-approved'`; decision notes may contain the
group's short rationale and source issue IDs. Do not persist the transcript,
tool calls, model ID, rejected alternatives, or SDK state.

`binding_overrides` are compiler inputs. `capture_dispositions` are operator
review-state inputs. `canonical_episode_inputs` and `canonical_subtitle_inputs`
are what downstream consumers read.

## Web UI

Add `/operator/resolution-review` with existing Jinja, HTMX, and CSS:

Use three columns: a 50-series paged rail, a 50-capture paged issue list, and
the selected agent session with tool events and proposal card.

Upgrade the one vendored HTMX file from 2.0.8 to an **exactly pinned HTMX
4.0.0-beta6**, and vendor that release's `hx-sse` extension. Never reference
`@next` or a CDN at runtime. HTMX 4's SSE extension uses `fetch()` and a
`ReadableStream`, so an ordinary `hx-post` can send the series, model choice,
and optional ephemeral key and consume its `text/event-stream` response. This
is the native HTMX path; add no custom browser stream parser.

Each unnamed SSE message contains server-rendered HTML rooted at an
`<hx-partial>`; dynamic values are HTML-escaped by the template. The extension
directs the activity fragments to
`#agent-events`, status fragments to `#agent-status`, and the proposal card to
`#proposal`. For example:

```html
data: <hx-partial hx-target="#agent-events" hx-swap="beforeend">...</hx-partial>

data: <hx-partial hx-target="#proposal" hx-swap="innerHTML">...</hx-partial>
```

The request is still an ordinary declarative form:

```html
<form hx-post="/operator/resolution-review/series/123/run"
      hx-target="#agent-panel"
      hx-swap="none">
  ...
</form>
```

This avoids a job-creation endpoint, EventSource GET endpoint, polling loop,
WebSocket, custom JavaScript protocol, and public JSON event contract.

The beta migration is small but real. Make it a separate first patch and smoke
every existing operator interaction before adding the agent page:

- rename the sole listener from `htmx:afterSwap` to `htmx:after:swap` and read
  its target from `event.detail.ctx.target`;
- explicitly restore the 2.x no-timeout behavior (`defaultTimeout: 0`), because
  HTMX 4 otherwise times out after 60 seconds and an agent run can exceed it;
- preserve the existing 2.x error-swap behavior by configuring 4xx/5xx as
  `noSwap`, unless a route deliberately returns an error partial; and
- accept HTMX 4's disabled implicit inheritance. The current operator keeps
  its request attributes on the requesting element, so it does not need the
  compatibility extension.

This repo currently has only nine `hx-get` sites, no boosted navigation,
history integration, or custom HTMX extensions, and one HTMX event listener.
That limited surface makes the beta a reasonable local-operator trade. The
risk is release churn: beta6 itself renamed a lifecycle event, so upgrades
after the exact pin are deliberate changes with browser smoke tests.

The initial run POST stays open only while the agent is working. It ends after
`approval_required` is sent. Accept and reject are separate POSTs that return a
new SSE stream while the same SDK run resumes. Browser refresh loses the
rendered event log and approval card, so the user simply reruns the cheap
review. Do not persist or replay the event log.

Routes:

```text
GET  /operator/resolution-review
GET  /operator/resolution-review/series/{anilist_id}
GET  /operator/resolution-review/issues/{issue_id}
POST /operator/resolution-review/series/{anilist_id}/run              -> SSE
POST /operator/resolution-review/paused/{token}/accept                -> SSE
POST /operator/resolution-review/paused/{token}/reject                -> SSE
```

The proposal card renders the saved preview: every capture, current series,
decision, destination series when applicable, final episode, and rationale.
Reject requires a reason. Disable both buttons after submission; the pending
token is single-use.

## Cost and rollout

The proposal adds one dependency to `packages/data`, one PostgreSQL migration,
one repository/application service, and a few routes/fragments. It adds no
queue, websocket, vector store, background agent, or prompt registry.

Implementation order:

1. Plan model, registry, and fake-model HITL tests.
2. In-memory paused-state dictionary and accepted control writeback.
3. Exact-pinned HTMX 4 beta migration and existing-operator browser smoke.
4. Bounded issue/AniList/subtitle adapters and rendered event projection.
5. Three-column page, native POST-SSE, and request-scoped model selector.
6. Manually review a small DEV sample before considering batching.

Critical tests are: proposal pauses before write; reject writes no control head;
accept applies exactly once; stale/duplicate acceptance fails safely.
