"""Process-local Windows directory change guards, never a TTL correctness cache.

Only immutable ADS installation/library fingerprints use this helper. Unknown
filesystems, missing roots, failed handles or signaled changes force full reads.
API contract: https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-findfirstchangenotificationw
"""
import atexit
import copy
import ctypes
import os
from pathlib import Path
import threading


class Watch:
    def __init__(self, paths):
        self.handles=[];self.paths=tuple(Path(p).resolve() for p in paths);self.identities=[]
        if os.name!='nt':raise OSError('Native change guards are Windows-only')
        from ctypes import wintypes as w
        self.api=ctypes.WinDLL('kernel32',use_last_error=True)
        for name,args,result in (
            ('FindFirstChangeNotificationW',[w.LPCWSTR,w.BOOL,w.DWORD],w.HANDLE),
            ('FindCloseChangeNotification',[w.HANDLE],w.BOOL),
            ('WaitForSingleObject',[w.HANDLE,w.DWORD],w.DWORD),
            ('GetDriveTypeW',[w.LPCWSTR],w.UINT)):
            f=getattr(self.api,name);f.argtypes=args;f.restype=result
        try:
            for p in self.paths:
                if not p.is_dir() or self.api.GetDriveTypeW(p.anchor)!=3:
                    raise OSError('Require existing local fixed-drive directories')
                s=p.stat();self.identities.append((s.st_dev,s.st_ino))
                h=self.api.FindFirstChangeNotificationW(str(p),True,0x1|0x2|0x4|0x8|0x10|0x100)
                if h==ctypes.c_void_p(-1).value:raise ctypes.WinError(ctypes.get_last_error())
                self.handles.append(h)
        except BaseException:self.close();raise

    def unchanged(self):
        try:
            if not self.handles:return False
            for p,identity,h in zip(self.paths,self.identities,self.handles):
                s=p.stat()
                if (s.st_dev,s.st_ino)!=identity or self.api.WaitForSingleObject(h,0)!=258:return False
            return True
        except OSError:return False

    def close(self):
        for h in self.handles:self.api.FindCloseChangeNotification(h)
        self.handles=[]


class Memo:
    def __init__(self):self.entries={};self.lock=threading.RLock()
    def close(self):
        with self.lock:
            for watch,_ in self.entries.values():watch.close()
            self.entries.clear()
    def get(self,paths,compute,cacheable=lambda:True):
        key=tuple(sorted(str(Path(p).resolve()) for p in paths))
        with self.lock:
            old=self.entries.get(key)
            if old and old[0].unchanged():return copy.deepcopy(old[1])
            if old:old[0].close();del self.entries[key]
            try:watch=Watch(key)
            except OSError:return compute()
            try:value=compute()
            except BaseException:watch.close();raise
            if not watch.unchanged():
                watch.close()
                raise RuntimeError('ENVIRONMENT_CHANGED_DURING_READ: fingerprint was not stable; do not reuse it')
            if cacheable():
                if len(self.entries)>=16:self.close()
                self.entries[key]=(watch,copy.deepcopy(value))
            else:watch.close()
            return value


memo=Memo()
atexit.register(memo.close)
