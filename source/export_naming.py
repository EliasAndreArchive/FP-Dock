"""Recording-date session names. Camera metadata wins over file timestamps."""
from datetime import datetime,timedelta,timezone
from pathlib import Path
import re,struct
import tifffile


def parse_day(value):
    if isinstance(value,bytes):value=value.decode(errors='replace')
    match=re.match(r'^(\d{4})[:-](\d{2})[:-](\d{2})',str(value).strip())
    if not match:return None
    try:return datetime(*map(int,match.groups())).date().isoformat()
    except ValueError:return None


def parse_recorded(value):
    if isinstance(value,bytes):value=value.decode(errors='replace')
    day=parse_day(value)
    if not day:return None
    match=re.match(r'^\d{4}[:-]\d{2}[:-]\d{2}[ T](\d{2}):(\d{2}):(\d{2})',str(value).strip())
    if match:
        try:
            hour,minute,second=map(int,match.groups())
            datetime(2000,1,1,hour,minute,second)
            return day+f' {hour:02}:{minute:02}:{second:02}'
        except ValueError:pass
    return day


def dng_recorded(path):
    with tifffile.TiffFile(path) as image:
        page=image.pages[0]
        tags=page.tags
        exif=tags.get(34665)
        values=exif.value if exif and isinstance(exif.value,dict) else {}
        for key in ('DateTimeOriginal','DateTimeDigitized',36867,36868):
            day=parse_recorded(values.get(key))
            if day:return day
        for code in (36867,36868,306):
            tag=tags.get(code)
            day=parse_recorded(tag.value) if tag else None
            if day:return day
    return None


def dng_day(path):
    stamp=dng_recorded(path)
    return stamp[:10] if stamp else None


def movie_day(path,include_time=False):
    # MOV/MP4 movie-header creation time; skip payloads rather than reading media.
    epoch=datetime(1904,1,1,tzinfo=timezone.utc)
    with open(path,'rb') as f:
        def walk(end,depth=0):
            while f.tell()+8<=end:
                start=f.tell();header=f.read(8)
                if len(header)!=8:return None
                size,kind=struct.unpack('>I4s',header);head=8
                if size==1:
                    extra=f.read(8)
                    if len(extra)!=8:return None
                    size=struct.unpack('>Q',extra)[0];head=16
                if size==0:size=end-start
                if size<head or start+size>end:return None
                if kind==b'moov' and depth<2:
                    found=walk(start+size,depth+1)
                    if found:return found
                elif kind==b'mvhd':
                    data=f.read(min(size-head,20))
                    if len(data)>=12:
                        count=8 if data[0]==1 else 4
                        seconds=int.from_bytes(data[4:4+count],'big')
                        if seconds:
                            try:
                                stamp=epoch+timedelta(seconds=seconds)
                                return stamp.strftime('%Y-%m-%d %H:%M:%S') if include_time else stamp.date().isoformat()
                            except OverflowError:pass
                f.seek(start+size)
            return None
        return walk(Path(path).stat().st_size)


def clip_recording_days(clip):
    days=[];fallback=False
    clip.recorded_at=None;clip.recorded_until=None
    for path in dict.fromkeys((clip.frames[0],clip.frames[-1])):
        try:
            stamp=dng_recorded(path) if path.suffix.lower()=='.dng' else movie_day(path,include_time=True)
        except (OSError,ValueError,TypeError,struct.error):stamp=None
        day=stamp[:10] if stamp else None
        if path==clip.frames[0]:clip.recorded_at=stamp
        if stamp:clip.recorded_until=stamp
        if not day:
            fallback=True
            # Explicit fallback is reported in the naming preview.
            from PySide6.QtCore import QDateTime,QTimeZone
            day=QDateTime.fromMSecsSinceEpoch(path.stat().st_mtime_ns//1000000,QTimeZone(b'Europe/Berlin')).toString('yyyy-MM-dd')
        days.append(day)
    return tuple(sorted(set(days))),fallback


def recorded_label(clip,compact=False):
    stamp=getattr(clip,'recorded_at',None)
    if not stamp:return 'Date unavailable' if compact else 'Recording date unavailable'
    day=stamp[:10]
    if compact:
        date=datetime.strptime(day,'%Y-%m-%d').strftime('%d.%m.%Y')
        return date+(' · '+stamp[11:16] if len(stamp)>10 else '')
    return day+(' · '+stamp[11:] if len(stamp)>10 else '')


def date_range(clips):
    days=sorted(day for clip in clips for day in getattr(clip,'recording_days',()))
    if not days:return 'Recording dates unavailable'
    value=days[0] if days[0]==days[-1] else days[0]+' – '+days[-1]
    if any(getattr(c,'date_fallback',False) for c in clips):value+=' · includes file dates'
    return value


def safe_label(text,max_length=100):
    text=re.sub(r'[<>:"/\\|?*\x00-\x1f]',' ',str(text))
    return re.sub(r'\s+',' ',text).strip(' .')[:max_length].rstrip(' .')


def session_name(clips,label=''):
    days=sorted(day for clip in clips for day in getattr(clip,'recording_days',()))
    if not days:return None
    prefix=days[0] if days[0]==days[-1] else days[0]+' - '+days[-1]
    suffix=safe_label(label)
    return prefix+(' '+suffix if suffix else '')
