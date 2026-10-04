"""Explicit development-time build. Never called on application startup."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
PIN = '2eeeba56e9edd762b4b38467bab96c2517163158'  # v1.8.3


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('backend', choices=['cpu', 'vulkan', 'cuda', 'hip'])
    parser.add_argument('--amdgpu-targets', default='')
    args = parser.parse_args()
    source = ROOT / 'vendor' / 'whisper.cpp'
    if not source.exists():
        subprocess.run(['git', 'clone', '--depth', '1', '--branch', 'v1.8.3',
                        'https://github.com/ggml-org/whisper.cpp.git', str(source)], check=True)
    revision = subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD'], text=True).strip()
    if revision != PIN:
        raise SystemExit(f'Expected pinned whisper.cpp {PIN}; found {revision}. Use a separate checkout for upgrades.')
    build = ROOT / 'build' / f'bridge-{args.backend}'
    configure = ['cmake', '-S', str(ROOT / 'native'), '-B', str(build),
                 f'-DLOCAL_BACKEND={args.backend}', '-DGGML_CUDA=OFF', '-DGGML_VULKAN=OFF', '-DGGML_HIP=OFF']
    build_env = os.environ.copy()
    if os.name == 'nt':
        # Work from an ordinary terminal, including when VS is absent from PATH.
        candidates = list(Path('C:/Program Files/Microsoft Visual Studio').glob('*/ */VC/Auxiliary/Build/vcvars64.bat'))
        candidates += list(Path('C:/Program Files/Microsoft Visual Studio').glob('*/*/VC/Auxiliary/Build/vcvars64.bat'))
        if not candidates:
            raise SystemExit('Install Visual Studio C++ Build Tools with a Windows SDK first.')
        vcvars = sorted(candidates)[-1]
        output = subprocess.check_output(f'cmd /d /c call "{vcvars}" >nul && set', text=True)
        build_env.update(dict(line.split('=', 1) for line in output.splitlines() if '=' in line and not line.startswith('=')))
        vs = vcvars.parents[3]
        cmake_dir = vs / 'Common7/IDE/CommonExtensions/Microsoft/CMake/CMake/bin'
        ninja_dir = vs / 'Common7/IDE/CommonExtensions/Microsoft/CMake/Ninja'
        build_env['PATH'] = str(cmake_dir) + os.pathsep + str(ninja_dir) + os.pathsep + build_env.get('Path', build_env.get('PATH', ''))
        configure[0] = str(cmake_dir / 'cmake.exe') if (cmake_dir / 'cmake.exe').exists() else 'cmake'
        configure += ['-G', 'Ninja', '-DCMAKE_BUILD_TYPE=Release']
    if args.backend != 'cpu':
        configure += [f'-DGGML_{args.backend.upper()}=ON']
    if args.amdgpu_targets:
        configure += [f'-DAMDGPU_TARGETS={args.amdgpu_targets}']
    subprocess.run(configure, check=True, env=build_env)
    subprocess.run([configure[0], '--build', str(build), '--config', 'Release', '--parallel', str(min(os.cpu_count() or 4, 12))], check=True, env=build_env)
    target = ROOT / 'backends' / args.backend
    subprocess.run([configure[0], '--install', str(build), '--config', 'Release', '--prefix', str(target)], check=True, env=build_env)
    # Some accelerator dependencies may be DLLs despite the static ggml build.
    for dll in build.rglob('*.dll'):
        shutil.copy2(dll, target / dll.name)
    shutil.copy2(source / 'LICENSE', target / 'whisper-LICENSE.txt')
    print(f'Backend ready: {target}')


if __name__ == '__main__':
    main()
