"""Print the tables of a run.py --out JSON again (python eval/wake/summary.py result.json)."""
import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import run  # noqa: E402

d = json.loads(pathlib.Path(sys.argv[1]).read_text())
run.report(argparse.Namespace(**d['args']), d['pos'], d['neg'], 0)
