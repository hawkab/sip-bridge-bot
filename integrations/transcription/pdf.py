from __future__ import annotations

import logging
import os
from pathlib import Path
from threading import Lock

from reportlab.lib.colors import HexColor
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer
from xml.sax.saxutils import escape

logger = logging.getLogger(__name__)

_FONT_REGISTRY_LOCK = Lock()
_FONT_NAME = 'TranscriptionDejaVuSans'
_FONT_CANDIDATES = (
    '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
    '/usr/share/fonts/TTF/DejaVuSans.ttf',
    '/usr/share/fonts/dejavu/DejaVuSans.ttf',
)


class TranscriptionPdfRenderer:
    def __init__(self, config):
        self.config = config

    def render_for_recording(self, recording_path: str | Path, transcription_text: str) -> tuple[str | None, str | None]:
        text = (transcription_text or '').strip()
        if not text:
            return None, None

        source_path = Path(recording_path)
        target_dir = Path(self.config.CALL_TRANSCRIBE_ARTIFACTS_DIR)
        target_dir.mkdir(parents=True, exist_ok=True)

        pdf_name = f'{source_path.stem}-transcription.pdf'
        pdf_path = target_dir / pdf_name
        self._build_pdf(pdf_path, source_path.name, text)
        return str(pdf_path), pdf_name

    def _build_pdf(self, pdf_path: Path, recording_file_name: str, transcription_text: str) -> None:
        font_name = _ensure_unicode_font()
        styles = getSampleStyleSheet()
        title_style = ParagraphStyle(
            'TranscriptionTitle',
            parent=styles['Title'],
            fontName=font_name,
            fontSize=16,
            leading=20,
            alignment=TA_LEFT,
            textColor=HexColor('#1f2937'),
            spaceAfter=8,
        )
        meta_style = ParagraphStyle(
            'TranscriptionMeta',
            parent=styles['BodyText'],
            fontName=font_name,
            fontSize=9,
            leading=12,
            textColor=HexColor('#6b7280'),
            spaceAfter=10,
        )
        body_style = ParagraphStyle(
            'TranscriptionBody',
            parent=styles['BodyText'],
            fontName=font_name,
            fontSize=10.5,
            leading=15,
            textColor=HexColor('#111827'),
            spaceAfter=6,
        )

        doc = SimpleDocTemplate(
            str(pdf_path),
            pagesize=A4,
            leftMargin=18 * mm,
            rightMargin=18 * mm,
            topMargin=18 * mm,
            bottomMargin=16 * mm,
            title='Транскрибация звонка',
            author='sip-bridge-bot',
            subject='Транскрибация разговора',
        )

        story = [
            Paragraph('Транскрибация звонка', title_style),
            Paragraph(escape(f'Файл записи: {recording_file_name}'), meta_style),
            Spacer(1, 2 * mm),
        ]

        for line in transcription_text.splitlines():
            value = line.strip()
            if not value:
                continue
            story.append(Paragraph(escape(value), body_style))

        doc.build(story)


def _ensure_unicode_font() -> str:
    with _FONT_REGISTRY_LOCK:
        try:
            pdfmetrics.getFont(_FONT_NAME)
            return _FONT_NAME
        except KeyError:
            pass

        for candidate in _FONT_CANDIDATES:
            if os.path.exists(candidate):
                pdfmetrics.registerFont(TTFont(_FONT_NAME, candidate))
                return _FONT_NAME

    logger.warning('DejaVuSans.ttf not found, fallback to Helvetica; Cyrillic rendering may be broken')
    return 'Helvetica'
