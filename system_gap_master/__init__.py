"""Public, user-neutral helpers for system-gap-master."""

from typing import Any

__all__ = ["ConflictCopyReconciler", "ReconcilerError"]


def __getattr__(name: str) -> Any:
    if name in __all__:
        from .conflict_copy_reconciler import (
            ConflictCopyReconciler,
            ReconcilerError,
        )

        return {
            "ConflictCopyReconciler": ConflictCopyReconciler,
            "ReconcilerError": ReconcilerError,
        }[name]
    raise AttributeError(name)
