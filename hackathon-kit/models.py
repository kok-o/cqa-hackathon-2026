"""CPU baselines for a small, ready-to-model table and labeled ticket text."""

import json

import numpy as np
import pandas as pd
from catboost import CatBoostClassifier
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, log_loss
from sklearn.pipeline import make_pipeline


def prepare_features(frame, features, categorical):
    matrix = frame.loc[:, features].copy()
    for name in categorical:
        matrix[name] = ("value:" + matrix[name].astype("string")).fillna("missing:")
    return matrix


def top_k_metrics(y, probabilities, top_k):
    order = np.argsort(-probabilities, kind="stable")[:top_k]
    labels = y.to_numpy()
    positives = int(labels.sum())
    hits = int(labels[order].sum())
    base_rate = positives / len(labels)
    precision = hits / len(order)
    return {
        "rows": len(labels),
        "base_rate": base_rate,
        "average_precision": float(average_precision_score(labels, probabilities)),
        "log_loss": float(log_loss(labels, probabilities)),
        "k": len(order),
        "hits_at_k": hits,
        "precision_at_k": precision,
        "recall_at_k": hits / positives,
        "lift_at_k": precision / base_rate,
    }


def score_activation(
    frame, *, features, categorical, valid_from, holdout_from, observed_through, horizon_days, top_k,
):
    """Fit on mature labels and score validation and untouched future holdout.

    Input must contain as_of, target (0/1) and only precomputed point-in-time features.
    Split boundaries and zone-less as_of values are interpreted as UTC.
    """
    if horizon_days <= 0 or top_k <= 0:
        raise ValueError("horizon_days and top_k must be positive")
    features = list(features)
    categorical = list(categorical)
    if (
        not features
        or len(features) != len(set(features))
        or not set(categorical) <= set(features)
        or {"as_of", "target"} & set(features)
    ):
        raise ValueError("Use unique, explicit features and a categorical subset")

    times = pd.to_datetime(frame["as_of"], utc=True, format="mixed")
    valid_start = pd.to_datetime(valid_from, utc=True)
    holdout_start = pd.to_datetime(holdout_from, utc=True)
    last_observed = pd.to_datetime(observed_through, utc=True)
    if times.isna().any() or not (valid_start < holdout_start < last_observed):
        raise ValueError("Provide valid timestamps and ordered time boundaries")
    if not frame["target"].isin([0, 1]).all():
        raise ValueError("target must contain only 0 and 1")

    mature_at = times + pd.Timedelta(days=horizon_days)
    train = mature_at <= valid_start
    valid = (times >= valid_start) & (mature_at <= holdout_start)
    holdout = (times >= holdout_start) & (mature_at <= last_observed)
    y = frame["target"].astype(int)
    for name, mask in (("train", train), ("validation", valid), ("holdout", holdout)):
        if y[mask].nunique() != 2:
            raise ValueError(f"{name} needs both classes after the horizon gap")

    matrix = prepare_features(frame, features, categorical)
    model = CatBoostClassifier(
        iterations=400,
        depth=6,
        learning_rate=0.05,
        loss_function="Logloss",
        random_seed=42,
        thread_count=2,
        allow_writing_files=False,
        verbose=False,
    )
    model.fit(
        matrix.loc[train], y.loc[train],
        cat_features=categorical,
        eval_set=(matrix.loc[valid], y.loc[valid]),
        early_stopping_rounds=35,
    )
    summary = {}
    for name, mask in (("validation", valid), ("holdout", holdout)):
        probabilities = model.predict_proba(matrix.loc[mask])[:, 1]
        summary[name] = top_k_metrics(y.loc[mask], probabilities, top_k)
    return model, summary


def ticket_pipeline():
    """Fit the entire pipeline on training texts only; prepare time/group splits first."""
    return make_pipeline(
        TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), max_features=100_000),
        LogisticRegression(max_iter=500, class_weight="balanced", random_state=42),
    )


def demo():
    rng = np.random.default_rng(42)
    n = 1000
    visits = rng.integers(0, 10, n)
    channel = rng.choice(["app", "web"], n)
    probability = 0.08 + 0.045 * visits + 0.10 * (channel == "app")
    synthetic = pd.DataFrame({
        "as_of": pd.date_range("2026-01-01", periods=n, freq="2h", tz="UTC"),
        "visits_7d": visits,
        "channel": channel,
        "target": (rng.random(n) < probability).astype(int),
    })
    _, metrics = score_activation(
        synthetic,
        features=["visits_7d", "channel"], categorical=["channel"],
        valid_from="2026-02-08", holdout_from="2026-03-01", observed_through="2026-04-01",
        horizon_days=7, top_k=25,
    )
    print(json.dumps(metrics, indent=2))
    pipeline = ticket_pipeline()
    pipeline.fit(
        ["deposit failed", "bank transfer missing", "reset password", "cannot sign in"],
        ["payment", "payment", "access", "access"],
    )
    if list(pipeline.predict(["deposit failed", "reset password"])) != ["payment", "access"]:
        raise RuntimeError("Text pipeline smoke check failed")
    print("OK: temporal CatBoost and local text pipeline ran on synthetic input")


if __name__ == "__main__":
    demo()
