"""Make a small, valid Bayer CinemaDNG test sequence; no downloaded footage."""
from pathlib import Path
import sys
import numpy as np
import tifffile

import tempfile,shutil
root=Path(__file__).resolve().parents[1]
output=Path(sys.argv[1]) if len(sys.argv)>1 else root/"Demo/CINEMA"
(root/"build").mkdir(exist_ok=True)
work=tempfile.TemporaryDirectory(dir=root/"build")
target=Path(work.name)
target.mkdir(parents=True, exist_ok=True)
w, h = 768, 432
y, x = np.mgrid[0:h, 0:w].astype(float)
u, v = x/w, y/h
for i in range(48):
    t = i/48
    top = np.array([.10, .19, .26])
    horizon = np.array([.92, .58, .35])
    rgb = top[None,None,:]*(1-v[:,:,None]) + horizon[None,None,:]*v[:,:,None]
    sun = ((u-(.66-.018*t))**2 + ((v-.35)*h/w)**2) < .035**2
    rgb[sun] = [1, .86, .59]
    for depth, base, amp, color in [(0,.55,.055,[.39,.36,.38]), (1,.65,.07,[.25,.29,.32]), (2,.78,.095,[.12,.19,.22])]:
        curve = base + amp*np.sin((u+t*(.035+depth*.02))*8+depth*1.9) + amp*.42*np.sin(u*21+depth)
        rgb[v > curve] = color
    # A winding warm path leads into the foreground.
    center = .50 + .055*np.sin(v*8+t*.2)
    width = .006 + np.maximum(0,v-.7)*.12
    road = (v>.73) & (abs(u-center)<width)
    rgb[road] = [.55,.46,.34]
    grain = np.random.default_rng(i).normal(0,.001,(h,w,1))
    linear = np.clip(rgb+grain, 0, 1)**2.2
    raw = np.empty((h,w), np.uint16)
    raw[0::2,0::2] = (linear[0::2,0::2,0]*3900+64).astype(np.uint16)
    raw[0::2,1::2] = (linear[0::2,1::2,1]*3900+64).astype(np.uint16)
    raw[1::2,0::2] = (linear[1::2,0::2,1]*3900+64).astype(np.uint16)
    raw[1::2,1::2] = (linear[1::2,1::2,2]*3900+64).astype(np.uint16)
    tags = [(50706,'B',4,(1,4,0,0),False), (50707,'B',4,(1,1,0,0),False),
            (50708,'s',0,'Framehouse demo camera',False),
            (33421,'H',2,(2,2),False),(33422,'B',4,(0,1,1,2),False),
            (50710,'B',3,(0,1,2),False),(50711,'H',1,1,False),
            (50714,'d',1,64.0,False),(50717,'I',1,4095,False),
            (50721,'2i',9,(3240454,1000000,-1537139,1000000,-498531,1000000,-969266,1000000,1876011,1000000,41556,1000000,55643,1000000,-204026,1000000,1057225,1000000),False),
            (50728,'2I',3,(1,1,1,1,1,1),False),(50778,'H',1,21,False),
            (51044,'2i',1,(24,1),False)]
    tifffile.imwrite(target/f'DESERT_{i+1:06}.dng',raw,photometric=32803,
                     metadata=None,extratags=tags)
print(f'Created {48} Bayer DNG frames in {target}')

frames=sorted(target.glob('*.dng'))
names=['Desert study','Last light','Distant ridge','Quiet horizon','Evening walk','Warm earth','Blue hour','Open country','After the sun','Soft shadows','Golden line','Field notes']
output.mkdir(parents=True,exist_ok=True)
for i,name in enumerate(names):
    folder=output/f'{i+1:02} - {name}';folder.mkdir(exist_ok=True)
    for j,frame in enumerate(frames if i==0 else frames[i*3:i*3+4]):
        shutil.copy2(frame,folder/f'DEMO_{j+1:06}.dng')
(output/'demo-notes.txt').write_text('Generated test footage, not camera footage. All support files are included in transfer.\n',encoding='utf-8')
work.cleanup()
print(f'Created 12 synthetic clips and 93 files in {output}')
