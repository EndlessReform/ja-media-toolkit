# /// script
# dependencies = [
#   "fastapi",
#   "uvicorn",
#   "duckdb",
#   "pydantic",
#   "httpx",
#   "pandas",
#   "numpy",
# ]
# ///

import sys
import json
import argparse
from pathlib import Path

from fastapi import FastAPI, Request, Query
from fastapi.responses import HTMLResponse, Response
from contextlib import asynccontextmanager
import duckdb
import uvicorn

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Load data from sys.argv if available, or from a default
    import sys
    path_str = None
    for i, arg in enumerate(sys.argv):
        if arg == "anilist_exploratory.py" or arg.endswith("anilist_exploratory.py"):
            if i + 1 < len(sys.argv):
                path_str = sys.argv[i+1]
                break
    
    if path_str:
        load_data(Path(path_str))
    else:
        print("Warning: No input path found in argv during lifespan initialization.")
        
    yield

app = FastAPI(lifespan=lifespan)

# Global state for the data and normalization constants
DB = None
FILE_PATH = None
NORM_CONSTS = {}
KITSUNEKKO_SUBS: dict[int, bool] = {}
SIDECAR_PATH: Path | None = None
MANUAL_INCLUDE: set[int] = set()
MANUAL_EXCLUDE: set[int] = set()
MANUAL_PICKS: dict[str, int] = {}  # query -> anilist_id

def load_data(path: Path):
    global DB, FILE_PATH, NORM_CONSTS
    FILE_PATH = path
    # Use an in-memory DuckDB
    DB = duckdb.connect(":memory:")
    
    # Load JSONL
    # We use duckdb's read_json_auto for JSONL
    DB.execute(f"CREATE TABLE raw_data AS SELECT * FROM read_json_auto('{path}')")
    
    # Pre-process candidates into a flat view for scoring
    # anilist_candidates is a list of objects. We'll extract the top 2.
    DB.execute("""
        CREATE VIEW processed_data AS 
        SELECT 
            *, 
            anilist_candidates[1].popularity as pop, 
            anilist_candidates[1].score as top_score,
            (anilist_candidates[1].score - anilist_candidates[2].score) as spread
        FROM raw_data
        WHERE array_length(anilist_candidates) > 0
    """)
    
    # Calculate normalization constants
    stats = DB.execute("""
        SELECT 
            min(pop) as min_pop, max(pop) as max_pop,
            min(top_score) as min_score, max(top_score) as max_score,
            min(spread) as min_spread, max(spread) as max_spread
        FROM processed_data
    """).fetchone()
    
    NORM_CONSTS = {
        "min_pop": stats[0] or 0, "max_pop": stats[1] or 1,
        "min_score": stats[2] or 0, "max_score": stats[3] or 1,
        "min_spread": stats[4] or 0, "max_spread": stats[5] or 1,
    }

    # Check Kitsunekko subtitle presence for all AniList IDs
    KITSUNEKKO_SUBS.clear()
    try:
        import os
        import httpx
        # Resolve base URL: env var > config.toml [services].root_url + gateway path
        kk_base = os.environ.get("KITSUNEKKO_SUBTITLES_BASE_URL") or os.environ.get("JA_MEDIA_KITSUNEKKO_SUBTITLES_URL")
        if not kk_base:
            try:
                import tomllib
                config_path = Path.home() / ".config" / "ja-media-toolkit" / "config.toml"
                if config_path.exists():
                    with open(config_path, "rb") as f:
                        cfg = tomllib.load(f)
                    root = cfg.get("services", {}).get("root_url")
                    if root:
                        kk_base = f"{root}/api/v1/subtitles"
            except Exception:
                pass
        if not kk_base:
            raise ValueError("No Kitsunekko base URL configured")
        kk_http = httpx.Client(base_url=kk_base.rstrip("/"), timeout=3.0, trust_env=False)
        all_ids = DB.execute("SELECT DISTINCT anilist_candidates[1].anilist_id FROM raw_data WHERE array_length(anilist_candidates) > 0").fetchall()
        for (aid,) in all_ids:
            try:
                resp = kk_http.get(f"/series/anilist/{aid}")
                resp.raise_for_status()
                KITSUNEKKO_SUBS[aid] = resp.json().get("exists", False)
            except Exception:
                KITSUNEKKO_SUBS[aid] = False
        kk_http.close()
        print(f"[anilist_exploratory] Kitsunekko subs cached for {len(KITSUNEKKO_SUBS)} series")
    except Exception as e:
        print(f"[anilist_exploratory] Kitsunekko service unavailable: {e}")

    # Load manual overrides from sidecar
    load_manual_overrides()

