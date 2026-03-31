"""Tests for menu selection rendering and audio cue synthesis."""
import pytest
from pathlib import Path
from config import EINK_IMAGE_SIZE, MENU_AUDIO_CUE_PATH
from cv_engine.image_processor import EInkImageProcessor


class TestMenuSelectionRendering:
    """Test suite for render_menu_selection method."""

    @pytest.fixture
    def processor(self):
        """Create image processor instance."""
        return EInkImageProcessor()

    @pytest.fixture
    def baseline_entries(self):
        """Standard menu entries."""
        return ["STEM", "Chat", "Follow", "Call Parent"]

    def test_render_menu_selection_returns_correct_size(self, processor, baseline_entries):
        """Verify packed image has correct size."""
        result = processor.render_menu_selection(baseline_entries, 0)
        assert isinstance(result, bytes)
        assert len(result) == EINK_IMAGE_SIZE

    def test_render_menu_selection_all_indices(self, processor, baseline_entries):
        """Verify rendering succeeds for each menu position."""
        for index in range(4):
            result = processor.render_menu_selection(baseline_entries, index)
            assert len(result) == EINK_IMAGE_SIZE

    def test_render_menu_selection_clamps_out_of_range_index(self, processor, baseline_entries):
        """Verify index clamping for out-of-range values."""
        # Should clamp to 0
        result = processor.render_menu_selection(baseline_entries, -5)
        assert len(result) == EINK_IMAGE_SIZE

        # Should clamp to 3 (last valid index)
        result = processor.render_menu_selection(baseline_entries, 10)
        assert len(result) == EINK_IMAGE_SIZE

    def test_render_menu_selection_with_custom_title(self, processor, baseline_entries):
        """Verify custom title rendering."""
        result = processor.render_menu_selection(
            baseline_entries, 1, title="Custom Menu"
        )
        assert len(result) == EINK_IMAGE_SIZE

    def test_render_menu_selection_normalizes_malformed_entries(self, processor):
        """Verify malformed entries fall back to baseline."""
        # Too few entries
        result = processor.render_menu_selection(["STEM", "Chat"], 0)
        assert len(result) == EINK_IMAGE_SIZE

        # Too many entries
        result = processor.render_menu_selection(
            ["STEM", "Chat", "Follow", "Call", "Extra"], 0
        )
        assert len(result) == EINK_IMAGE_SIZE

        # Empty entries
        result = processor.render_menu_selection([], 0)
        assert len(result) == EINK_IMAGE_SIZE

    def test_render_menu_selection_handles_none_entries(self, processor):
        """Verify None entries use baseline."""
        result = processor.render_menu_selection(None, 2)
        assert len(result) == EINK_IMAGE_SIZE

    def test_render_menu_selection_different_from_unselected(self, processor, baseline_entries):
        """Verify selection produces different output than unselected menu."""
        selected = processor.render_menu_selection(baseline_entries, 1)
        unselected = processor.render_home_menu(baseline_entries)
        
        # They should be different (selection adds highlight)
        assert selected != unselected

    def test_render_menu_selection_changes_with_index(self, processor, baseline_entries):
        """Verify different selections produce different images."""
        img0 = processor.render_menu_selection(baseline_entries, 0)
        img1 = processor.render_menu_selection(baseline_entries, 1)
        img2 = processor.render_menu_selection(baseline_entries, 2)
        img3 = processor.render_menu_selection(baseline_entries, 3)

        # All should be unique
        images = [img0, img1, img2, img3]
        unique_images = set(images)
        assert len(unique_images) == 4, "Each selection should produce unique output"


class TestMenuAudioCue:
    """Test suite for menu audio cue synthesis."""

    def test_audio_cue_file_exists(self):
        """Verify audio cue was synthesized at module import."""
        audio_path = Path(MENU_AUDIO_CUE_PATH)
        assert audio_path.exists(), f"Audio cue not found at {MENU_AUDIO_CUE_PATH}"
        assert audio_path.stat().st_size > 0, "Audio cue file is empty"

    def test_audio_cue_is_valid_mp3(self):
        """Verify audio cue has MP3 characteristics."""
        audio_path = Path(MENU_AUDIO_CUE_PATH)
        assert audio_path.exists()
        
        # Check for MP3 header signature
        with open(audio_path, "rb") as f:
            header = f.read(3)
            # MP3 files typically start with ID3 tag or MPEG frame sync
            # ID3v2 starts with 'ID3'
            # MPEG frame sync starts with 0xFF 0xFB or 0xFF 0xF3 or similar
            is_id3 = header[:3] == b'ID3'
            is_mpeg_sync = header[0] == 0xFF and (header[1] & 0xE0) == 0xE0
            
            assert is_id3 or is_mpeg_sync, "File does not appear to be valid MP3"

    def test_audio_cue_path_configuration(self):
        """Verify audio cue path matches configuration."""
        from config import MENU_AUDIO_CUE_PATH, MENU_AUDIO_CUE_TEXT, MENU_AUDIO_CUE_VOICE
        
        assert MENU_AUDIO_CUE_PATH == "/tmp/menu_confirm.mp3"
        assert MENU_AUDIO_CUE_TEXT == "Confirmed"
        assert MENU_AUDIO_CUE_VOICE == "en-US-AriaNeural"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
