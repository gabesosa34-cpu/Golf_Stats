import json
import math
import re
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
OUTPUT_JSON = BASE_DIR / "golf_stats_data.json"

GRAPHQL_ENDPOINT = "https://orchestrator.pgatour.com/graphql"
API_KEY = "da2-gsrx5bibzbb4njvhl7t37wqyl4"

YEARS = list(range(2000, 2027))
TOP_N = None  # no cap: keep every ranked player in each stat table

STAT_DETAILS_QUERY = """
query StatDetails($tourCode: TourCode!, $statId: String!, $year: Int, $eventQuery: StatDetailEventQuery) {
  statDetails(tourCode: $tourCode, statId: $statId, year: $year, eventQuery: $eventQuery) {
    tourCode
    year
    displaySeason
    statId
    statType
    tournamentPills { tournamentId displayName }
    yearPills { year displaySeason }
    statTitle
    statDescription
    tourAvg
    lastProcessed
    statHeaders
    rows {
      ... on StatDetailsPlayer {
        __typename
        playerId
        playerName
        country
        countryFlag
        rank
        rankDiff
        rankChangeTendency
        stats { statName statValue color }
      }
      ... on StatDetailTourAvg {
        __typename
        displayName
        value
      }
    }
  }
}
"""

STAT_OVERVIEW_QUERY = """
query StatOverview($tourCode: TourCode!, $year: Int) {
  statOverview(tourCode: $tourCode, year: $year) {
    tourCode
    year
    categories {
      category
      displayName
      subCategories {
        displayName
        stats { statId statTitle }
      }
    }
  }
}
"""

PLAYER_TOURNAMENT_RESULTS_QUERY = """
query PlayerResults($playerId: ID!) {
  playerProfileTournamentResults(playerId: $playerId) {
    playerId
    tournaments {
      tournamentNum
      tournaments {
        tournamentId
        year
        roundScores { roundScore }
      }
    }
  }
}
"""

NOTABLE_MONEY_RANK_CUTOFF = 150

SELECTED_STAT_TITLES = [
    "Official Money",
    "Victory Leaders",
    "Top 10 Finishes",
    "FedExCup Standings",
    "Official World Golf Ranking",
    "All-Around Ranking",
    "SG: Total",
    "SG: Tee-to-Green",
    "SG: Off-the-Tee",
    "SG: Approach the Green",
    "SG: Around-the-Green",
    "SG: Putting",
    "Driving Distance",
    "Driving Accuracy Percentage",
    "Greens in Regulation Percentage",
    "Scoring Average (Adjusted)",
    "Scoring Average (Actual)",
    "Putting Average",
    "Putts Per Round",
    "Sand Save Percentage",
    "Scrambling",
    "Total Driving",
    "Birdie Average",
    "Birdie or Better Percentage",
    "Bogey Avoidance",
    "Par 5 Scoring Average",
    "Total Eagles",
]

CORE_WIDE_ORDER = [
    "official_money",
    "official_money_ytd_victories",
    "victory_leaders",
    "top_10_finishes",
    "fedexcup_standings",
    "official_world_golf_ranking",
    "all_around_ranking",
    "sg_total",
    "sg_tee_to_green",
    "sg_off_the_tee",
    "sg_approach_the_green",
    "sg_around_the_green",
    "sg_putting",
    "driving_distance",
    "driving_accuracy_percentage",
    "greens_in_regulation_percentage",
    "scoring_average_adjusted",
    "scoring_average_actual",
    "putting_average",
    "putts_per_round",
    "sand_save_percentage",
    "scrambling",
    "total_driving",
    "birdie_average",
    "birdie_or_better_percentage",
    "bogey_avoidance",
    "par_5_scoring_average",
    "total_eagles",
]


def slug(value):
    value = value.lower().replace("&", "and")
    value = re.sub(r"[^a-z0-9]+", "_", value)
    return value.strip("_")


