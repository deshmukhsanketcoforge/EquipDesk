"""Entry point for the `agent` compose service (`make demo`): the demo, cases A to F."""

import sys

from agent.run_demo import main as run_demo_main


def main() -> int:
    return run_demo_main()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
