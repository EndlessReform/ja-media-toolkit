# subsync get-series proposal

Status: initial frontend slice implemented. `subsync get-series` reads existing
services and renders Rich/JSON; the offline mock uses the same renderer.

## Recommendation and maintenance cost

Add `ja-media subsync get-series`, reusing `get-id` title search and the existing
subtitle inventory API. Implement this entirely in the frontend. No server
changes, redeploy, migrations, persistent storage or background jobs.

The earlier proposal did not establish a user benefit sufficient to justify
server changes. Parser consistency and fresher metadata could improve accuracy,
but no inspected series demonstrated an actual failure that required those
changes. They are removed from scope. The useful first deliverable is a compact
answer from metadata we already have.

```sh
ja-media subsync get-series --anilist-id 154587
ja-media subsync get-series "Sousou no Frieren"
ja-media subsync get-series "Sousou no Frieren" --force-anilist
```

`get-id` stays the ID lookup; `get-series` adds episode availability. Extending
`get-id` with a report option is a credible smaller alternative, but `get-series`
expresses the requested output directly without changing existing ID output.
Share its search options and candidate presentation. Select a title candidate
before fetching inventory; an explicit AniList ID skips search.

## Screen

Lead with title/ID and one summary line: aired/planned episodes, episodes with
subs, episodes with 2+ named groups. The main display is a Rich grid with episode
IDs as columns and release counts beneath them. A second row counts groups so
multiple file versions do not masquerade as different groups. Wrap long series
into fixed-width blocks based on terminal width, repeating row labels.

Use zero for missing subtitles, a marker at the airing frontier, and a dash for
upcoming episodes with no files. Keep actual counts visible if future-numbered
files exist. No continuous-coverage statistic, tail-lag statistic, per-group
columns or paragraph of diagnostics in the default display. A short note appears
only when needed, such as an unknown frontier or unassigned files.

Releases means indexed subtitle files, counted by distinct subtitle ID. Groups
means distinct nonempty group hints, trimmed and case-folded. Unknown groups
show `?` when none can be identified; known groups can still be counted when
some files are unnamed. More than one release does not imply more than one
group. Count the summary's 2+ groups against aired episodes only.

## Data flow and execution

1. Frontend calls the existing AniList search SDK for title input, including
   `force_anilist` and existing format filters. That search executes on the
   configured service with its existing upstream/cache behavior.
2. With the selected ID, read the existing series file list and AniList metadata.
   Use selected search metadata when already returned; request needed extra
   fields during search. An ID input uses the existing exact-ID metadata call.
3. On the user's machine, bucket files by returned `episode_local`, count files
   and group hints, then render the grid. Report unassigned files in a short
   footer; do not introduce a second filename parser for this report.
4. Use known total for finished entries. For airing entries, use an available
   next-airing episode N with a future timestamp to estimate N-1 aired episodes.
   Label this as an AniList estimate. If metadata does not support that estimate,
   show `Aired: ?`, the known counts, and no coverage percentage. Do not guess a
   weekly schedule. Planned episode count is separate from the aired count.

The existing services continue owning their current indexes and caches. All new
aggregation and rendering executes locally. Results exist in terminal output;
optional JSON redirection creates a user-owned local report. There are no new
server artifacts to maintain. Joins use the AniList ID and supplied local episode
numbers. Normal cost is a metadata read and inventory read after title selection.

## Existing limitations, with practical consequences

The listing's stored episode numbers can differ from the runtime parser used
by episode-specific subsync retrieval. For this inventory view, use the listing
as supplied. If a real title shows wrong counts, investigate that example before
proposing shared parser or service work.

AniList's cached schedule can lag, affecting the aired denominator. Existing
`--force-anilist` selects upstream-backed title search; it does not bypass all
caching. Exact-ID lookup has no such option today. V1 preserves those semantics:
force is a search option, and ID plus force should explain that it requires a
query. Do not claim forced freshness or quietly re-search an exact ID by title.
If reliably current airing counts become necessary, first measure mismatches on
actual requested titles and present the benefit and maintenance cost of changing
refresh behavior for approval.

These are repository findings and proposed tradeoffs, not measured corpus error
rates. The Rich mock is synthetic. None of these limitations blocks the grid.

## Implementation scope after layout review

Keep search reuse, aggregation and rendering in focused frontend modules. Verify
counting with duplicate versions, multiple groups, unnamed groups, missing
episodes and unknown schedule. Check narrow-terminal wrapping. Distinguish API
failure from a successful empty inventory. No media processing or new runtime
dependencies are needed; Rich and the SDKs already exist.

A separate report API or lake product would add deployment and maintenance work
without a demonstrated benefit for this one-title read. Reconsider only if a
concrete reuse or correctness problem warrants it.
