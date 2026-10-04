# Local Transcriber 1.1.0 validation

Tested on Windows 11 x64, Intel Core i7-14700F, 32 GB RAM and AMD Radeon RX 9060 XT 16 GB.

- Automated suite: **78 passed**, with no GPU required. Includes quality heuristics, Thai repetition, VAD intervals, merging, edited exports, projects, history, queue state, recommendations, search, cancellation and media metadata.
- English MP4: Base on CPU, genuine progress/word metadata, editable transcript and all eight export formats.
- Local player: seek, play and pause; edited subtitle overlays. Search, idle buttons, two-file serial queue, project saving/opening and persistent settings exercised through the desktop GUI.
- A new application process opens the saved edited project without invoking inference.
- Speech followed by ten seconds of silence and ten seconds of synthetic instrumental audio: local Silero VAD omitted the trailing non-speech section. This fixture is synthetic; it is not a speech accuracy benchmark.
- Real Thai recording: over 22 minutes, Turbo on AMD Vulkan, network calls blocked. Detected Thai independently of the stream's English metadata tag. Private source, transcript, keywords and screenshots are excluded from Git and the release.
- Halfway cancellation on the real long recording: monotonic processed duration; child processes stopped, model lease released and temporary audio removed.
- Optional KeyBERT dependency installation verified in an isolated managed Python environment. No embedding weights downloaded by that check.

The final onefile EXE is copied alone outside the checkout and run with PATH limited to Windows System32. Embedded FFmpeg/ffprobe, CPU and Vulkan inference are exercised. Tests cover first-use Base download, the absence of other Whisper downloads, a second offline cached run, edited exports and project reopening. Runtime extraction directories disappear after normal exit. Release asset SHA-256 is checked against the tested copied EXE and the publicly downloaded release asset.

Final packaged long run: 1,352.469 seconds of audio, 670 cues, detected `th`; 277.656 seconds processing (4.87× realtime), 284.469 seconds including launch/export/exit. Sampled application/child RAM peak 506 MiB; system VRAM allocation peak 4.64 GiB. GUI timer's largest gap was 1.657 seconds; short workflow maximum was 0.187 seconds, so final result rendering/export is not uniformly frame-smooth. A damaged cached Turbo model was rejected before inference and repaired through a fully verified download.

Tested executable: 252,824,622 bytes; SHA-256 `4abbad37270190d030bbe171227de7222218889c296ee785b7bb283f5af4bd49`.

Confidence is an uncalibrated model token statistic and quality warnings are heuristics. VAD is not a general music detector. CUDA/HIP execution is unverified on this AMD/Vulkan machine; full gated pyannote diarization is unverified without its optional local model. True native transcription pause is unsupported; queue pause finishes the current file. Sampled VRAM is system allocation and RAM includes application/child processes. Short batch checks establish functional resource release, not a days-long leak endurance test.
