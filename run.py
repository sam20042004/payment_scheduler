"""CLI: evaluate one case folder and print the Result as JSON.

    python run.py cases/case1_feasible_even
"""

from __future__ import annotations

import json
import sys

from feasibility.engine import evaluate_offer
from feasibility.models import load_case
from tests.test_cases import *
from tests.test_smoke import *


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: python run.py <case_dir>", file=sys.stderr)
        return 2
    client, offer, rules = load_case(argv[1])
    result = evaluate_offer(client, offer, rules)
    print(json.dumps(result.to_dict(), indent=2))
    # test_case1_feasible_even()
    test_case2_infeasible_minima()
    # test_case3_requires_balloon()
    # test_case4_tiered_minimums()

    test_loaders_parse_case1()
    test_eom_helpers()
    test_default_first_payment_is_eom()
    test_monthly_cadence_follows_eom()
    test_monthly_cadence_preserves_day()
    test_result_serialization_roundtrip()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
