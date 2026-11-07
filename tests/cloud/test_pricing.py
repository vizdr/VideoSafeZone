"""The server's plan price (DEALER-PANEL-PLAN.md D9, D19) against the shared vectors.

tests/fixtures/pricing-cases.json is read here and by client-app/tests/pricing.spec.ts, which
checks the page's preview (client-app/src/pricing.ts) against the same cases: the price a dealer
sees while editing is the price the server stores.
"""
import json
from pathlib import Path

import pytest

from loaders import load_lambda

directory = load_lambda("vsz_directory")
VECTORS = json.loads((Path(__file__).parent.parent / "fixtures" / "pricing-cases.json").read_text())


@pytest.mark.parametrize("case", VECTORS["cases"], ids=[c["name"] for c in VECTORS["cases"]])
def test_the_server_prices_each_shared_case(case):
    discount = case.get("annualDiscountPercent", VECTORS["annualDiscountPercent"])
    args = (VECTORS["prices"], discount, case["lines"], case["retentionDays"], case["interval"])
    if case["cents"] is None:
        with pytest.raises(directory.DirectoryError, match="no price"):
            directory.price_plan(*args)
    else:
        assert directory.price_plan(*args) == case["cents"]


def test_the_vectors_cover_both_roundings_and_a_missing_price():
    names = " ".join(c["name"] for c in VECTORS["cases"])
    assert "rounded down" in names and "rounded up" in names
    assert any(c["cents"] is None for c in VECTORS["cases"])
