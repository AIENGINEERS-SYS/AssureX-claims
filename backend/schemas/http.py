"""Common strict HTTP input contract, independent of route handlers."""
from marshmallow import Schema, ValidationError, pre_load


class StrictSchema(Schema):
    @pre_load
    def normalize(self, data, **kwargs):
        if not isinstance(data, dict):
            raise ValidationError("Expected a JSON object.")
        data = dict(data)
        for key in ("email", "full_name"):
            if isinstance(data.get(key), str):
                data[key] = data[key].strip()
        if isinstance(data.get("email"), str):
            data["email"] = data["email"].lower()
        return data
