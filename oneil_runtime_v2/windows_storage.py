"""Read-only fail-closed Windows fixed-volume/DACL inspection; never repairs ACLs."""
import os
from pathlib import Path
from .supervisor import safe_path


ANCESTOR_WRITE_MASK = 0x40000000 | 0x10000000 | 0x000D0156


def validate_ace(kind, principal, mask, allowed, *, ancestor=False):
    if kind == 1: return
    if kind != 0 or (principal not in allowed and (not ancestor or mask & ANCESTOR_WRITE_MASK)):
        raise RuntimeError('BROAD_OR_UNSUPPORTED_DACL_DENIED')


def inspect_private_path(path, *, _ancestor=False):
    if os.name != 'nt':
        raise RuntimeError('NATIVE_WINDOWS_STORAGE_REQUIRED')
    import ctypes as c
    from ctypes import wintypes as w
    kernel = c.WinDLL('kernel32', use_last_error=True)
    adv = c.WinDLL('advapi32', use_last_error=True)
    def bind(lib, name, args, result):
        fn = getattr(lib, name); fn.argtypes = args; fn.restype = result
        return fn
    volume = bind(kernel, 'GetVolumePathNameW', [w.LPCWSTR, w.LPWSTR, w.DWORD], w.BOOL)
    drive = bind(kernel, 'GetDriveTypeW', [w.LPCWSTR], w.UINT)
    info = bind(adv, 'GetNamedSecurityInfoW', [w.LPWSTR,w.DWORD,w.DWORD,c.POINTER(c.c_void_p),c.c_void_p,c.POINTER(c.c_void_p),c.c_void_p,c.POINTER(c.c_void_p)], w.DWORD)
    control = bind(adv, 'GetSecurityDescriptorControl', [c.c_void_p,c.POINTER(w.WORD),c.POINTER(w.DWORD)], w.BOOL)
    ace = bind(adv, 'GetAce', [c.c_void_p,w.DWORD,c.POINTER(c.c_void_p)], w.BOOL)
    sidstr = bind(adv, 'ConvertSidToStringSidW', [c.c_void_p,c.POINTER(w.LPWSTR)], w.BOOL)
    free = bind(kernel, 'LocalFree', [c.c_void_p], c.c_void_p)
    close = bind(kernel, 'CloseHandle', [w.HANDLE], w.BOOL)
    process = bind(kernel, 'GetCurrentProcess', [], w.HANDLE)
    token = bind(adv, 'OpenProcessToken', [w.HANDLE,w.DWORD,c.POINTER(w.HANDLE)], w.BOOL)
    tokeninfo = bind(adv, 'GetTokenInformation', [w.HANDLE,c.c_int,c.c_void_p,w.DWORD,c.POINTER(w.DWORD)], w.BOOL)
    def sid(ptr):
        value = w.LPWSTR()
        if not sidstr(ptr,c.byref(value)): raise RuntimeError('SID_INSPECTION_FAILED')
        try: return value.value
        finally: free(c.cast(value,c.c_void_p))
    handle = w.HANDLE()
    if not token(process(),8,c.byref(handle)): raise RuntimeError('TOKEN_INSPECTION_FAILED')
    try:
        size = w.DWORD(); tokeninfo(handle,1,None,0,c.byref(size))
        if not size.value: raise RuntimeError('TOKEN_INSPECTION_FAILED')
        buf = c.create_string_buffer(size.value)
        if not tokeninfo(handle,1,buf,size,c.byref(size)): raise RuntimeError('TOKEN_INSPECTION_FAILED')
        user = sid(c.cast(buf,c.POINTER(c.c_void_p))[0])
    finally: close(handle)
    allowed = {user, 'S-1-5-18', 'S-1-5-32-544'}
    path = safe_path(path, directory=Path(path).is_dir())
    root = c.create_unicode_buffer(32768)
    if not volume(str(path),root,len(root)) or drive(root.value) != 3:
        raise RuntimeError('LOCAL_FIXED_VOLUME_REQUIRED')
    descriptor = c.c_void_p(); owner = c.c_void_p(); dacl = c.c_void_p()
    if info(str(path),1,5,c.byref(owner),None,c.byref(dacl),None,c.byref(descriptor)):
        raise RuntimeError('DACL_INSPECTION_FAILED')
    try:
        flags = w.WORD(); revision = w.DWORD()
        if not control(descriptor,c.byref(flags),c.byref(revision)) or not dacl.value:
            raise RuntimeError('EXPLICIT_PRIVATE_DACL_REQUIRED')
        if sid(owner) not in allowed: raise RuntimeError('PRIVATE_OWNER_REQUIRED')
        # ACL header: revision, reserved, size, ACE count, reserved.
        count = c.cast(dacl,c.POINTER(w.WORD))[2]
        if not count: raise RuntimeError('PRIVATE_DACL_REQUIRED')
        for index in range(count):
            entry = c.c_void_p()
            if not ace(dacl,index,c.byref(entry)): raise RuntimeError('DACL_INSPECTION_FAILED')
            kind = c.cast(entry,c.POINTER(c.c_ubyte))[0]
            if kind == 1: continue  # A deny ACE cannot grant access.
            if kind != 0: raise RuntimeError('BROAD_OR_UNSUPPORTED_DACL_DENIED')
            mask = c.cast(entry.value+4,c.POINTER(w.DWORD))[0]
            validate_ace(kind,sid(entry.value+8),mask,allowed,ancestor=_ancestor)
    finally: free(descriptor)
    if not _ancestor:
        for parent in path.parents: inspect_private_path(parent,_ancestor=True)
    return True


def inspect_profile(directory, *, inspector=inspect_private_path):
    directory = safe_path(directory, directory=True)
    inspector(directory)
    for path in directory.iterdir():
        if not path.is_file(): raise RuntimeError('UNEXPECTED_PROFILE_ENTRY')
        safe_path(path)
        inspector(path)
    return True
