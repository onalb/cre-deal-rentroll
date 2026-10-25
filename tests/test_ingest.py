from pathlib import Path

from cre_rentroll.ingest import ingest_bytes
from cre_rentroll.schemas import CanonicalRentRoll, UnitRow, mean_positive_sqft

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def test_mean_positive_sqft_skips_zero_and_missing():
    units = [
        UnitRow(unit_id="1", sqft=700),
        UnitRow(unit_id="2", sqft=800),
        UnitRow(unit_id="3", sqft=0),
        UnitRow(unit_id="4", sqft=None),
    ]
    assert mean_positive_sqft(units) == 750.0
    assert CanonicalRentRoll(layout="one_row", units=units).avg_sqft == 750.0
    assert mean_positive_sqft([UnitRow(unit_id="x")]) is None


def test_realpage_la_cordoba_sf_header_parses_sqft():
    raw = (FIXTURES / "lacordoba_like.csv").read_bytes()
    roll = ingest_bytes("La Cordoba Rent Roll.csv", raw)
    assert roll.layout == "realpage"
    by_id = {u.unit_id: u for u in roll.units}
    assert by_id["1-101"].sqft == 700
    assert by_id["1-102"].sqft == 750
    assert by_id["1-103"].sqft == 800
    assert roll.avg_sqft == 750.0


def test_realpage_scheduled_sqft_header_parses_sqft():
    raw = (FIXTURES / "lacordoba_scheduled.csv").read_bytes()
    roll = ingest_bytes("lacordoba_scheduled.csv", raw)
    assert roll.layout == "realpage"
    assert all(u.sqft and u.sqft > 0 for u in roll.units)
    assert roll.avg_sqft == round((689 + 689 + 1009) / 3, 2)


def test_one_row_sqft_header_parses_sqft():
    raw = (FIXTURES / "one_row.csv").read_bytes()
    roll = ingest_bytes("one_row.csv", raw)
    assert roll.layout == "one_row"
    assert [u.sqft for u in roll.units] == [900, 900, 650]
    assert roll.avg_sqft == round((900 + 900 + 650) / 3, 2)


def test_one_row_netsf_header_parses_sqft():
    csv = (
        "UnitID,PlanID,NetSF,OccStatus,InPlaceRent\n"
        "A-1,1x1,610,Occupied,1200\n"
        "A-2,1x1,610,Occupied,1210\n"
        "B-1,2x2,947,Vacant,0\n"
    )
    roll = ingest_bytes("bayview.csv", csv.encode())
    assert roll.layout == "one_row"
    assert [u.sqft for u in roll.units] == [610, 610, 947]
    assert roll.avg_sqft == round((610 + 610 + 947) / 3, 2)


def test_realpage_sq_dot_ft_and_positional_size_column():
    dotted = (
        "Bldg-Unit,Unit Type,Sq. Ft.,Unit Status,Charge Code,Amount\n"
        "1-101,1x1,689,Occupied,Charge Total:,1500\n"
        "1-102,1x1,700,Occupied,Charge Total:,1550\n"
        "1-103,2x2,1000,Vacant,Charge Total:,0\n"
    )
    roll = ingest_bytes("cordoba.csv", dotted.encode())
    assert [u.sqft for u in roll.units] == [689, 700, 1000]

    unlabeled = (
        "Bldg-Unit,Unit Type,,Unit Status,Charge Code,Amount\n"
        "2-201,1x1,680,Occupied,Charge Total:,1400\n"
        "2-202,1x1,680,Occupied,Charge Total:,1410\n"
        "2-203,1x1,720,Occupied,Charge Total:,1500\n"
    )
    pos = ingest_bytes("cordoba2.csv", unlabeled.encode())
    assert [u.sqft for u in pos.units] == [680, 680, 720]
