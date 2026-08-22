"""Tier 4: behavioural entity classifier.

Used only when tiers 1-3 have nothing, and capped at moderate confidence: a
behavioural inference is a lead for an investigator, never grounds for a freeze
request on its own.

If a trained model exists at ml/artifacts/entity_classifier.joblib it is used;
otherwise a transparent rule-based scorer stands in, so the tier always
produces an explainable answer rather than silently disappearing.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.attribution.features import FEATURE_ORDER, AddressFeatures
from app.attribution.models import Candidate
from app.config import BACKEND_ROOT
from app.core.enums import AttributionMethod, EntityType
from app.logging_config import get_logger

logger = get_logger(__name__)

MODEL_PATH = BACKEND_ROOT / "ml" / "artifacts" / "entity_classifier.joblib"

CLASS_LABELS = [
    "cex_deposit",
    "mixer",
    "bridge",
    "p2p_merchant",
    "personal",
    "contract",
]

CLASS_TO_ENTITY: dict[str, EntityType] = {
    "cex_deposit": EntityType.CEX_DEPOSIT,
    "mixer": EntityType.MIXER,
    "bridge": EntityType.BRIDGE,
    "p2p_merchant": EntityType.P2P_MERCHANT,
    "personal": EntityType.PERSONAL_WALLET,
    "contract": EntityType.DEFI_CONTRACT,
}

CLASS_DISPLAY: dict[str, str] = {
    "cex_deposit": "Probable exchange deposit address",
    "mixer": "Probable mixing service",
    "bridge": "Probable cross-chain bridge",
    "p2p_merchant": "Probable P2P / OTC merchant",
    "personal": "Probable personal wallet",
    "contract": "Probable smart contract",
}


@dataclass
class Prediction:
    label: str
    probability: float
    probabilities: dict[str, float]
    source: str
    rationale: list[str]


def _rule_based(features: AddressFeatures) -> Prediction:
    """Transparent fallback scorer.

    Each rule is a plain statement an investigator can check by hand, which
    matters more here than squeezing out extra accuracy.
    """
    scores = dict.fromkeys(CLASS_LABELS, 0.05)
    reasons: dict[str, list[str]] = {label: [] for label in CLASS_LABELS}

    def bump(label: str, amount: float, reason: str) -> None:
        scores[label] += amount
        reasons[label].append(reason)

    if features.sweep_ratio >= 0.9 and features.max_out_share >= 0.8:
        bump("cex_deposit", 0.45, "sweeps almost everything to a single destination")
    if features.in_degree >= 3 and features.out_degree <= 2:
        bump("cex_deposit", 0.25, f"{int(features.in_degree)} payers in, few destinations out")
    if features.balance_retention < 0.05 and features.tx_count >= 3:
        bump("cex_deposit", 0.15, "retains no balance between transactions")

    if features.out_degree >= 8 and features.in_degree >= 8:
        bump("mixer", 0.3, "high fan-in and fan-out")
    if features.value_dispersion < 0.25 and features.tx_count >= 10:
        bump("mixer", 0.25, "uniform transaction values, characteristic of pooling")
    if features.round_amount_share >= 0.7 and features.tx_count >= 6:
        bump("mixer", 0.2, "predominantly round-denomination amounts")

    if features.out_degree >= 5 and features.asset_diversity >= 3:
        bump("bridge", 0.25, "many destinations across several assets")
    if features.timing_regularity >= 0.6 and features.tx_count >= 10:
        bump("bridge", 0.15, "highly regular timing, suggesting automation")

    if features.in_degree >= 5 and features.out_degree >= 5 and features.reuse_rate < 0.3:
        bump("p2p_merchant", 0.35, "many distinct counterparties on both sides")
    if 0.2 <= features.balance_retention <= 0.6:
        bump("p2p_merchant", 0.12, "maintains a working float")

    if features.tx_count <= 6 and features.in_degree <= 2 and features.out_degree <= 2:
        bump("personal", 0.4, "few transactions and few counterparties")
    if features.balance_retention > 0.5:
        bump("personal", 0.2, "retains most of what it receives")

    if features.in_degree >= 20 and features.reuse_rate > 0.5:
        bump("contract", 0.25, "very high inbound degree with repeat callers")

    total = sum(scores.values()) or 1.0
    probabilities = {label: value / total for label, value in scores.items()}
    best = max(probabilities, key=probabilities.get)
    return Prediction(
        label=best,
        probability=probabilities[best],
        probabilities=probabilities,
        source="rule-based",
        rationale=reasons[best] or ["no strong behavioural signal"],
    )


class EntityClassifier:
    def __init__(self) -> None:
        self._model: Any | None = None
        self._loaded = False

    def _load(self) -> Any | None:
        if self._loaded:
            return self._model
        self._loaded = True
        if not Path(MODEL_PATH).exists():
            logger.info("entity_classifier_absent_using_rules", path=str(MODEL_PATH))
            return None
        try:
            import joblib

            self._model = joblib.load(MODEL_PATH)
            logger.info("entity_classifier_loaded", path=str(MODEL_PATH))
        except Exception as exc:
            logger.warning("entity_classifier_load_failed", error=str(exc))
            self._model = None
        return self._model

    @property
    def is_trained(self) -> bool:
        return self._load() is not None

    def warm(self) -> None:
        """Load the model now so the first prediction is not the slow one."""
        self._load()

    def predict(self, features: AddressFeatures) -> Prediction:
        model = self._load()
        if model is None:
            return _rule_based(features)
        try:
            import numpy as np

            vector = np.array([features.to_vector()], dtype=float)
            probabilities = model.predict_proba(vector)[0]
            classes = [str(c) for c in getattr(model, "classes_", CLASS_LABELS)]
            mapping = {
                label: float(prob)
                for label, prob in zip(classes, probabilities, strict=False)
            }
            best = max(mapping, key=mapping.get)
            return Prediction(
                label=best,
                probability=mapping[best],
                probabilities=mapping,
                source="gradient-boosted classifier",
                rationale=self._top_features(features),
            )
        except Exception as exc:
            logger.warning("entity_classifier_predict_failed", error=str(exc))
            return _rule_based(features)

    @staticmethod
    def _top_features(features: AddressFeatures) -> list[str]:
        values = features.to_dict()
        ranked = sorted(
            ((name, values.get(name, 0.0)) for name in FEATURE_ORDER),
            key=lambda item: abs(item[1]),
            reverse=True,
        )[:4]
        return [f"{name}={value}" for name, value in ranked]


classifier = EntityClassifier()


def classify_address(features: AddressFeatures) -> Candidate | None:
    prediction = classifier.predict(features)
    if prediction.label == "personal" or prediction.probability < 0.45:
        return None

    entity_type = CLASS_TO_ENTITY.get(prediction.label, EntityType.UNKNOWN)
    if entity_type is EntityType.UNKNOWN:
        return None

    # Behavioural evidence tops out well below a direct label match.
    confidence = 0.45 + (prediction.probability - 0.45) * 0.55

    return Candidate(
        entity_name=CLASS_DISPLAY.get(prediction.label, prediction.label),
        entity_type=entity_type,
        method=AttributionMethod.ML_CLASSIFIER,
        confidence=confidence,
        evidence={
            "predicted_class": prediction.label,
            "probability": round(prediction.probability, 4),
            "model": prediction.source,
            "top_signals": prediction.rationale,
            "class_probabilities": {
                k: round(v, 4) for k, v in sorted(
                    prediction.probabilities.items(), key=lambda kv: kv[1], reverse=True
                )[:4]
            },
            "caveat": (
                "Behavioural inference only. Names a likely entity type, not a specific "
                "company, and is not sufficient basis for a freeze request."
            ),
        },
    )
