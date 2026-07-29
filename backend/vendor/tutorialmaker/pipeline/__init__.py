"""Pipeline package: YouTube topic -> captioned .docx tutorial (API-based, no download).

Stages (see app.run_pipeline for orchestration):
    search -> sentiment (YouTube Data API comments) -> transcribe (youtube-transcript-api)
    -> tutorial (LLM) -> frames (yt-dlp stream URL + ffmpeg -ss, weighted timestamps)
    -> captions (VLM) -> docx_builder
"""
