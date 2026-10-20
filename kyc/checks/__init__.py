"""Pluggable verification checks.

Each check is a callable ``(ctx: CheckContext) -> CheckOutcome``. The runner executes
every registered check, persists the outcomes and feeds them to the risk scorer.
Register a new check with ``@register`` in a module imported by ``runner``.
"""

from .base import CheckContext, CheckOutcome, register, registered_checks

__all__ = ["CheckContext", "CheckOutcome", "register", "registered_checks"]
