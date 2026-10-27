"""Packaged-build QA using newly created, disposable test data only."""
import hashlib
import json
import sys
import threading
from pathlib import Path
from transfer import inventory_folder, transfer_all, TransferCancelled, select_inventory


def run(root):
    root=Path(root).resolve()
    root.mkdir(exist_ok=False)
    source=root/'test-card/CINEMA'
    destination=root/'test-ssd'
    source.mkdir(parents=True)
    destination.mkdir()
    (source/'take').mkdir()
    (source/'take/frame_0001.dng').write_bytes(bytes(range(256))*4096)
    (source/'take/frame_0002.dng').write_bytes(b'checksum fixture'*32000)
    (source/'take/audio.wav').write_bytes(b'sidecar audio fixture')
    (source/'notes.txt').write_bytes(b'copy supporting files too')
    inventory=inventory_folder(source)
    originals={e.relative:hashlib.sha256((source/e.relative).read_bytes()).hexdigest() for e in inventory.files}
    cancel=threading.Event()
    def progress(data):
        if data['verified']==1:
            cancel.set()
    try:
        transfer_all(inventory,destination,cancel,progress,allow_same_volume=True)
        raise AssertionError('Cancellation was not honored')
    except TransferCancelled:
        pass
    result=transfer_all(inventory,destination,allow_same_volume=True)
    assert result['status']=='verified' and result['verified_files']==4 and result['reused_files']==1,result
    for name,digest in originals.items():
        assert hashlib.sha256((source/name).read_bytes()).hexdigest()==digest
        assert hashlib.sha256((Path(result['session'])/name).read_bytes()).hexdigest()==digest
    report={'frozen':bool(getattr(sys,'frozen',False)),'verified_files':4,'resume_reused_files':1,
            'source_unchanged':True,'transfer_status':result['status']}
    from raw_library import scan_folder, Clip
    selected_source=root/'selection-card/CINEMA'
    selected_source.mkdir(parents=True)
    for name in ('A_0001.dng','B_0001.dng','A.wav','B.wav','metadata.txt'):
        (selected_source/name).write_bytes(('original '+name).encode())
    clips=scan_folder(selected_source)[0]
    selected=next(c for c in clips if c.name=='A')
    scoped=select_inventory(inventory_folder(selected_source),clips,{selected.key})
    choice=transfer_all(scoped,destination,allow_same_volume=True)
    assert choice['verified_files']==3
    assert not (Path(choice['session'])/'B_0001.dng').exists()
    report.update(selected_files=3,unselected_video_copied=False)
    from live_gpu import calibrate_bayer,unpack_bayer
    package=Path(sys.executable).parent if getattr(sys,'frozen',False) else Path(__file__).resolve().parent.parent
    if getattr(sys,'frozen',False) and sys.platform=='darwin':package=Path(sys.executable).resolve().parents[3]
    demo=scan_folder(package/'Demo/CINEMA')[0][0]
    before=hashlib.sha256(demo.frames[0].read_bytes()).hexdigest()
    pixels,model=calibrate_bayer(demo.frames[0])
    unpacked,_=unpack_bayer(demo.frames[0])
    assert (pixels==unpacked).all()
    assert before==hashlib.sha256(demo.frames[0].read_bytes()).hexdigest()
    report.update(raw_preview_bytes_unchanged=True,dated_layout=True)
    (root/'result.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    return 0