def parse_numeric(value):
    if value is None:
        return None
    text = str(value).strip()
    if not text or text in {"-", "--", "N/A"}:
        return None
    if "'" in text or "\"" in text:
        return None
    text = text.replace("$", "").replace(",", "").replace("%", "")
    text = re.sub(r"^[Tt]", "", text)
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    if not match:
        return None
    try:
        number = float(match.group(0))
    except ValueError:
        return None
    if math.isfinite(number):
        return number
    return None


def gql(query, variables):
    payload = json.dumps({"query": query, "variables": variables}).encode("utf-8")
    request = urllib.request.Request(
        GRAPHQL_ENDPOINT,
        data=payload,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0",
            "x-api-key": API_KEY,
            "x-pgat-platform": "web",
        },
    )
    for attempt in range(4):
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                data = json.loads(response.read().decode("utf-8"))
            if "errors" in data and data["errors"]:
                raise RuntimeError(json.dumps(data["errors"])[:800])
            return data["data"]
        except (urllib.error.URLError, TimeoutError, RuntimeError) as exc:
            if attempt == 3:
                raise
            time.sleep(1.5 * (attempt + 1))


def fetch_stat_details(year, stat_id, event_query=None):
    variables = {
        "tourCode": "R",
        "statId": stat_id,
        "year": int(year),
        "eventQuery": event_query,
    }
    return gql(STAT_DETAILS_QUERY, variables).get("statDetails")


def fetch_overview(year):
    return gql(STAT_OVERVIEW_QUERY, {"tourCode": "R", "year": int(year)}).get("statOverview")


def unofficial_scoring_averages(player_id):
    """Reconstruct a per-season actual scoring average from career round-by-round
    results, for seasons where the player fell short of PGA Tour's minimum
    rounds-played threshold to appear in the official ranked stat table."""
    result = gql(PLAYER_TOURNAMENT_RESULTS_QUERY, {"playerId": player_id}).get("playerProfileTournamentResults")
    strokes_by_year = {}
    rounds_by_year = {}
    for group in (result or {}).get("tournaments") or []:
        for event in group.get("tournaments") or []:
            year = event.get("year")
            if year is None:
                continue
            for round_score in event.get("roundScores") or []:
                score = round_score.get("roundScore")
                if score and score.lstrip("-").isdigit():
                    strokes_by_year[year] = strokes_by_year.get(year, 0) + int(score)
                    rounds_by_year[year] = rounds_by_year.get(year, 0) + 1
    return {
        year: {"avg": round(strokes_by_year[year] / rounds_by_year[year], 3), "rounds": rounds_by_year[year]}
        for year in strokes_by_year
        if rounds_by_year.get(year)
    }


def flatten_catalog(overview):
    catalog = []
    if not overview:
        return catalog
    seen = set()
    for category in overview.get("categories") or []:
        for subcategory in category.get("subCategories") or []:
            for stat in subcategory.get("stats") or []:
                key = (stat.get("statId"), stat.get("statTitle"), category.get("category"), subcategory.get("displayName"))
                if key in seen:
                    continue
                seen.add(key)
                catalog.append(
                    {
                        "year": overview.get("year"),
                        "category": category.get("category"),
                        "category_display": category.get("displayName"),
                        "subcategory": subcategory.get("displayName"),
                        "stat_id": stat.get("statId"),
                        "stat_title": stat.get("statTitle"),
                    }
                )
    return catalog


def selected_stats_from_catalog(catalog):
    by_title = {}
    for item in catalog:
        by_title.setdefault(item["stat_title"], item)
    selected = []
    seen = set()
    for title in SELECTED_STAT_TITLES:
        item = by_title.get(title)
        if item and item["stat_id"] not in seen:
            selected.append(item)
            seen.add(item["stat_id"])
    return selected


def player_rows(detail):
    rows = []
    for row in detail.get("rows") or []:
        if row.get("__typename") == "StatDetailsPlayer":
            rows.append(row)
    return rows


