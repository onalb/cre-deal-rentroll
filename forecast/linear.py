"""Linear regression fit by batch gradient descent.

Loss is mean squared error plus L2 on every weight except the bias:

    L(w) = (1/n) * sum((x·w - y)^2) + l2 * sum(w_j^2 for j > 0)

Features are standardized on the training set. The bias is the first weight
and is not regularized. Gradient descent starts at zero.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class LinearModel:
    feature_names: list[str]
    means: list[float]
    stds: list[float]
    weights: list[float]
    bias: float
    l2: float
    epochs_run: int
    train_rmse: float

    def predict_row(self, raw: list[float]) -> float:
        z = _standardize(raw, self.means, self.stds)
        return self.bias + sum(w * v for w, v in zip(self.weights, z))

    def to_dict(self) -> dict:
        return {
            "feature_names": self.feature_names,
            "means": self.means,
            "stds": self.stds,
            "weights": self.weights,
            "bias": self.bias,
            "l2": self.l2,
            "epochs_run": self.epochs_run,
            "train_rmse": self.train_rmse,
        }

    @classmethod
    def from_dict(cls, data: dict) -> LinearModel:
        return cls(
            feature_names=list(data["feature_names"]),
            means=[float(v) for v in data["means"]],
            stds=[float(v) for v in data["stds"]],
            weights=[float(v) for v in data["weights"]],
            bias=float(data["bias"]),
            l2=float(data["l2"]),
            epochs_run=int(data["epochs_run"]),
            train_rmse=float(data.get("train_rmse", data.get("train_rmse_gap", 0.0))),
        )


def fit_gradient_descent(
    rows: list[list[float]],
    y: list[float],
    feature_names: list[str],
    *,
    lr: float = 0.08,
    epochs: int = 6000,
    l2: float = 1e-3,
    tol: float = 1e-12,
) -> LinearModel:
    if not rows or len(rows) != len(y):
        raise ValueError("rows and y must be the same non-empty length")
    if any(len(row) != len(feature_names) for row in rows):
        raise ValueError("every row must match feature_names")
    means, stds = _moments(rows)
    zrows = [_standardize(row, means, stds) for row in rows]
    n = len(y)
    p = len(feature_names)
    bias = 0.0
    weights = [0.0] * p
    prev = float("inf")
    ran = 0
    for ran in range(1, epochs + 1):
        grad_b = 0.0
        grad_w = [0.0] * p
        sse = 0.0
        for z, target in zip(zrows, y):
            pred = bias + sum(w * v for w, v in zip(weights, z))
            err = pred - target
            sse += err * err
            grad_b += err
            for j, v in enumerate(z):
                grad_w[j] += err * v
        bias -= lr * (2.0 / n) * grad_b
        for j in range(p):
            grad = (2.0 / n) * grad_w[j] + 2.0 * l2 * weights[j]
            weights[j] -= lr * grad
        loss = sse / n + l2 * sum(w * w for w in weights)
        if abs(prev - loss) < tol:
            break
        prev = loss
    rmse = _rmse([bias + sum(w * v for w, v in zip(weights, z)) for z in zrows], y)
    return LinearModel(
        feature_names=list(feature_names),
        means=means,
        stds=stds,
        weights=weights,
        bias=bias,
        l2=l2,
        epochs_run=ran,
        train_rmse=rmse,
    )


def ordinary_least_squares(
    rows: list[list[float]],
    y: list[float],
    means: list[float],
    stds: list[float],
    l2: float,
) -> tuple[float, list[float]]:
    """Ridge normal equations for the same loss gradient descent minimizes."""
    zrows = [_standardize(row, means, stds) for row in rows]
    n = len(y)
    p = len(means)
    dim = p + 1
    # Design column 0 is the bias.
    ata = [[0.0] * dim for _ in range(dim)]
    aty = [0.0] * dim
    for z, target in zip(zrows, y):
        design = [1.0, *z]
        for r in range(dim):
            aty[r] += design[r] * target
            for c in range(dim):
                ata[r][c] += design[r] * design[c]
    for j in range(1, dim):
        ata[j][j] += n * l2
    sol = _solve(ata, aty)
    return sol[0], sol[1:]


def rmse(pred: list[float], y: list[float]) -> float:
    return _rmse(pred, y)


def r2(pred: list[float], y: list[float]) -> float:
    if not y:
        return 0.0
    mean = sum(y) / len(y)
    ss_tot = sum((v - mean) ** 2 for v in y)
    ss_res = sum((a - b) ** 2 for a, b in zip(y, pred))
    if ss_tot <= 1e-12:
        return 0.0
    return 1.0 - ss_res / ss_tot


def _moments(rows: list[list[float]]) -> tuple[list[float], list[float]]:
    n = len(rows)
    p = len(rows[0])
    means: list[float] = []
    stds: list[float] = []
    for j in range(p):
        col = [row[j] for row in rows]
        mu = sum(col) / n
        var = sum((v - mu) ** 2 for v in col) / n
        sd = var ** 0.5
        means.append(mu)
        stds.append(sd if sd > 1e-12 else 1.0)
    return means, stds


def _standardize(row: list[float], means: list[float], stds: list[float]) -> list[float]:
    return [(v - mu) / sd for v, mu, sd in zip(row, means, stds)]


def _rmse(pred: list[float], y: list[float]) -> float:
    n = len(y)
    return (sum((a - b) ** 2 for a, b in zip(pred, y)) / n) ** 0.5


def _solve(matrix: list[list[float]], vector: list[float]) -> list[float]:
    dim = len(vector)
    aug = [row[:] + [vector[i]] for i, row in enumerate(matrix)]
    for col in range(dim):
        pivot = max(range(col, dim), key=lambda r: abs(aug[r][col]))
        aug[col], aug[pivot] = aug[pivot], aug[col]
        scale = aug[col][col]
        if abs(scale) < 1e-12:
            raise ValueError("design matrix is singular")
        for j in range(col, dim + 1):
            aug[col][j] /= scale
        for r in range(dim):
            if r == col:
                continue
            factor = aug[r][col]
            for j in range(col, dim + 1):
                aug[r][j] -= factor * aug[col][j]
    return [aug[i][dim] for i in range(dim)]
