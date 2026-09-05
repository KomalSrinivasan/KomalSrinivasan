#!/usr/bin/env python3
"""
Enhanced GitHub stats generator.

- Produces metrics for three periods:
  * All time (from account creation to today)
  * Last year (calendar 2025)
  * This year (calendar 2026 up to today)

Metrics:
- total contributions (contributionCalendar.totalContributions)
- total commits
- PRs opened
- PRs merged
- issues opened
- PR reviews
- current streak (days)
- longest streak (days)

Requires: PERSONAL_TOKEN env var (preferred) or fallback to GITHUB_TOKEN.
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
    return datetime(dt.year, dt.month, dt.day, tzinfo=timezone.utc).isoformat()

def date_to_ymd(dt: date):
    return dt.strftime("%Y-%m-%d")

def get_user_created_at(login, token):
    data = graphql_request(USER_QUERY, {"login": login}, token)
    created_iso = data["user"]["createdAt"]
    # parse
    ct = datetime.fromisoformat(created_iso.replace("Z", "+00:00")).date()
    return ct

def get_contribs_for_range(login, from_dt_iso, to_dt_iso, token):
    variables = {"login": login, "from": from_dt_iso, "to": to_dt_iso}
    data = graphql_request(CONTRIBS_QUERY, variables, token)
    cc = data["user"]["contributionsCollection"]
    # flatten calendar days to list
    days = []
    for week in cc["contributionCalendar"]["weeks"]:
        for d in week["contributionDays"]:
            days.append({"date": d["date"], "count": d["contributionCount"]})
    # sort just in case
    days.sort(key=lambda x: x["date"])
    return {
        "totalContributions": cc["contributionCalendar"]["totalContributions"],
        "totalCommits": cc.get("totalCommitContributions", 0),
        "totalPRs_contrib": cc.get("totalPullRequestContributions", 0),
        "totalIssues": cc.get("totalIssueContributions", 0),
        "totalReviews": cc.get("totalPullRequestReviewContributions", 0),
        "days": days
    }

def search_count(query_string, token):
    data = graphql_request(SEARCH_QUERY, {"queryString": query_string}, token)
    return data["search"]["issueCount"]

def compute_streaks(days):
    """
    days: list of {"date": "YYYY-MM-DD", "count": N}, sorted ascending
    returns (current_streak_days, longest_streak_days)
    """
    if not days:
        return 0, 0
    longest = 0
    current = 0
    max_curr_end = 0
    # convert into mapping date->count for consecutive check
    day_map = {d["date"]: d["count"] for d in days}
    # iterate from earliest to latest
    date_list = [datetime.fromisoformat(d["date"]).date() for d in days]
    # we need to walk continuous calendar days between min and max
    start = date_list[0]
    end = date_list[-1]
    ptr = start
    longest = 0
    current = 0
    # compute current streak meaning streak that ends at end (latest day)
    while ptr <= end:
        key = ptr.isoformat()
        count = day_map.get(key, 0)
        if count and count > 0:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
        ptr += timedelta(days=1)
    # current streak (ending at last calendar day):
    # find streak ending at end
    streak = 0
    ptr = end
    while True:
        key = ptr.isoformat()
        if day_map.get(key, 0) and day_map.get(key, 0) > 0:
            streak += 1
            ptr -= timedelta(days=1)
            # stop if ptr < start
            if ptr < start:
                break
        else:
            break
    return streak, longest

def build_table_section(title, metrics):
    """
    metrics dict should contain:
    totalContributions, totalCommits, prs_opened, prs_merged, totalIssues, totalReviews, current_streak, longest_streak
    """
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
    all_from_iso = iso_datetime(created_at)
    all_to_iso = iso_datetime(today)

    # Last year: calendar 2025
    last_year = 2025
    ly_from = date(last_year, 1, 1)
    ly_to = date(last_year, 12, 31)

    # This year: 2026 calendar year-to-date
    this_year = today.year
    ty_from = date(this_year, 1, 1)
    ty_to = today

    # fetch contributionsCollection for each period
    all_data = get_contribs_for_range(login, all_from_iso, all_to_iso, token)
    ly_data = get_contribs_for_range(login, iso_datetime(ly_from), iso_datetime(ly_to), token)
    ty_data = get_contribs_for_range(login, iso_datetime(ty_from), iso_datetime(ty_to), token)

    # compute streaks
    all_curr, all_long = compute_streaks(all_data["days"])
    ly_curr, ly_long = compute_streaks(ly_data["days"])
    ty_curr, ty_long = compute_streaks(ty_data["days"])

    # get PR counts & merged using search (issueCount)
    def prs_opened_count(period_from, period_to):
        q = f"type:pr author:{login} created:{date_to_ymd(period_from)}..{date_to_ymd(period_to)}"
        return search_count(q, token)

    def prs_merged_count(period_from, period_to):
        q = f"type:pr author:{login} merged:{date_to_ymd(period_from)}..{date_to_ymd(period_to)}"
        return search_count(q, token)

    # All-time PRs opened / merged (no range)
    all_prs_opened = search_count(f"type:pr author:{login}", token)
    all_prs_merged = search_count(f"type:pr author:{login} is:merged", token)

    # Last year
    ly_prs_opened = prs_opened_count(ly_from, ly_to)
    ly_prs_merged = prs_merged_count(ly_from, ly_to)

    # This year
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

    # Compose markdown block
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
        # append at end if markers not present
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
