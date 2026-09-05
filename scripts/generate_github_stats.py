#!/usr/bin/env python3
"""
Enhanced GitHub stats generator (fixed to avoid the GraphQL 1-year 'from..to' limit).

- Splits requests longer than 1 year into <=365-day chunks and aggregates results.
- Produces metrics for three periods:
  * All time (from account creation to today)
  * Last year (calendar 2025)
  * This year (current year YTD)

Requires PERSONAL_TOKEN env var (preferred) or fallback to GITHUB_TOKEN.
"""

import os
import sys
import argparse
import requests
import textwrap
from datetime import datetime, date, timezone, timedelta

GQL_URL = "https://api.github.com/graphql"
README_PATH = "README.md"

MARKER_START = "<!-- GITHUB-STATS:START -->"
MARKER_END = "<!-- GITHUB-STATS:END -->"

# GraphQL snippets/queries
USER_QUERY = """
query($login: String!) {
  user(login: $login) {
    createdAt
  }
}
"""

CONTRIBS_QUERY = """
query($login: String!, $from: DateTime!, $to: DateTime!) {
  user(login: $login) {
    contributionsCollection(from: $from, to: $to) {
      contributionCalendar {
        totalContributions
        weeks {
          contributionDays {
            date
            contributionCount
          }
        }
      }
      totalCommitContributions
      totalPullRequestContributions
      totalIssueContributions
      totalPullRequestReviewContributions
    }
  }
}
"""

SEARCH_QUERY = """
query($queryString: String!) {
  search(query: $queryString, type: ISSUE) {
    issueCount
  }
}
"""

