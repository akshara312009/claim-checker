# Club Expense Claim Checker

This repository implements the **Club Expense Claim Checker** capstone project as described in the faculty specification.

## Project Overview

- **Purpose**: Read expense claim CSV files, extract structured information using simple rule‑based parsing (simulating an LLM), validate the claims against club budgets, and produce four output files:
  - `payable.csv` – claims approved for payment.
  - `review.csv` – claims that need further review (missing info, missing bill, over budget, etc.).
  - `results.csv` – every claim with its computed status and JSON payload.
  - `summary.txt` – a short report with counts and per‑club spending.

## Repository Structure

```
ClubExpenseClaimChecker/
├─ claims.csv               # Input claims (example data)
├─ budgets.csv              # Category limits per club
├─ main.py                  # Core program
├─ eval_harness.py          # Simple test harness
├─ system_prompt.txt        # System prompt used for LLM (simulated)
├─ redteam_inputs.txt       # Three adversarial claim examples
├─ judgment_framework.md    # Comparison of correct vs. incorrect classifications
├─ cost_latency_note.md     # Estimated API cost & latency
├─ ai_decision_journal.md   # Log of LLM decisions (simulated)
├─ reflection.md            # 400‑word reflection on the project
├─ .gitignore               # Files to ignore in version control
└─ README.md                # This file
```

## How to Run

1. Make sure you have Python 3.9+ installed.
2. Install any dependencies (none required beyond the standard library):
   ```
   pip install -r requirements.txt
   ```
3. Run the program:
   ```
   python main.py
   ```
   The script will read `claims.csv` and `budgets.csv` from the repository root and generate the four output files.

4. Run the evaluation harness (optional):
   ```
   python eval_harness.py
   ```
   This will perform basic sanity checks on the generated outputs.

## License

This project is provided for educational purposes and is licensed under the MIT License.
