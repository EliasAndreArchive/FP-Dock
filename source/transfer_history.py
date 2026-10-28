"""Read existing Extras receipts; confirm complete clips against both file hashes."""
import json
import re
from pathlib import Path,PurePosixPath

class ReadGate:
    def __init__(self,cancel,paused):self.cancel,self.paused=cancel,paused
    def is_set(self):
        while self.paused.is_set():
            if self.cancel.wait(.1):return True
        return self.cancel.is_set()

from transfer import APP_ID,hash_file,is_link,check_cancel,_assert_entry


def safe_file(root,relative):
    rel=PurePosixPath(relative)
    if rel.is_absolute() or not rel.parts or any(p in ('..','.') or ':' in p or '\\' in p for p in rel.parts):
        raise ValueError('Invalid receipt path')
    path=root.joinpath(*rel.parts)
    if root not in path.resolve(strict=True).parents:
        raise ValueError('Receipt escapes export')
    for parent in [path,*path.parents]:
        if parent==root or root in parent.parents:
            if is_link(parent):raise ValueError('Linked export path')
    return path


def find_transferred(inventory,clips,destination,cancel,cache=None):
    cache=cache if cache is not None else {}
    found={}
    entries={e.relative:e for e in inventory.files}
    root=Path(destination).resolve(strict=True)
    sessions=[root]+[p for p in root.iterdir() if p.is_dir() and not is_link(p)]
    def digest(path):
        check_cancel(cancel)
        st=path.stat()
        key=(str(path),st.st_dev,st.st_ino,st.st_size,st.st_mtime_ns,st.st_ctime_ns)
        if key not in cache:cache[key]=hash_file(path,cancel)[0]
        return cache[key]
    for session in sessions:
        check_cancel(cancel)
        for extras in session.glob('Extras*'):
            try:
                if not extras.is_dir() or is_link(extras):continue
                manifest=safe_file(session,extras.name+'/transfer.json')
                receipt=safe_file(session,extras.name+'/checksums.sha256')
                if manifest.stat().st_size>8*1024*1024 or receipt.stat().st_size>64*1024*1024:continue
                data=json.loads(manifest.read_text(encoding='utf-8'))
                if data.get('app')!=APP_ID or data.get('status')!='verified':continue
                hashes={}
                for line in receipt.read_text(encoding='utf-8').splitlines():
                    match=re.fullmatch(r'([0-9a-fA-F]{64})  (.+)',line)
                    if not match:raise ValueError('Invalid checksum receipt')
                    relative=match[2]
                    if relative in hashes:raise ValueError('Duplicate receipt path')
                    hashes[relative]=match[1].lower()
                if len(hashes)!=data.get('files') or data.get('verified_files')!=data.get('files'):continue
                for clip in clips:
                    check_cancel(cancel)
                    if clip.key in found:continue
                    paths=[*clip.frames,*([clip.audio] if clip.audio else [])]
                    relatives=[p.resolve().relative_to(inventory.source).as_posix() for p in paths]
                    if not relatives or not all(rel in hashes and rel in entries for rel in relatives):continue
                    valid=True
                    for relative in relatives:
                        source=safe_file(inventory.source,relative)
                        try:
                            target=safe_file(session,relative)
                        except (OSError,ValueError):
                            valid=False;break
                        entry=entries[relative]
                        _assert_entry(source,entry,inventory.device)
                        if target.stat().st_size!=entry.size or digest(source)!=hashes[relative] or digest(target)!=hashes[relative]:
                            valid=False;break
                        _assert_entry(source,entry,inventory.device)
                    if valid:found[clip.key]=str(session)
            except (OSError,ValueError,TypeError,KeyError):
                continue
    return found
