# FP Dock preview builds

Native Windows x64, Linux x64, Apple-silicon macOS, and Intel macOS packages built from the commit identified by this release.

- Browse CinemaDNG clips and preview them with the bundled Resolve output LUT.
- Apply visual-only pre-LUT highlight reduction, post-LUT ETTR, and shadow lift.
- Choose clips and copy original files with independent SHA-256 destination verification.
- Use date-based session folders, resume interrupted transfers, and keep checks in `Extras`.
- Switch between light/dark themes and Quality/Balanced/Fast preview resolution.
- Detect newly mounted `CINEMA` media and optionally run at login.

Download the archive for your OS/architecture and its `.sha256` file. Extract the complete archive. Windows needs `_internal` beside `FPDock.exe`; macOS users should move `FPDock.app` to Applications before enabling Auto-open.

These are prerelease builds, not signed/notarized public-store installers. Windows camera/GPU playback has been physically tested; Linux/macOS build and transfer checks run in CI, with hardware validation still needed. See the README and platform notes for details. All image adjustments are preview-only and do not modify the RAW footage.
