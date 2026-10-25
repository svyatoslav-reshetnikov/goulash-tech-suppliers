"""Scoring constants in one place. Weights sum to 100.

Change the weights here only; scoring, the UI breakdown bars (via `breakdown_max` in the API) and the tests read them.
"""

# Maximum points per component (sum = 100). "relevance" is the product match.
WEIGHTS: dict[str, int] = {"relevance": 40, "location": 20, "completeness": 25, "reliability": 15}

# Product match points by the strongest evidence found in the supplier's extracted data.
PRODUCT_POINTS: dict[str, int] = {
    "price_list": 40,  # the product (or a close synonym) appears in the price list
    "assortment": 32,  # named in the assortment text
    "description": 20,  # named in the company description
    "name": 10,  # only in the company name: does not confirm the product
    "generic": 8,  # only a generic claim ("полный ассортимент продуктов питания") or a part of the product phrase
    "none": 0,
}
# A supplier is recommended only if its product match is at least this share of the maximum.
RECOMMEND_MIN_PRODUCT_SHARE = 0.5

# Location points (max 20). "nationwide" is a generic promise and ranks below a neighbouring region;
# "other" ranks below "unknown": a known foreign address still earns completeness points.
LOCATION_POINTS: dict[str, int] = {"local": 20, "delivers": 17, "neighbor": 12, "nationwide": 9, "other": 1, "unknown": 7}
NEUTRAL_LOCATION_POINTS = 10  # no region selected: the same for everyone

RECOMMEND_TOP = 3
RECOMMEND_MIN_SCORE = 40
