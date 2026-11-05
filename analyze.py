"""Optional CLI. With --snapshot, reproduce the terminal view offline."""
import argparse
import json
import sys
from pathlib import Path
from analyzer import Unavailable, analyze, terminal


def main():
    p = argparse.ArgumentParser()
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("url",nargs="?")
    group.add_argument("--snapshot",type=Path)
    p.add_argument("--save-json",type=Path)
    args = p.parse_args()
    try:
        report = json.loads(args.snapshot.read_text()) if args.snapshot else analyze(args.url)
        if args.save_json:
            args.save_json.write_text(json.dumps(report,indent=2,ensure_ascii=False))
        sys.stdout.write(terminal(report))
    except (Unavailable,ValueError,OSError,KeyError) as e:
        p.exit(2,f"Unavailable: {e}\n")


if __name__=="__main__":
    main()
