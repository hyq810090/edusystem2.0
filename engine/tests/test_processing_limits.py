"""Bounded resource configuration preserves defaults and never echoes secrets."""
from dataclasses import FrozenInstanceError, asdict

import pytest

from mathbank.processing_limits import ProcessingLimits, LIMIT_SPECS, resolve_processing_limits


def test_existing_defaults_and_immutable_policy():
    value = resolve_processing_limits({})
    assert asdict(value) == {'image_upload_bytes':10485760, 'image_pixels':30000000,
        'normalized_image_bytes':20971520, 'document_upload_bytes':31457280,
        'text_source_bytes':5242880, 'pdf_pages':80, 'document_workers':2, 'document_queue':4,
        'classify_images':4, 'classify_image_bytes':4194304, 'pdf_ocr_workers':4}
    with pytest.raises(FrozenInstanceError):
        value.pdf_pages = 500


@pytest.mark.parametrize('field', list(LIMIT_SPECS))
def test_each_capacity_override_reaches_exact_units_and_preserves_other_fields(field):
    name, default, low, high, unit = LIMIT_SPECS[field]
    for selected in {low, high}:
        value = asdict(resolve_processing_limits({name: str(selected)}))
        before = asdict(ProcessingLimits())
        assert value[field] == selected * unit
        value.pop(field);before.pop(field)
        assert value == before


@pytest.mark.parametrize('bad', ['', '-1', '1.5', 'NaN', 'inf', '１２', '9'*100, 'PRIVATE_TOKEN_OR_PATH', True])
def test_bad_limits_fall_back_without_exposing_the_supplied_value(bad):
    field = 'document_upload_bytes'
    name, default, low, high, unit = LIMIT_SPECS[field]
    with pytest.warns(RuntimeWarning) as captured:
        actual = getattr(resolve_processing_limits({name: bad}), field)
    assert actual == default * unit
    if bad == 'PRIVATE_TOKEN_OR_PATH':
        assert str(bad) not in str(captured[0].message)
    assert name in str(captured[0].message)