def load_manual_overrides():
    global MANUAL_INCLUDE, MANUAL_EXCLUDE, MANUAL_PICKS, SIDECAR_PATH
    if not FILE_PATH:
        return
    SIDECAR_PATH = Path(str(FILE_PATH) + ".manual_overrides.json")
    if SIDECAR_PATH.exists():
        try:
            with open(SIDECAR_PATH) as f:
                data = json.load(f)
            MANUAL_INCLUDE = set(int(v) for v in data.get("include", []))
            MANUAL_EXCLUDE = set(int(v) for v in data.get("exclude", []))
            MANUAL_PICKS = {str(k): int(v) for k, v in data.get("picks", {}).items()}
            print(f"[anilist_exploratory] Manual overrides loaded: {len(MANUAL_INCLUDE)} included, {len(MANUAL_EXCLUDE)} excluded, {len(MANUAL_PICKS)} picks")
        except Exception as e:
            print(f"[anilist_exploratory] Failed to load manual overrides: {e}")
    else:
        MANUAL_INCLUDE = set()
        MANUAL_EXCLUDE = set()
        MANUAL_PICKS = {}

def save_manual_overrides():
    if not SIDECAR_PATH:
        return
    tmp = SIDECAR_PATH.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump({"include": sorted(MANUAL_INCLUDE), "exclude": sorted(MANUAL_EXCLUDE), "picks": MANUAL_PICKS}, f)
    tmp.rename(SIDECAR_PATH)

def _quote_qid(query: str) -> str:
    import re
    return re.sub(r'[^a-zA-Z0-9]', '_', query)[:64]

def _html_escape(text: str) -> str:
    return text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('"', '&quot;')

def _json_escape(text: str) -> str:
    return text.replace('\\', '\\\\').replace("'", "\\'").replace('\n', '\\n').replace('\r', '\\r')

def _sql_escape(text: str) -> str:
    return text.replace("'", "''")

def get_score_sql(w_pop=0.6, w_score=0.3, w_spread=0.1):
    # Norm pop: lower is better
    pop_norm = f"(({NORM_CONSTS['max_pop']} - pop) / NULLIF({NORM_CONSTS['max_pop']} - {NORM_CONSTS['min_pop']}, 0))"
    # Norm score: higher is better
    score_norm = f"((top_score - {NORM_CONSTS['min_score']}) / NULLIF({NORM_CONSTS['max_score']} - {NORM_CONSTS['min_score']}, 0))"
    # Norm spread: higher is better
    spread_norm = f"((spread - {NORM_CONSTS['min_spread']}) / NULLIF({NORM_CONSTS['max_spread']} - {NORM_CONSTS['min_spread']}, 0))"
    
    return f"({w_pop} * {pop_norm}) + ({w_score} * {score_norm}) + ({w_spread} * {spread_norm})"

