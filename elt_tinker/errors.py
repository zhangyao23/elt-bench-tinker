"""Infrastructure failures are excluded from RL instead of becoming bad-policy rewards."""


class InfrastructureError(RuntimeError):
    """No scientifically usable reward can be computed for this episode."""


class SQLPolicyError(ValueError):
    """The action was rejected before warehouse execution by the SQL boundary."""
