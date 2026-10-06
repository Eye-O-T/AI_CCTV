"""외부로 노출 가능한 명세 오류만 전달한다."""


class APIError(Exception):
    def __init__(self, status: int, code: str, message: str, field: str | None = None):
        super().__init__(code)
        self.status = status
        self.code = code
        self.message = message
        self.field = field

    def body(self) -> dict:
        return {"error": {"code": self.code, "message": self.message, "field": self.field}}
