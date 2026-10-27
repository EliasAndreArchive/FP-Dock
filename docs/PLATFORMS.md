# Platform support and builds

| Feature | Windows | Linux | macOS |
| --- | --- | --- | --- |
| Manual folder selection, DNG previews, verified transfer | Included | Included | Included |
| Newly mounted `CINEMA` discovery | Native notifications + 1 s polling | 1 s mounted-volume polling | 1 s mounted-volume polling |
| Standard media mount locations | Removable / USB-backed drive letters | `/media`, `/run/media`, `/mnt` | `/Volumes` |
| Per-user login startup | Startup-folder shortcut | XDG autostart `.desktop` entry | User LaunchAgent plist |
| GPU path | OpenGL 3.3 core | OpenGL 3.3 core | OpenGL 4.1 core request; Apple OpenGL is deprecated |
| Fallback | CPU RAW decoding | CPU RAW decoding | CPU RAW decoding |

Windows playback has been physically tested with a Sigma fp and an AMD Radeon RX 7800 XT. GitHub-hosted Linux and macOS builds validate the source tests and headless packaged-app/transfer smoke tests. They do not establish real-time playback performance, real card hotplug behavior, or hardware color parity on those platforms.

On Linux, media mounted elsewhere can be opened manually. A desktop without a system tray may not support the hide-to-tray workflow. Polling can miss an unplug/replug cycle completed entirely between polls. macOS privacy permissions can require allowing access to removable volumes. Install the app in its permanent location before enabling login startup.

## Startup files

FP Dock creates only its own per-user startup entry:

- Windows: `%APPDATA%/Microsoft/Windows/Start Menu/Programs/Startup/FP Dock.lnk`
- Linux: `$XDG_CONFIG_HOME/autostart/FP-Dock.desktop`, or `~/.config/autostart/FP-Dock.desktop`
- macOS: `~/Library/LaunchAgents/space.eliasandre.fpdock.plist`

Turning Auto-open off removes that entry. On macOS the LaunchAgent is picked up at the next login; the currently running app already performs the watch. Administrator access is not needed for registration.

## Reproducible native packaging

The build script uses pinned Python dependencies and PyInstaller 6.22.3. The workflow builds independently on Windows x64, Ubuntu 22.04 x64, macOS 14 arm64, and macOS 15 Intel. Packages contain the application, original source, license notices, demo data, dependency/build information, and a checksum for the archive.

PyInstaller builds for its host OS; it is not a cross-compiler. The Linux package targets desktop systems compatible with the build's glibc and Qt requirements, rather than promising compatibility with every Linux distribution. macOS archives preserve bundle symlinks with `ditto`. Linux tar archives preserve executable permissions.

Sources: [GitHub-hosted runner reference](https://docs.github.com/en/actions/reference/runners/github-hosted-runners), [PyInstaller manual](https://pyinstaller.org/en/stable/), [Qt mounted-volume discovery](https://doc.qt.io/qt-6.12/qstorageinfo.html).

Intel macOS uses rawpy 0.25.1 because newer rawpy versions no longer provide an Intel macOS wheel. Other platforms use 0.27.1. The exact dependency version is recorded in each package; behavior is checked against that platform's build.
