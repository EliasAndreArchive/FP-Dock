"""Explicit, confirmed source deletion; never recursively remove clip folders."""
from pathlib import Path
from transfer import _assert_entry, is_link,check_cancel


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
        relative=canonical.relative_to(root)
        entry=entries.get(relative.as_posix())
        if entry is None:
            raise ValueError("Source files changed. Refresh before deleting.")
        plan.append((canonical,entry))
        if progress and (i%64==0 or i==len(paths)):
            progress({'phase':'Preparing deletion','processed':i,'total':total,'file':str(path)})
    return tuple(plan)


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
    except Exception as exc:
        return deleted,str(exc)
    return deleted,""
