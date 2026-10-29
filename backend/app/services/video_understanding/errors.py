"""Safe execution errors never include provider response bodies, keys or private media paths."""


class UnderstandingFailure(Exception):
    """Carry a fixed actionable explanation through a failed persistent attempt."""
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message

