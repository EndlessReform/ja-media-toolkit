# Anilist Metadata Expansion Plan

## Motivation
Currently, the `anilist-search` service is used primarily as a fuzzy-title-to-ID lookup tool. However, the underlying dataset contains rich metadata that can significantly enhance other parts of the toolkit:
- **ASR Biasing**: Using `characters` (English and Native names) as a vocabulary for Named Entity Recognition (NER) or ASR biasing to improve transcription accuracy for specific series.
- **Coverage Tracking**: Using `startDate_year`, `season`, and `format` to determine if all expected episodes of a series are present in our local library.
- **Content Discovery**: Using `description` for high-level plot context during mining.

## Interesting Fields identified
Based on exploration of `anilist_anime_data_complete.csv`:
- `description`: High coverage (~94%). Useful for plot summaries.
- `characters`: Moderate coverage (~64%). Contains JSON with native names and roles (MAIN/BACKGROUND).
- `idMal`: Useful for cross-referencing with MyAnimeList.
- `relations`: Useful for identifying sequels/prequels.
- `popularity`/`favourites`: Can be used to prioritize processing of more popular series.

### Data Structure Details
- **`characters`**: A JSON list of character objects. Each object contains:
    - `role`: (e.g., `MAIN`, `BACKGROUND`)
    - `node`: Character profile containing `name.full` (English), `name.native` (Japanese), and `description`.
    - `voiceActors`: A list of voice actor objects, each containing `name.full`, `name.native`, and `languageV2` (e.g., `Japanese`).
    - This is the primary source for ASR biasing terms (both character and seiyuu names).

- **`relations`**: A JSON list of related anime IDs and the relationship type (e.g., `SEQUEL`, `PREQUEL`), enabling automated discovery of series order.
- **`staff` / `studios`**: JSON lists containing contributors and production companies, which can be used to filter content by specific creators.

## Technical Assessment

### 1. Indexing the entire CSV
The current implementation materializes a subset of columns into an `anime` table for FTS.

**Proposed approach:**
- Expand the `anime` table schema in `db.py` to include all useful columns from the CSV during the `INSERT INTO anime` step.
- **JSON Types**: Explicitly cast `characters`, `relations`, `staff`, and `studios` to the `JSON` type in DuckDB. This enables the use of specialized JSON functions for querying.
- **DuckDB Indexing**: DuckDB supports `PRIMARY KEY` constraints which automatically create indices. The `aid` is already a primary key in the current materialized table, ensuring $O(1)$ or $O(\log N)$ lookups.

### 2. Efficient Metadata Access
Since we are using DuckDB's `JSON` type, we can perform complex extractions without fetching the entire blob to the client.

**Example: Get all Seiyuu for a series**
```sql
SELECT 
    json_extract(va, '$.node.name.native') as seiyuu_native
FROM (
    SELECT unnest(CAST(characters AS JSON)) as char_obj 
    FROM anime WHERE aid = '123'
) chars,
unnest(char_obj->'voiceActors') as va
```
This allows the API to offer specialized endpoints (e.g., `/api/v1/anilist/anime/{id}/seiyuu`) that return flat lists of names rather than raw JSON.

### 1. Indexing the entire CSV
The current implementation materializes a subset of columns into an `anime` table for FTS.

**Proposed approach:**
- Expand the `anime` table schema in `db.py` to include all useful columns from the CSV during the `INSERT INTO anime` step.
- **JSON Types**: Explicitly cast `characters`, `relations`, `staff`, and `studios` to the `JSON` type in DuckDB. This enables the use of specialized JSON functions for querying.
- **DuckDB Indexing**: DuckDB supports `PRIMARY KEY` constraints which automatically create indices. The `aid` is already a primary key in the current materialized table, ensuring $O(1)$ or $O(\log N)$ lookups.

### 2. Efficient Metadata Access
Since we are using DuckDB's `JSON` type, we can perform complex extractions without fetching the entire blob to the client.

**Example: Get all Seiyuu for a series**
```sql
SELECT 
    json_extract(va, '$.node.name.native') as seiyuu_native
FROM (
    SELECT unnest(CAST(characters AS JSON)) as char_obj 
    FROM anime WHERE aid = '123'
) chars,
unnest(char_obj->'voiceActors') as va
```
This allows the API to offer specialized endpoints (e.g., `/api/v1/anilist/anime/{id}/seiyuu`) that return flat lists of names rather than raw JSON.

### 3. Per-series API Design
To avoid the complexity of GraphQL for internal use, a REST-like "Detail" API can be added.

**Proposed Endpoint:**
`GET /api/v1/anilist/anime/{id}?fields=characters,description`

**Implementation Details:**
- **Query**: `SELECT <fields> FROM anime WHERE aid = <id>`
- **Field Filtering**: The `fields` query parameter can be used to dynamically build the `SELECT` clause, preventing the transfer of large JSON blobs (like `characters`) when not needed.
- **Complexity**: Low. Adding one route to `app.py` and a helper method in `db.py` to execute the ID-based lookup.

## Implementation Difficulty: Low
- The infrastructure (DuckDB, FastAPI/Flask server) is already in place.
- The data is already available in the CSV.
- Changes are limited to schema expansion and a new API endpoint.
