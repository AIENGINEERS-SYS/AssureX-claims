"""Safe, structured errors for the document-processing API."""
from werkzeug.exceptions import HTTPException


class DocumentError(HTTPException):
    def __init__(self, error_code: str, message: str, status: int = 400, *, details=None):
        super().__init__(description=message)
        self.code = status
        self.error_code = error_code
        self.details = details


def document_error(code: str, message: str, status: int = 400, *, details=None):
    return DocumentError(code, message, status, details=details)
