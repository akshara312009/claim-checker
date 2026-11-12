"""Club Expense Claim Checker  (standard library only, no pip installs).

What it does
  1. Reads claims.csv and budgets.csv.
  2. Asks an LLM to pull out amount, category and bill for each claim note.
  3. Checks those facts against the note with Python.
  4. Applies the rules in order and keeps running totals per club.
  5. Writes payable.csv, review.csv, results.csv and summary.txt.

The tool only SORTS claims. The faculty treasurer signs every payment
after seeing the bill.

Usage
  python claim_checker.py claims.csv budgets.csv
  python claim_checker.py claims.csv budgets.csv --outdir output
  python claim_checker.py claims.csv budgets.csv --offline   (no API key needed)

The API key is read from the ANTHROPIC_API_KEY environment variable or from
a .env file. It is never written in this code.
"""

import argparse
import csv
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

# ----------------------------------------------------------------------
# Settings
# ----------------------------------------------------------------------
CATEGORIES = ["printing", "food", "travel", "prizes", "equipment", "costumes", "other"]
STATUSES = ["ok_to_pay", "needs_bill", "needs_approval", "over_budget", "needs_more_info"]
APPROVAL_LIMIT = 5000          # above this amount, a person must look at the claim
BILL_WORDS = ["bill", "receipt", "invoice", "ticket"]

# Phrases that mean the bill is missing or will come later.
BILL_MISSING_PATTERNS = [
    r"\bno (bill|receipt|invoice|ticket)",
    r"\bwithout\b",
    r"\bmissing\b",
    r"\bnot (attached|available|provided|received|yet|given)",
    r"\blater\b",
    r"\btomorrow\b",
    r"\bwill be (sent|given|submitted|attached|provided)",
    r"\bwill (send|submit|attach|share)",
    r"\bto follow\b",
    r"\byet to\b",
    r"\b(lost|forgot|forgotten|pending)\b",
]

API_URL = "https://api.anthropic.com/v1/messages"
DEFAULT_MODEL = "claude-haiku-5-5"   # change with --model or CLAIM_MODEL

# Used when the LLM reply is unusable. It always lands in needs_more_info.
FALLBACK = {"amount": None, "category": "other", "bill": False,
            "summary": "LLM reply unusable"}

# Counters for the cost and latency note.
STATS = {"calls": 0, "seconds": 0.0}


# ----------------------------------------------------------------------
# Small helpers
# ----------------------------------------------------------------------
def fmt(number):
    """Format a number with commas: 1250 -> 1,250 and 99.5 -> 99.50."""
    number = float(number)
    if number.is_integer():
        return f"{number:,.0f}"
    return f"{number:,.2f}"


def load_dotenv(path=".env"):
    """Read KEY=value lines from a .env file into the environment."""
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


