"""Entry point for the `agent` compose service (`make demo`)."""

import sys


def main() -> int:
    print(
        "The EquipDesk ReAct demo is not implemented yet (EQP-10 to EQP-13). "
        "For the one-call proof, run `make one-call`.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
