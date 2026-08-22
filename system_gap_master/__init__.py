"""Public, user-neutral helpers for system-gap-master."""

from typing import Any

__version__ = "1.5.0"

__all__ = [
    "__version__",
    "ConflictCopyReconciler",
    "ReconcilerError",
    "RepublicaTransitError",
    "RepublicaTransitPaths",
    "TicketHandoff",
    "TicketRouteAdapterError",
    "TrustedPeerPathError",
    "TrustedPeerPathRegistry",
    "TrustedPeerSftpError",
    "TrustedPeerSftpExecutor",
    "create_ticket_handoff",
    "validate_route_intent",
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
        from .ticket_route_adapter import (
            TicketHandoff,
            TicketRouteAdapterError,
            create_ticket_handoff,
            validate_route_intent,
        )
        from .trusted_peer_paths import (
            TrustedPeerPathError,
            TrustedPeerPathRegistry,
        )
        from .trusted_peer_sftp_executor import (
            TrustedPeerSftpError,
            TrustedPeerSftpExecutor,
        )

        return {
            "ConflictCopyReconciler": ConflictCopyReconciler,
            "ReconcilerError": ReconcilerError,
            "RepublicaTransitError": RepublicaTransitError,
            "RepublicaTransitPaths": RepublicaTransitPaths,
            "TicketHandoff": TicketHandoff,
            "TicketRouteAdapterError": TicketRouteAdapterError,
            "TrustedPeerPathError": TrustedPeerPathError,
            "TrustedPeerPathRegistry": TrustedPeerPathRegistry,
            "TrustedPeerSftpError": TrustedPeerSftpError,
            "TrustedPeerSftpExecutor": TrustedPeerSftpExecutor,
            "create_ticket_handoff": create_ticket_handoff,
            "validate_route_intent": validate_route_intent,
        }[name]
    raise AttributeError(name)
