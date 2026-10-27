"""Read-only source inventory, resumable copying, and SHA-256 verification."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

APP_ID = "FieldNotes Ingest"
CHUNK = 4 * 1024 * 1024
RESERVE = 64 * 1024 * 1024


class TransferError(Exception):
    pass


class TransferCancelled(TransferError):
    pass


@dataclass(frozen=True)
class Entry:
    relative: str
    size: int
    mtime_ns: int


@dataclass(frozen=True)
class Inventory:
    source: Path
    files: tuple[Entry, ...]
    directories: tuple[str, ...]
    fingerprint: str
    device: int
    selection: dict | None = None

    @property
    def total_bytes(self):
        return sum(e.size for e in self.files)


def check_cancel(cancel):
    if cancel is not None and cancel.is_set():
        raise TransferCancelled("Transfer cancelled. Verified copies are kept; the card is unchanged.")


def is_link(path: Path):
    info = path.lstat()
    return path.is_symlink() or bool(getattr(info, "st_file_attributes", 0) & 0x400)


def inventory_folder(source, cancel=None):
    source = Path(source).absolute()
    if is_link(source):
        raise TransferError("Choose the actual CINEMA folder, not a link or junction.")
    source = source.resolve(strict=True)
    if not source.is_dir():
        raise TransferError("The source folder is unavailable.")
    device=source.stat().st_dev
    files, directories = [], []
    def walk(folder):
        check_cancel(cancel)
        with os.scandir(folder) as scan:
            children = sorted(scan, key=lambda e: e.name.casefold())
        for child in children:
            check_cancel(cancel)
            path = Path(child.path)
            if is_link(path):
                raise TransferError(f"Source contains a link or junction: {path}. No complete-transfer claim can be made.")
            relative = path.relative_to(source).as_posix()
            info = child.stat(follow_symlinks=False)
            if stat.S_ISDIR(info.st_mode):
                directories.append(relative)
                walk(path)
            elif stat.S_ISREG(info.st_mode):
                files.append(Entry(relative, info.st_size, info.st_mtime_ns))
            else:
                raise TransferError(f"Unsupported source entry: {path}")
    walk(source)
    encoded = json.dumps({"files": [(f.relative, f.size, f.mtime_ns) for f in files],
                          "directories": directories}, ensure_ascii=False, separators=(",", ":")).encode()
    if source.stat().st_dev!=device:
        raise TransferError("Source card changed while reading its inventory. Refresh the source.")
    return Inventory(source, tuple(files), tuple(directories), hashlib.sha256(encoded).hexdigest(),device)


VIDEO_FILES={".mov",".mp4",".m4v",".mxf",".avi",".mkv",".mts",".m2ts"}


def select_inventory(inventory,clips,selected_keys):
    selected_keys=set(selected_keys)
    selected=[c for c in clips if c.key in selected_keys]
    if not selected:
        raise TransferError("Select at least one clip to transfer.")
    if len(selected)==len(clips):
        return inventory
    files=set()
    sequences=[]
    folders=set()
    selected_audio=set()
    for clip in selected:
        folders.add(clip.folder.relative_to(inventory.source).as_posix())
        first=clip.frames[0]
        match=re.match(r"^(.*?)(\d+)$",first.stem)
        if first.suffix.lower()==".dng" and match:
            sequences.append([clip.folder.relative_to(inventory.source).as_posix(),match[1]])
        else:
            files.update(p.relative_to(inventory.source).as_posix() for p in clip.frames)
        if clip.audio:
            selected_audio.add(clip.audio.relative_to(inventory.source).as_posix())
    other_audio={c.audio.relative_to(inventory.source).as_posix() for c in clips if c.audio}-selected_audio
    files.update(selected_audio)
    spec={"clips":[c.frames[0].relative_to(inventory.source).as_posix() for c in selected],
          "files":sorted(files),"raw_sequences":sequences,"folders":sorted(folders),"other_audio":sorted(other_audio)}
    return scope_inventory(inventory,spec)


def scope_inventory(inventory,spec):
    if spec is None:
        return inventory
    explicit=set(spec["files"])
    sequences={tuple(pair) for pair in spec["raw_sequences"]}
    folders={PurePosixPath(p) for p in spec["folders"]}
    ancestors={p for folder in folders for p in (folder,*folder.parents)}
    other_audio=set(spec["other_audio"])
    def support_folder(folder):
        return folder in ancestors or any(p in folders for p in (folder,*folder.parents))
    def include(entry):
        rel=entry.relative
        p=PurePosixPath(rel)
        if rel in explicit:
            return True
        if p.suffix.lower()==".dng":
            match=re.match(r"^(.*?)(\d+)$",p.stem)
            return bool(match and (p.parent.as_posix(),match[1]) in sequences)
        if p.suffix.lower() in VIDEO_FILES or rel in other_audio:
            return False
        return support_folder(p.parent)
    files=tuple(e for e in inventory.files if include(e))
    directories=tuple(d for d in inventory.directories if support_folder(PurePosixPath(d)))
    data={"files":[(e.relative,e.size,e.mtime_ns) for e in files],"directories":directories,"selection":spec}
    fingerprint=hashlib.sha256(json.dumps(data,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    return Inventory(inventory.source,files,directories,fingerprint,inventory.device,spec)


def _assert_entry(path, entry, device=None):
    if is_link(path):
        raise TransferError(f"A file became a link during transfer: {entry.relative}")
    info = path.stat()
    if device is not None and info.st_dev!=device:
        raise TransferError("Source card was replaced during transfer. Refresh the source.")
    if not stat.S_ISREG(info.st_mode) or (info.st_size, info.st_mtime_ns) != (entry.size, entry.mtime_ns):
        raise TransferError(f"Source changed during transfer: {entry.relative}. Stop recording and try again.")


def hash_file(path, cancel=None, progress=None):
    if is_link(path):
        raise TransferError(f"Refusing to read a link: {path}")
    before = path.stat()
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while True:
            check_cancel(cancel)
            data = stream.read(CHUNK)
            if not data:
                break
            digest.update(data)
            size += len(data)
            if progress:
                progress(len(data))
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns) or size != before.st_size:
        raise TransferError(f"File changed while being read: {path}")
    return digest.hexdigest(), size


def _json_write(path, payload):
    temp = path.with_suffix(".json.tmp")
    if path.is_symlink() or temp.is_symlink():
        raise TransferError("Transfer metadata path was replaced by a link.")
    with temp.open("w", encoding="utf-8") as stream:
        json.dump(payload, stream, indent=2, ensure_ascii=False)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, path)


class SessionLock:
    """OS releases the lock on process exit, including a crash."""
    def __init__(self, path):
        self.path = path
        self.stream = None

    def __enter__(self):
        if (self.path.exists() and is_link(self.path)) or self.path.is_symlink():
            raise TransferError("Invalid transfer lock path.")
        self.stream = self.path.open("a+b")
        self.stream.seek(0, 2)
        if self.stream.tell() == 0:
            self.stream.write(b"\0")
            self.stream.flush()
        self.stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.stream.close()
            raise TransferError("Another transfer is already using this destination session.") from exc
        return self

    def __exit__(self, *_):
        self.stream.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(self.stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(self.stream, fcntl.LOCK_UN)
        self.stream.close()


def _inside(path, root):
    return path == root or root in path.parents


def destination_check(source, destination, allow_same_volume=False):
    source = Path(source).resolve(strict=True)
    destination = Path(destination).resolve(strict=True)
    if not destination.is_dir():
        raise TransferError("Choose an existing destination folder on your PC or SSD.")
    if _inside(destination, source):
        raise TransferError("The destination cannot be inside the source CINEMA folder.")
    if not allow_same_volume and source.stat().st_dev == destination.stat().st_dev:
        raise TransferError("Choose a different drive from the source card, so the copy is actually off the card.")
    return destination


def _session_for(inventory, destination,folder_name=None):
    # New sessions place clips directly under a Berlin-local date/time folder.
    for session in sorted(destination.iterdir(), reverse=True):
        if not re.match(r"^\d{4}-\d{2}-\d{2}",session.name):
            continue
        if not session.is_dir() or is_link(session):
            continue
        for extras in session.glob("Extras*"):
            if not extras.is_dir() or is_link(extras):
                continue
            manifest=extras/"transfer.json"
            try:
                if is_link(manifest):
                    continue
                data=json.loads(manifest.read_text(encoding="utf-8"))
                if data.get("app")==APP_ID and data.get("version")==2 and data.get("fingerprint")==inventory.fingerprint and (folder_name is None or data.get('folder_name')==folder_name):
                    return session,extras.name
            except (OSError,ValueError,TypeError):
                continue
    from PySide6.QtCore import QDateTime,QTimeZone
    stamp=QDateTime.currentDateTimeUtc().toTimeZone(QTimeZone(b"Europe/Berlin")).toString("yyyy-MM-dd_HH-mm-ss")
    if folder_name:
        from export_naming import safe_label
        if safe_label(folder_name,160)!=folder_name or not re.match(r'^\d{4}-\d{2}-\d{2}',folder_name):
            raise TransferError('Invalid export folder name.')
        stamp=folder_name
    suffix=1
    while True:
        session=destination/(stamp if suffix==1 else stamp+(' ('+str(suffix)+')' if folder_name else '-'+str(suffix)))
        try:
            session.mkdir()
            break
        except FileExistsError:
            suffix+=1
    occupied={Path(e.relative).parts[0].casefold() for e in inventory.files}
    occupied.update(Path(d).parts[0].casefold() for d in inventory.directories)
    extras_name="Extras"
    suffix=1
    while extras_name.casefold() in occupied:
        extras_name="Extras (checks)" if suffix==1 else "Extras (checks "+str(suffix)+")"
        suffix+=1
    return session,extras_name


def _safe_path(root, relative):
    path = root / relative
    if not _inside(path.resolve(strict=False), root.resolve()):
        raise TransferError(f"Destination path escapes the transfer folder: {relative}")
    cursor = path
    while cursor != root:
        if cursor.exists() and is_link(cursor):
            raise TransferError(f"Destination contains a link or junction: {cursor}")
        cursor = cursor.parent
    return path


def _publish(temp, final):
    # Windows rename is atomic and fails if the final file already exists.
    # POSIX rename overwrites, so use exclusive hard-link publication there.
    if os.name == "nt":
        os.rename(temp, final)
    else:
        os.link(temp, final)
        temp.unlink()


def transfer_all(inventory, destination, cancel=None, progress=None, allow_same_volume=False,folder_name=None):
    """Copies all files. Every existing/resumed file is re-hashed at both ends."""
    destination = destination_check(inventory.source, destination, allow_same_volume)
    if inventory.source.stat().st_dev!=inventory.device:
        raise TransferError("Source card changed. Refresh the source before transferring.")
    if not inventory.files:
        raise TransferError("The CINEMA folder contains no files to transfer.")
    session,extras_name = _session_for(inventory, destination,folder_name)
    extras = session/extras_name
    _safe_path(session,extras_name)
    extras.mkdir(exist_ok=True)
    manifest_path = extras/"transfer.json"
    target = session
    partials = extras/"Partials"
    metadata = {"app": APP_ID, "version": 2, "extras_folder":extras_name, "source": str(inventory.source),
                "fingerprint": inventory.fingerprint, "folder_name":folder_name,"files": len(inventory.files),
                "bytes": inventory.total_bytes, "verified_files": 0, "status": "in_progress",
                "selection":inventory.selection or "all"}
    copied = reused = verified = processed = 0
    checksums = []
    total_work = max(1, inventory.total_bytes * 2)
    def report(phase, entry=None, delta=0):
        nonlocal processed
        processed += delta
        if progress:
            progress({"phase": phase, "file": entry.relative if entry else "", "processed": processed,
                      "total": total_work, "verified": verified, "files": len(inventory.files), "session": str(session)})
    with SessionLock(extras / "transfer.lock"):
        try:
            check_cancel(cancel)
            _json_write(manifest_path, metadata)
            _safe_path(extras,"Partials")
            partials.mkdir(exist_ok=True)
            for directory in inventory.directories:
                _safe_path(target, directory).mkdir(parents=True, exist_ok=True)
            # Partial files restart from byte zero. Reclaim their old allocation
            # before checking free space, so an interrupted large file can resume
            # even when its old partial occupies most of the remaining capacity.
            for entry in inventory.files:
                check_cancel(cancel)
                temp=partials/(hashlib.sha256(entry.relative.encode()).hexdigest()+".partial")
                if temp.exists():
                    if is_link(temp):
                        raise TransferError("A partial-file path was replaced by a link.")
                    with temp.open("wb"):
                        pass
            remaining = sum(e.size for e in inventory.files if not _safe_path(target, e.relative).exists())
            if shutil.disk_usage(destination).free < remaining + RESERVE:
                raise TransferError("Not enough free destination space for all files plus a 64 MB reserve.")
            for entry in inventory.files:
                check_cancel(cancel)
                source = inventory.source / entry.relative
                _assert_entry(source, entry, inventory.device)
                final = _safe_path(target, entry.relative)
                final.parent.mkdir(parents=True, exist_ok=True)
                if final.exists():
                    if not final.is_file() or final.stat().st_size != entry.size:
                        raise TransferError(f"Existing destination differs: {entry.relative}. It was not overwritten.")
                    report("Checking existing copy", entry)
                    source_hash, _ = hash_file(source, cancel, lambda n: report("Checking source", entry, n))
                    _assert_entry(source, entry, inventory.device)
                    destination_hash, _ = hash_file(final, cancel, lambda n: report("Verifying existing copy", entry, n))
                    if source_hash != destination_hash:
                        raise TransferError(f"Checksum mismatch in existing copy: {entry.relative}. It was not overwritten.")
                    reused += 1
                else:
                    temp = partials / (hashlib.sha256(entry.relative.encode()).hexdigest() + ".partial")
                    if temp.exists() and is_link(temp):
                        raise TransferError("A partial-file path was replaced by a link.")
                    digest = hashlib.sha256()
                    read_bytes = 0
                    report("Copying", entry)
                    with source.open("rb") as incoming, temp.open("wb") as outgoing:
                        while True:
                            check_cancel(cancel)
                            data = incoming.read(CHUNK)
                            if not data:
                                break
                            outgoing.write(data)
                            digest.update(data)
                            read_bytes += len(data)
                            report("Copying", entry, len(data))
                        outgoing.flush()
                        os.fsync(outgoing.fileno())
                    _assert_entry(source, entry, inventory.device)
                    if read_bytes != entry.size:
                        raise TransferError(f"Source file size changed: {entry.relative}")
                    source_hash = digest.hexdigest()
                    report("Verifying copy", entry)
                    destination_hash, size = hash_file(temp, cancel, lambda n: report("Verifying copy", entry, n))
                    if destination_hash != source_hash or size != entry.size:
                        raise TransferError(f"Checksum mismatch after copying: {entry.relative}. Original is unchanged.")
                    os.utime(temp, ns=(entry.mtime_ns, entry.mtime_ns))
                    _publish(temp, final)
                    copied += 1
                verified += 1
                checksums.append(f"{source_hash}  {entry.relative}\n")
                metadata["verified_files"] = verified
                if verified % 16 == 0:
                    _json_write(manifest_path, metadata)
                report("Verified", entry)
            report("Checking that every source file is included")
            after = inventory_folder(inventory.source, cancel)
            after = scope_inventory(after,inventory.selection)
            if after.fingerprint != inventory.fingerprint or after.device!=inventory.device:
                raise TransferError("The CINEMA folder changed during the transfer. Refresh and transfer again; completion was not certified.")
            receipt = _safe_path(extras, "checksums.sha256")
            receipt_temp = extras / "checksums.tmp"
            if receipt_temp.exists() and is_link(receipt_temp):
                raise TransferError("Invalid checksum receipt path.")
            with receipt_temp.open("w", encoding="utf-8", newline="\n") as stream:
                stream.writelines(checksums)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(receipt_temp, receipt)
            metadata.update(status="verified", verified_files=verified, copied_files=copied,
                            reused_files=reused, verified_at=datetime.now(timezone.utc).isoformat())
            _json_write(manifest_path, metadata)
            return {**metadata, "session": str(session)}
        except BaseException as exc:
            metadata.update(status="cancelled" if isinstance(exc, TransferCancelled) else "incomplete", error=str(exc))
            try:
                _json_write(manifest_path, metadata)
            except OSError:
                pass
            raise
