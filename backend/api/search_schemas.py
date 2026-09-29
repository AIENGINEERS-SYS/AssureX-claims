"""One strict filter contract for query strings, saved searches and exports."""
from datetime import date, timedelta
import math
from marshmallow import Schema, fields, validate, pre_load, validates_schema, ValidationError
from backend.services.warranty_calculations import current_date

STATUSES = ['draft', 'submitted', 'under_evaluation', 'additional_information_required', 'manual_review', 'approved', 'rejected', 'closed', 'under_review']
WARRANTY_STATUSES = ['active', 'near_expiry', 'expired', 'extended_warranty', 'not_started', 'no_warranty']
MULTI = {'claim_status', 'warranty_status', 'category'}
KINDS = ['global', 'claims', 'products', 'warranties', 'review']


class Confidence(fields.Field):
    def _deserialize(self, value, attr, data, **kwargs):
        try:
            if isinstance(value, bool):
                raise ValueError()
            percentage = isinstance(value, str) and value.strip().endswith('%')
            number = float(value.strip()[:-1] if percentage else value)
            if percentage:
                number /= 100
            if not math.isfinite(number) or not 0 <= number <= 1:
                raise ValueError()
            return number
        except (TypeError, ValueError):
            raise ValidationError('Use a value from 0 to 1, or an explicit percentage such as 70%.')


class Identifier(fields.Field):
    def _deserialize(self, value, attr, data, **kwargs):
        if isinstance(value, bool) or not isinstance(value, (int, str)):
            raise ValidationError('Use a numeric record ID or a public user ID.')
        value = str(value).strip()
        if value.isascii() and value.isdigit() and 1 <= int(value) <= 2147483647:
            return value
        if value.startswith('USR-') and 5 <= len(value) <= 32:
            return value
        raise ValidationError('Invalid user ID.')


class SearchFilters(Schema):
    q = fields.String(load_default='', validate=validate.Length(max=120))
    claim_id = fields.String(validate=validate.Length(min=1, max=32))
    product_id = fields.String(validate=validate.Length(min=1, max=32))
    serial_number = fields.String(validate=validate.Length(min=1, max=150))
    serial_match = fields.String(load_default='partial', validate=validate.OneOf(['partial', 'exact']))
    product_name = fields.String(validate=validate.Length(min=1, max=200))
    brand = fields.String(validate=validate.Length(min=1, max=100))
    model = fields.String(validate=validate.Length(min=1, max=100))
    category = fields.List(fields.String(validate=validate.Length(min=1, max=100)), validate=validate.Length(max=20))
    claim_status = fields.List(fields.String(validate=validate.OneOf(STATUSES)), validate=validate.Length(max=9))
    warranty_status = fields.List(fields.String(validate=validate.OneOf(WARRANTY_STATUSES)), validate=validate.Length(max=6))
    min_confidence = Confidence()
    max_confidence = Confidence()
    reviewer_id = Identifier()
    reviewer_name = fields.String(validate=validate.Length(min=1, max=201))
    assignment = fields.String(validate=validate.OneOf(['assigned', 'unassigned']))
    customer_id = Identifier()
    customer = fields.String(validate=validate.Length(min=1, max=201))
    provider = fields.String(validate=validate.Length(min=1, max=200))
    extended_warranty = fields.Boolean()
    date_field = fields.String(load_default='submission_date', validate=validate.OneOf(['submission_date', 'review_date', 'warranty_expiry_date']))
    date_preset = fields.String(validate=validate.OneOf(['today', '7d', '30d', '90d', 'custom']))
    start_date = fields.Date()
    end_date = fields.Date()
    sort = fields.String(load_default='newest', validate=validate.OneOf(['newest', 'oldest', 'highest_confidence', 'lowest_confidence', 'recently_updated', 'warranty_expiry_date']))
    order = fields.String(validate=validate.OneOf(['asc', 'desc']))
    page = fields.Integer(load_default=1, validate=validate.Range(min=1, max=100000))
    page_size = fields.Integer(load_default=25, validate=validate.Range(min=1, max=100))

    @pre_load
    def normalize(self, data, **kwargs):
        if not isinstance(data, dict):
            raise ValidationError('Expected an object of search filters.')
        data = dict(data)
        for key, value in data.items():
            if isinstance(value, str):
                data[key] = value.strip()
        for key in MULTI & data.keys():
            value = data[key]
            if isinstance(value, str):
                value = value.split(',')
            if isinstance(value, list) and all(isinstance(item, str) for item in value):
                data[key] = list(dict.fromkeys(item.strip().lower().replace(' ', '_') if key != 'category'
                    else item.strip().lower() for item in value))
        return data

    @validates_schema
    def ranges(self, data, **kwargs):
        if data.get('min_confidence', 0) > data.get('max_confidence', 1):
            raise ValidationError('Minimum confidence cannot exceed maximum confidence.')
        if data.get('start_date', date.min) > data.get('end_date', date.max):
            raise ValidationError('Start date cannot be after end date.')
        if data.get('date_preset') not in {None, 'custom'} and ('start_date' in data or 'end_date' in data):
            raise ValidationError('Choose a date preset or explicit dates, not both.')
        if data.get('date_preset') == 'custom' and not {'start_date', 'end_date'} <= data.keys():
            raise ValidationError('Custom ranges require both start and end dates.')
        if data.get('assignment') == 'unassigned' and ('reviewer_id' in data or 'reviewer_name' in data):
            raise ValidationError('Unassigned claims cannot also specify a reviewer.')


def normalize(raw):
    data = SearchFilters().load(raw)
    return {key: value.isoformat() if isinstance(value, date) else value for key, value in data.items()}


def query_params(args):
    data = {}
    for key in args:
        values = args.getlist(key)
        if key in MULTI:
            data[key] = [part for value in values for part in value.split(',')]
        elif len(values) > 1:
            raise ValidationError({key: ['Supply this parameter only once.']})
        else:
            data[key] = values[0]
    return data


def date_bounds(filters):
    preset = filters.get('date_preset')
    if preset and preset != 'custom':
        end = current_date()
        days = {'today': 1, '7d': 7, '30d': 30, '90d': 90}[preset]
        return end - timedelta(days=days - 1), end
    return tuple(date.fromisoformat(filters[key]) if filters.get(key) else None for key in ('start_date', 'end_date'))


class SavedSearchInput(Schema):
    name = fields.String(required=True, validate=validate.Length(min=1, max=80))
    scope = fields.String(required=True, validate=validate.OneOf(KINDS))
    filters = fields.Dict(required=True)
