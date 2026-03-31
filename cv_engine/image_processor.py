"""E-Ink image processor for 4.2" display (400x300, 1-bit)."""
import logging
import re
from pathlib import Path
from typing import List, Optional, Sequence, Union

import numpy as np

try:
    from PIL import Image
except ImportError:
    Image = None

from config import EINK_WIDTH, EINK_HEIGHT, EINK_IMAGE_SIZE

# CJK font search order — install with: sudo apt install fonts-noto-cjk
_CJK_FONT_PATHS = [
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJKsc-Regular.otf",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/noto/NotoSansCJK-Regular.ttc",
]

logger = logging.getLogger(__name__)


class EInkImageProcessor:
    """Prepares images for 4.2" E-Ink display (400x300, 1-bit)."""

    def __init__(
        self,
        width: int = EINK_WIDTH,
        height: int = EINK_HEIGHT,
        dither: bool = True,
    ):
        self.width = width
        self.height = height
        self.dither = dither
        self._check_pil()

    def _check_pil(self) -> None:
        """Check if PIL is available."""
        if Image is None:
            raise RuntimeError("Pillow not installed. Install with: pip install Pillow")

    def process(self, image_source: Union[str, np.ndarray, "Image.Image"]) -> bytes:
        """Process image for E-Ink display.

        Pipeline:
        1. Load image
        2. Crop to 4:3 aspect ratio
        3. Resize to 400x300
        4. Convert to grayscale
        5. Apply Floyd-Steinberg dithering
        6. Pack to 1-bit (MSB first)

        Args:
            image_source: Image path, numpy array, or PIL Image

        Returns:
            15000 bytes of 1-bit packed image data
        """
        # Load image
        img = self._load_image(image_source)

        # Crop to 4:3 aspect ratio
        img = self._crop_aspect_ratio(img, 4, 3)

        # Resize to target dimensions
        img = img.resize((self.width, self.height), Image.Resampling.LANCZOS)

        # Convert to grayscale
        img = img.convert("L")

        # Apply dithering or simple threshold
        if self.dither:
            img = self._floyd_steinberg_dither(img)
        else:
            img = img.point(lambda x: 0 if x < 128 else 255, mode="1")

        # Convert to 1-bit
        img = img.convert("1")

        # Pack to bytes (MSB first)
        return self._pack_to_bytes(img)

    def _load_image(
        self, source: Union[str, np.ndarray, "Image.Image"]
    ) -> "Image.Image":
        """Load image from various sources."""
        if isinstance(source, str):
            path = Path(source)
            if not path.exists():
                raise FileNotFoundError(f"Image not found: {source}")
            return Image.open(path)
        elif isinstance(source, np.ndarray):
            return Image.fromarray(source)
        elif hasattr(source, "mode"):  # PIL Image
            return source
        else:
            raise TypeError(f"Unsupported image type: {type(source)}")

    def _crop_aspect_ratio(
        self, img: "Image.Image", aspect_w: int, aspect_h: int
    ) -> "Image.Image":
        """Crop image to specified aspect ratio (center crop)."""
        target_ratio = aspect_w / aspect_h
        current_ratio = img.width / img.height

        if current_ratio > target_ratio:
            # Image is too wide, crop horizontally
            new_width = int(img.height * target_ratio)
            left = (img.width - new_width) // 2
            img = img.crop((left, 0, left + new_width, img.height))
        elif current_ratio < target_ratio:
            # Image is too tall, crop vertically
            new_height = int(img.width / target_ratio)
            top = (img.height - new_height) // 2
            img = img.crop((0, top, img.width, top + new_height))

        return img

    def _floyd_steinberg_dither(self, img: "Image.Image") -> "Image.Image":
        """Apply Floyd-Steinberg dithering."""
        # Convert to numpy for processing
        pixels = np.array(img, dtype=np.float32)
        height, width = pixels.shape

        for y in range(height):
            for x in range(width):
                old_pixel = pixels[y, x]
                new_pixel = 0 if old_pixel < 128 else 255
                pixels[y, x] = new_pixel
                error = old_pixel - new_pixel

                # Distribute error to neighboring pixels
                if x + 1 < width:
                    pixels[y, x + 1] += error * 7 / 16
                if y + 1 < height:
                    if x > 0:
                        pixels[y + 1, x - 1] += error * 3 / 16
                    pixels[y + 1, x] += error * 5 / 16
                    if x + 1 < width:
                        pixels[y + 1, x + 1] += error * 1 / 16

        # Clip values and convert back
        pixels = np.clip(pixels, 0, 255).astype(np.uint8)
        return Image.fromarray(pixels, mode="L")

    def _pack_to_bytes(self, img: "Image.Image") -> bytes:
        """Pack 1-bit image to bytes (MSB first, row-major)."""
        # Get pixel data
        pixels = list(img.getdata())

        # Pack 8 pixels per byte
        packed = bytearray()
        for i in range(0, len(pixels), 8):
            byte = 0
            for j in range(8):
                if i + j < len(pixels):
                    # 1 = white, 0 = black (invert for e-ink)
                    if pixels[i + j] == 0:  # Black pixel
                        byte |= 1 << (7 - j)
            packed.append(byte)

        # Verify size
        if len(packed) != EINK_IMAGE_SIZE:
            raise ValueError(
                f"Packed image size mismatch: {len(packed)} != {EINK_IMAGE_SIZE}"
            )

        return bytes(packed)

    def process_text(
        self,
        text: str,
        font_size: int = 24,
        font_path: Optional[str] = None,
        align: str = "center",
    ) -> bytes:
        """Render text to E-Ink image.

        Args:
            text: Text to render
            font_size: Font size in pixels
            font_path: Path to TTF font file (uses default if None)
            align: Text alignment ("left", "center", "right")

        Returns:
            15000 bytes of packed image data
        """
        from PIL import ImageDraw, ImageFont

        # Create white background
        img = Image.new("L", (self.width, self.height), 255)
        draw = ImageDraw.Draw(img)

        # Load font
        try:
            if font_path:
                font = ImageFont.truetype(font_path, font_size)
            else:
                font = ImageFont.load_default()
        except Exception:
            font = ImageFont.load_default()

        # Calculate text position
        lines = text.split("\n")
        line_height = font_size + 4
        total_height = len(lines) * line_height
        y = (self.height - total_height) // 2

        for line in lines:
            bbox = draw.textbbox((0, 0), line, font=font)
            text_width = bbox[2] - bbox[0]

            if align == "center":
                x = (self.width - text_width) // 2
            elif align == "right":
                x = self.width - text_width - 10
            else:
                x = 10

            draw.text((x, y), line, fill=0, font=font)
            y += line_height

        return self.process(img)

    # ------------------------------------------------------------------ #
    #  CJK helpers                                                        #
    # ------------------------------------------------------------------ #

    def _load_cjk_font(self, size: int):
        """Return a TrueType font with CJK support, or PIL default on failure."""
        from PIL import ImageFont
        for path in _CJK_FONT_PATHS:
            if Path(path).exists():
                try:
                    return ImageFont.truetype(path, size)
                except Exception:
                    continue
        logger.warning(
            "No CJK font found — Chinese text will not render. "
            "Install with: sudo apt install fonts-noto-cjk"
        )
        return ImageFont.load_default()

    @staticmethod
    def _clean_text(text: str) -> str:
        """Strip emojis (supplementary-plane chars) and trailing '→' decorators."""
        # Remove supplementary-plane characters (emojis, symbols outside BMP)
        text = re.sub(r"[^\u0000-\uFFFF]", "", text)
        # Remove trailing arrow that was only there as an emoji separator
        text = re.sub(r"\s*→\s*$", "", text)
        return text.strip()

    @staticmethod
    def _wrap_text(text: str, font, draw, max_width: int) -> List[str]:
        """Wrap text character-by-character (works for CJK and Latin)."""
        lines: List[str] = []
        current = ""
        for char in text:
            candidate = current + char
            w = draw.textbbox((0, 0), candidate, font=font)[2]
            if w > max_width and current:
                lines.append(current)
                current = char
            else:
                current = candidate
        if current:
            lines.append(current)
        return lines

    def render_home_menu(
        self,
        entries: Sequence[str] | None = None,
        title: str = "WonderBall Home",
    ) -> bytes:
        """Render deterministic child home menu for boot-time display.

        Expected baseline entries are exactly:
        STEM, Chat, Follow, Call Parent.
        Malformed entry lists are normalized back to this baseline.
        """
        from PIL import Image, ImageDraw

        baseline = ("STEM", "Chat", "Follow", "Call Parent")
        if entries is None:
            normalized = baseline
        else:
            cleaned = tuple(str(item).strip() for item in entries if str(item).strip())
            normalized = cleaned if len(cleaned) == 4 else baseline
            if len(cleaned) != 4:
                logger.warning(
                    "Home menu entries malformed (count=%s), using baseline.",
                    len(cleaned),
                )

        img = Image.new("L", (self.width, self.height), 255)
        draw = ImageDraw.Draw(img)

        font_header = self._load_cjk_font(20)
        font_item = self._load_cjk_font(22)

        # Header bar
        header_height = 38
        draw.rectangle([0, 0, self.width - 1, header_height], fill=0)
        draw.text((12, 9), title, fill=255, font=font_header)

        # Menu rows (4 deterministic rows)
        row_top = header_height + 14
        row_height = (self.height - row_top - 10) // 4
        for index, label in enumerate(normalized):
            y0 = row_top + index * row_height
            y1 = y0 + row_height

            if index > 0:
                draw.line([(0, y0), (self.width - 1, y0)], fill=0, width=1)

            bullet_cx = 22
            bullet_cy = y0 + row_height // 2
            draw.ellipse(
                [bullet_cx - 7, bullet_cy - 7, bullet_cx + 7, bullet_cy + 7],
                outline=0,
                width=2,
            )

            text_y = y0 + max(0, (row_height - 22) // 2)
            draw.text((40, text_y), label, fill=0, font=font_item)

            # Keep final border deterministic
            if index == 3:
                draw.line([(0, y1), (self.width - 1, y1)], fill=0, width=1)

        return self._pack_to_bytes(img.convert("1"))

    def render_lesson(
        self,
        question: str,
        options: List[str],
        title: str = "WonderBall STEM",
    ) -> bytes:
        """Render a structured MCQ lesson card for the 400×300 e-ink display.

        Layout
        ──────
          ┌─────────────────────────────────────┐  Y=0
          │■ WonderBall STEM                    │  Header  30 px (black bg)
          ├─────────────────────────────────────┤  Y=30
          │  Question text (word-wrapped)       │  Question 86 px
          ├─────────────────────────────────────┤  Y=116
          │  ●A  option one                     │  ╮
          │  ●B  option two                     │  ║  4 × 46 px = 184 px
          │  ●C  option three                   │  ║
          │  ●D  option four                    │  ╯
          └─────────────────────────────────────┘  Y=300

        Returns 15 000 bytes of 1-bit packed data ready for the ESP32.
        """
        from PIL import Image, ImageDraw

        img = Image.new("L", (self.width, self.height), 255)
        draw = ImageDraw.Draw(img)

        PAD = 10          # horizontal margin
        HDR_H = 30        # header bar height
        Q_TOP = HDR_H + 6
        Q_LINE_H = 26     # px per wrapped question line
        Q_LINES = 3       # max lines reserved for question
        Q_AREA_H = Q_LINES * Q_LINE_H   # = 78 px
        DIV_Y = HDR_H + Q_AREA_H + 8   # = 116
        OPT_AREA = self.height - DIV_Y  # = 184 px
        OPT_H = OPT_AREA // 4           # = 46 px
        CIRCLE_R = 11
        LABELS = ["A", "B", "C", "D"]

        font_hdr = self._load_cjk_font(16)
        font_q   = self._load_cjk_font(22)
        font_opt = self._load_cjk_font(18)
        font_lbl = self._load_cjk_font(15)

        # ── Header ───────────────────────────────────────────────────────
        draw.rectangle([0, 0, self.width - 1, HDR_H - 1], fill=0)
        draw.text((PAD, (HDR_H - 18) // 2), title, fill=255, font=font_hdr)

        # ── Question ─────────────────────────────────────────────────────
        q_clean = self._clean_text(question)
        q_lines = self._wrap_text(q_clean, font_q, draw, self.width - 2 * PAD)
        y = Q_TOP
        for line in q_lines[:Q_LINES]:
            draw.text((PAD, y), line, fill=0, font=font_q)
            y += Q_LINE_H

        # ── Divider ───────────────────────────────────────────────────────
        draw.line([(0, DIV_Y), (self.width - 1, DIV_Y)], fill=0, width=2)

        # ── Options ──────────────────────────────────────────────────────
        for i, (lbl, raw_opt) in enumerate(zip(LABELS, options[:4])):
            opt_text = self._clean_text(raw_opt)
            y0 = DIV_Y + 2 + i * OPT_H

            # Row separator (between options)
            if i > 0:
                draw.line([(0, y0 - 1), (self.width - 1, y0 - 1)], fill=0, width=1)

            # Filled circle label
            cy = y0 + OPT_H // 2
            cx = PAD + CIRCLE_R
            draw.ellipse(
                [cx - CIRCLE_R, cy - CIRCLE_R, cx + CIRCLE_R, cy + CIRCLE_R],
                fill=0,
            )
            lw = draw.textbbox((0, 0), lbl, font=font_lbl)[2]
            lh = draw.textbbox((0, 0), lbl, font=font_lbl)[3]
            draw.text((cx - lw // 2, cy - lh // 2 - 1), lbl, fill=255, font=font_lbl)

            # Option text (single line; truncated if too long)
            tx = PAD + CIRCLE_R * 2 + 8
            ty = y0 + (OPT_H - 20) // 2
            opt_lines = self._wrap_text(opt_text, font_opt, draw, self.width - tx - PAD)
            if opt_lines:
                draw.text((tx, ty), opt_lines[0], fill=0, font=font_opt)

        # Convert directly to 1-bit (simple threshold, no dithering — keeps text sharp)
        img_1bit = img.convert("1")
        return self._pack_to_bytes(img_1bit)

    def create_pattern(self, pattern_type: str = "checkerboard") -> bytes:
        """Create test pattern for E-Ink display.

        Args:
            pattern_type: "checkerboard", "gradient", "border"

        Returns:
            15000 bytes of packed image data
        """
        img = Image.new("L", (self.width, self.height), 255)
        pixels = img.load()

        if pattern_type == "checkerboard":
            block_size = 20
            for y in range(self.height):
                for x in range(self.width):
                    if ((x // block_size) + (y // block_size)) % 2:
                        pixels[x, y] = 0

        elif pattern_type == "gradient":
            for y in range(self.height):
                for x in range(self.width):
                    pixels[x, y] = int(255 * x / self.width)

        elif pattern_type == "border":
            for y in range(self.height):
                for x in range(self.width):
                    if x < 5 or x >= self.width - 5 or y < 5 or y >= self.height - 5:
                        pixels[x, y] = 0

        return self.process(img)
