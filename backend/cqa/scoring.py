"""Deterministic provisional scoring, separate from model judgement.

The organizer's eight-criterion rubric has not been supplied. This implements
the documented global weighted normalized mean, without inventing penalties.
"""

from decimal import Decimal, ROUND_HALF_UP

from .models import ChecklistVersion, Criterion, CriterionResult


def _decimal(value: float | int) -> Decimal:
    return Decimal(str(value))


def _number(value: Decimal) -> float:
    return float(value.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP))


def normalize_answer(criterion: Criterion, answer: bool | float | str | None) -> Decimal:
    if criterion.answer_type == "boolean":
        if type(answer) is not bool:
            raise ValueError("Для булевого критерия требуется true или false")
        return Decimal(int(answer == criterion.positive_answer))
    if criterion.answer_type == "scale":
        if type(answer) not in (int, float):
            raise ValueError("Для шкалы требуется число")
        value = _decimal(answer)
        minimum, maximum = _decimal(criterion.scale_min), _decimal(criterion.scale_max)
        if not value.is_finite() or not minimum <= value <= maximum:
            raise ValueError("Ответ вне заданной шкалы")
        normalized = (value - minimum) / (maximum - minimum)
        return normalized if criterion.higher_is_better else Decimal(1) - normalized
    if not isinstance(answer, str) or answer not in criterion.choices:
        raise ValueError("Ответ отсутствует в вариантах критерия")
    return _decimal(criterion.choices[answer])


def calculate_score(checklist: ChecklistVersion, results: list[CriterionResult]) -> dict:
    by_id = {item.criterion_id: item for item in results}
    if len(by_id) != len(results) or set(by_id) != {c.criterion_id for c in checklist.criteria}:
        raise ValueError("Ожидается ровно один результат для каждого критерия")
    earned, applicable = Decimal(0), Decimal(0)
    rows = []
    sections: dict[str, dict] = {}
    unresolved = 0
    for criterion in checklist.criteria:
        result = by_id[criterion.criterion_id]
        if result.status == "not_applicable" and not criterion.na_allowed:
            raise ValueError("N/A не разрешён для этого критерия")
        weight = _decimal(criterion.weight)
        normalized = normalize_answer(criterion, result.answer) if result.status == "applicable" else None
        if result.status == "needs_review":
            unresolved += 1
        if result.status != "not_applicable":
            applicable += weight
        if normalized is not None:
            earned += weight * normalized
        rows.append({
            "criterion_id": criterion.criterion_id,
            "status": result.status,
            "normalized_score": _number(normalized) if normalized is not None else None,
            "weight": criterion.weight,
            "contribution": None,
        })
        if criterion.section_id:
            section = sections.setdefault(criterion.section_id, {"earned": Decimal(0), "weight": Decimal(0), "unknown": False})
            section["unknown"] |= result.status == "needs_review"
            if result.status != "not_applicable":
                section["weight"] += weight
            if normalized is not None:
                section["earned"] += weight * normalized
    status = "incomplete" if unresolved else "complete" if applicable else "not_applicable"
    total = None
    if status == "complete":
        total = float((100 * earned / applicable).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
        for row, criterion in zip(rows, checklist.criteria, strict=True):
            result = by_id[criterion.criterion_id]
            if result.status == "applicable":
                row["contribution"] = _number(100 * _decimal(criterion.weight) * normalize_answer(criterion, result.answer) / applicable)
    section_scores = {
        key: None if values["unknown"] or not values["weight"] else float((100 * values["earned"] / values["weight"]).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
        for key, values in sections.items()
    }
    return {
        "total": total,
        "status": status,
        "coverage": _number(Decimal(len(results) - unresolved) / len(results)),
        "earned_weight": _number(earned),
        "applicable_weight": _number(applicable),
        "criteria": rows,
        "sections": section_scores,
        "gross_error": any(result.gross_error for result in results),
    }
