import logging

from .middleware import request_id_var


class RequestIdFilter(logging.Filter):
    """Put the id of the request being handled on every log record, as ``request_id`` (``-`` outside a request).

    Django logs a 4xx or 5xx answer after the middleware has finished, so those records name their request
    (``record.request``) and the id is read from it; every other record takes the id of the running request.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        request = getattr(record, "request", None)
        record.request_id = getattr(request, "request_id", None) or request_id_var.get()
        return True
