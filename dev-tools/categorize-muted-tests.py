#!/usr/bin/env python3
"""
categorize-muted-tests.py — add a fix-category column to fetch-muted-tests output

Reads a CSV produced by fetch-muted-tests.py and appends a `category` column:

  production_fix   closing PR touched non-test production source files
  test_fix         closing PR touched only test/fixture files
  infrastructure   issue closed NOT_PLANNED due to transient CI failure
                   (connection errors, passing on immediate retry, etc.)
  stale            issue closed NOT_PLANNED for other reasons
                   (test renamed/removed, won't fix, duplicate)
  unmute_only      issue COMPLETED but no closing PR is traceable

Classification order:
  1. NOT_PLANNED issues → inspect issue body for infrastructure signals
  2. COMPLETED with closing_pr → inspect PR files
  3. COMPLETED with no closing_pr but unmute_pr references a fix → inspect that PR
  4. Otherwise → unmute_only

Usage:
  ./dev-tools/categorize-muted-tests.py --input muted_tests.csv
  ./dev-tools/categorize-muted-tests.py --input muted_tests.csv --output out.csv
  ./dev-tools/categorize-muted-tests.py --input muted_tests.csv --format json
"""

import argparse
import csv
import json
import re
import subprocess
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Classification helpers
# ---------------------------------------------------------------------------

# Patterns in the issue body that identify a transient CI / infrastructure failure.
_INFRA_PATTERNS = re.compile(
    r"ConnectException|Connection refused|SocketException|SocketTimeoutException"
    r"|passed on immediate retry|passed.*retry|retry.*passed"
    r"|UncheckedIOException.*connect|accidentally.*muted|mass.muted",
    re.IGNORECASE,
)

# Paths that are production source (not test code).
# Matches */src/main/java/**, */src/main/resources/**, and top-level non-test files.
_PROD_PATH = re.compile(
    r"/src/main/(?:java|resources|antlr)/"
    r"|^(?!.*(?:Test|IT|Spec|test|qa|testFixtures|fixture))[^/]+\.java$"
)

# Paths that are clearly test/fixture code — used as a fallback check.
_TEST_PATH = re.compile(
    r"/src/test/|/src/internalClusterTest/|/src/javaRestTest/"
    r"|Test\.java$|IT\.java$|/testFixtures/|/qa/|[Ss]pec\.java$"
    r"|/test/resources/|\.csv$|restspec\.json$"
)


def is_production_file(path):
    """Return True if this file path is non-test production source."""
    if not _PROD_PATH.search(path):
        return False
    # Files in src/main/java whose package path or class name contains 'test'
    # are test helpers compiled into the main source set for cross-module access.
    m = re.search(r'/src/main/(?:java|resources|antlr)/(.*)', path, re.IGNORECASE)
    if m:
        parts = m.group(1).replace('\\', '/').split('/')
        pkg_dirs   = parts[:-1]
        class_name = parts[-1].rsplit('.', 1)[0] if parts else ''
        if any('test' in p.lower() for p in pkg_dirs) or 'test' in class_name.lower():
            return False
    return True


def is_infrastructure(body):
    """Return True if the issue body signals a transient CI failure."""
    return bool(_INFRA_PATTERNS.search(body or ""))


def classify_by_files(files):
    """
    Given a list of file paths changed by a PR, return 'production_fix'
    if any production file was touched, 'test_fix' if only test files were
    touched, or None if the file list is empty.
    """
    if not files:
        return None
    if any(is_production_file(f) for f in files):
        return "production_fix"
    return "test_fix"


# ---------------------------------------------------------------------------
# GitHub helpers
# ---------------------------------------------------------------------------

def gh_graphql(query, retries=1):
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


