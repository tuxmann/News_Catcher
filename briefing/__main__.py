"""Run overnight briefing: python -m briefing"""

from __future__ import annotations

import config
from briefing.run import run_briefing


def main() -> None:
    if not config.BRIEFING_CONFIG_FILE.is_file():
        raise SystemExit(
            f"Create {config.BRIEFING_CONFIG_FILE} from briefing.yaml.example"
        )
    path = run_briefing()
    print(f"Briefing complete: {path}")


if __name__ == "__main__":
    main()
