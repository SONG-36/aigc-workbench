"""Capability-scoped effective requirements, not a second requirement store or AI planner."""
from .domain import Task, ImageSlot


def effective_requirements(task: Task, slot: ImageSlot) -> dict | None:
    if task.overall_requirement is None:
        return None  # Preserve legacy snapshots and pure-observation request fingerprints.
    return {
        "overall": task.overall_requirement.projection(),
        "slot": {"task_id": str(task.id), "slot_id": str(slot.id), "scope": "ONE_SLOT", "text": slot.requirements},
        "application": "C03_C04_C05 / C06 / C07; NOT C01_C02_PURE_OBSERVATION",
        "authority": "OPERATOR_REQUIREMENTS; NOT PRODUCT_FACTS_OR_CERTIFICATION",
        "inheritance": "Current requirements together with selected revision base and this feedback; no historical instruction replay.",
        "pass_protection": "New overall requirements apply to subsequent explicit operations; existing Pass and delivery remain unchanged.",
        "conflict_policy": "Do not resolve important product conflicts by last-input-wins. On explicit re-edit of a passed image, "
            "confirm only a clear, mutually unsatisfiable overall/valid-slot conflict that actually affects this operation; "
            "no extra confirmation without substantive conflict, no unrelated tasks or global approval flow.",
    }
