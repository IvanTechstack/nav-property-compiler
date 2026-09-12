import pytest

from nav_property_compiler import main


@pytest.fixture
def stats():
    return {
        "mls": "MLS# 123456",
        "beds": 4,
        "baths": 3,
        "sqft": "2,450",
        "lot_size": "11,999 sq ft",
        "year": 1998,
    }


def test_required_stats_occupy_first_six_positions(stats):
    bullets = main._structured_bullets_24(
        stats,
        ["Chef's kitchen"]
        + [f"Distinct property feature {index}" for index in range(17)],
    )

    assert bullets[:6] == [
        "MLS# 123456",
        "4 Bedrooms",
        "3 Bathrooms",
        "2,450 sq ft Living Area",
        "11,999 sq ft Lot Size",
        "Built 1998",
    ]


def test_missing_mls_is_rejected_instead_of_using_placeholder(stats):
    stats["mls"] = ""

    with pytest.raises(ValueError, match="mls"):
        main._structured_bullets_24(stats, [])


@pytest.mark.parametrize(
    ("lot_size", "expected"),
    [
        ("11,999 sq ft", "11,999 sq ft Lot Size"),
        ("12,000 sq ft", "0.28 acres Lot Size"),
    ],
)
def test_lot_size_converts_to_acres_at_12000_sqft(lot_size, expected):
    assert main._format_lot_size(lot_size) == expected


def test_legacy_feature_only_payload_keeps_its_first_feature(stats):
    legacy_payload = ["Chef's kitchen", "Heated saltwater pool"] + [
        f"Distinct physical feature {index}" for index in range(16)
    ]

    bullets = main._structured_bullets_24(stats, legacy_payload)

    assert bullets[6:] == legacy_payload


def test_new_payload_discards_ai_supplied_stat_positions(stats):
    ai_stats = [
        "wrong MLS",
        "wrong bedrooms",
        "wrong bathrooms",
        "wrong sq ft",
        "wrong lot size",
        "wrong year built",
    ]
    features = [f"Unique feature {index}" for index in range(18)]

    bullets = main._structured_bullets_24(stats, ai_stats + features)

    assert bullets[6:] == features


def test_feature_deduplication_preserves_first_seen_order(stats):
    supplied = [
        "Chef's kitchen",
        "Waterfront views",
        "Chefs Kitchen",
        "Private garden",
        "waterfront-views",
    ] + [f"Distinct lifestyle highlight {index}" for index in range(15)]

    bullets = main._structured_bullets_24(stats, supplied)

    assert bullets[6:9] == [
        "Chef's kitchen",
        "Waterfront views",
        "Private garden",
    ]


def test_rendered_property_highlights_have_exactly_24_slots(monkeypatch, stats):
    monkeypatch.setattr(main, "list_objects", lambda prefix: [])

    html = main._for_sale_html(
        "test-property",
        {
            "stats": stats,
            "bullets_24": [
                f"Distinct property highlight {index}" for index in range(18)
            ],
        },
        "",
    )

    assert html.count("<div class='feat'>") == 24


def test_placeholders_are_stripped_and_never_rendered(stats):
    supplied = [
        "Additional property feature TBD 1",
        "Feature TBD",
        "Private fenced backyard",
    ] + [f"Named neighborhood highlight {index}" for index in range(17)]

    bullets = main._structured_bullets_24(stats, supplied)

    assert len(bullets) == 24
    assert not any("tbd" in bullet.lower() for bullet in bullets)
    assert "Private fenced backyard" in bullets