def fetch_pr_files(pr_numbers, batch_size=50):
    """
    Return a dict mapping PR number (str) -> list of changed file paths.
    PRs with >100 files are fetched with pagination; very large PRs are
    capped at 300 files (enough to classify production vs test).
    """
    numbers = [str(n) for n in pr_numbers if n]
    results = {}

    for start in range(0, len(numbers), batch_size):
        batch = numbers[start : start + batch_size]
        parts = [
            f'  p{n}: repository(owner:"elastic", name:"elasticsearch") {{'
            f'    pullRequest(number: {n}) {{'
            f'      files(first: 100) {{ nodes {{ path }} }}'
            f'    }}'
            f'  }}'
            for n in batch
        ]
        data = gh_graphql("{\n" + "\n".join(parts) + "\n}", retries=1).get("data", {})
        for key, val in data.items():
            if not val or not val.get("pullRequest"):
                continue
            pr_num = key[1:]  # strip leading 'p'
            results[pr_num] = [
                node["path"]
                for node in val["pullRequest"]["files"]["nodes"]
            ]

    return results


def fetch_issue_bodies(issue_numbers, batch_size=50):
    """Return a dict mapping issue number (str) -> issue body text."""
    numbers = [str(n) for n in issue_numbers if n]
    results = {}

    for start in range(0, len(numbers), batch_size):
        batch = numbers[start : start + batch_size]
        parts = [
            f'  i{n}: repository(owner:"elastic", name:"elasticsearch") {{'
            f'    issue(number: {n}) {{ number body }}'
            f'  }}'
            for n in batch
        ]
        data = gh_graphql("{\n" + "\n".join(parts) + "\n}", retries=1).get("data", {})
        for key, val in data.items():
            if not val or not val.get("issue"):
                continue
            iss = val["issue"]
            results[str(iss["number"])] = iss.get("body") or ""

    return results


def fetch_pr_descriptions(pr_numbers, batch_size=20):
    """
    Return a dict mapping PR number (str) -> {"title": str, "body": str}.

    Batch size is kept small (20) because PR bodies can be very large.
    """
    numbers = [str(n) for n in pr_numbers if n]
    results = {}

    for start in range(0, len(numbers), batch_size):
        batch = numbers[start : start + batch_size]
        parts = [
            f'  p{n}: repository(owner:"elastic", name:"elasticsearch") {{'
            f'    pullRequest(number: {n}) {{ title body }}'
            f'  }}'
            for n in batch
        ]
        data = gh_graphql("{\n" + "\n".join(parts) + "\n}", retries=1).get("data", {})
        for key, val in data.items():
            if not val or not val.get("pullRequest"):
                continue
            pr = val["pullRequest"]
            results[key[1:]] = {  # strip leading 'p'
                "title": pr.get("title") or "",
                "body":  pr.get("body")  or "",
            }
        done = min(start + batch_size, len(numbers))
        print(f"  read {done}/{len(numbers)} PR descriptions…", file=sys.stderr, end="\r")

    print(file=sys.stderr)
    return results


# Signals that strongly indicate the PR is purely about test / infrastructure work.
# We only reclassify production_fix → test_fix when multiple signals fire together,
# so a single word match in a long PR body is not enough on its own.
_PR_TEST_TITLE = re.compile(
    r"\bflak(?:y|iness)\b"
    r"|csv.?spec\b"
    r"|\bfixture\b"
    r"|\btest\s+timeout\b"
    r"|increase\s+timeout"
    r"|\bintermittent\b"
    r"|\bignore\s+error\b"
    r"|\bunmute\s+test"
    r"|\bsuppress\s+warning"
    r"|GenerativeIT",
    re.IGNORECASE,
)

_PR_PROD_TITLE = re.compile(
    r"\[bug\]"
    r"|\bregression\b"
    r"|\bincorrect\s+(?:result|behavior|mapping|query)\b"
    r"|\bwrong\s+(?:result|behavior|answer)\b"
    r"|\bnull\s+pointer\b|\bnpe\b"
    r"|\bdata\s+loss\b|\bcorrupt"
    r"|\bfix\s+(?:bug|race|deadlock|memory\s+leak|resource\s+leak)\b",
    re.IGNORECASE,
)

