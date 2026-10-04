# Third-party software

The application is built using Python (PSF license), PySide6/Qt (LGPLv3/GPL/commercial options), psutil (BSD), platformdirs (MIT), requests (Apache-2.0), PyInstaller (GPL with its bootloader distribution exception), and whisper.cpp/ggml (MIT). Optional features use pyannote.audio (MIT), Community-1 model weights (CC-BY-4.0 and their access conditions), KeyBERT (MIT), sentence-transformers (Apache-2.0), and all-MiniLM-L6-v2 weights (Apache-2.0).

Consult each upstream project's license for exact conditions and dependency notices. whisper.cpp's license is copied beside each built runtime. The application source and native bridge are supplied here; native builds are pinned and reproducible. A directory-based PyInstaller package keeps Qt shared libraries replaceable. Redistributors must include required Qt/Python/package license texts and allow the applicable library replacement/relinking rights. PyInstaller packages dependency license metadata where available, but redistributors should audit it before public distribution.

Sources:

- [Python](https://www.python.org/psf/license/)
- [Qt/PySide licensing](https://doc.qt.io/qtforpython-6/licenses.html)
- [whisper.cpp MIT license](https://github.com/ggml-org/whisper.cpp/blob/v1.8.3/LICENSE)
- [psutil](https://github.com/giampaolo/psutil/blob/master/LICENSE)
- [PyInstaller](https://pyinstaller.org/en/stable/license.html)
- [Community-1](https://huggingface.co/pyannote/speaker-diarization-community-1)
- [MiniLM embedding model](https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2)

The single-file build (`build.bat`) embeds **FFmpeg/ffprobe 9.0.2-full_build-www.gyan.dev**, GPLv3, as separate child executables. Their LICENSE and README.txt, including configuration and source revision, are embedded under `tools/`. Source: [946fcce07b](https://github.com/FFmpeg/FFmpeg/commit/946fcce07b); binary provider: [Gyan builds](https://www.gyan.dev/ffmpeg/builds/). The tools are invoked through subprocess pipes, not linked into the application. Redistributors must supply the exact corresponding source, including dependencies and build materials, required by their FFmpeg build. See [FFmpeg legal information](https://ffmpeg.org/legal.html). CUDA/HIP/Vulkan runtime distribution follows the respective vendor licenses; the Windows graphics driver supplies the Vulkan loader.

The onefile package extracts shared Qt libraries at launch. Complete source and packaging scripts allow rebuilding with replacement libraries; a directory build remains available for direct replacement. This project imposes no restriction on reverse engineering for debugging library modifications.
