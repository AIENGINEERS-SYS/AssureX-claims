"""Strict JSON at HTTP boundaries: NaN and infinities are never valid API numbers."""
from flask.json.provider import DefaultJSONProvider


class StrictJSONProvider(DefaultJSONProvider):
    def dumps(self, obj, **kwargs):
        kwargs["allow_nan"] = False
        return super().dumps(obj, **kwargs)

    def loads(self, value, **kwargs):
        def invalid_constant(value):
            raise ValueError("Non-finite JSON number")
        kwargs["parse_constant"] = invalid_constant
        return super().loads(value, **kwargs)