def graphql_request(query, variables, token):
    headers = {"Authorization": f"bearer {token}"}
    resp = requests.post(GQL_URL, json={"query": query, "variables": variables}, headers=headers, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if "errors" in data:
        raise RuntimeError(data["errors"])
    return data.get("data", {})

def iso_datetime(dt: date):
    # produce ISO datetime string at midnight UTC for the date
    return datetime(dt.year, dt.month, dt.day, tzinfo=timezone.utc).isoformat()

def date_to_ymd(dt: date):
    return dt.strftime("%Y-%m-%d")

def get_user_created_at(login, token):
    data = graphql_request(USER_QUERY, {"login": login}, token)
    created_iso = data["user"]["createdAt"]
    ct = datetime.fromisoformat(created_iso.replace("Z", "+00:00")).date()
    return ct

def get_contribs_for_range_dates(login, from_date: date, to_date: date, token):
    """
    Fetch contributionsCollection for a date range. If the range exceeds 365 days,
    split into chunks of <=365 days and aggregate totals and contribution days.
    Returns dict with totals and a combined 'days' list of {date, count}.
    """
    if from_date > to_date:
        return {
            "totalContributions": 0,
            "totalCommits": 0,
            "totalPRs_contrib": 0,
            "totalIssues": 0,
            "totalReviews": 0,
            "days": []
        }

    agg_total_contribs = 0
    agg_total_commits = 0
    agg_total_prs_contrib = 0
    agg_total_issues = 0
    agg_total_reviews = 0
    day_map = {}  # date (YYYY-MM-DD) -> count

    start = from_date
    while start <= to_date:
        chunk_end = min(start + timedelta(days=364), to_date)  # <=365 days per chunk
        variables = {
            "login": login,
            "from": iso_datetime(start),
            "to": iso_datetime(chunk_end)
        }
        data = graphql_request(CONTRIBS_QUERY, variables, token)
        cc = data["user"]["contributionsCollection"]
        # sum numeric totals
        agg_total_contribs += cc["contributionCalendar"]["totalContributions"] or 0
        agg_total_commits += cc.get("totalCommitContributions", 0) or 0
        agg_total_prs_contrib += cc.get("totalPullRequestContributions", 0) or 0
        agg_total_issues += cc.get("totalIssueContributions", 0) or 0
        agg_total_reviews += cc.get("totalPullRequestReviewContributions", 0) or 0

        # collect days
        for week in cc["contributionCalendar"]["weeks"]:
            for d in week["contributionDays"]:
                key = d["date"]
                # multiple chunks shouldn't overlap on same date, but just in case sum counts
                day_map[key] = day_map.get(key, 0) + (d.get("contributionCount") or 0)

        # advance
        start = chunk_end + timedelta(days=1)

    # create sorted days list
    days = [{"date": k, "count": v} for k, v in sorted(day_map.items())]

    return {
        "totalContributions": agg_total_contribs,
        "totalCommits": agg_total_commits,
        "totalPRs_contrib": agg_total_prs_contrib,
        "totalIssues": agg_total_issues,
        "totalReviews": agg_total_reviews,
        "days": days
    }

def search_count(query_string, token):
    data = graphql_request(SEARCH_QUERY, {"queryString": query_string}, token)
    return data["search"]["issueCount"]

def compute_streaks(days, period_start_date=None, period_end_date=None):
    """
    days: list of {"date": "YYYY-MM-DD", "count": N}, sorted ascending
    returns (current_streak_days, longest_streak_days)
    """
    if not days:
        return 0, 0

    # Build day_map
    day_map = {d["date"]: d["count"] for d in days}
    # Determine full range to scan
    if period_start_date is None:
        start = datetime.fromisoformat(days[0]["date"]).date()
    else:
        start = period_start_date
    if period_end_date is None:
        end = datetime.fromisoformat(days[-1]["date"]).date()
    else:
        end = period_end_date

    longest = 0
    current_run = 0
    ptr = start
    while ptr <= end:
        key = ptr.isoformat()
        if day_map.get(key, 0) > 0:
            current_run += 1
            if current_run > longest:
                longest = current_run
        else:
            current_run = 0
        ptr += timedelta(days=1)

    # current streak ending at 'end'
    streak = 0
    ptr = end
    while ptr >= start:
        if day_map.get(ptr.isoformat(), 0) > 0:
            streak += 1
            ptr -= timedelta(days=1)
        else:
            break

    return streak, longest

def build_table_section(title, metrics):
    md = f"### {title}\n\n"
    md += "| Metric | Count |\n"
    md += "|---|---:|\n"
    md += f"| Total contributions | {metrics['totalContributions']} |\n"
    md += f"| Total commits | {metrics['totalCommits']} |\n"
    md += f"| Pull requests opened | {metrics['prs_opened']} |\n"
    md += f"| Pull requests merged | {metrics['prs_merged']} |\n"
    md += f"| Issues opened | {metrics['totalIssues']} |\n"
    md += f"| PR reviews | {metrics['totalReviews']} |\n"
    md += f"| Current streak (days) | {metrics['current_streak']} |\n"
    md += f"| Longest streak (days) | {metrics['longest_streak']} |\n\n"
    return md

def generate_block(login, token):
    today = datetime.now(timezone.utc).date()

    # get user creation date for all-time base
    created_at = get_user_created_at(login, token)

    # define periods
    all_from = created_at
    all_to = today

    last_year = 2025
    ly_from = date(last_year, 1, 1)
    ly_to = date(last_year, 12, 31)

    this_year = today.year
    ty_from = date(this_year, 1, 1)
    ty_to = today

    # fetch contributions for each period (handles >1yr by splitting)
    all_data = get_contribs_for_range_dates(login, all_from, all_to, token)
    ly_data = get_contribs_for_range_dates(login, ly_from, ly_to, token)
    ty_data = get_contribs_for_range_dates(login, ty_from, ty_to, token)

    # compute streaks (limit the scanning range properly)
    all_curr, all_long = compute_streaks(all_data["days"], period_start_date=all_from, period_end_date=all_to)
    ly_curr, ly_long = compute_streaks(ly_data["days"], period_start_date=ly_from, period_end_date=ly_to)
    ty_curr, ty_long = compute_streaks(ty_data["days"], period_start_date=ty_from, period_end_date=ty_to)

    # PR counts using search
    def prs_opened_count(period_from, period_to):
        q = f"type:pr author:{login} created:{date_to_ymd(period_from)}..{date_to_ymd(period_to)}"
        return search_count(q, token)

    def prs_merged_count(period_from, period_to):
        q = f"type:pr author:{login} merged:{date_to_ymd(period_from)}..{date_to_ymd(period_to)}"
        return search_count(q, token)

    all_prs_opened = search_count(f"type:pr author:{login}", token)
    all_prs_merged = search_count(f"type:pr author:{login} is:merged", token)

    ly_prs_opened = prs_opened_count(ly_from, ly_to)
    ly_prs_merged = prs_merged_count(ly_from, ly_to)

    ty_prs_opened = prs_opened_count(ty_from, ty_to)
    ty_prs_merged = prs_merged_count(ty_from, ty_to)

    # Build metrics dicts
    all_metrics = {
        "totalContributions": all_data["totalContributions"],
        "totalCommits": all_data["totalCommits"],
        "prs_opened": all_prs_opened,
        "prs_merged": all_prs_merged,
        "totalIssues": all_data["totalIssues"],
        "totalReviews": all_data["totalReviews"],
        "current_streak": all_curr,
        "longest_streak": all_long,
    }
    ly_metrics = {
        "totalContributions": ly_data["totalContributions"],
        "totalCommits": ly_data["totalCommits"],
        "prs_opened": ly_prs_opened,
        "prs_merged": ly_prs_merged,
        "totalIssues": ly_data["totalIssues"],
        "totalReviews": ly_data["totalReviews"],
        "current_streak": ly_curr,
        "longest_streak": ly_long,
    }
    ty_metrics = {
        "totalContributions": ty_data["totalContributions"],
        "totalCommits": ty_data["totalCommits"],
        "prs_opened": ty_prs_opened,
        "prs_merged": ty_prs_merged,
        "totalIssues": ty_data["totalIssues"],
        "totalReviews": ty_data["totalReviews"],
        "current_streak": ty_curr,
        "longest_streak": ty_long,
    }

    md = MARKER_START + "\n\n"
    md += build_table_section("All time", all_metrics)
    md += build_table_section(f"Year {last_year}", ly_metrics)
    md += build_table_section(f"Year {this_year} (YTD)", ty_metrics)
    md += MARKER_END + "\n"
    return md

def update_readme_block(readme_path, block_text):
    with open(readme_path, "r", encoding="utf-8") as f:
        content = f.read()
    if MARKER_START in content and MARKER_END in content:
        pre, rest = content.split(MARKER_START, 1)
        _, post = rest.split(MARKER_END, 1)
        new_content = pre + block_text + post
    else:
        new_content = content + "\n\n" + block_text
    if new_content != content:
        with open(readme_path, "w", encoding="utf-8") as f:
            f.write(new_content)
        print("README updated")
        return True
    print("No changes")
    return False

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--username", required=True, help="GitHub username to fetch stats for")
    parser.add_argument("--readme", default=README_PATH)
    args = parser.parse_args()

    token = os.environ.get("PERSONAL_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        print("Error: PERSONAL_TOKEN or GITHUB_TOKEN must be set", file=sys.stderr)
        sys.exit(1)

    try:
        block = generate_block(args.username, token)
    except Exception as e:
        print("Error generating stats:", e, file=sys.stderr)
        sys.exit(1)

    updated = update_readme_block(args.readme, block)
    sys.exit(0 if updated else 0)

if __name__ == "__main__":
    main()
