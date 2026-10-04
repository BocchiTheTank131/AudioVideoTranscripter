# Validation — 3 October 2026

Environment: Windows 11, Python 3.12, PySide6 6.11.2, pinned whisper.cpp v1.8.3, Intel Core i7-14700F, AMD Radeon RX 9060 XT with a Vulkan-compatible AMD driver. Actual runtime probes reported about 16 GiB of GPU memory. FFmpeg/ffprobe were already installed on PATH.

## Automated logic checks

`python -m pytest -q`: **48 passed**. No GPU or model download is required for these tests. Exports, user edits, settings persistence, cancellation, metadata, backend availability, model integrity, optional failure isolation and timestamp merging are covered.

## Real source-application flows

Generated a synthetic spoken WAV using Windows SAPI, then encoded an MP4 locally. `scripts/make_test_media.ps1` reproduces the fixtures.

- **CPU:** imported MP4 by a GUI drag/drop event; selected base; downloaded and SHA-256 verified only base; recognized speech with real segment/word timestamps; extracted frequency keywords; edited a cue; exported TXT/SRT/VTT/JSON/CSV/MD/TSV/LRC; verified every format retained the edit.
- **Offline CPU:** repeated with networking blocked. Installed base was reused without another download.
- **AMD Vulkan:** built the native GPU runtime once, probed the RX 9060 XT, then completed the same GUI/export flow offline with real Vulkan inference.
- **Translation/light theme:** ran the actual translate-to-English inference task offline and captured the light interface. The test speech was English; this validates the translation task invocation and output handling, not a foreign-language translation quality benchmark.
- **Long recording:** transcribed 325.89 seconds, crossing the five-minute native chunk boundary. Received 17 real progress callbacks with monotonic processed duration and 49 segments; the final segment ended at 325.25 seconds. The full pipeline took about 5.52 seconds on this sample/GPU; this repetitive synthetic fixture is not a general speed benchmark.
- **Cancellation:** cancelled during real Vulkan inference. The complete cancelled pipeline stopped in about 0.55 seconds, left no job cache files and released its model lease.
- **Unicode paths:** ran real offline Vulkan inference with both the model directory and temporary audio directory named in Thai. UTF-8 word-token grouping is also checked by the test suite.

GUI timers continued firing during inference. Measured maximum timer gaps were approximately 30–80 ms across these small flows. This is measured evidence of responsiveness for the tested fixtures, not a universal worst-case bound.

Reports/screenshots: `cpu/`, `vulkan/`, `cpu-translation/`, `long-report.json`. The spoken word “Vulkan” was misrecognized by base in this sample; the app preserves the model's output rather than substituting invented text.

## Packaged executable

Built a portable directory with PyInstaller. CPU/Vulkan native runtimes are bundled; Whisper models, FFmpeg and optional ML frameworks are excluded. Approximate installed directory size: **170 MB**. FFmpeg is detected from the user's local installation.

Packaged-app QA uses the native Windows Qt platform, the actual application GUI and the same cached base model. Auto selected Vulkan; offline transcription, keyword fallback, cue editing and all eight exports passed, with exit code 0 and no warnings. The process saved `packaged/packaged-report.json` and `packaged/packaged-application.png`.

## Scope of verification

- NVIDIA CUDA and AMD HIP integration/detection/build paths are implemented, but execution on those backends was not verified: this machine has an AMD Vulkan GPU and no CUDA/HIP runtime/toolchain.
- Community-1 and KeyBERT run in isolated, offline optional workers. Their setup/download/configuration paths are implemented, and missing-dependency/model failures preserve core transcription. Full gated Community-1 inference and installed KeyBERT inference were not run in this environment. No gated-model account conditions were accepted on the user's behalf.
- CPU and AMD Vulkan are the shipped and hardware-verified runtimes. Optional GPU builds can be installed without changing application source.
- This is an unsigned portable executable, with no installer. Users on another machine must install FFmpeg/ffprobe or supply them in the documented tools folder.
# Single-file release verification — 2026-10-04

`dist/LocalTranscriber.exe` embeds Python, Qt, FFmpeg/ffprobe and CPU/Vulkan runtimes. Copied only the executable into `C:/Users/Win/Downloads/LocalTranscriber-single-file-test/exe-alone`, launched with that working directory and PATH restricted to Windows System32. Used an isolated application-data directory with a hard-linked cached base model; no project backend directories were configured. Model HTTP requests were blocked by the existing offline QA mode.

Both CPU and automatic Vulkan runs passed media inspection, decoding with embedded FFmpeg, model verification, real inference, offline keywords, transcript editing and all eight exports. Auto detected the AMD Radeon RX 9060 XT. Both produced six segments without warnings. Launch/extraction/full QA took approximately 8.1 seconds each on this machine; this is not a general transcription speed benchmark. Maximum GUI timer gaps were 32 ms (CPU) and 47 ms (Vulkan). Self-extracted runtime directories disappeared after normal exit. The executable's folder still contained only the executable afterward.

Evidence: `singlefile-cpu/packaged-report.json`, `singlefile-auto/packaged-report.json` and corresponding screenshots. The 48-test suite passed. This validates independence from the repository and installed Python/FFmpeg on the test machine; other Windows PCs and CUDA/HIP were not tested in this release.
