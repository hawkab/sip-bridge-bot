from __future__ import annotations

import logging
import os
from pathlib import Path
from threading import Lock

from reportlab.lib import colors
from reportlab.lib.colors import HexColor
from reportlab.lib.enums import TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from xml.sax.saxutils import escape

logger = logging.getLogger(__name__)

_FONT_REGISTRY_LOCK = Lock()
_FONT_NAME = 'TranscriptionDejaVuSans'
_FONT_CANDIDATES = (
    '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
    '/usr/share/fonts/TTF/DejaVuSans.ttf',
    '/usr/share/fonts/dejavu/DejaVuSans.ttf',
)

_BUBBLE_COLORS = {
    'left': HexColor('#f3f4f6'),
    'right': HexColor('#dbeafe'),
}


class TranscriptionPdfRenderer:
    def __init__(self, config):
        self.config = config

    def render_for_recording(self, recording_path: str | Path, conversation: list[dict] | None) -> tuple[str | None, str | None]:
        rows = [row for row in (conversation or []) if str(row.get('text') or '').strip()]
        if not rows:
            return None, None

        source_path = Path(recording_path)
        target_dir = Path(self.config.CALL_TRANSCRIBE_ARTIFACTS_DIR)
        target_dir.mkdir(parents=True, exist_ok=True)

        pdf_name = f'{source_path.stem}-transcription.pdf'
        pdf_path = target_dir / pdf_name
        self._build_pdf(pdf_path, source_path.name, rows)
        return str(pdf_path), pdf_name

    def _build_pdf(self, pdf_path: Path, recording_file_name: str, conversation: list[dict]) -> None:
        font_name = _ensure_unicode_font()
        styles = getSampleStyleSheet()
        title_style = ParagraphStyle(
            'TranscriptionTitle',
            parent=styles['Title'],
            fontName=font_name,
            fontSize=16,
            leading=20,
            alignment=TA_LEFT,
            textColor=HexColor('#111827'),
            spaceAfter=6,
        )
        meta_style = ParagraphStyle(
            'TranscriptionMeta',
            parent=styles['BodyText'],
            fontName=font_name,
            fontSize=9,
            leading=12,
            textColor=HexColor('#6b7280'),
            spaceAfter=4,
        )
        bubble_text_style = ParagraphStyle(
            'TranscriptionBubbleText',
            parent=styles['BodyText'],
            fontName=font_name,
            fontSize=10.5,
            leading=14,
            textColor=HexColor('#111827'),
            spaceAfter=0,
        )
        bubble_meta_left_style = ParagraphStyle(
            'TranscriptionBubbleMetaLeft',
            parent=styles['BodyText'],
            fontName=font_name,
            fontSize=8.5,
            leading=11,
            textColor=HexColor('#4b5563'),
            alignment=TA_LEFT,
            spaceAfter=3,
        )
        bubble_meta_right_style = ParagraphStyle(
            'TranscriptionBubbleMetaRight',
            parent=styles['BodyText'],
            fontName=font_name,
            fontSize=8.5,
            leading=11,
            textColor=HexColor('#334155'),
            alignment=TA_RIGHT,
            spaceAfter=3,
        )
        legend_style = ParagraphStyle(
            'TranscriptionLegend',
            parent=styles['BodyText'],
            fontName=font_name,
            fontSize=8.5,
            leading=11,
            textColor=HexColor('#6b7280'),
            spaceAfter=8,
        )

        doc = SimpleDocTemplate(
            str(pdf_path),
            pagesize=A4,
            leftMargin=16 * mm,
            rightMargin=16 * mm,
            topMargin=16 * mm,
            bottomMargin=14 * mm,
            title='Транскрибация звонка',
            author='sip-bridge-bot',
            subject='Транскрибация разговора',
        )

        story = [
            Paragraph('Транскрибация звонка', title_style),
            Paragraph(escape(f'Файл записи: {recording_file_name}'), meta_style),
            Paragraph('Формат: сообщения выстроены по времени, как переписка в чате.', legend_style),
            Spacer(1, 2 * mm),
        ]

        for row in conversation:
            bubble = self._build_chat_bubble(
                row=row,
                bubble_text_style=bubble_text_style,
                bubble_meta_left_style=bubble_meta_left_style,
                bubble_meta_right_style=bubble_meta_right_style,
            )
            story.append(bubble)
            story.append(Spacer(1, 2.3 * mm))

        doc.build(story)

    def _build_chat_bubble(
        self,
        *,
        row: dict,
        bubble_text_style: ParagraphStyle,
        bubble_meta_left_style: ParagraphStyle,
        bubble_meta_right_style: ParagraphStyle,
    ) -> Table:
        speaker = str(row.get('speaker') or 'SPEAKER')
        channel = str(row.get('channel') or 'left')
        start_hms = str(row.get('start_hms') or '')
        end_hms = str(row.get('end_hms') or '')
        text = escape(str(row.get('text') or '').strip())
        side = 'right' if channel == 'right' else 'left'
        meta_style = bubble_meta_right_style if side == 'right' else bubble_meta_left_style
        align = TA_RIGHT if side == 'right' else TA_LEFT
        meta = escape(f'{speaker}  {start_hms} - {end_hms}'.strip())

        bubble_inner = Table(
            [[Paragraph(meta, meta_style)], [Paragraph(text, bubble_text_style)]],
            colWidths=[118 * mm],
        )
        bubble_inner.setStyle(
            TableStyle(
                [
                    ('BACKGROUND', (0, 0), (-1, -1), _BUBBLE_COLORS[side]),
                    ('BOX', (0, 0), (-1, -1), 0.6, colors.white),
                    ('LEFTPADDING', (0, 0), (-1, -1), 10),
                    ('RIGHTPADDING', (0, 0), (-1, -1), 10),
                    ('TOPPADDING', (0, 0), (-1, -1), 7),
                    ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
                    ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                    ('ALIGN', (0, 0), (-1, -1), 'RIGHT' if side == 'right' else 'LEFT'),
                    ('ROUNDEDCORNERS', [8, 8, 8, 8]),
                ]
            )
        )

        if side == 'right':
            outer = Table([[ '', bubble_inner ]], colWidths=[42 * mm, 118 * mm])
        else:
            outer = Table([[ bubble_inner, '' ]], colWidths=[118 * mm, 42 * mm])

        outer.setStyle(
            TableStyle(
                [
                    ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                    ('ALIGN', (0, 0), (-1, -1), 'RIGHT' if side == 'right' else 'LEFT'),
                    ('LEFTPADDING', (0, 0), (-1, -1), 0),
                    ('RIGHTPADDING', (0, 0), (-1, -1), 0),
                    ('TOPPADDING', (0, 0), (-1, -1), 0),
                    ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
                ]
            )
        )
        return outer


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
