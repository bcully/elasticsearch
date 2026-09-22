#!/usr/bin/env python3
"""
fetch-muted-tests.py — collect unmuted test data from git + GitHub

For each test entry removed from muted-tests.yml, records:
  - test class / method
  - the GitHub issue that tracked the mute
  - when that issue was opened / closed / why / by what PR
  - labels on the issue (including team labels)

Usage:
  # Last 100 unmuted tests
  ./dev-tools/fetch-muted-tests.py

  # Last 50
  ./dev-tools/fetch-muted-tests.py --count 50

  # All unmutes in a date window
  ./dev-tools/fetch-muted-tests.py --since 2026-03-01 --until 2026-09-22

  # JSON output
  ./dev-tools/fetch-muted-tests.py --since 2026-06-01 --format json --output june.json

Requires: git, gh (GitHub CLI, authenticated)
"""

import argparse
import csv
import json
import re
import subprocess
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------

def git(repo, *args, check=False):
    result = subprocess.run(
        ["git", "-C", repo] + list(args),
        capture_output=True, text=True,
    )
    if check and result.returncode != 0:
        print(f"git error: {result.stderr.strip()}", file=sys.stderr)
    return result.stdout


def iter_unmute_commits(repo, since=None, until=None):
    """
    Stream (commit_info, diff_text) pairs for commits that removed entries
    from muted-tests.yml, using a single `git log -p` process.

    The caller can break early; the underlying git process is terminated via
    the generator's finally block so it never blocks waiting for a reader.
    """
    cmd = [
        "git", "-C", repo,
        "log", "-p",
        "--format=COMMIT %H %as %s",
    ]
    if since:
        cmd += [f"--since={since}"]
    if until:
        cmd += [f"--until={until}"]
    cmd += ["--", "muted-tests.yml"]

    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, text=True)
    try:
        current = None
        diff_lines = []
        for raw in proc.stdout:
            line = raw.rstrip("\n")
            if line.startswith("COMMIT "):
                if current is not None:
                    yield current, "\n".join(diff_lines)
                parts = line.split(" ", 3)
                current = {
                    "sha":     parts[1] if len(parts) > 1 else "",
                    "date":    parts[2] if len(parts) > 2 else "",
                    "subject": parts[3] if len(parts) > 3 else "",
                }
                diff_lines = []
            else:
                diff_lines.append(line)
        if current is not None:
            yield current, "\n".join(diff_lines)
    finally:
        proc.stdout.close()
        proc.terminate()
        proc.wait()


def parse_removed_entries(diff_text):
    """
    Extract muted-tests.yml entries that were removed in a diff.

    Each entry in the file looks like:
        - class: "org.elasticsearch.SomeTest"
          method: "someMethod"             # optional
          issue: "https://github.com/.../issues/12345"

    Returns a list of dicts with keys: class, method (optional), issue.
    """
    entries = []
    current = {}

    for line in diff_text.splitlines():
        if not line.startswith("-"):
            # Flush completed entry on any non-removed line
            if "class" in current and "issue" in current:
                entries.append(current)
            current = {}
            continue

        content = line[1:]  # strip leading '-'

        # Entries are unquoted ("class: Foo") but may occasionally be quoted ("class: \"Foo\"")
        m = re.match(r'\s*-\s+class:\s+"?([^"\n]+?)"?\s*$', content)
        if m:
            if "class" in current and "issue" in current:
                entries.append(current)
            current = {"class": m.group(1).strip()}
            continue

        if not current:
            continue

        m = re.match(r'\s+method:\s+"?(.+?)"?\s*$', content)
        if m:
            current["method"] = m.group(1).strip()
            continue

        m = re.match(r'\s+issue:\s+"?(.+?)"?\s*$', content)
        if m:
            current["issue"] = m.group(1).strip()

    if "class" in current and "issue" in current:
        entries.append(current)

    return entries



def extract_pr_number(subject):
    m = re.search(r"\(#(\d+)\)", subject)
    return m.group(1) if m else ""


def extract_issue_number(issue_url):
    m = re.search(r"/issues/(\d+)$", issue_url or "")
    return m.group(1) if m else ""


# ---------------------------------------------------------------------------
# GitHub helpers
# ---------------------------------------------------------------------------

