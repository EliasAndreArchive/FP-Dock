# Third-party notices

FP Dock application code is MIT licensed. Dependencies and supplied assets retain their own licenses. No third-party library source is modified by this project.

| Component | Version / source | License |
| --- | --- | --- |
| Python | Build interpreter; exact version in `BUILD-INFO.json` | PSF |
| PySide6, Shiboken6, Qt | 6.12.0; https://code.qt.io/cgit/pyside/pyside-setup.git/ and https://code.qt.io/cgit/qt/ | LGPLv3 option and component notices |
| rawpy / LibRaw | rawpy 0.27.1; https://github.com/letmaik/rawpy and https://github.com/LibRaw/LibRaw | MIT / LGPL-2.1-or-later option; bundled notices apply |
| NumPy | 2.5.4; https://github.com/numpy/numpy | BSD and bundled notices |
| tifffile | 2026.10.15; https://github.com/cgohlke/tifffile | BSD-3-Clause |
| imagecodecs | 2026.10.10; https://github.com/cgohlke/imagecodecs | BSD-3-Clause and bundled codec notices |
| FFmpeg | Supplied through the installed Qt wheels | Wheel-specific component notices and LGPL terms |
| PyInstaller bootloader | 6.22.3; https://github.com/pyinstaller/pyinstaller | GPL with bootloader distribution exception |
| Inter | https://github.com/rsms/inter | SIL Open Font License; `licenses/Inter-OFL.txt` |
| Colour reference definitions | https://github.com/colour-science/colour | BSD-3-Clause; `licenses/Colour-BSD.txt` |

Native packages include dependency license directories copied from installed distributions and the application source/build scripts. Qt/PySide6 and its bundled libraries remain separate replaceable files under the PyInstaller runtime directory (`_internal` on Windows/Linux; bundle Frameworks/Resources on macOS). Users may replace compatible LGPL libraries and modify/debug the application as permitted by those licenses; replacing libraries may require re-signing a macOS bundle for local use.

Upstream source is available from the linked projects at the exact dependency versions pinned in `source/requirements.txt` and reported in each package's `BUILD-INFO.json`. Qt's source repositories include component license texts. LGPL/GPL texts and Python/Inter/Colour license notices are also included under `licenses/`.

`source/assets/resolve-dwg-to-rec709.cube` is the user-supplied 65-point Resolve output-transform export bundled for this project. It is not third-party implementation source for Resolve's proprietary tone mapper, and the application's MIT license does not relicense Blackmagic software or other upstream assets. The supplied website mark and visual customization belong to the project owner.
