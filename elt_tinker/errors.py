"""Infrastructure failures are excluded from RL instead of becoming bad-policy rewards."""


class InfrastructureError(RuntimeError):
    """No scientifically usable reward can be computed for this episode."""
