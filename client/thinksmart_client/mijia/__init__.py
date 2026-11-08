"""Xiaomi Mijia devices straight from the Mi cloud: QR login on the screen, a device page, and the
self.home.* voice tools. Login, signing and the MIoT spec handling are ported from MenuBarMijia
(https://gitlab.com/jryang1997/MenuBarMijia, MIT, jryang1997)."""
from .service import MijiaService

__all__ = ['MijiaService']
