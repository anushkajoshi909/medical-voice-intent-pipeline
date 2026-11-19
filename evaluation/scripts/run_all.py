#!/usr/bin/env python3
"""Run the whole final evaluation batch and aggregate it. One command, resumable.

Steps (each is also a script of its own and can be run alone):
  1. run_eval.py --tag final500     4 models x 3 repetitions at 500 completion tokens + Ludyga baseline B0
  2. run_eval.py --tag final4000    gpt-oss-120b and DeepSeek V4.1 Flash x 3 repetitions at 4000 tokens
  3. run_e3.py                      entity resolution (Stages 4-5), no LLM
  4. build_final_report.py          one aggregated results file + per-command file

Resume: run_eval.py skips run folders of the same date that are already complete; run_e3.py skips an existing E3
folder. If the batch is interrupted, start this script again on the same day. Run folders are never overwritten.

To report from runs made earlier (no new LLM calls), call build_final_report.py directly with --date, --tags,
--baseline and --e3 (see its docstring).

Usage: python evaluation/scripts/run_all.py [--skip-llm] [--tag500 T] [--tag4000 T]
  --skip-llm   only E3 + aggregation from existing runs
  --tag500 / --tag4000   dataset tags of the two parts (default final500 / final4000); a new batch with
               changed code gets its own tags (e.g. fixes500 / fixes4000), so it is never mixed with older runs
"""
import argparse
import subprocess
import sys
from pathlib import Path

S = Path(__file__).resolve().parent
sys.path.insert(0, str(S))
ROOT = S.parent.parent
PY = sys.executable
BIG = ["openai/gpt-oss-120b", "deepseek-ai/DeepSeek-V4.1-Flash"]


def run(*cmd):
    print("\n$", " ".join(str(c) for c in cmd), flush=True)
    subprocess.run([PY, *map(str, cmd)], cwd=ROOT, check=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-llm", action="store_true")
    ap.add_argument("--tag500", default="final500")
    ap.add_argument("--tag4000", default="final4000")
    args = ap.parse_args()
    if not args.skip_llm:
        run(S / "run_eval.py", "--tag", args.tag500)
        run(S / "run_eval.py", "--models", *BIG, "--max-tokens", 4000, "--no-baseline", "--tag", args.tag4000)
    # E3 gets the batch tag, and the report names it explicitly, so a batch never picks up an E3 folder of
    # another batch of the same day. (A batch that runs over midnight: E3 runs on the second date; give
    # build_final_report.py --date <second date> --also-dates <first date> by hand, see evaluation.md.)
    from run_e3 import e3_folder_name
    run(S / "run_e3.py", "--tag", args.tag500)
    run(S / "build_final_report.py", "--tags", args.tag500, args.tag4000, "--e3", e3_folder_name(args.tag500))


if __name__ == "__main__":
    main()
