class LazyTranscriptionPdfRenderer:
    """Load ReportLab/fonts only when a call actually has recognized speech."""
    def __init__(self, config):
        self.config = config
        self._renderer = None

    def render_for_recording(self, recording_path, conversation):
        if not any(str(row.get('text') or '').strip() for row in (conversation or [])):
            return None, None
        if self._renderer is None:
            from integrations.transcription.pdf import TranscriptionPdfRenderer
            self._renderer = TranscriptionPdfRenderer(self.config)
        return self._renderer.render_for_recording(recording_path, conversation)