def first_stat_value(row):
    stats = row.get("stats") or []
    if not stats:
        return None
    return stats[0].get("statValue")


def stat_source_url(year, stat_id):
    return f"https://www.pgatour.com/stats/stat.{stat_id}.y{year}.html"


def build_1962_rows():
    players = [
        (1, "Arnold Palmer", 81448),
        (2, "Gene Littler", 66201),
        (3, "Jack Nicklaus", 61869),
        (4, "Billy Casper", 61842),
        (5, "Bob Goalby", 46241),
        (6, "Gary Player", 45838),
        (7, "Doug Sanders", 43340),
        (8, "Dave Ragan", 37327),
        (9, "Bobby Nichols", 34312),
        (10, "Phil Rodgers", 32182),
    ]
    return [
        {
            "year": 1962,
            "rank": rank,
            "player": name,
            "official_money": money,
            "official_money_display": f"${money:,.0f}",
            "coverage_note": "Publicly verified 1962 source lists the top 10 money leaders, not a full top 50.",
            "source_url": "https://en.wikipedia.org/wiki/1962_PGA_Tour",
        }
        for rank, name, money in players
    ]


def main():
    generated_at = datetime.now().isoformat(timespec="seconds")

    overview_2026 = fetch_overview(2026)
    stat_catalog = flatten_catalog(overview_2026)
    selected_stats = selected_stats_from_catalog(stat_catalog)
    selected_by_id = {item["stat_id"]: item for item in selected_stats}

    top50_rows = []
    wide_by_key = {}
    long_rows = []
    failures = []
    year_notes = []

    event_query_by_year = {}
    event_label_by_year = {}

    for year in YEARS:
        detail = fetch_stat_details(year, "109")
        if not detail:
            failures.append({"year": year, "stat_id": "109", "stat_title": "Official Money", "error": "No detail returned"})
            continue

        event_query = None
        event_label = "Full season"
        if year == 2026:
            pills = detail.get("tournamentPills") or []
            if pills:
                latest = pills[0]
                event_query = {"queryType": "THROUGH_EVENT", "tournamentId": latest.get("tournamentId")}
                event_label = f"Through {latest.get('displayName')} ({latest.get('tournamentId')})"
                detail = fetch_stat_details(year, "109", event_query=event_query)

        event_query_by_year[year] = event_query
        event_label_by_year[year] = event_label
        year_notes.append({"year": year, "coverage": event_label, "last_processed": detail.get("lastProcessed")})

        rows = player_rows(detail)[:TOP_N]
        for row in rows:
            key = (year, row.get("playerId"))
            money_value = first_stat_value(row)
            top_record = {
                "year": year,
                "rank": row.get("rank"),
                "player_id": row.get("playerId"),
                "player": row.get("playerName"),
                "country": row.get("country"),
                "official_money": money_value,
                "official_money_numeric": parse_numeric(money_value),
                "source_url": stat_source_url(year, "109"),
                "coverage": event_label,
            }
            for stat in row.get("stats") or []:
                field = "official_money" if slug(stat.get("statName") or "") == "money" else f"official_money_{slug(stat.get('statName') or '')}"
                top_record[field] = stat.get("statValue")
            top50_rows.append(top_record)

            wide_by_key[key] = {
                "year": year,
                "money_rank": row.get("rank"),
                "player_id": row.get("playerId"),
                "player": row.get("playerName"),
                "country": row.get("country"),
                "coverage": event_label,
                "source_url": stat_source_url(year, "109"),
                "official_money": money_value,
                "official_money_numeric": parse_numeric(money_value),
            }
            for stat in row.get("stats") or []:
                field = "official_money" if slug(stat.get("statName") or "") == "money" else f"official_money_{slug(stat.get('statName') or '')}"
                wide_by_key[key][field] = stat.get("statValue")

    rank_by_key = {}
    name_by_key = {}
    for row in top50_rows:
        year = row["year"]
        player_id = row["player_id"]
        if player_id:
            rank_by_key[(year, player_id)] = row["rank"]
            name_by_key[(year, player_id)] = row["player"]

    jobs = []
    for year in YEARS:
        for stat in selected_stats:
            jobs.append((year, stat["stat_id"], stat["stat_title"]))

    def fetch_job(job):
        year, stat_id, stat_title = job
        try:
            detail = fetch_stat_details(year, stat_id, event_query=event_query_by_year.get(year))
            return {"ok": True, "year": year, "stat_id": stat_id, "stat_title": stat_title, "detail": detail}
        except Exception as exc:
            return {"ok": False, "year": year, "stat_id": stat_id, "stat_title": stat_title, "error": str(exc)[:500]}

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(fetch_job, job) for job in jobs]
        completed = 0
        for future in as_completed(futures):
            completed += 1
            result = future.result()
            if not result["ok"] or not result.get("detail"):
                failures.append(
                    {
                        "year": result["year"],
                        "stat_id": result["stat_id"],
                        "stat_title": result["stat_title"],
                        "error": result.get("error", "No detail returned"),
                    }
                )
                continue

            year = result["year"]
            stat_id = result["stat_id"]
            detail = result["detail"]
            meta = selected_by_id.get(stat_id, {})
            stat_title = detail.get("statTitle") or result["stat_title"]
            base_slug = slug(stat_title)

            for row in player_rows(detail):
                player_id = row.get("playerId")
                if not player_id:
                    continue
                key = (year, player_id)
                wide = wide_by_key.setdefault(
                    key,
                    {
                        "year": year,
                        "money_rank": rank_by_key.get(key),
                        "player_id": player_id,
                        "player": row.get("playerName") or name_by_key.get(key),
                        "country": row.get("country"),
                        "coverage": event_label_by_year.get(year),
                        "source_url": stat_source_url(year, stat_id),
                    },
                )

                stats = row.get("stats") or []
                if stats:
                    first_value = stats[0].get("statValue")
                    wide[base_slug] = first_value
                    numeric = parse_numeric(first_value)
                    if numeric is not None:
                        wide[f"{base_slug}_numeric"] = numeric

                for stat in stats:
                    stat_name = stat.get("statName")
                    stat_value = stat.get("statValue")
                    stat_slug = slug(stat_name or "value")
                    if len(stats) > 1:
                        wide_field = f"{base_slug}_{stat_slug}"
                        wide[wide_field] = stat_value
                        numeric = parse_numeric(stat_value)
                        if numeric is not None:
                            wide[f"{wide_field}_numeric"] = numeric

                    long_rows.append(
                        {
                            "year": year,
                            "player_id": player_id,
                            "player": row.get("playerName"),
                            "country": row.get("country"),
                            "money_rank": rank_by_key.get(key),
                            "stat_id": stat_id,
                            "stat_title": stat_title,
                            "stat_category": meta.get("category_display"),
                            "stat_subcategory": meta.get("subcategory"),
                            "stat_table_rank": row.get("rank"),
                            "stat_name": stat_name,
                            "stat_value": stat_value,
                            "stat_numeric": parse_numeric(stat_value),
                            "source_url": stat_source_url(year, stat_id),
                            "coverage": event_label_by_year.get(year),
                        }
                    )
            if completed % 50 == 0:
                print(f"Fetched {completed}/{len(jobs)} stat tables")

    notable_player_ids = set()
    for row in wide_by_key.values():
        rank = parse_numeric(row.get("money_rank"))
        player_id = row.get("player_id")
        if player_id and rank is not None and rank <= NOTABLE_MONEY_RANK_CUTOFF:
            notable_player_ids.add(player_id)

    def reconstruct_job(player_id):
        try:
            return player_id, unofficial_scoring_averages(player_id), None
        except Exception as exc:
            return player_id, None, str(exc)[:300]

    unofficial_by_player = {}
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(reconstruct_job, player_id) for player_id in notable_player_ids]
        completed = 0
        for future in as_completed(futures):
            completed += 1
            player_id, per_year, error = future.result()
            if error:
                failures.append({"year": None, "stat_id": "unofficial_scoring", "stat_title": "Unofficial Scoring Average", "error": f"{player_id}: {error}"})
            else:
                unofficial_by_player[player_id] = per_year
            if completed % 100 == 0:
                print(f"Reconstructed scoring for {completed}/{len(notable_player_ids)} notable players")

    unofficial_fill_count = 0
    for row in wide_by_key.values():
        if row.get("scoring_average_numeric") is not None:
            continue
        info = (unofficial_by_player.get(row.get("player_id")) or {}).get(row.get("year"))
        if info:
            row["scoring_average_unofficial_actual"] = f"{info['avg']:.3f}"
            row["scoring_average_unofficial_actual_numeric"] = info["avg"]
            row["scoring_average_unofficial_rounds"] = info["rounds"]
            unofficial_fill_count += 1

    wide_rows = list(wide_by_key.values())
    wide_rows.sort(key=lambda r: (r.get("year") or 0, parse_numeric(r.get("money_rank")) or 9999, r.get("player") or ""))
    top50_rows.sort(key=lambda r: (r.get("year") or 0, parse_numeric(r.get("rank")) or 9999, r.get("player") or ""))
    long_rows.sort(key=lambda r: (r.get("year") or 0, parse_numeric(r.get("money_rank")) or 9999, r.get("player") or "", r.get("stat_title") or "", r.get("stat_name") or ""))

    data = {
        "generated_at": generated_at,
        "run_date": datetime.now().strftime("%Y-%m-%d"),
        "coverage": {
            "modern_years": YEARS,
            "top_n": TOP_N,
            "top_filter": "All ranked PGA Tour players in each stat table for each season (no cap).",
            "season_2026_note": event_label_by_year.get(2026, "Partial 2026 data through completed events available from PGA Tour stats."),
            "season_1962_note": "Publicly verified 1962 source lists the top 10 money leaders, not a full top 50.",
            "gir_definition": "GIR means Greens in Regulation: reaching the green with at least two strokes left for par. GIR% is greens hit in regulation divided by holes played.",
            "scoring_unofficial_note": f"For seasons below the PGA Tour's minimum rounds-played threshold to qualify for the official ranked Scoring Average, an unofficial actual scoring average (total strokes / rounds played, no course-difficulty adjustment) is reconstructed from that player's individual tournament results. Limited to players who ranked inside the top {NOTABLE_MONEY_RANK_CUTOFF} in Official Money for at least one season.",
        },
        "sources": [
            {
                "name": "PGA Tour Stats",
                "url": "https://www.pgatour.com/stats",
                "used_for": "Official Money top 50 filters and selected season statistics for 2000-2026.",
            },
            {
                "name": "1962 PGA Tour",
                "url": "https://en.wikipedia.org/wiki/1962_PGA_Tour",
                "used_for": "1962 money list leaders available from the public season summary.",
            },
        ],
        "year_notes": year_notes,
        "selected_stats": selected_stats,
        "stat_catalog_2026": stat_catalog,
        "rows_1962": build_1962_rows(),
        "top50_by_year": top50_rows,
        "selected_metrics_wide": wide_rows,
        "stat_values_long": long_rows,
        "failures": failures,
        "core_wide_order": CORE_WIDE_ORDER,
    }
    OUTPUT_JSON.write_text(json.dumps(data, indent=2), encoding="utf-8")
    print(f"Wrote {OUTPUT_JSON}")
    print(f"Top 50 rows: {len(top50_rows)}")
    print(f"Wide rows: {len(wide_rows)}")
    print(f"Long rows: {len(long_rows)}")
    print(f"Notable players reconstructed: {len(notable_player_ids)}")
    print(f"Unofficial scoring average rows filled: {unofficial_fill_count}")
    print(f"Failures: {len(failures)}")


if __name__ == "__main__":
    main()
