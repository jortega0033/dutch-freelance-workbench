"""The one exception type the HTTP layer turns into a JSON error response."""


class ApiError(Exception):
    """An expected, client-visible failure.

    status  HTTP status code
    code    stable machine-readable code, e.g. "STALE_VERSION"
    message human-readable text; never contains SQL, bodies or tokens
    fields  {field_name: message} (validation-style errors)
    extra   additional keys merged into the "error" object
    headers additional response headers (e.g. Retry-After)
    """

    def __init__(self, status, code, message, fields=None, extra=None, headers=None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.fields = fields
        self.extra = extra
        self.headers = headers or {}

    def to_payload(self):
        error = {"code": self.code, "message": self.message}
        if self.fields:
            error["fields"] = dict(self.fields)
        if self.extra:
            error.update(self.extra)
        return {"error": error}