PHPBB_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>AniList Exploratory</title>
    <script src="https://unpkg.com/htmx.org@1.9.10"></script>
    <script>
        document.body.addEventListener('htmx:configRequest', function(evt) {{
            if (evt.path === '/toggle' || evt.path === '/pick') {{
                Object.assign(evt.parameters, Object.fromEntries(new URLSearchParams(window.location.search)));
            }}
        }});
        function togglePicker(qid) {{
            var row = document.getElementById('picker-' + qid);
            if (row) {{
                row.style.display = row.style.display === 'none' ? 'table-row' : 'none';
            }}
        }}
    </script>
    <style>
        body {{ 
            font-family: Verdana, Geneva, sans-serif; 
            background-color: #F9F9F9; 
            color: #333; 
            margin: 20px; 
        }}
        h1 {{ color: #107C41; font-size: 1.5em; }}
        .container {{ max-width: 1200px; margin: 0 auto; }}
        .controls {{ 
            background: #E1E1E1; 
            padding: 10px; 
            border: 1px solid #CCCCCC; 
            margin-bottom: 20px; 
            font-size: 0.9em;
        }}
        .controls form {{ display: flex; gap: 15px; align-items: center; flex-wrap: wrap; }}
        .controls label {{ display: flex; align-items: center; gap: 5px; }}
        table {{ 
            width: 100%; 
            border-collapse: collapse; 
            border: 1px solid #CCCCCC; 
            background: white;
        }}
        th {{ 
            background: #C1C1C1; 
            color: #000; 
            text-align: left; 
            padding: 8px; 
            border: 1px solid #CCCCCC;
            font-weight: bold;
            font-size: 0.85em;
        }}
        td {{ 
            padding: 6px 8px; 
            border: 1px solid #CCCCCC; 
            font-size: 0.85em; 
        }}
        tr:nth-child(even) {{ background: #F2F2F2; }}
        .score-pill {{ 
            display: inline-block; 
            padding: 2px 6px; 
            border-radius: 4px; 
            font-weight: bold; 
            background: #DDD;
        }}
        .pass {{ background: #C1E1C1; color: #107C41; }}
        .fail {{ background: #E1C1C1; color: #B22222; }}
        .pagination {{ margin-top: 20px; text-align: center; font-size: 0.85em; }}
        .pagination-info {{ margin-bottom: 10px; }}
        .btn {{ 
            padding: 5px 15px; 
            background: #E1E1E1; 
            border: 1px solid #999; 
            cursor: pointer; 
            text-decoration: none; 
            color: #333;
            font-size: 0.85em;
            margin: 0 2px;
        }}
        .btn.active {{ 
            background: #AAA; 
            color: white; 
            font-weight: bold;
        }}
        .btn.disabled {{ 
            color: #999; 
            cursor: not-allowed; 
            border-color: #DDD;
        }}
        .btn-special {{
            background: #C1C1C1;
            border: 2px solid #999;
            font-weight: bold;
        }}
        .hover-title {{
            border-bottom: 1px dotted #666;
            cursor: help;
            color: #666;
        }}
        .show-others-wrap {{
            display: inline-block;
            position: relative;
        }}
        .show-others-trigger {{
            color: #107C41;
            cursor: pointer;
            font-size: 0.8em;
            margin-left: 4px;
            text-decoration: underline;
        }}
        .show-others-trigger:hover {{
            color: #0a5c30;
        }}
        .hover-tooltip {{
            display: none;
            position: absolute;
            bottom: 100%;
            left: 0;
            background: white;
            border: 1px solid #CCCCCC;
            padding: 10px;
            width: 400px;
            max-height: 300px;
            overflow-y: auto;
            box-shadow: 0 4px 12px rgba(0, 0, 0, 0.2);
            z-index: 999;
            margin-bottom: 4px;
        }}
        .show-others-wrap:hover .hover-tooltip {{
            display: block;
        }}
        .hover-tooltip h4 {{
            margin: 0 0 8px 0;
            font-size: 0.9em;
            color: #107C41;
        }}
        .tooltip-candidate {{
            padding: 6px 0;
            border-bottom: 1px solid #EEE;
            font-size: 0.85em;
        }}
        .tooltip-candidate:last-child {{
            border-bottom: none;
        }}
        .tooltip-candidate a {{
            color: #107C41;
            text-decoration: none;
            font-weight: bold;
        }}
        .tooltip-candidate a:hover {{
            text-decoration: underline;
        }}
        .tooltip-meta {{
            color: #666;
            font-size: 0.9em;
        }}
        .override-manual {{ background: #A3D9A3; color: #107C41; }}
        .override-excluded {{ background: #F5B7B1; color: #B22222; }}
        select.control-select {{
            padding: 4px 8px;
            font-size: 0.85em;
            border: 1px solid #999;
            background: #E1E1E1;
        }}
        .export-btn {{
            display: inline-block;
            margin-left: 10px;
            padding: 5px 15px;
            background: #107C41;
            color: white;
            border: 1px solid #0a5c30;
            cursor: pointer;
            text-decoration: none;
            font-size: 0.85em;
            font-weight: bold;
        }}
        .export-btn:hover {{
            background: #0a5c30;
        }}
        .picker-row td {{
            background: #FFFDE7;
            padding: 12px 8px 12px 40px;
            border-left: 3px solid #F9A825;
        }}
        .picker-title {{
            font-weight: bold;
            color: #E65100;
            margin-bottom: 8px;
            font-size: 0.85em;
        }}
        .picker-candidate {{
            display: flex;
            align-items: center;
            gap: 8px;
            padding: 4px 6px;
            margin: 2px 0;
            border-radius: 4px;
            cursor: pointer;
        }}
        .picker-candidate:hover {{
            background: #FFF9C4;
        }}
        .picker-candidate input[type="radio"] {{
            accent-color: #107C41;
            width: 16px;
            height: 16px;
            cursor: pointer;
        }}
        .picker-candidate label {{
            cursor: pointer;
            font-size: 0.85em;
            flex: 1;
        }}
        .picker-meta {{
            color: #666;
            font-size: 0.8em;
            margin-left: 8px;
        }}
        .pick-btn {{
            padding: 2px 8px;
            background: #FFF9C4;
            border: 1px solid #F9A825;
            cursor: pointer;
            font-size: 0.75em;
            color: #E65100;
            border-radius: 3px;
        }}
        .pick-btn:hover {{
            background: #FFF176;
        }}
        .picker-current {{
            display: inline-block;
            margin-left: 8px;
            font-size: 0.75em;
            color: #107C41;
        }}
    </style>
</head>
<body>
    <div class="container">
        <h1>AniList Match Explorer</h1>
        <div class="controls">
            <form hx-get="/results" hx-target="#results-container" hx-push-url="true" hx-trigger="change">
                <label>Weights: 
                    Pop <input type="number" step="0.05" name="w_pop" value="{w_pop}" style="width: 60px"> 
                    Score <input type="number" step="0.05" name="w_score" value="{w_score}" style="width: 60px"> 
                    Spread <input type="number" step="0.05" name="w_spread" value="{w_spread}" style="width: 60px">
                </label>
                <label>Pass Gate: <input type="number" step="0.01" name="gate" value="{gate}" style="width: 60px"></label>
                <label>Page Size: <input type="number" name="page_size" value="{page_size}" style="width: 60px"></label>
                <label>Subbed only: <input type="checkbox" name="subbed_only" {subbed_attr}></label>
                <label>Source: <select name="source_filter" class="control-select"><option value="all"{all_sel}>All</option><option value="auto_only"{auto_sel}>Auto Pass Only</option><option value="manual_only"{manual_sel}>Manual Overrides</option></select></label>
                <input type="hidden" name="page" value="1">
            </form>
            <div style="margin-top: 8px; font-size: 0.8em; color: #666; display: flex; align-items: center; gap: 15px;">
                <span><strong>File:</strong> {file_path}</span>
                <a class="export-btn" href="{export_url}" hx-get="{export_url}" hx-target="_top">Export Passing (JSONL)</a>
            </div>
        </div>
        <div id="results-container">
            {results_content}
        </div>
    </div>
</body>
</html>
"""

TABLE_HEADER = """
<table>
    <thead>
        <tr>
            <th>Override</th>
            <th>Query</th>
            <th>Best Match</th>
            <th>Popularity</th>
            <th>Score</th>
            <th>Spread</th>
            <th>Subs</th>
            <th>Metric</th>
        </tr>
    </thead>
    <tbody>
"""

TABLE_ROW = """
        <tr>
            <td><label style="white-space:nowrap"><input type="checkbox" hx-post="/toggle" hx-vals='{{"anilist_id": {anilist_id}}}' hx-push-url="false" hx-target="#results-container" {checked_attr}><span class="score-pill {status_class}">{status_label}</span></label></td>
            <td>{query}</td>
            <td>{display_title}{others_trigger}{picker_btn}</td>
            <td>{pop}</td>
            <td>{score}</td>
            <td>{spread:.4f}</td>
            <td>{subs_indicator}</td>
            <td><span class="score-pill {status}">{metric:.4f}</span></td>
        </tr>
"""

PICKER_ROW = """
        <tr id="picker-{qid}" style="display:none" class="picker-row">
            <td colspan="8">
                <div class="picker-title">Pick Match for: {query_display}</div>
                {candidates_html}
            </td>
        </tr>
"""

TABLE_FOOTER = "</tbody></table>"

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    gate = float(request.query_params.get("gate", "0.5"))
    page = int(request.query_params.get("page", "1"))
    page_size = int(request.query_params.get("page_size", "50"))
    w_pop = float(request.query_params.get("w_pop", "0.5"))
    w_score = float(request.query_params.get("w_score", "0.25"))
    w_spread = float(request.query_params.get("w_spread", "0.25"))
    subbed_only = request.query_params.get("subbed_only", "").lower() in ("1", "on", "true")
    source_filter = request.query_params.get("source_filter", "all")

    results_content = await get_results_html(page, page_size, gate, w_pop, w_score, w_spread, subbed_only, source_filter)

    export_params = f"gate={gate}&w_pop={w_pop}&w_score={w_score}&w_spread={w_spread}"
    if subbed_only:
        export_params += "&subbed_only=1"
    export_url = f"/export?{export_params}"

    return PHPBB_TEMPLATE.format(
        file_path=FILE_PATH,
        w_pop=w_pop, w_score=w_score, w_spread=w_spread,
        gate=gate,
        page_size=page_size,
        subbed_attr="checked" if subbed_only else "",
        export_url=export_url,
        all_sel=" selected" if source_filter == "all" else "",
        auto_sel=" selected" if source_filter == "auto_only" else "",
        manual_sel=" selected" if source_filter == "manual_only" else "",
        results_content=results_content,
    )

async def get_results_html(page: int, page_size: int, gate: float, w_pop=0.6, w_score=0.3, w_spread=0.1, subbed_only=False, source_filter="all"):
    # Calculate current metric
    score_sql = get_score_sql(w_pop, w_score, w_spread)

    # Build base table with optional subs filter
    base_table = "processed_data"
    if subbed_only and KITSUNEKKO_SUBS:
        subbed_ids = [str(k) for k, v in KITSUNEKKO_SUBS.items() if v]
        if subbed_ids:
            DB.execute(f"CREATE OR REPLACE VIEW filtered_data AS SELECT * FROM processed_data WHERE anilist_candidates[1].anilist_id IN ({','.join(subbed_ids)})")
            base_table = "filtered_data"

    # Apply source filter for display (all / auto_only / manual_only)
    display_table = base_table
    if source_filter == "auto_only":
        DB.execute(f"CREATE OR REPLACE VIEW auto_view AS SELECT * FROM {base_table} WHERE {score_sql} >= {gate}")
        display_table = "auto_view"
    elif source_filter == "manual_only":
        all_manual = MANUAL_INCLUDE | MANUAL_EXCLUDE
        pick_queries = list(MANUAL_PICKS.keys())
        if all_manual or pick_queries:
            conditions = []
            if all_manual:
                manual_ids_str = ",".join(str(x) for x in sorted(all_manual))
                conditions.append(f"anilist_candidates[1].anilist_id IN ({manual_ids_str})")
            if pick_queries:
                escaped_queries = ",".join(f"'{_sql_escape(q)}'" for q in pick_queries)
                conditions.append(f"query IN ({escaped_queries})")
            DB.execute(f"CREATE OR REPLACE VIEW manual_view AS SELECT * FROM {base_table} WHERE {' OR '.join(conditions)}")
            display_table = "manual_view"
        else:
            DB.execute("CREATE OR REPLACE TABLE __empty_manual__ AS SELECT * FROM processed_data WHERE 1=0")
            display_table = "__empty_manual__"

    # Total count for pagination
    total_count = DB.execute(f"SELECT count(*) FROM {display_table}").fetchone()[0]
    total_pages = (total_count + page_size - 1) // page_size if total_count > 0 else 1
    
    # Find where the gate cuts (count of auto-passing records from base)
    total_passing = DB.execute(f"SELECT count(*) FROM {base_table} WHERE {score_sql} >= {gate}").fetchone()[0]
    cut_page = (total_passing // page_size) + 1 if total_passing > 0 else 1

    offset = (page - 1) * page_size

    # Query the data from display table (respects source filter)
    query = f"""
        SELECT *, {score_sql} as metric
        FROM {display_table}
        ORDER BY metric DESC
        LIMIT {page_size} OFFSET {offset}
    """
    rows = DB.execute(query).df()
    
    # Table Generation
    if rows.empty:
        table_html = "<p>No results found.</p>"
    else:
        import ast as _ast
        html = TABLE_HEADER
        for _, row in rows.iterrows():
            query_text = row['query']
            candidates = row.get('anilist_candidates', [])
            if isinstance(candidates, str):
                try:
                    candidates = _ast.literal_eval(candidates)
                except (ValueError, SyntaxError):
                    candidates = []

            # Check for a manual pick on this query
            picked_id = MANUAL_PICKS.get(query_text)
            cand_dicts = [c for c in candidates if isinstance(c, dict)]
            has_pick = picked_id is not None and picked_id in [c.get('anilist_id') for c in cand_dicts]

            # Determine which candidate to display
            if has_pick:
                c = next((c for c in cand_dicts if c.get('anilist_id') == picked_id), cand_dicts[0] if cand_dicts else {})
            else:
                c = cand_dicts[0] if cand_dicts else {}

            eng = c.get('title_english')
            rom = c.get('title_romaji')
            nat = c.get('title_native') or "N/A"
            anilist_id = c.get('anilist_id')
            link_prefix = f'<a href="https://anilist.co/anime/{anilist_id}" target="_blank" style="color: #107C41; text-decoration: none; font-weight: bold;">' if anilist_id else ''
            link_suffix = '</a>' if anilist_id else ''

            if eng and rom:
                display_title = f'{link_prefix}{eng}{link_suffix}<br><span class="hover-title" title="{nat}">{rom}</span>'
            elif eng:
                display_title = f'{link_prefix}{eng}{link_suffix}<br><span class="hover-title" title="{nat}">N/A</span>'
            elif rom:
                display_title = f'{link_prefix}<span class="hover-title" title="{nat}">{rom}</span>{link_suffix}'
            else:
                display_title = "N/A"

            # Build "Show others" hover tooltip if there are additional candidates
            others_trigger = ""
            other_candidates = [c for c in candidates if c.get('anilist_id') != anilist_id]
            if len(other_candidates) > 0:
                others_html_parts = []
                for oc in other_candidates:
                    oc_id = oc.get('anilist_id')
                    oc_eng = oc.get('title_english', '')
                    oc_rom = oc.get('title_romaji', '')
                    oc_nat = oc.get('title_native', '')
                    oc_score = oc.get('score', 0)
                    oc_pop = oc.get('popularity', 0)
                    title_text = oc_eng or oc_rom or "Unknown"
                    link_tag = f'<a href="https://anilist.co/anime/{oc_id}" target="_blank">{title_text}</a>' if oc_id else title_text
                    subtitle = []
                    if oc_eng and oc_rom and oc_eng != oc_rom:
                        subtitle.append(oc_rom)
                    if oc_nat:
                        subtitle.append(oc_nat)
                    subtitle_html = f'<br><span class="tooltip-meta">{" | ".join(subtitle)}</span>' if subtitle else ''
                    others_html_parts.append(f'<div class="tooltip-candidate">{link_tag}{subtitle_html}<br><span class="tooltip-meta">Score: {oc_score:.2f} | Pop: {oc_pop}</span></div>')
                others_trigger = f'''<br><span class="show-others-wrap">({len(other_candidates)} others)<span class="hover-tooltip"><h4>Other Candidates</h4>{" ".join(others_html_parts)}</span></span>'''

            auto_pass = row['metric'] >= gate
            is_manually_included = anilist_id in MANUAL_INCLUDE
            is_manually_excluded = anilist_id in MANUAL_EXCLUDE
            effective = (not is_manually_excluded) and (auto_pass or is_manually_included)
            status = "pass" if effective else "fail"
            has_subs = KITSUNEKKO_SUBS.get(anilist_id, False)
            subs_indicator = '<span class="score-pill pass">yes</span>' if has_subs else '<span class="score-pill fail">no</span>'
            checked_attr = "checked" if effective else ""

            if has_pick:
                status_label, status_class = "picked", "override-manual"
            elif is_manually_included and not auto_pass:
                status_label, status_class = "manual", "override-manual"
            elif is_manually_excluded and auto_pass:
                status_label, status_class = "excluded", "override-excluded"
            elif auto_pass:
                status_label, status_class = "auto", "pass"
            else:
                status_label, status_class = "fail", "fail"

            # Picker button for failed rows without a pick
            picker_btn = ""
            if not auto_pass and not has_pick and len(candidates) > 1:
                qid = _quote_qid(query_text)
                picker_btn = f'<button class="pick-btn" onclick="togglePicker(&apos;{qid}&apos;)">Pick Match</button>'

            html += TABLE_ROW.format(
                query=query_text,
                display_title=display_title,
                others_trigger=others_trigger,
                picker_btn=picker_btn,
                pop=row['pop'],
                score=row['top_score'],
                spread=row['spread'],
                subs_indicator=subs_indicator,
                metric=row['metric'],
                status=status,
                anilist_id=anilist_id or 0,
                checked_attr=checked_attr,
                status_label=status_label,
                status_class=status_class
            )

            # Picker row for failed rows without a pick
            if not auto_pass and not has_pick and len(candidates) > 1:
                qid = _quote_qid(query_text)
                picker_candidates = candidates
                cands_html = []
                for pc in picker_candidates:
                    pc_id = pc.get('anilist_id')
                    pc_eng = pc.get('title_english', '')
                    pc_rom = pc.get('title_romaji', '')
                    pc_nat = pc.get('title_native', '')
                    pc_score = pc.get('score', 0)
                    pc_pop = pc.get('popularity', 0)
                    pc_type = pc.get('type', '')
                    title_display = pc_eng or pc_rom or "Unknown"
                    meta_parts = []
                    if pc_rom and pc_rom != pc_eng:
                        meta_parts.append(pc_rom)
                    if pc_nat:
                        meta_parts.append(pc_nat)
                    if pc_type:
                        meta_parts.append(pc_type)
                    if pc_score:
                        meta_parts.append(f"Score: {pc_score:.2f}")
                    if pc_pop:
                        meta_parts.append(f"Pop: {pc_pop}")
                    meta_str = " | ".join(meta_parts)
                    radio_id = f"radio-{qid}-{pc_id}"
                    cands_html.append(f'''<div class="picker-candidate">
                        <input type="radio" name="pick-{qid}" id="{radio_id}" value="{pc_id}" hx-post="/pick" hx-vals='{{"query": {_json_escape(query_text)}, "anilist_id": {pc_id}}}' hx-target="#results-container" hx-push-url="false">
                        <label for="{radio_id}">{title_display}</label>
                        <span class="picker-meta">{meta_str}</span>
                    </div>''')
                html += PICKER_ROW.format(
                    qid=qid,
                    query_display=_html_escape(query_text),
                    candidates_html="\n".join(cands_html)
                )

        html += TABLE_FOOTER
        table_html = html

    # Pagination Bar Generation
    subbed_param = "&subbed_only=1" if subbed_only else ""
    source_param = f"&source_filter={source_filter}" if source_filter != "all" else ""
    params = f"gate={gate}&w_pop={w_pop}&w_score={w_score}&w_spread={w_spread}&page_size={page_size}{subbed_param}{source_param}"
    
    prev_page = max(1, page - 1)
    next_page = min(total_pages, page + 1)
    
    # Build breadcrumbs
    pagination_btns = []
    
    # Special: Jump to Cut
    pagination_btns.append(f'<button class="btn btn-special" hx-get="/results?page={cut_page}&{params}" hx-target="#results-container" hx-push-url="true">Jump to Cut (p{cut_page})</button>')
    
    # First
    pagination_btns.append(f'<button class="btn {"disabled" if page == 1 else ""}" hx-get="/results?page=1&{params}" hx-target="#results-container" hx-push-url="true">{ "First" }</button>')
    # Prev
    pagination_btns.append(f'<button class="btn {"disabled" if page == 1 else ""}" hx-get="/results?page={prev_page}&{params}" hx-target="#results-container" hx-push-url="true">{ "Prev" }</button>')
    
    # Page numbers (small window around current)
    start_page = max(1, page - 2)
    end_page = min(total_pages, page + 2)
    for p in range(start_page, end_page + 1):
        css_class = "btn active" if p == page else "btn"
        pagination_btns.append(f'<button class="{css_class}" hx-get="/results?page={p}&{params}" hx-target="#results-container" hx-push-url="true">{p}</button>')
    
    # Next
    pagination_btns.append(f'<button class="btn {"disabled" if page == total_pages else ""}" hx-get="/results?page={next_page}&{params}" hx-target="#results-container" hx-push-url="true">{ "Next" }</button>')
    # Last
    pagination_btns.append(f'<button class="btn {"disabled" if page == total_pages else ""}" hx-get="/results?page={total_pages}&{params}" hx-target="#results-container" hx-push-url="true">{ "Last" }</button>')
    
    pagination_html = f"""
    <div class="pagination">
        <div class="pagination-info">Page {page} of {total_pages} ({total_count} total records, {total_passing} passing gate)</div>
        {" ".join(pagination_btns)}
    </div>
    """
    
    return f'<div id="results-table">{table_html}</div>{pagination_html}'

@app.get("/results")
async def results(request: Request, page: int = 1, page_size: int = 50, gate: float = 0.5, w_pop: float = 0.5, w_score: float = 0.25, w_spread: float = 0.25, subbed_only: str = "", source_filter: str = "all"):
    html = await get_results_html(page, page_size, gate, w_pop, w_score, w_spread, subbed_only.lower() in ("1", "on", "true"), source_filter)
    return HTMLResponse(content=html)

@app.post("/pick")
async def pick_candidate(request: Request):
    body = await request.json()
    query_text = body.get("query", "")
    aid = int(body.get("anilist_id", 0))
    if not query_text or not aid:
        return Response(status_code=400)
    MANUAL_PICKS[query_text] = aid
    save_manual_overrides()
    gate = float(body.get("gate", "0.5"))
    w_pop = float(body.get("w_pop", "0.5"))
    w_score = float(body.get("w_score", "0.25"))
    w_spread = float(body.get("w_spread", "0.25"))
    page = int(body.get("page", "1"))
    page_size = int(body.get("page_size", "50"))
    subbed_only = body.get("subbed_only", "").lower() in ("1", "on", "true")
    source_filter = body.get("source_filter", "all")
    html = await get_results_html(page, page_size, gate, w_pop, w_score, w_spread, subbed_only, source_filter)
    return HTMLResponse(content=html)

@app.post("/toggle")
async def toggle_override(request: Request):
    body = await request.json()
    aid = int(body.get("anilist_id", 0))
    if not aid:
        return Response(status_code=400)
    gate = float(body.get("gate", "0.5"))
    w_pop = float(body.get("w_pop", "0.5"))
    w_score = float(body.get("w_score", "0.25"))
    w_spread = float(body.get("w_spread", "0.25"))
    score_sql = get_score_sql(w_pop, w_score, w_spread)
    row = DB.execute(f"SELECT {score_sql} as metric FROM processed_data WHERE anilist_candidates[1].anilist_id = {aid} LIMIT 1").fetchone()
    auto_pass = row[0] >= gate if row else False
    is_manually_included = aid in MANUAL_INCLUDE
    is_manually_excluded = aid in MANUAL_EXCLUDE
    effective = (not is_manually_excluded) and (auto_pass or is_manually_included)
    if effective:
        # Toggle OFF: create exclusion (or remove inclusion)
        MANUAL_INCLUDE.discard(aid)
        if not auto_pass:
            pass  # Was manually included, now just removed — falls back to auto (fail)
        else:
            MANUAL_EXCLUDE.add(aid)  # Auto passes, user wants to exclude
    else:
        # Toggle ON: create inclusion (or remove exclusion)
        MANUAL_EXCLUDE.discard(aid)
        if not auto_pass:
            MANUAL_INCLUDE.add(aid)  # Auto fails, user wants to include
    save_manual_overrides()
    page = int(body.get("page", "1"))
    page_size = int(body.get("page_size", "50"))
    subbed_only = body.get("subbed_only", "").lower() in ("1", "on", "true")
    source_filter = body.get("source_filter", "all")
    html = await get_results_html(page, page_size, gate, w_pop, w_score, w_spread, subbed_only, source_filter)
    return HTMLResponse(content=html)

@app.get("/export")
async def export_passing(request: Request):
    gate = float(request.query_params.get("gate", "0.5"))
    w_pop = float(request.query_params.get("w_pop", "0.5"))
    w_score = float(request.query_params.get("w_score", "0.25"))
    w_spread = float(request.query_params.get("w_spread", "0.25"))
    subbed_only = request.query_params.get("subbed_only", "").lower() in ("1", "on", "true")
    score_sql = get_score_sql(w_pop, w_score, w_spread)
    base_table = "processed_data"
    if subbed_only and KITSUNEKKO_SUBS:
        subbed_ids = [str(k) for k, v in KITSUNEKKO_SUBS.items() if v]
        if subbed_ids:
            DB.execute(f"CREATE OR REPLACE VIEW export_filtered AS SELECT * FROM processed_data WHERE anilist_candidates[1].anilist_id IN ({','.join(subbed_ids)})")
            base_table = "export_filtered"
    rows = DB.execute(f"""
        SELECT query, anilist_candidates, ({score_sql}) as metric
        FROM {base_table}
        WHERE ({score_sql}) >= {gate}
        ORDER BY metric DESC
    """).fetchall()
    manual_ids = sorted(MANUAL_INCLUDE)
    if manual_ids:
        manual_ids_str = ",".join(str(x) for x in manual_ids)
        manual_rows = DB.execute(f"""
            SELECT query, anilist_candidates, ({score_sql}) as metric
            FROM {base_table}
            WHERE anilist_candidates[1].anilist_id IN ({manual_ids_str}) AND ({score_sql}) < {gate}
            ORDER BY metric DESC
        """).fetchall()
    else:
        manual_rows = []
    exported_ids = set()
    output_lines = []
    for row in rows:
        query_text = row[0]
        candidates = list(row[1]) if row[1] else []
        if query_text in MANUAL_PICKS and candidates:
            picked_id = MANUAL_PICKS[query_text]
            picked_cand = next((c for c in candidates if c.get("anilist_id") == picked_id), None)
            if picked_cand:
                candidates = [picked_cand] + [c for c in candidates if c.get("anilist_id") != picked_id]
        aid = candidates[0].get("anilist_id") if candidates else None
        if aid and aid not in exported_ids and aid not in MANUAL_EXCLUDE:
            exported_ids.add(aid)
            output_lines.append(json.dumps({"query": query_text, "candidates": candidates, "metric": row[2], "source": "auto"}))
    for row in manual_rows:
        query_text = row[0]
        candidates = list(row[1]) if row[1] else []
        if query_text in MANUAL_PICKS and candidates:
            picked_id = MANUAL_PICKS[query_text]
            picked_cand = next((c for c in candidates if c.get("anilist_id") == picked_id), None)
            if picked_cand:
                candidates = [picked_cand] + [c for c in candidates if c.get("anilist_id") != picked_id]
        aid = candidates[0].get("anilist_id") if candidates else None
        if aid and aid not in exported_ids:
            exported_ids.add(aid)
            output_lines.append(json.dumps({"query": query_text, "candidates": candidates, "metric": row[2], "source": "manual"}))
    return Response(content="\n".join(output_lines) + "\n", media_type="application/x-ndjson", headers={"Content-Disposition": 'attachment; filename="passing_export.jsonl"'})

if __name__ == "__main__":
    import os
    # Ensure the project root is in sys.path so that uvicorn can import 'scripts.exploration...'
    root_dir = Path(sys.argv[0]).resolve().parents[2]
    if str(root_dir) not in sys.path:
        sys.path.insert(0, str(root_dir))

    parser = argparse.ArgumentParser(description="AniList Exploratory Dashboard")
    parser.add_argument("path", type=Path, help="Path to the input JSONL file")
    parser.add_argument("--reload", action="store_true", help="Enable uvicorn reload")
    args = parser.parse_args()
    
    if args.reload:
        uvicorn.run("scripts.exploration.anilist_exploratory:app", host="0.0.0.0", port=8000, reload=True)
    else:
        uvicorn.run(app, host="0.0.0.0", port=8000)
