"""Tests for arbitration remote-active notice rendering."""

import pytest

import cv_engine.image_processor as image_processor_module
from config import EINK_IMAGE_SIZE
from cv_engine.image_processor import EInkImageProcessor


def test_render_remote_active_notice_returns_packed_bitmap_size():
    processor = EInkImageProcessor()

    packed = processor.render_remote_active_notice("Remote Control Active")

    assert isinstance(packed, bytes)
    assert len(packed) == EINK_IMAGE_SIZE


def test_render_remote_active_notice_handles_unicode_and_long_text():
    processor = EInkImageProcessor()

    unicode_payload = processor.render_remote_active_notice("遠端控制啟用中")
    long_payload = processor.render_remote_active_notice("Remote Control Active " * 20)

    assert len(unicode_payload) == EINK_IMAGE_SIZE
    assert len(long_payload) == EINK_IMAGE_SIZE
    assert unicode_payload != long_payload


def test_render_remote_active_notice_rejects_empty_text():
    processor = EInkImageProcessor()

    with pytest.raises(ValueError, match="non-empty"):
        processor.render_remote_active_notice("   ")


def test_render_remote_active_notice_rejects_non_positive_font_size():
    processor = EInkImageProcessor()

    with pytest.raises(ValueError, match="font_size"):
        processor.render_remote_active_notice("Remote", font_size=0)

    with pytest.raises(ValueError, match="font_size"):
        processor.render_remote_active_notice("Remote", font_size=-8)


def test_render_remote_active_notice_gracefully_degrades_when_pillow_unavailable(monkeypatch):
    # Build instance without __init__ so _check_pil() does not raise when Image is None.
    processor = object.__new__(EInkImageProcessor)
    processor.width = 400
    processor.height = 300
    processor.dither = True

    monkeypatch.setattr(image_processor_module, "Image", None)

    payload = processor.render_remote_active_notice("Remote Control Active", font_size=32)

    assert isinstance(payload, bytes)
    assert len(payload) == EINK_IMAGE_SIZE
    assert payload == (bytes([0xFF]) * EINK_IMAGE_SIZE)
