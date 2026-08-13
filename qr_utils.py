"""
Генерация QR-кодов для wdtt:// ссылок
"""

from __future__ import annotations

import io
import qrcode
from qrcode.image.pil import PilImage


def make_qr_bytes(text: str, border: int = 2) -> bytes:
    """Генерирует QR-код и возвращает PNG в виде bytes"""
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=border,
    )
    qr.add_data(text)
    qr.make(fit=True)

    img: PilImage = qr.make_image(fill_color="black", back_color="white")

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf.read()


def link_short_label(link: str) -> str:
    """Человекочитаемая метка для wdtt:// ссылки."""
    return "WDTT VPN"
