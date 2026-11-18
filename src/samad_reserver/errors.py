class ReservationError(RuntimeError):
    """Raised when a Samad reservation operation cannot be completed safely."""


class InsufficientBalanceError(ReservationError):
    def __init__(self, required_toman: int, balance_toman: int) -> None:
        self.required_toman = required_toman
        self.balance_toman = balance_toman
        super().__init__(
            f"Insufficient wallet balance: {required_toman} toman required, "
            f"{balance_toman} toman available"
        )
