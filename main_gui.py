import csv
import re
import json
import os
from pathlib import Path
from collections import defaultdict
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from tkinter.scrolledtext import ScrolledText


def load_claims(path):
    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def load_budgets(path):
    budgets = {}
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            category = row["category"].strip().lower()
            budgets[category] = int(row["limit_per_club"])
    return budgets


def parse_amount(note):
    # Finds the first amount, with or without Rs., ₹ or $.
    match = re.search(
        r"(?:Rs\.?|₹|\$)?\s*([0-9][0-9,]*)",
        note,
        re.IGNORECASE
    )
    if not match:
        return None
    try:
        return int(match.group(1).replace(",", ""))
    except ValueError:
        return None


def detect_category(note, categories):
    note_lower = note.lower()
    for category in categories:
        if category in note_lower:
            return category
    return None


def has_bill(note):
    note_lower = note.lower()
    return any(
        word in note_lower
        for word in ("bill", "receipt", "invoice", "ticket")
    )


def process_claims(claims_path, budgets_path):
    claims = load_claims(claims_path)
    budgets = load_budgets(budgets_path)
    categories = list(budgets.keys())

    totals = defaultdict(int)
    summary_counts = defaultdict(int)
    payable_count = 0
    review_count = 0

    output_dir = Path(claims_path).resolve().parent
    payable_path = output_dir / "payable.csv"
    review_path = output_dir / "review.csv"
    results_path = output_dir / "results.csv"
    summary_path = output_dir / "summary.txt"

    with open(payable_path, "w", newline="", encoding="utf-8") as payable_f, \
         open(review_path, "w", newline="", encoding="utf-8") as review_f, \
         open(results_path, "w", newline="", encoding="utf-8") as results_f:

        payable_writer = csv.writer(payable_f)
        review_writer = csv.writer(review_f)
        results_writer = csv.writer(results_f)

        header = ["id", "club", "date", "note"]
        payable_writer.writerow(header)
        review_writer.writerow(header + ["reason"])
        results_writer.writerow(["id", "club", "date", "note", "json"])

        for claim in claims:
            note = claim.get("note", "")
            amount = parse_amount(note)
            category = detect_category(note, categories)
            bill = has_bill(note)

            payload = {
                "amount": amount,
                "category": category,
                "bill": bill,
                "summary": note[:30] + ("..." if len(note) > 30 else "")
            }

            if amount is None:
                status, reason = "needs_more_info", "no amount"
            elif category is None:
                status, reason = "needs_more_info", "no category"
            elif not bill:
                status, reason = "needs_bill", "bill missing"
            else:
                key = (claim["club"], category)
                if totals[key] + amount > budgets.get(category, 0):
                    status, reason = "over_budget", "budget exceeded"
                else:
                    status, reason = "ok_to_pay", ""
                    totals[key] += amount
                    summary_counts[claim["club"]] += amount

            if status == "ok_to_pay":
                payable_count += 1
                payable_writer.writerow([
                    claim["id"], claim["club"], claim["date"], note
                ])
            else:
                review_count += 1
                review_writer.writerow([
                    claim["id"], claim["club"], claim["date"], note, reason
                ])

            results_writer.writerow([
                claim["id"], claim["club"], claim["date"], note,
                json.dumps(payload)
            ])

    with open(summary_path, "w", encoding="utf-8") as summary_f:
        summary_f.write(f"Total claims processed: {len(claims)}\n")
        summary_f.write(f"Payable claims: {payable_count}\n")
        summary_f.write(f"Claims needing review: {review_count}\n")
        summary_f.write("Spending per club:\n")
        for club, amount in summary_counts.items():
            summary_f.write(f"  {club}: {amount}\n")

    return {
        "total": len(claims),
        "payable": payable_count,
        "review": review_count,
        "output_dir": output_dir,
        "summary": summary_path
    }


class ExpenseCheckerGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Club Expense Claim Checker")
        self.root.geometry("820x650")
        self.root.minsize(700, 560)

        self.claims_var = tk.StringVar(value="claims.csv")
        self.budgets_var = tk.StringVar(value="budgets.csv")
        self.last_output_dir = None

        self.build_interface()

    def build_interface(self):
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        style.configure("Title.TLabel", font=("Segoe UI", 19, "bold"))
        style.configure("Subtitle.TLabel", font=("Segoe UI", 10))
        style.configure("Run.TButton", font=("Segoe UI", 11, "bold"), padding=9)
        style.configure("Metric.TLabel", font=("Segoe UI", 17, "bold"))

        main = ttk.Frame(self.root, padding=22)
        main.pack(fill="both", expand=True)

        ttk.Label(
            main, text="Club Expense Claim Checker", style="Title.TLabel"
        ).pack(anchor="w")

        ttk.Label(
            main,
            text="Select your CSV files, then run the claim validation.",
            style="Subtitle.TLabel"
        ).pack(anchor="w", pady=(3, 18))

        files_frame = ttk.LabelFrame(main, text="Input files", padding=14)
        files_frame.pack(fill="x", pady=(0, 14))

        self.add_file_row(files_frame, "Claims CSV:", self.claims_var, 0, self.browse_claims)
        self.add_file_row(files_frame, "Budgets CSV:", self.budgets_var, 1, self.browse_budgets)

        self.run_button = ttk.Button(
            main, text="Run Claim Checker", style="Run.TButton",
            command=self.run_checker
        )
        self.run_button.pack(fill="x", pady=(0, 14))

        metrics = ttk.Frame(main)
        metrics.pack(fill="x", pady=(0, 14))

        self.total_label = self.metric_card(metrics, "Total claims", "—", 0)
        self.payable_label = self.metric_card(metrics, "Payable claims", "—", 1)
        self.review_label = self.metric_card(metrics, "Needs review", "—", 2)

        output_frame = ttk.LabelFrame(main, text="Run details", padding=10)
        output_frame.pack(fill="both", expand=True)

        self.output = ScrolledText(
            output_frame, height=12, wrap="word",
            font=("Consolas", 10), state="disabled"
        )
        self.output.pack(fill="both", expand=True)

        bottom = ttk.Frame(main)
        bottom.pack(fill="x", pady=(12, 0))

        ttk.Button(
            bottom, text="Open Output Folder",
            command=self.open_output_folder
        ).pack(side="left")

        ttk.Label(
            bottom, text="Outputs: payable.csv, review.csv, results.csv, summary.txt"
        ).pack(side="right")

        self.log("Ready. Choose the claims.csv and budgets.csv files.")

    def add_file_row(self, parent, label, variable, row, command):
        ttk.Label(parent, text=label, width=14).grid(
            row=row, column=0, sticky="w", pady=6
        )
        entry = ttk.Entry(parent, textvariable=variable)
        entry.grid(row=row, column=1, sticky="ew", padx=8, pady=6)
        ttk.Button(parent, text="Browse...", command=command).grid(
            row=row, column=2, pady=6
        )
        parent.columnconfigure(1, weight=1)

    def metric_card(self, parent, title, value, column):
        card = ttk.LabelFrame(parent, text=title, padding=12)
        card.grid(row=0, column=column, sticky="nsew", padx=5)
        label = ttk.Label(card, text=value, style="Metric.TLabel")
        label.pack()
        parent.columnconfigure(column, weight=1)
        return label

    def browse_claims(self):
        path = filedialog.askopenfilename(
            title="Select claims CSV",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")]
        )
        if path:
            self.claims_var.set(path)

    def browse_budgets(self):
        path = filedialog.askopenfilename(
            title="Select budgets CSV",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")]
        )
        if path:
            self.budgets_var.set(path)

    def log(self, message):
        self.output.configure(state="normal")
        self.output.insert("end", message + "\n")
        self.output.see("end")
        self.output.configure(state="disabled")

    def run_checker(self):
        claims_path = Path(self.claims_var.get())
        budgets_path = Path(self.budgets_var.get())

        if not claims_path.is_file():
            messagebox.showerror(
                "Claims file not found",
                "Please select a valid claims.csv file."
            )
            return

        if not budgets_path.is_file():
            messagebox.showerror(
                "Budgets file not found",
                "Please select a valid budgets.csv file."
            )
            return

        try:
            result = process_claims(str(claims_path), str(budgets_path))
            self.last_output_dir = result["output_dir"]

            self.total_label.config(text=str(result["total"]))
            self.payable_label.config(text=str(result["payable"]))
            self.review_label.config(text=str(result["review"]))

            self.log("")
            self.log("Claim checking completed successfully.")
            self.log(f"Total claims processed: {result['total']}")
            self.log(f"Payable claims: {result['payable']}")
            self.log(f"Claims needing review: {result['review']}")
            self.log(f"Output folder: {result['output_dir']}")
            self.log("Generated: payable.csv, review.csv, results.csv, summary.txt")

            messagebox.showinfo(
                "Completed",
                "Claim checking completed successfully!\n\n"
                f"Payable claims: {result['payable']}\n"
                f"Claims needing review: {result['review']}\n\n"
                f"Files saved in:\n{result['output_dir']}"
            )

        except (KeyError, ValueError, OSError, csv.Error) as error:
            messagebox.showerror("Could not process files", str(error))
            self.log(f"ERROR: {error}")

    def open_output_folder(self):
        folder = self.last_output_dir or Path(self.claims_var.get()).resolve().parent
        if not folder.exists():
            messagebox.showwarning("Folder not found", "The output folder does not exist yet.")
            return

        try:
            os.startfile(str(folder))  # Windows
        except AttributeError:
            messagebox.showinfo("Output folder", str(folder))
        except OSError as error:
            messagebox.showerror("Cannot open folder", str(error))


if __name__ == "__main__":
    root = tk.Tk()
    app = ExpenseCheckerGUI(root)
    root.mainloop()
