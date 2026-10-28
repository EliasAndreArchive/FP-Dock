"""Explicit, confirmed source deletion; never recursively remove clip folders."""
from pathlib import Path
from transfer import _assert_entry, is_link


def deletion_plan(inventory, clips, selected_keys):
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
    for path in sorted(paths):
        relative=path.relative_to(root)
        if path==root or root not in path.resolve(strict=True).parents:
            raise ValueError("Refusing to delete outside the source folder.")
        if any(is_link(parent) for parent in [root,*path.parents] if parent==root or root in parent.parents):
            raise ValueError("Refusing to delete through a linked folder.")
        entry=entries.get(relative.as_posix())
        if entry is None:
            raise ValueError("Source files changed. Refresh before deleting.")
        _assert_entry(path,entry,inventory.device)
        plan.append((path,entry))
    return tuple(plan)


def delete_files(plan, device, cancelled):
    deleted=0
    try:
        # Validate the entire selection again after the confirmation dialog.
        for path,entry in plan:
            _assert_entry(path,entry,device)
        for path,entry in plan:
            if cancelled.is_set():
                raise OSError("Cancelled or source disconnected")
            if any(is_link(p) for p in path.parents):
                raise OSError("Source folder became a link")
            _assert_entry(path,entry,device)
            path.unlink()
            deleted+=1
    except Exception as exc:
        return deleted,str(exc)
    return deleted,""
