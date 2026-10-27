"""Read-only Resolve output LUTs. Input must be DaVinci WG/Intermediate.

Tetrahedral interpolation is shared by CPU previews and the GPU implementation.
The LUT supplies the output gamma, tone mapping and OOTF: never apply them twice.
"""
from functools import lru_cache
from pathlib import Path
from dataclasses import dataclass
import numpy as np

PREFIX='Resolve output LUT: '


@dataclass(frozen=True)
class Cube:
    table: np.ndarray
    minimum: np.ndarray
    maximum: np.ndarray

    @property
    def size(self):return self.table.shape[0]

    def apply(self,encoded):
        p=np.clip((np.asarray(encoded,dtype=np.float32)-self.minimum)/(self.maximum-self.minimum),0,1)*(self.size-1)
        base=np.minimum(np.floor(p).astype(np.int32),self.size-2)
        part=p-base
        # Sort fractional components descending; four vertices form the tetrahedron.
        order=np.argsort(-part,axis=-1,kind='stable')
        first=np.eye(3,dtype=np.int32)[order[...,0]]
        second=first+np.eye(3,dtype=np.int32)[order[...,1]]
        def at(offset):
            index=base+offset
            return self.table[index[...,2],index[...,1],index[...,0]]
        f=np.take_along_axis(part,order,axis=-1)
        a,b,c,d=at(0),at(first),at(second),at(1)
        return a+f[...,0,None]*(b-a)+f[...,1,None]*(c-b)+f[...,2,None]*(d-c)


@lru_cache(maxsize=4)
def load_cube(path):
    path=Path(path)
    if path.stat().st_size>40*1024**2:raise ValueError('LUT file is too large (maximum 40 MB).')
    size=None;minimum=np.zeros(3,np.float32);maximum=np.ones(3,np.float32);rows=[]
    for line in path.read_text(encoding='utf-8-sig').splitlines():
        fields=line.split('#',1)[0].strip().split()
        if not fields:continue
        key=fields[0]
        try:
            if key=='TITLE':continue
            if key=='LUT_3D_SIZE':
                if size is not None or len(fields)!=2:raise ValueError('Duplicate or invalid LUT size.')
                size=int(fields[1])
                if not 2<=size<=65:raise ValueError('Use a 3D cube LUT with 2–65 points.')
            elif key in ('DOMAIN_MIN','DOMAIN_MAX'):
                values=np.array(fields[1:],np.float32)
                if values.shape!=(3,):raise ValueError('Invalid LUT domain.')
                if key=='DOMAIN_MIN':minimum=values
                else:maximum=values
            elif key=='LUT_3D_INPUT_RANGE':
                if len(fields)!=3:raise ValueError('Invalid LUT input range.')
                minimum=np.full(3,float(fields[1]),np.float32);maximum=np.full(3,float(fields[2]),np.float32)
            elif key.startswith('LUT_'):
                raise ValueError('Export a standalone 3D .cube LUT, without a 1D shaper.')
            else:
                if len(fields)!=3:raise ValueError('Invalid LUT row.')
                rows.append(tuple(map(float,fields)))
                if len(rows)>65**3:raise ValueError('Too many LUT entries.')
        except (TypeError,OverflowError) as exc:raise ValueError('Invalid .cube LUT.') from exc
    if size is None or len(rows)!=size**3:raise ValueError('LUT size does not match its color entries.')
    table=np.asarray(rows,np.float32).reshape(size,size,size,3)
    if not np.isfinite(table).all() or not np.isfinite(minimum).all() or not np.isfinite(maximum).all() or np.any(maximum<=minimum):
        raise ValueError('LUT contains invalid numbers or domain.')
    table.setflags(write=False)
    return Cube(table,minimum,maximum)


def resolve_view(path):
    path=str(Path(path).resolve())
    load_cube(path)
    return PREFIX+path


def is_resolve_view(view):return isinstance(view,str) and view.startswith(PREFIX)


def view_cube(view):return load_cube(view[len(PREFIX):])
