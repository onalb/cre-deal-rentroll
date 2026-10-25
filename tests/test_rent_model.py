import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from forecast import dollars_per_sqft, fit_peer_rent, peer_rent
from forecast.linear import fit_gradient_descent, ordinary_least_squares


def test_gradient_descent_recovers_a_line():
    rows = [[x] for x in range(-20, 21)]
    y = [1.5 + 2.0 * x for x, in rows]
    model = fit_gradient_descent(rows, y, ["x"], lr=0.1, epochs=4000, l2=0.0)
    assert abs(model.bias - 1.5) < 0.05
    std = model.stds[0]
    assert abs(model.weights[0] / std - 2.0) < 0.05
    bias, weights = ordinary_least_squares(rows, y, model.means, model.stds, l2=0.0)
    assert abs(bias - model.bias) < 0.05
    assert abs(weights[0] - model.weights[0]) < 0.05


def test_peer_rent_follows_square_feet():
    units = []
    for sqft in (480, 480, 480, 610, 610, 610, 780, 780, 780):
        units.append(
            {
                "sqft": sqft,
                "beds": 1,
                "reno": False,
                "retail": False,
                "in_place": 400 + 0.5 * sqft,
            }
        )
    model = fit_peer_rent(units)
    small = peer_rent(model, {"sqft": 480, "beds": 1, "reno": False, "retail": False})
    large = peer_rent(model, {"sqft": 780, "beds": 1, "reno": False, "retail": False})
    assert large > small
    assert abs(dollars_per_sqft(model) - 0.5) < 0.15
