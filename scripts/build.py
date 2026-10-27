"""Build on the target OS: Windows x64, Linux x64, or macOS arm64/x64."""
from pathlib import Path
import hashlib
import importlib.metadata
import json
import os
import platform
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    os.chdir(ROOT)
    system = platform.system()
    arch = 'arm64' if platform.machine().lower() in ('arm64', 'aarch64') else 'x64'
    names = {'Windows': 'Windows', 'Linux': 'Linux', 'Darwin': 'macOS'}
    name = f'FP-Dock-{names[system]}-{arch}'
    command = [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--onedir',
               '--name', 'FPDock', '--distpath', 'dist', '--workpath', 'build/pyinstaller',
               '--specpath', 'build', '--add-data', str(ROOT/'source/assets')+os.pathsep+'assets',
               '--collect-all', 'rawpy', '--collect-all', 'imagecodecs',
               '--exclude-module', 'av', '--exclude-module', 'tkinter']
    if system != 'Linux':
        command += ['--windowed']
    if system == 'Windows':
        command += ['--icon', str(ROOT/'source/app.ico')]
    if system == 'Darwin':
        command += ['--osx-bundle-identifier', 'space.eliasandre.fpdock']
    command += [str(ROOT/'source/ingest.py')]
    subprocess.run(command, check=True)
    # Avoid an unrelated ICU DLL collected from a codec wheel overriding Qt's DLL.
    if system == 'Windows':
        for dll in ('icuuc.dll', 'icudt78.dll'):
            (ROOT/'dist/FPDock/_internal'/dll).unlink(missing_ok=True)
    output = ROOT/'dist'/name
    output.mkdir(exist_ok=False)
    if system == 'Darwin':
        shutil.move(str(ROOT/'dist/FPDock.app'), output/'FPDock.app')
        executable = output/'FPDock.app/Contents/MacOS/FPDock'
    else:
        bundle = ROOT/'dist/FPDock'
        for item in bundle.iterdir():
            shutil.move(str(item), output/item.name)
        executable = output/('FPDock.exe' if system == 'Windows' else 'FPDock')
    for item in ('README.md', 'LICENSE', 'THIRD-PARTY-NOTICES.md', 'requirements.txt', 'requirements-build.txt'):
        shutil.copy2(ROOT/item, output/item)
    for folder in ('source', 'scripts', 'docs', 'licenses', 'Demo'):
        if (ROOT/folder).exists():
            shutil.copytree(ROOT/folder, output/folder,
                            ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '*.egg-info'))
    versions = {}
    for package in ('PySide6-Essentials', 'PySide6-Addons', 'shiboken6', 'rawpy', 'numpy', 'tifffile', 'imagecodecs', 'pyinstaller'):
        distribution = importlib.metadata.distribution(package)
        versions[package] = distribution.version
        metadata = Path(distribution._path)
        if (metadata/'licenses').exists():
            shutil.copytree(metadata/'licenses', output/'licenses'/package, dirs_exist_ok=True)
    report = {'os': system, 'architecture': arch, 'python': platform.python_version(),
              'commit': os.environ.get('GITHUB_SHA', 'local'), 'dependencies': versions,
              'executable': str(executable.relative_to(output))}
    (output/'BUILD-INFO.json').write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    artifacts = ROOT/'artifacts'
    artifacts.mkdir(exist_ok=True)
    if system == 'Windows':
        archive = Path(shutil.make_archive(str(artifacts/name), 'zip', output.parent, output.name))
    elif system == 'Darwin':
        archive = artifacts/(name+'.zip')
        subprocess.run(['ditto', '-c', '-k', '--sequesterRsrc', '--keepParent', str(output), str(archive)], check=True)
    else:
        archive = Path(shutil.make_archive(str(artifacts/name), 'gztar', output.parent, output.name))
    digest = hashlib.file_digest(archive.open('rb'), 'sha256').hexdigest()
    archive.with_name(archive.name+'.sha256').write_text(digest+'  '+archive.name+'\n', encoding='utf-8')
    (ROOT/'build/bundle.json').write_text(json.dumps({'root': str(output), 'executable': str(executable)}), encoding='utf-8')
    print(archive)


if __name__ == '__main__':
    main()