# Phrases in the PR body that clearly frame the PR as test-infrastructure work.
_PR_TEST_BODY = re.compile(
    r"\bflak(?:y|iness|ing)\b"
    r"|\btest\s+infra(?:structure)?\b"
    r"|\btest\s+was\s+(?:incorrectly|wrong)\b"
    r"|\bcsvspec\b|csv.spec\s+(?:test|suite|case)"
    r"|\bincorrect\s+(?:test\s+)?assertion\b"
    r"|\btest\s+timeout\b|increase\s+timeout"
    r"|\bmuted.tests\.yml\b.*\bunmut"
    r"|\bintermittent\s+(?:test\s+)?failure\b"
    r"|\bfixture\s+(?:load|setup|startup)\b",
    re.IGNORECASE,
)

# Phrases in the PR body that clearly frame the PR as fixing a production bug.
_PR_PROD_BODY = re.compile(
    r"\broot\s+cause\b.{0,200}(?:bug|incorrect|wrong|race|leak)"
    r"|\bregression\s+(?:introduced|caused|from)\b"
    r"|\bproduction\s+(?:bug|issue|behavior)\b"
    r"|\bincorrect\s+(?:result|behavior|output|response)\b"
    r"|\bwrong\s+(?:result|behavior|answer|output)\b"
    r"|\bnull\s+pointer\b|\bNPE\b"
    r"|\bdata\s+(?:loss|corrupt|inconsisten)\b"
    r"|\brace\s+condition\b|\bdeadlock\b"
    r"|\bmemory\s+leak\b|\bresource\s+leak\b",
    re.IGNORECASE,
)


def classify_by_pr_description(title, body):
    """
    Return "production_fix", "test_fix", or None (ambiguous) based on PR text.

    We only return a non-None value when the evidence is clear enough to override
    the path-based classification; ambiguous PRs return None (keep existing).
    """
    test_title  = bool(_PR_TEST_TITLE.search(title))
    prod_title  = bool(_PR_PROD_TITLE.search(title))
    test_body   = len(_PR_TEST_BODY.findall(body))
    prod_body   = len(_PR_PROD_BODY.findall(body))

    # Strong test signal: test title + at least one test body signal
    if test_title and not prod_title and test_body >= 1 and prod_body == 0:
        return "test_fix"

    # Multiple test body signals with no production signal
    if test_body >= 2 and prod_body == 0 and not prod_title:
        return "test_fix"

    # Clear production signal
    if prod_title or prod_body >= 2:
        if prod_body > test_body:
            return "production_fix"

    return None  # ambiguous — keep path-based result


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

OUTPUT_FIELDS_EXTRA = ["category"]  # appended after all existing columns


