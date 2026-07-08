from __future__ import annotations

import json
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
DATA_PATH = BASE_DIR / "golf_stats_data.json"
SCHEDULE_PATH = BASE_DIR / "upcoming_tournaments_2026.json"
INDEX_PATH = BASE_DIR / "index.html"

FIELD_NAMES = [
    "year",
    "moneyRank",
    "playerId",
    "player",
    "country",
    "money",
    "wins",
    "top10",
    "owgrPoints",
    "fedexPoints",
    "scoringAvg",
    "sgTotal",
    "coverage",
    "scoringAvgUnofficial",
    "scoringAvgUnofficialRounds",
]


def js(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def number(value, default=0, digits=None):
    if value is None:
        return default
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    if digits is not None:
        parsed = round(parsed, digits)
    if parsed.is_integer():
        return int(parsed)
    return parsed


def replace_between(text, start_marker, end_marker, replacement):
    start = text.index(start_marker)
    end = text.index(end_marker, start)
    return f"{text[:start]}{replacement}{text[end:]}"


def build_season_rows(data):
    rows = []
    for row in data.get("selected_metrics_wide", []):
        if not row.get("year") or not row.get("money_rank") or not row.get("player"):
            continue
        rows.append(
            [
                number(row.get("year"), digits=0),
                number(row.get("money_rank"), digits=0),
                str(row.get("player_id") or ""),
                row.get("player") or "",
                row.get("country") or "",
                number(row.get("official_money_numeric"), digits=0),
                number(row.get("official_money_ytd_victories_numeric") or row.get("victory_leaders_numeric"), digits=0),
                number(row.get("top_10_finishes_numeric"), digits=0),
                number(row.get("official_world_golf_ranking_numeric"), digits=4),
                number(row.get("fedexcup_standings_numeric"), digits=3),
                number(row.get("scoring_average_numeric"), digits=3),
                number(row.get("sg_total_numeric"), digits=3),
                row.get("coverage") or "",
                number(row.get("scoring_average_unofficial_actual_numeric"), default=None, digits=3),
                number(row.get("scoring_average_unofficial_rounds"), default=None, digits=0),
            ]
        )
    rows.sort(key=lambda item: (item[0], item[1], item[3]))
    return rows


def build_tournament_rows():
    if not SCHEDULE_PATH.exists():
        return "", []
    schedule = json.loads(SCHEDULE_PATH.read_text(encoding="utf-8"))
    rows = []
    for event in schedule.get("events", []):
        rows.append(
            [
                event.get("start_date") or "",
                event.get("end_date") or "",
                event.get("name") or "",
                event.get("course") or "",
                event.get("location") or "",
                event.get("category") or "",
            ]
        )
    return schedule.get("as_of_date") or "", rows


def main():
    data = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    index = INDEX_PATH.read_text(encoding="utf-8")

    season_rows = build_season_rows(data)
    constants_block = (
        f"    const FIELD_NAMES = {js(FIELD_NAMES)};\n"
        f"    const SEASON_ROWS = {js(season_rows)};\n"
    )
    index = replace_between(index, "    const FIELD_NAMES =", "    const UPCOMING_AS_OF_DATE =", constants_block)

    upcoming_as_of, tournament_rows = build_tournament_rows()
    schedule_block = (
        f"    const UPCOMING_AS_OF_DATE = {js(upcoming_as_of)};\n"
        f"    const UPCOMING_TOURNAMENTS = {js(tournament_rows)}"
        ".map(([startDate, endDate, name, course, location, category]) => ({ startDate, endDate, name, course, location, category }));\n"
    )
    index = replace_between(index, "    const UPCOMING_AS_OF_DATE =", "    const RUN_DATE =", schedule_block)

    coverage_note = (data.get("coverage") or {}).get("season_2026_note") or ""
    run_block = (
        f"    const RUN_DATE = {js(data.get('run_date') or '')};\n"
        f"    const GENERATED_AT = {js(data.get('generated_at') or '')};\n"
        f"    const COVERAGE_NOTE = {js(coverage_note)};\n"
    )
    index = replace_between(index, "    const RUN_DATE =", "    const PERFORMANCE_STATS_URL =", run_block)

    INDEX_PATH.write_text(index, encoding="utf-8")
    print(f"Synced {len(season_rows)} season rows into {INDEX_PATH.name}")


if __name__ == "__main__":
    main()
