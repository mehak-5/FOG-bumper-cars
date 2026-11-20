"""QR code generation for highlight reel public sharing."""

from __future__ import annotations

import base64
import io

import qrcode
from qrcode.constants import ERROR_CORRECT_M


def generate_qr_png(data: str, box_size: int = 10, border: int = 2) -> bytes:
    """Generate PNG bytes of QR code from text/URL."""
    qr = qrcode.QRCode(
        version=None,
        error_correction=ERROR_CORRECT_M,
        box_size=box_size,
        border=border,
    )
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def generate_qr_data_url(data: str, box_size: int = 10, border: int = 2) -> str:
    """Generate base64 data:image/png;base64,... string from QR code."""
    png_bytes = generate_qr_png(data, box_size=box_size, border=border)
    b64 = base64.b64encode(png_bytes).decode("ascii")
    return f"data:image/png;base64,{b64}"