def gh_graphql(query, retries=1):
    """Run a GitHub GraphQL query via gh CLI; retry once on failure."""
    for attempt in range(retries + 1):
        result = subprocess.run(
            ["gh", "api", "graphql", "-f", f"query={query}"],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            try:
                return json.loads(result.stdout)
            except json.JSONDecodeError:
                pass
        if attempt < retries:
            continue
    return {}


_ISSUE_FRAGMENT = """
  number createdAt closedAt state stateReason
  labels(first: 20) { nodes { name } }
  timelineItems(last: 10, itemTypes: [CLOSED_EVENT]) {
    nodes {
      ... on ClosedEvent {
        closer { ... on PullRequest { number } }
      }
    }
  }
"""


def fetch_issues(issue_numbers, batch_size=50):
    """
    Batch-fetch issue metadata from GitHub GraphQL.

    Returns a dict mapping issue_number (str) -> dict with keys:
      date_opened, date_closed, state_reason, closing_pr, labels
    """
    numbers = [str(n) for n in issue_numbers if n]
    results = {}

    for start in range(0, len(numbers), batch_size):
        batch = numbers[start : start + batch_size]
        parts = []
        for n in batch:
            parts.append(
                f'  i{n}: repository(owner:"elastic", name:"elasticsearch") {{'
                f'    issue(number: {n}) {{ {_ISSUE_FRAGMENT} }}'
                f'  }}'
            )
        query = "{\n" + "\n".join(parts) + "\n}"
        data = gh_graphql(query, retries=1).get("data", {})

        for key, val in data.items():
            if not val or not val.get("issue"):
                continue
            iss = val["issue"]
            num = str(iss["number"])

            closing_pr = ""
            for item in iss.get("timelineItems", {}).get("nodes", []):
                closer = (item or {}).get("closer") or {}
                if closer.get("number"):
                    closing_pr = str(closer["number"])
                    break

            results[num] = {
                "date_opened": (iss.get("createdAt") or "")[:10],
                "date_closed": (iss.get("closedAt") or "")[:10],
                "state_reason": iss.get("stateReason") or "",
                "closing_pr": closing_pr,
                "labels": ",".join(
                    node["name"]
                    for node in iss.get("labels", {}).get("nodes", [])
                ),
            }

    return results


def fetch_seed_reproducibility(issue_numbers, batch_size=5):
    """
    For each issue, look for a 'Test Failure Analysis' comment from
    elasticsearchmachine and extract whether the failure reproduced with
    the same deterministic seed.

    Returns a dict mapping issue_number (str) -> "yes" | "no" | "".
    Empty string means the bot comment was absent (older issues) or had
    no Result line.

    Batch size is small (5) because comment bodies can be very large.
    """
    numbers = [str(n) for n in issue_numbers if n]
    results = {n: "" for n in numbers}

    for start in range(0, len(numbers), batch_size):
        batch = numbers[start : start + batch_size]
        parts = [
            f'  i{n}: repository(owner:"elastic", name:"elasticsearch") {{'
            f'    issue(number: {n}) {{'
            f'      comments(first: 15) {{'
            f'        nodes {{ author {{ login }} body }}'
            f'      }}'
            f'    }}'
            f'  }}'
            for n in batch
        ]
        data = gh_graphql("{\n" + "\n".join(parts) + "\n}", retries=1).get("data", {})
        for key, val in data.items():
            if not val or not val.get("issue"):
                continue
            num = key[1:]  # strip leading 'i'
            for comment in val["issue"]["comments"]["nodes"]:
                if comment["author"]["login"] != "elasticsearchmachine":
                    continue
                body = comment.get("body") or ""
                if "Test Failure Analysis" not in body:
                    continue
                m = re.search(r"Result[:\s]+\*\*([^*\n]+)", body)
                if m:
                    raw = m.group(1).strip().lower()
                    # Variants seen: "not reproduced", "Not reproducible", "reproduced ✨"
                    if raw.startswith("not ") or raw.startswith("n/a"):
                        results[num] = "no"
                    elif "reproduced" in raw or "reproducible" in raw:
                        results[num] = "yes"
                    # "blocked" and other infrastructure failures → leave empty
                break  # use the first matching comment

        done = min(start + batch_size, len(numbers))
        print(
            f"  checked {done}/{len(numbers)} issues for seed reproducibility…",
            file=sys.stderr, end="\r",
        )

    print(file=sys.stderr)
    return results


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

OUTPUT_FIELDS = [
    "test_class",
    "test_method",
    "issue_number",
    "issue_url",
    "date_muted",
    "date_opened",
    "date_closed",
    "state_reason",
    "closing_pr",
    "labels",
    "seed_reproduces",
    "unmute_commit",
    "unmute_date",
    "unmute_pr",
]


def main():
    parser = argparse.ArgumentParser(
        description="Collect unmuted-test records from git history + GitHub."
    )

    scope = parser.add_mutually_exclusive_group()
    scope.add_argument(
        "--count", type=int, default=100, metavar="N",
        help="Stop after collecting N test entries (default: 100).",
    )
    scope.add_argument(
        "--since", metavar="DATE",
        help="Collect all unmutes on or after this ISO date (e.g. 2026-03-01). "
             "Mutually exclusive with --count.",
    )

    parser.add_argument(
        "--until", metavar="DATE",
        help="Upper date bound. Works with both --count and --since.",
    )
    parser.add_argument(
        "--repo", default=".",
        help="Path to elasticsearch git repo (default: current directory).",
    )
    parser.add_argument(
        "--output", default="muted_tests.csv",
        help="Output file path (default: muted_tests.csv).",
    )
    parser.add_argument(
        "--format", choices=["csv", "json"], default="csv",
        help="Output format (default: csv).",
    )
    parser.add_argument(
        "--include-reproducibility", action="store_true",
        help=(
            "Fetch issue comments to check whether the test failure reproduced "
            "with the same deterministic seed (from the elasticsearchmachine "
            "CI bot's 'Test Failure Analysis' comment). Adds a 'seed_reproduces' "
            "column: 'yes', 'no', or empty when the bot comment is absent. "
            "Requires one additional round of GitHub API calls."
        ),
    )

    args = parser.parse_args()
    repo = str(Path(args.repo).resolve())

    # Verify repo
    if not Path(repo, ".git").exists():
        sys.exit(f"error: {repo!r} is not a git repository")

    # Verify gh is available and authenticated
    auth = subprocess.run(["gh", "auth", "status"], capture_output=True, text=True)
    if auth.returncode != 0:
        sys.exit("error: gh CLI is not authenticated — run `gh auth login` first")

    print("Scanning git history for unmute commits…", file=sys.stderr)

    rows = []
    commits_seen = 0
    gen = iter_unmute_commits(repo, since=args.since, until=args.until)
    try:
        for commit, diff in gen:
            commits_seen += 1
            entries = parse_removed_entries(diff)
            unmute_pr = extract_pr_number(commit["subject"])

            for entry in entries:
                rows.append({
                    "test_class": entry["class"],
                    "test_method": entry.get("method", ""),
                    "issue_number": extract_issue_number(entry.get("issue", "")),
                    "issue_url": entry.get("issue", ""),
                    "unmute_commit": commit["sha"][:12],
                    "unmute_date": commit["date"],
                    "unmute_pr": unmute_pr,
                    # filled in below
                    "date_muted": "",
                    "date_opened": "",
                    "date_closed": "",
                    "state_reason": "",
                    "closing_pr": "",
                    "labels": "",
                    "seed_reproduces": "",
                })

            if args.since is None and len(rows) >= args.count:
                rows = rows[: args.count]
                break
    finally:
        gen.close()  # terminates the git process if we stopped early

    if not rows:
        sys.exit("No unmuted test entries found in the given range.")

    print(
        f"Collected {len(rows)} unmuted test entries from {commits_seen} commits.",
        file=sys.stderr,
    )

    unique_issues = sorted({r["issue_number"] for r in rows if r["issue_number"]})
    print(
        f"Fetching data for {len(unique_issues)} unique GitHub issues…",
        file=sys.stderr,
    )
    issue_data = fetch_issues(unique_issues)

    for row in rows:
        info = issue_data.get(row["issue_number"], {})
        for key in ("date_opened", "date_closed", "state_reason", "closing_pr", "labels"):
            row[key] = info.get(key, "")
        row["date_muted"] = row["date_opened"]  # issue creation == when the test was muted
        row["seed_reproduces"] = ""

    if args.include_reproducibility:
        print(
            f"Fetching seed-reproducibility for {len(unique_issues)} issues…",
            file=sys.stderr,
        )
        repro = fetch_seed_reproducibility(unique_issues)
        for row in rows:
            row["seed_reproduces"] = repro.get(row["issue_number"], "")
        yes = sum(1 for v in repro.values() if v == "yes")
        no  = sum(1 for v in repro.values() if v == "no")
        na  = sum(1 for v in repro.values() if v == "")
        print(
            f"  seed_reproduces: yes={yes}  no={no}  n/a={na}",
            file=sys.stderr,
        )

    # Write output
    if args.format == "json":
        with open(args.output, "w") as f:
            json.dump([{k: r[k] for k in OUTPUT_FIELDS} for r in rows], f, indent=2)
    else:
        with open(args.output, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=OUTPUT_FIELDS, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)

    print(f"Wrote {len(rows)} rows → {args.output}", file=sys.stderr)


if __name__ == "__main__":
    main()
