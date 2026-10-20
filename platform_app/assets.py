"""Upload URLs: workers serve /static/uploads/..., browsers see /static/uploads/<space>/..."""


def rewrite_assets(value, space_id, outgoing=True):
    old, new = '/static/uploads/', f'/static/uploads/{space_id}/'
    if isinstance(value, str):
        return value.replace(old, new) if outgoing else value.replace(new, old)
    if isinstance(value, list):
        return [rewrite_assets(v, space_id, outgoing) for v in value]
    if isinstance(value, dict):
        return {rewrite_assets(k, space_id, outgoing): rewrite_assets(v, space_id, outgoing) for k, v in value.items()}
    return value