# ----------------------------------------------------------------------
# Reading the input files
# ----------------------------------------------------------------------
def load_claims(path):
    """Return a list of claim dictionaries from claims.csv."""
    try:
        with open(path, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            needed = {"id", "club", "date", "note"}
            if not reader.fieldnames or not needed.issubset(reader.fieldnames):
                sys.exit(f"Error: {path} must have columns id,club,date,note")
            claims = []
            for row in reader:
                claims.append({key: (row[key] or "").strip() for key in needed})
            return claims
    except FileNotFoundError:
        sys.exit(f"Error: cannot find {path}")


def load_budgets(path):
    """Return {category: limit_per_club} from budgets.csv."""
    try:
        with open(path, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            needed = {"category", "limit_per_club"}
            if not reader.fieldnames or not needed.issubset(reader.fieldnames):
                sys.exit(f"Error: {path} must have columns category,limit_per_club")
            budgets = {}
            for row in reader:
                try:
                    budgets[row["category"].strip().lower()] = float(row["limit_per_club"])
                except (ValueError, TypeError):
                    print(f"Warning: skipped bad budget row {row}")
            return budgets
    except FileNotFoundError:
        sys.exit(f"Error: cannot find {path}")


# ----------------------------------------------------------------------
# The LLM step
# ----------------------------------------------------------------------
def parse_json_reply(text):
    """Pull the first JSON object out of the LLM's text reply."""
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError("no JSON object in reply")
    return json.loads(match.group(0))


def call_llm(club, date, note, system_prompt, model):
    """Send one claim to the LLM API and return the parsed JSON (a dict)."""
    api_key = os.environ["ANTHROPIC_API_KEY"]
    body = json.dumps({
        "model": model,
        "max_tokens": 300,
        "system": system_prompt,
        "messages": [{"role": "user",
                      "content": f"Club: {club}\nDate: {date}\nNote: {note}"}],
    }).encode("utf-8")
    request = urllib.request.Request(API_URL, data=body, method="POST", headers={
        "content-type": "application/json",
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
    })
    last_error = None
    for attempt in range(2):                      # one retry for network trouble
        try:
            start = time.time()
            with urllib.request.urlopen(request, timeout=30) as response:
                reply = json.loads(response.read().decode("utf-8"))
            STATS["calls"] += 1
            STATS["seconds"] += time.time() - start
            text = "".join(block.get("text", "") for block in reply.get("content", []))
            return parse_json_reply(text)
        except (urllib.error.URLError, TimeoutError) as err:
            last_error = err
            time.sleep(2)
    raise RuntimeError(f"API call failed: {last_error}")


def offline_extract(note):
    """Simple keyword version of the LLM, for testing without an API key."""
    text = note.lower()
    numbers = numbers_in(note)
    amount = numbers[0] if len(numbers) == 1 else None      # many numbers: do not guess
    if re.search(r"\b(each|per|x)\b", text):
        amount = None                                        # per-item price, not a total
    keywords = [
        ("printing", ["print", "banner", "poster", "flex", "certificate", "sticker", "pamphlet"]),
        ("food", ["snack", "lunch", "dinner", "pizza", "refreshment", "food", "water", "breakfast", "meal"]),
        ("travel", ["auto", "bus", "fare", "taxi", "uber", "train", "travel", "fuel", "petrol"]),
        ("prizes", ["troph", "prize", "medal", "gift", "award"]),
        ("costumes", ["costume", "dress", "makeup"]),
        ("equipment", ["kit", "sensor", "arduino", "driver", "batter", "cable", "string",
                       "board", "equipment", "tool", "speaker"]),
    ]
    category = "other"
    for name, words in keywords:
        if any(word in text for word in words):
            category = name
            break
    bill = any(word in text for word in BILL_WORDS)
    return {"amount": amount, "category": category, "bill": bill,
            "summary": " ".join(note.split()[:6])}


def is_valid(data):
    """True only if the LLM reply has the right keys and the right kinds of values."""
    if not isinstance(data, dict):
        return False
    for key in ("amount", "category", "bill", "summary"):
        if key not in data:
            return False
    amount = data["amount"]
    if amount is not None:
        if isinstance(amount, bool) or not isinstance(amount, (int, float)):
            return False
    if data["category"] not in CATEGORIES:
        return False
    if not isinstance(data["bill"], bool):
        return False
    if not isinstance(data["summary"], str):
        return False
    return True


def classify(claim, system_prompt, model, offline):
    """Get facts for one claim. Returns (data, used_fallback). Never crashes."""
    try:
        if not claim["note"]:
            raise ValueError("empty note")
        if offline:
            data = offline_extract(claim["note"])
        else:
            data = call_llm(claim["club"], claim["date"], claim["note"], system_prompt, model)
        if not is_valid(data):
            raise ValueError("reply failed validation")
        data["summary"] = " ".join(data["summary"].split()[:10])   # keep to 10 words
        return data, False
    except Exception as err:                                       # one bad reply must not stop the run
        print(f"  Warning: {claim['id']}: {err}. Using fallback.")
        return dict(FALLBACK), True


# ----------------------------------------------------------------------
# Python fact checks
# ----------------------------------------------------------------------
def numbers_in(note):
    """List every number written in the note. '1,250' counts as 1250."""
    found = []
    for text in re.findall(r"\d[\d,]*(?:\.\d+)?", note):
        try:
            found.append(float(text.replace(",", "")))
        except ValueError:
            pass
    return found


def check_facts(note, data):
    """Check the LLM's amount and bill against the note.
    Returns (amount, bill, warnings)."""
    warnings = []

    # Amount must be a number that is actually written in the note.
    amount = data["amount"]
    if amount is not None:
        written = any(abs(amount - n) < 0.005 for n in numbers_in(note))
        if not written:
            warnings.append(f"LLM amount {fmt(amount)} is not written in the note, ignored")
            amount = None
        elif amount <= 0:
            warnings.append("amount is not positive, ignored")
            amount = None

    # Bill is true only if the note mentions a bill and does not say it is missing.
    bill = data["bill"]
    text = note.lower()
    if bill:
        if not any(word in text for word in BILL_WORDS):
            warnings.append("LLM said bill attached but note does not mention one")
            bill = False
        elif any(re.search(pattern, text) for pattern in BILL_MISSING_PATTERNS):
            warnings.append("note says the bill is missing or coming later")
            bill = False

    return amount, bill, warnings


# ----------------------------------------------------------------------
# The payment rules
# ----------------------------------------------------------------------
def decide(amount, bill, club, category, date, totals, day_totals, budgets):
    """Apply the rules in order. Returns (status, reason).
    Only an ok_to_pay claim is added to the running totals."""
    if amount is None:
        return "needs_more_info", "No amount written in the note"

    if not bill:
        return "needs_bill", "No bill attached, or bill missing or coming later"

    if amount > APPROVAL_LIMIT:
        return "needs_approval", f"Amount {fmt(amount)} is above {APPROVAL_LIMIT:,}"

    day_total = day_totals.get((club, category, date), 0)
    if day_total > APPROVAL_LIMIT:
        return "needs_approval", (f"Same club, category and date add up to {fmt(day_total)}, "
                                  f"above {APPROVAL_LIMIT:,} (possible split claim)")

    limit = budgets.get(category)                  # no limit listed: no budget check
    if limit is not None:
        spent = totals.get((club, category), 0)
        if spent + amount > limit:
            return "over_budget", (f"{category} spending would be {fmt(spent + amount)}, "
                                   f"limit is {fmt(limit)}")

    totals[(club, category)] = round(totals.get((club, category), 0) + amount, 2)
    return "ok_to_pay", "All checks passed"


# ----------------------------------------------------------------------
# Writing the output files
# ----------------------------------------------------------------------
def write_csv(path, header, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)                      # quotes any field that has a comma
        writer.writerow(header)
        writer.writerows(rows)


def write_reports(records, totals, budgets, outdir):
    """Write payable.csv, review.csv, results.csv and summary.txt."""
    os.makedirs(outdir, exist_ok=True)

    payable = [[r["id"], r["club"], r["date"], r["category"], fmt(r["amount"]),
                r["summary"], r["note"], ""]
               for r in records if r["status"] == "ok_to_pay"]
    write_csv(os.path.join(outdir, "payable.csv"),
              ["id", "club", "date", "category", "amount", "summary", "note",
               "treasurer_signature"], payable)

    review = [[r["id"], r["club"], r["date"], r["status"], r["reason"], r["note"]]
              for r in records if r["status"] != "ok_to_pay"]
    write_csv(os.path.join(outdir, "review.csv"),
              ["id", "club", "date", "status", "reason", "note"], review)

    results = [[r["id"], r["club"], r["date"], r["category"],
                "" if r["amount"] is None else fmt(r["amount"]),
                "yes" if r["bill"] else "no", r["status"], r["reason"],
                "; ".join(r["warnings"])]
               for r in records]
    write_csv(os.path.join(outdir, "results.csv"),
              ["id", "club", "date", "category", "amount", "bill", "status",
               "reason", "fact_check_notes"], results)

    # summary.txt
    counts = {status: 0 for status in STATUSES}
    for r in records:
        counts[r["status"]] += 1
    lines = ["CLUB EXPENSE CLAIM CHECKER - SUMMARY",
             f"Claims processed: {len(records)}", "", "Status counts"]
    for status in STATUSES:
        lines.append(f"  {status:<16}: {counts[status]}")
    lines += ["", "Spending against limits (ok_to_pay claims only)"]
    clubs = sorted({r["club"] for r in records})
    for club in clubs:
        lines.append(club)
        categories = list(budgets.keys())
        for club_name, category in totals:          # add categories with no limit but some spending
            if club_name == club and category not in categories:
                categories.append(category)
        for category in sorted(categories):
            spent = totals.get((club, category), 0)
            limit = budgets.get(category)
            if limit is None:
                lines.append(f"  {category:<10} spent {fmt(spent)} (no limit set)")
            else:
                lines.append(f"  {category:<10} spent {fmt(spent)} of {fmt(limit)} limit "
                             f"({fmt(limit - spent)} left)")
    lines += ["", "This tool only sorts claims. The faculty treasurer signs every",
              "payment after seeing the bill."]
    with open(os.path.join(outdir, "summary.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    return counts


# ----------------------------------------------------------------------
# Main program
# ----------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Club Expense Claim Checker")
    parser.add_argument("claims", help="path to claims.csv")
    parser.add_argument("budgets", help="path to budgets.csv")
    parser.add_argument("--outdir", default=".", help="folder for the four output files")
    parser.add_argument("--offline", action="store_true",
                        help="use simple keyword rules instead of the LLM (for testing)")
    parser.add_argument("--prompt", default=os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "system_prompt.txt"))
    parser.add_argument("--model", default=os.environ.get("CLAIM_MODEL", DEFAULT_MODEL))
    parser.add_argument("--delay", type=float, default=0,
                        help="seconds to wait between API calls (for free-tier limits)")
    args = parser.parse_args()

    load_dotenv()
    claims = load_claims(args.claims)
    budgets = load_budgets(args.budgets)

    system_prompt = ""
    if not args.offline:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            sys.exit("Error: set ANTHROPIC_API_KEY (environment or .env file), "
                     "or run with --offline.")
        try:
            with open(args.prompt, encoding="utf-8") as f:
                system_prompt = f.read()
        except FileNotFoundError:
            sys.exit(f"Error: cannot find system prompt file {args.prompt}")

    # Keep claims in date order (sort is stable, so same-day claims keep file order).
    claims.sort(key=lambda c: c["date"])

    # Pass 1: get and check the facts for every claim.
    print(f"Reading {len(claims)} claims...")
    records = []
    for claim in claims:
        data, used_fallback = classify(claim, system_prompt, args.model, args.offline)
        amount, bill, warnings = check_facts(claim["note"], data)
        if used_fallback:
            warnings.append("LLM reply unusable, check the note by hand")
        records.append({**claim, "category": data["category"], "summary": data["summary"],
                        "amount": amount, "bill": bill, "warnings": warnings})
        if not args.offline and args.delay:
            time.sleep(args.delay)

    # Totals per (club, category, date), used to spot split claims.
    day_totals = {}
    for r in records:
        if r["amount"] is not None:
            key = (r["club"], r["category"], r["date"])
            day_totals[key] = day_totals.get(key, 0) + r["amount"]

    # Pass 2: apply the rules in order, keeping running totals per (club, category).
    totals = {}
    for r in records:
        r["status"], r["reason"] = decide(r["amount"], r["bill"], r["club"], r["category"],
                                          r["date"], totals, day_totals, budgets)

    try:
        counts = write_reports(records, totals, budgets, args.outdir)
    except OSError as err:
        sys.exit(f"Error writing output files: {err}")

    print("\nStatus counts")
    for status in STATUSES:
        print(f"  {status:<16}: {counts[status]}")
    print(f"\nFiles written to '{args.outdir}': payable.csv, review.csv, results.csv, summary.txt")
    if STATS["calls"]:
        average = STATS["seconds"] / STATS["calls"]
        print(f"API calls: {STATS['calls']}, average {average:.1f} s per call")


if __name__ == "__main__":
    main()
