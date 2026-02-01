"""E-Ink image processor for 4.2" display (400x300, 1-bit)."""
import logging
from pathlib import Path
from typing import Optional, Union

import numpy as np

try:
    from PIL import Image
except ImportError:
    Image = None

from config import EINK_WIDTH, EINK_HEIGHT, EINK_IMAGE_SIZE

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
