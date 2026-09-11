#!/usr/bin/env python3
"""SGW-861 — Merge human labels into benchmark/labels.json.

Usage:
    python3 benchmark/merge_human_labels.py --input verdicts.json [--reviewer "Steven (human)"] [--commit]

verdicts.json shape (fixture key -> verdict):
{
  "sanchezassociates": {"label": "good_fit", "reason": "Real CPA/attorney firm, phone verified, in-geography"},
  "lawlink": {"label": "bad_fit", "reason": "Directory listing — lawlink.com/listings"}
}

Behavior:
- Only touches keys present in the fixture AND in the verdicts file.
- Preserves the existing agent labels under labels["prior_labels"] (per-record, keyed by fixture key).
- Updates labels["labels"][key] = {"label": ..., "reason": ..., "labeled_by": "human", "labeled_at": "YYYY-MM-DD"}.
- Updates _meta with reviewer + review date (keeps prior meta in _meta.prior_meta).
- Writes a backup labels.json.bak-YYYYMMDD-HHMMSS before modifying.
- With --commit, prints the resulting evaluate.py run; WITHOUT --commit it's a dry run (no file write).

Hard guardrail: refuses to run if the input contains keys not in the fixture.
"""
import argparse
import datetime
import importlib.util
import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURE = os.path.join(HERE, "prospects.json")
LABELS = os.path.join(HERE, "labels.json")
VALID = {"good_fit", "possible_fit", "bad_fit", "unknown"}


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, help="verdicts.json path")
    ap.add_argument("--reviewer", default="Steven (human)", help="reviewer name recorded in _meta")
    ap.add_argument("--commit", action="store_true", help="actually write labels.json; default is dry run")
    args = ap.parse_args()

    fixture = load_json(FIXTURE)["prospects"]
    labels_doc = load_json(LABELS)
    labels = labels_doc["labels"]

    verdicts = load_json(args.input)
    bad = set(verdicts) - set(fixture)
    if bad:
        print(f"REFUSING: {len(bad)} verdict keys not in fixture: {sorted(bad)[:10]}")
        sys.exit(1)
    unknown_labels = {k: v.get("label") for k, v in verdicts.items() if v.get("label") not in VALID}
    if unknown_labels:
        print(f"REFUSING: invalid labels: {unknown_labels}")
        sys.exit(1)

    today = datetime.date.today().isoformat()

    if args.commit:
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        shutil.copy2(LABELS, f"{LABELS}.bak-{stamp}")
        print(f"Backup: {LABELS}.bak-{stamp}")

        # Preserve prior agent labels per-record (only for keys being changed)
        prior = {}
        for key, v in verdicts.items():
            if key in labels:
                prior[key] = dict(labels[key])
        if prior:
            labels_doc.setdefault("prior_labels", {})
            for key, p in prior.items():
                # keep the OLDEST recorded prior if one already exists (don't stack)
                labels_doc["prior_labels"].setdefault(key, p)

        # Apply human labels
        for key, v in verdicts.items():
            labels[key] = {
                "label": v["label"],
                "reason": v.get("reason", ""),
                "labeled_by": "human",
                "labeled_at": today,
            }

        # _meta update
        meta = labels_doc.setdefault("_meta", {})
        meta.setdefault("prior_meta", {})
        if "labeler" in meta:
            meta["prior_meta"].setdefault("labeler", meta["labeler"])
        meta["labeler"] = args.reviewer
        meta["labeled_at"] = today
        meta["review_note"] = "Human-verified ground truth. Agent labels preserved under labels.prior_labels / _meta.prior_meta."

        with open(LABELS, "w", encoding="utf-8") as f:
            json.dump(labels_doc, f, indent=2, ensure_ascii=False)
            f.write("\n")
        print(f"Committed {len(verdicts)} human labels -> {LABELS}")
    else:
        print(f"DRY RUN: would commit {len(verdicts)} human labels (--commit to write)")

    # Always re-run the evaluator after a commit
    if args.commit:
        r = subprocess.run(
            [sys.executable, os.path.join(HERE, "evaluate.py"), "--top", "10", "--verbose"],
            capture_output=True, text=True, cwd=os.path.dirname(HERE),
        )
        print("\n--- evaluate.py output ---")
        print(r.stdout)
        if r.returncode != 0:
            print("STDERR:", r.stderr, file=sys.stderr)
            sys.exit(r.returncode)
    else:
        print("(re-run evaluate.py after --commit)")


if __name__ == "__main__":
    main()