def main():
    parser = argparse.ArgumentParser(
        description="Augment fetch-muted-tests output with a fix category column."
    )
    parser.add_argument(
        "--input", required=True, metavar="FILE",
        help="CSV produced by fetch-muted-tests.py",
    )
    parser.add_argument(
        "--output", metavar="FILE",
        help="Output path (default: <input-stem>_categorized.csv / .json).",
    )
    parser.add_argument(
        "--format", choices=["csv", "json"], default="csv",
        help="Output format (default: csv).",
    )
    parser.add_argument(
        "--read-pr-bodies", action="store_true",
        help=(
            "Fetch the title and body of each closing PR and use them to "
            "verify production_fix classifications. Rows where the PR clearly "
            "describes test or infrastructure work are reclassified to test_fix. "
            "This makes one additional round of GitHub API calls (batched) and "
            "adds a 'pr_assessment' column to the output."
        ),
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        sys.exit(f"error: input file not found: {input_path}")

    if args.output:
        output_path = Path(args.output)
    else:
        stem = input_path.stem
        suffix = ".json" if args.format == "json" else ".csv"
        output_path = input_path.with_name(stem + "_categorized" + suffix)

    # Verify gh authentication
    auth = subprocess.run(["gh", "auth", "status"], capture_output=True, text=True)
    if auth.returncode != 0:
        sys.exit("error: gh CLI is not authenticated — run `gh auth login` first")

    # Read input
    with open(input_path, newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        input_fields = reader.fieldnames or []

    if not rows:
        sys.exit("error: input file is empty")

    print(f"Read {len(rows)} rows from {input_path}", file=sys.stderr)

    # -----------------------------------------------------------------------
    # Collect what we need to fetch
    # -----------------------------------------------------------------------

    # NOT_PLANNED rows need their issue body to distinguish infrastructure vs stale
    not_planned_issues = {
        r["issue_number"]
        for r in rows
        if r.get("state_reason") == "NOT_PLANNED" and r.get("issue_number")
    }

    # COMPLETED rows: try closing_pr first, fall back to unmute_pr
    prs_needed = set()
    for r in rows:
        if r.get("state_reason") == "COMPLETED":
            if r.get("closing_pr"):
                prs_needed.add(r["closing_pr"])
            elif r.get("unmute_pr"):
                prs_needed.add(r["unmute_pr"])

    print(
        f"Fetching bodies for {len(not_planned_issues)} NOT_PLANNED issues…",
        file=sys.stderr,
    )
    issue_bodies = fetch_issue_bodies(not_planned_issues)

    print(f"Fetching file lists for {len(prs_needed)} PRs…", file=sys.stderr)
    pr_files = fetch_pr_files(prs_needed)

    # -----------------------------------------------------------------------
    # Classify each row (path-based)
    # -----------------------------------------------------------------------

    for row in rows:
        state_reason = row.get("state_reason", "")
        closing_pr   = row.get("closing_pr", "")
        unmute_pr    = row.get("unmute_pr", "")
        issue_num    = row.get("issue_number", "")
        row["pr_assessment"] = ""

        if state_reason == "NOT_PLANNED":
            body = issue_bodies.get(issue_num, "")
            row["category"] = "infrastructure" if is_infrastructure(body) else "stale"

        elif state_reason == "COMPLETED":
            # Try the closing PR first, then the unmute PR
            cat = None
            for pr in [closing_pr, unmute_pr]:
                if pr and pr in pr_files:
                    cat = classify_by_files(pr_files[pr])
                    if cat:
                        break
            row["category"] = cat or "unmute_only"

        else:
            row["category"] = "unmute_only"

    # -----------------------------------------------------------------------
    # Optional: verify production_fix rows by reading PR descriptions
    # -----------------------------------------------------------------------

    if args.read_pr_bodies:
        prod_prs = set()
        for row in rows:
            if row["category"] == "production_fix":
                pr = row.get("closing_pr") or row.get("unmute_pr")
                if pr:
                    prod_prs.add(pr)

        print(
            f"Reading PR descriptions for {len(prod_prs)} production_fix PRs…",
            file=sys.stderr,
        )
        pr_desc = fetch_pr_descriptions(prod_prs)

        reclassified = 0
        for row in rows:
            if row["category"] != "production_fix":
                continue
            pr = row.get("closing_pr") or row.get("unmute_pr")
            if not pr or pr not in pr_desc:
                continue
            desc = pr_desc[pr]
            verdict = classify_by_pr_description(desc["title"], desc["body"])
            if verdict is not None:
                row["pr_assessment"] = verdict
                if verdict == "test_fix":
                    row["category"] = "test_fix"
                    reclassified += 1
            else:
                row["pr_assessment"] = "production_fix"  # confirmed by body

        print(
            f"Reclassified {reclassified} production_fix rows → test_fix "
            f"based on PR body content.",
            file=sys.stderr,
        )

    # -----------------------------------------------------------------------
    # Write output
    # -----------------------------------------------------------------------

    extra = OUTPUT_FIELDS_EXTRA[:]
    if args.read_pr_bodies and "pr_assessment" not in extra:
        extra.append("pr_assessment")
    out_fields = input_fields + [f for f in extra if f not in input_fields]

    if args.format == "json":
        with open(output_path, "w") as f:
            json.dump([{k: r.get(k, "") for k in out_fields} for r in rows], f, indent=2)
    else:
        with open(output_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=out_fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)

    # Summary
    from collections import Counter
    counts = Counter(r["category"] for r in rows)
    print(f"Wrote {len(rows)} rows → {output_path}", file=sys.stderr)
    print("Category breakdown:", file=sys.stderr)
    for cat, n in sorted(counts.items(), key=lambda x: -x[1]):
        print(f"  {cat:<20} {n:>5}  ({100*n/len(rows):.1f}%)", file=sys.stderr)


if __name__ == "__main__":
    main()
