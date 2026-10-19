"""Phase 1: a text assistant that opens apps and sites on Windows.

Run:  python main.py            (add --verbose to see which path handled each command)
"""

import argparse
import sys

from app import config
from app.brain.model import Brain
from app.brain.router import Router
from app.tools.applications import AppIndex
from app.tools.registry import SCHEMAS, Toolbox

N = config.ASSISTANT_NAME
EXIT_WORDS = {"exit", "quit", "goodbye", "bye"}


def build(model: str = config.MODEL, use_model: bool = True) -> Router:
    apps = AppIndex()
    brain = Brain(SCHEMAS, model=model) if use_model else None
    return Router(Toolbox(apps), brain)


def main() -> None:
    parser = argparse.ArgumentParser(description=f"{config.ASSISTANT_NAME} text assistant")
    parser.add_argument("--model", default=config.MODEL)
    parser.add_argument("--no-model", action="store_true", help="fast path only")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    print(f"{N}: Indexing your apps...")
    router = build(args.model, not args.no_model)
    print(f"{N}: Found {len(router.toolbox.apps.apps)} apps.")

    if router.brain:
        try:
            router.brain.warm_up()
            print(f"{N}: Model {args.model} loaded.")
        except Exception as e:
            print(f"{N}: Couldn't load {args.model} ({e}). Simple commands still work.")

    print(f"{N}: Online. Type 'help' for ideas or 'exit' to quit.")
    while True:
        try:
            text = input("\nYou: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if text.lower().strip(" .!") in EXIT_WORDS:
            break
        if text.lower() == "reload apps":
            router.toolbox.apps = AppIndex()
            print(f"{N}: Re-indexed, {len(router.toolbox.apps.apps)} apps.")
            continue
        result = router.handle(text)
        print(f"{N}: {result.reply}")
        if args.verbose:
            print(f"   [{result.source}, {result.ms:.0f} ms, calls={result.calls}]")
    print(f"{N}: Signing off.")


if __name__ == "__main__":
    sys.exit(main())
