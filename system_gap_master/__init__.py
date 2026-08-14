"""Public, user-neutral helpers for system-gap-master."""

from typing import Any

__version__ = "1.4.1"

__all__ = [
    "__version__",
    "ConflictCopyReconciler",
    "ReconcilerError",
    "RepublicaTransitError",
    "RepublicaTransitPaths",
    "TrustedPeerPathError",
    "TrustedPeerPathRegistry",
    "TrustedPeerSftpError",
    "TrustedPeerSftpExecutor",
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
        from .trusted_peer_sftp_executor import (
            TrustedPeerSftpError,
            TrustedPeerSftpExecutor,
        )

        return {
            "ConflictCopyReconciler": ConflictCopyReconciler,
            "ReconcilerError": ReconcilerError,
            "RepublicaTransitError": RepublicaTransitError,
            "RepublicaTransitPaths": RepublicaTransitPaths,
            "TrustedPeerPathError": TrustedPeerPathError,
            "TrustedPeerPathRegistry": TrustedPeerPathRegistry,
            "TrustedPeerSftpError": TrustedPeerSftpError,
            "TrustedPeerSftpExecutor": TrustedPeerSftpExecutor,
        }[name]
    raise AttributeError(name)
