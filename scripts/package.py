"""Build a movable desktop package; model weights stay in application data."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import argparse

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--bundle-ffmpeg', action='store_true', help='Only use with appropriate license notices and corresponding source distribution')
    parser.add_argument('--console', action='store_true', help='Diagnostic console build')
    parser.add_argument('--onefile', action='store_true', help='Embed runtimes in one self-extracting executable')
    options = parser.parse_args()
    name = 'LocalTranscriberDebug' if options.console else 'LocalTranscriber'
    args = [sys.executable, '-m', 'PyInstaller.utils.cliutils.makespec', '--console' if options.console else '--windowed', '--onefile' if options.onefile else '--onedir',
            '--name', name, '--paths', str(ROOT / 'src'),
            '--add-data', f'{ROOT / "src/transcription/catalog.json"}{os.pathsep}transcription',
            '--add-data', f'{ROOT / "src/transcription/languages.json"}{os.pathsep}transcription',
            '--add-data', f'{ROOT / "assets"}{os.pathsep}assets',
            '--exclude-module', 'torch', '--exclude-module', 'tensorflow', '--exclude-module', 'transformers',
            '--exclude-module', 'PySide6.QtWebEngineCore', '--exclude-module', 'PySide6.QtWebEngineWidgets',
            '--exclude-module', 'PySide6.QtQml', '--exclude-module', 'PySide6.QtQuick',
            '--exclude-module', 'pytest']
    for backend_name in ('cpu', 'vulkan', 'cuda', 'hip'):
        source = ROOT / 'backends' / backend_name
        if source.exists():
            # Only runtime files; CMake development libraries are not shipped.
            staging = ROOT / 'build' / 'runtime' / backend_name
            staging.mkdir(parents=True, exist_ok=True)
            for path in source.iterdir():
                if path.is_file() and path.suffix in ('.exe', '.dll', '.so', '.dylib', '.txt'):
                    shutil.copy2(path, staging / path.name)
            args += ['--add-data', f'{staging}{os.pathsep}backends/{backend_name}']
    tools = ROOT / 'build' / 'runtime' / 'tools'
    tools.mkdir(parents=True, exist_ok=True)
    if options.bundle_ffmpeg:
        for tool_name in ('ffmpeg', 'ffprobe'):
            source = shutil.which(tool_name)
            if not source:
                raise SystemExit(f'{tool_name} must be installed before bundling it.')
            source = Path(source).resolve()
            shutil.copy2(source, tools / source.name)
            for notice in ('LICENSE', 'LICENSE.txt', 'README.txt'):
                candidate = source.parent.parent / notice
                if candidate.is_file():
                    shutil.copy2(candidate, tools / ('FFmpeg-' + notice))
        args += ['--add-data', f'{tools}{os.pathsep}tools']
    for notice in ('README.md', 'THIRD_PARTY_NOTICES.md'):
        args += ['--add-data', f'{ROOT / notice}{os.pathsep}.']
    # Source of optional subprocess modules, runnable in an external Python env.
    for folder in ('diarization', 'keywords'):
        args += ['--add-data', f'{ROOT / "src" / folder}{os.pathsep}src/{folder}']
    args += [str(ROOT / 'src/app.py')]
    subprocess.run(args, cwd=ROOT, check=True)
    # Filter before archiving: deleting files after a build cannot repair onefile.
    spec = ROOT / (name + '.spec')
    filtering = """
from pathlib import PurePath
def keep_runtime(entry):
    name = PurePath(entry[0]).name.lower()
    return name not in ('icuuc.dll', 'icuin.dll', 'qt6pdf.dll', 'qpdf.dll') and not (name.startswith('icudt') and name.endswith('.dll'))
a.binaries = [entry for entry in a.binaries if keep_runtime(entry)]
a.datas = [entry for entry in a.datas if keep_runtime(entry)]
"""
    if os.name == 'nt':
        spec.write_text(spec.read_text('utf-8').replace('pyz = PYZ(a.pure)', filtering + '\npyz = PYZ(a.pure)'), 'utf-8')
    subprocess.run([sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', str(spec)], cwd=ROOT, check=True)
    if options.onefile:
        print(f'Single executable: {ROOT / "dist" / (name + ".exe")}')
        return
    destination = ROOT / 'dist' / name
    if os.name == 'nt':
        # Qt's optional PDF image plugin can pull a second ICU implementation
        # under the same DLL name as the Windows ICU API shim required by QtCore.
        # This app does not render PDFs. Exclude the plugin and its ICU dependency
        # so Windows 11 resolves its own ICU APIs, just as source execution does.
        internal = (destination / '_internal').resolve()
        unwanted = [internal / 'icuuc.dll', internal / 'icuin.dll', internal / 'PySide6/Qt6Pdf.dll',
                    internal / 'PySide6/plugins/imageformats/qpdf.dll']
        unwanted += list(internal.glob('icudt*.dll'))
        for path in unwanted:
            if path.resolve().is_relative_to(internal):
                path.unlink(missing_ok=True)
    shutil.copy2(ROOT / 'README.md', destination / 'README.md')
    shutil.copy2(ROOT / 'THIRD_PARTY_NOTICES.md', destination / 'THIRD_PARTY_NOTICES.md')
    # Include documentation screenshots/reports, never the speech test media.
    for relative in ('VALIDATION.md', 'cpu/application.png', 'cpu/models.png', 'cpu/report.json',
                     'vulkan/application.png', 'vulkan/report.json', 'long-report.json'):
        source = ROOT / 'verification' / relative
        if source.is_file():
            target = destination / 'verification' / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    print(f'Portable application: {destination}')


if __name__ == '__main__':
    main()
