#!/usr/bin/env python3
"""
Fetch contribution totals via GitHub GraphQL and update README.md between markers.

Prefers PERSONAL_TOKEN env var (set in workflow to your PAT), falls back to GITHUB_TOKEN.
"""

import os
import sys
import argparse
import requests
import textwrap

GQL_URL = "https://api.github.com/graphql"
GITHUB_README_MARKER_START = "<!-- GITHUB-STATS:START -->"
GITHUB_README_MARKER_END = "<!-- GITHUB-STATS:END -->"

QUERY = """
query($login: String!) {
  user(login: $login) {
    contributionsCollection {
      contributionCalendar { totalContributions }
      totalCommitContributions
      totalPullRequestContributions
      totalIssueContributions
      totalPullRequestReviewContributions
    }
    pullRequests(states: MERGED) {
      totalCount
    }
  }
}
"""

def fetch_stats(username, token):
    headers = {"Authorization": f"bearer {token}"}
    payload = {"query": QUERY, "variables": {"login": username}}
    r = requests.post(GQL_URL, json=payload, headers=headers, timeout=30)
    r.raise_for_status()
    data = r.json()
    if "errors" in data:
        raise RuntimeError(data["errors"])
    return data["data"]["user"]

def build_stats_table(user_data):
    cc = user_data["contributionsCollection"]
    total_contribs = cc["contributionCalendar"]["totalContributions"]
    total_commits = cc.get("totalCommitContributions", 0)
    total_prs = cc.get("totalPullRequestContributions", 0)
    total_issues = cc.get("totalIssueContributions", 0)
    total_reviews = cc.get("totalPullRequestReviewContributions", 0)
    merged_prs = user_data.get("pullRequests", {}).get("totalCount", 0)

    md = textwrap.dedent(f"""
    {GITHUB_README_MARKER_START}
    | Metric | Count |
    |---|---:|
    | Total contributions (year) | {total_contribs} |
    | Total commits | {total_commits} |
    | Pull requests opened | {total_prs} |
    | Pull requests merged | {merged_prs} |
    | Issues opened | {total_issues} |
    | PR reviews | {total_reviews} |
    {GITHUB_README_MARKER_END}
    """)
    return md

def update_readme(readme_path, stats_block):
    with open(readme_path, "r", encoding="utf-8") as f:
        content = f.read()

    if GITHUB_README_MARKER_START in content and GITHUB_README_MARKER_END in content:
        pre, rest = content.split(GITHUB_README_MARKER_START, 1)
        _, post = rest.split(GITHUB_README_MARKER_END, 1)
        new_content = pre + stats_block + post
    else:
        new_content = content + "\n\n" + stats_block

    if new_content != content:
        with open(readme_path, "w", encoding="utf-8") as f:
            f.write(new_content)
        print("README updated")
        return True
    print("No changes to README")
    return False

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--username", required=True)
    parser.add_argument("--readme", default="README.md")
    args = parser.parse_args()

    token = os.environ.get("PERSONAL_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        print("PERSONAL_TOKEN or GITHUB_TOKEN not set", file=sys.stderr)
        sys.exit(1)

    user_data = fetch_stats(args.username, token)
    stats_block = build_stats_table(user_data)
    update_readme(args.readme, stats_block)

if __name__ == "__main__":
    main()
