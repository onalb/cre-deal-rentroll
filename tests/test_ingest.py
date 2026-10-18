from pathlib import Path

from cre_rentroll.ingest import ingest_csv

ROLLS = Path(r"C:\Users\burak\Desktop\ML Interview")


def test_la_cordoba_unit_count():
    p = ROLLS / "La Cordoba Rent Roll 04-20-21 (2) 164.csv"
    if not p.exists():
        return
    roll = ingest_csv(p)
    assert roll.meta.layout == "lacordoba"
    assert len(roll.units) >= 150
    assert roll.meta.address_confidence == "high"
    assert "Houston" in (roll.meta.address or "")


def test_bay_view_unit_count():
    p = ROLLS / "RentRoll-Bay View Gardens Apartments-20210930-040414 - 250.csv"
    if not p.exists():
        return
    roll = ingest_csv(p)
    assert roll.meta.layout == "unit_row"
    assert len(roll.units) >= 200
    assert roll.meta.address_confidence == "high"
    assert "Portsmouth" in (roll.meta.address or "")


def test_chambers_hill_no_false_address():
    p = ROLLS / "RentRoll-Chambers Hill Elementary School-20231024-180834 - 252.csv"
    if not p.exists():
        return
    roll = ingest_csv(p)
    assert len(roll.units) >= 200
    assert roll.meta.address_confidence == "none"
