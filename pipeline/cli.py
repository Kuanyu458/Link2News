"""Stable console entry point with backward-compatible main.py flags."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def main():
    args = sys.argv[1:]
    command = args[0] if args and not args[0].startswith("-") else ""
    rest = args[1:] if command else args

    if command == "doctor":
        from . import doctor
        return doctor.main(rest)

    from . import main as pipeline_main
    translations = {
        "run": ["--all"],
        "collect": ["--collect"],
        "generate": ["--generate"],
        "regenerate": ["--regenerate"],
        "rerender": ["--rerender"],
        "publish": ["--publish"],
    }
    if command and command not in translations:
        raise SystemExit(f"unknown command: {command}")
    sys.argv = [sys.argv[0], *translations.get(command, []), *rest]
    return pipeline_main.main()


if __name__ == "__main__":
    main()
