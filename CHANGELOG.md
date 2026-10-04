# Changelog

## v1.1.0 — 2026-10-04

- Conservative hallucination/repetition flags and genuine token confidence metadata; optional retry/cleanup preserves raw cues.
- Optional local Silero VAD, downloaded only through explicit setup.
- Live-follow that respects manual scrolling, previous/next search, shortcuts, clearer detected-language labels and idle job controls.
- Local media playback, timestamp seeking and preview of edited subtitles.
- Hardware-aware presets and advisory model recommendations.
- Short-cue merging with original/cleaned export selection and preserved edits.
- Local project files, metadata-only benchmark history and a serial batch queue.
- Model verification status, folder access, managed optional dependency installation, diagnostics and concise errors.
- Versioned standalone Windows executable with bundled FFmpeg and CPU/Vulkan runtimes.

## v1.0.0 — 2026-10-04

- Local video/audio transcription with whisper.cpp and a PySide6 desktop interface.
- Base model by default; verified downloads and permanent on-demand model cache.
- CPU and AMD/NVIDIA Vulkan acceleration included in the Windows x64 build.
- Live progress, cancellation, resource monitoring and editable transcripts.
- TXT, SRT, VTT, JSON, CSV, Markdown, TSV and LRC export.
- Speech translation to English and optional local diarization/AI keywords.
- Single executable embeds Python, Qt, FFmpeg and native inference runtimes; runs independently of the source directory.
- Windows 11 x64/AVX2 target. CUDA/HIP and advanced ML features require optional setup.
