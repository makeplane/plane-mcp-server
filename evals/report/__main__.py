"""Command-line entry for evaluation reports.

Usage:
  python -m evals.report evals/output/A.jsonl
  python -m evals.report A.jsonl B.jsonl              # A/B delta (sign test + Wilson)
  python -m evals.report --table f1.jsonl f2.jsonl …  # per-task × per-surface
  python -m evals.report --table --markdown f1.jsonl f2.jsonl
"""

from .command import main

if __name__ == "__main__":
    raise SystemExit(main())
