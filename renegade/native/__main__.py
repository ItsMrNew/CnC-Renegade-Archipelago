import argparse
import json
from pathlib import Path
from .inspect import diagnose


def main():
    parser = argparse.ArgumentParser(description="Read-only Renegade native diagnostic; no memory writes")
    parser.add_argument("--pid", type=int)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = json.dumps(diagnose(args.pid), indent=2) + "\n"
    print(report)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(report, encoding="utf-8")


if __name__ == "__main__":
    main()
