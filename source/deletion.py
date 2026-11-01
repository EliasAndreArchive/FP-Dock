"""Confirmed deletion with bounded dedicated-folder cleanup; never recursive removal."""
from pathlib import Path
from transfer import _assert_entry, is_link,check_cancel


class DeletionPlan(tuple):
    def __new__(cls,files,directories=()):
        obj=super().__new__(cls,files)
        obj.directories=tuple(directories)
        return obj


def deletion_plan(inventory, clips, selected_keys, cancelled=None, progress=None):
    selected=[c for c in clips if c.key in selected_keys]
    if len(selected)!=len(selected_keys) or not selected:
        raise ValueError("The clip selection changed. Refresh and select clips again.")
    protected={Path(p).absolute() for c in clips if c.key not in selected_keys
               for p in [*c.frames,*([c.audio] if c.audio else [])]}
    paths={Path(p).absolute() for c in selected
           for p in [*c.frames,*([c.audio] if c.audio else [])]}
    paths-=protected
    entries={e.relative:e for e in inventory.files}
    root=inventory.source.resolve(strict=True)
    dedicated=set()
    unselected=[c.folder.resolve() for c in clips if c.key not in selected_keys]
    for clip in selected:
        folder=clip.folder.resolve(strict=True)
        if folder==root or root not in folder.parents:continue
        if any(other==folder or folder in other.parents for other in unselected):continue
        dedicated.add(folder)
    for entry in inventory.files:
        path=root/entry.relative
        if any(folder in path.parents for folder in dedicated):paths.add(path)
    paths-=protected
    canonical_protected={p.parent.resolve()/p.name for p in protected}
    directories=set(dedicated)
    for relative in inventory.directories:
        folder=root/relative
        if any(parent==folder or parent in folder.parents for parent in dedicated):directories.add(folder)
    plan=[]
    parents={}
    total=max(1,len(paths)*3)
    for i,path in enumerate(sorted(paths),1):
        check_cancel(cancelled)
        if path.parent not in parents:
            parent=path.parent.resolve(strict=True)
            if parent!=root and root not in parent.parents:
                raise ValueError("Refusing to delete outside the source folder.")
            for original in [path.parent,*path.parent.parents]:
                if original.resolve()==root or root in original.resolve().parents:
                    if is_link(original):raise ValueError("Refusing to delete through a linked folder.")
            parents[path.parent]=parent
        canonical=parents[path.parent]/path.name
        if canonical in canonical_protected:continue
        relative=canonical.relative_to(root)
        entry=entries.get(relative.as_posix())
        if entry is None:
            raise ValueError("Source files changed. Refresh before deleting.")
        plan.append((canonical,entry))
        if progress and (i%64==0 or i==len(paths)):
            progress({'phase':'Preparing deletion','processed':i,'total':total,'file':str(path)})
    unique={path:entry for path,entry in plan}
    return DeletionPlan(unique.items(),sorted(directories,key=lambda p:len(p.parts),reverse=True))


def delete_files(plan, device, cancelled, progress=None):
    deleted=0
    try:
        # Validate the entire selection again after the confirmation dialog.
        total=max(1,len(plan)*3)
        for i,(path,entry) in enumerate(plan,1):
            check_cancel(cancelled)
            _assert_entry(path,entry,device)
            if progress and (i%64==0 or i==len(plan)):
                progress({'phase':'Checking files before deletion','processed':len(plan)+i,'total':total,'file':str(path)})
        for path,entry in plan:
            if cancelled.is_set():
                raise OSError("Cancelled or source disconnected")
            if any(is_link(p) for p in path.parents):
                raise OSError("Source folder became a link")
            _assert_entry(path,entry,device)
            path.unlink()
            deleted+=1
            if progress and (deleted%32==0 or deleted==len(plan)):
                progress({'phase':f'Deleted {deleted:,} / {len(plan):,} files','processed':len(plan)*2+deleted,'total':total,'file':str(path)})
        for folder in getattr(plan,'directories',()):
            check_cancel(cancelled)
            if any(is_link(p) for p in [folder,*folder.parents]):
                raise OSError("Clip folder became a link; cleanup stopped")
            if folder.stat().st_dev!=device:raise OSError("Source device changed")
            folder.rmdir() # Only empty folders; new/unrecognized files stop cleanup safely.
    except Exception as exc:
        return deleted,str(exc)
    return deleted,""
