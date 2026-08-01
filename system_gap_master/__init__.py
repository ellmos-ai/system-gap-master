"""Public, user-neutral helpers for system-gap-master."""

from typing import Any

__all__ = [
    "ConflictCopyReconciler",
    "ReconcilerError",
    "RepublicaTransitError",
    "RepublicaTransitPaths",
    "TrustedPeerPathError",
    "TrustedPeerPathRegistry",
]


def __getattr__(name: str) -> Any:
    if name in __all__:
        from .conflict_copy_reconciler import (
            ConflictCopyReconciler,
            ReconcilerError,
        )
        from .republica_transit import (
            RepublicaTransitError,
            RepublicaTransitPaths,
        )
        from .trusted_peer_paths import (
            TrustedPeerPathError,
            TrustedPeerPathRegistry,
        )

        return {
            "ConflictCopyReconciler": ConflictCopyReconciler,
            "ReconcilerError": ReconcilerError,
            "RepublicaTransitError": RepublicaTransitError,
            "RepublicaTransitPaths": RepublicaTransitPaths,
            "TrustedPeerPathError": TrustedPeerPathError,
            "TrustedPeerPathRegistry": TrustedPeerPathRegistry,
        }[name]
    raise AttributeError(name)
