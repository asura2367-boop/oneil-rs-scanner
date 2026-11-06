"""Read-only Linux storage preflight; no chmod, ACL, mount or ownership repair.

This is a conservative snapshot, not a defense against a hostile owner/root or
filesystem races. Only unambiguous writable ext4/XFS mounts are supported.
"""
import os
from pathlib import Path
import re
import stat
import sys

from oneil_execution_v2.domain import require


def require_linux_user():
    require(sys.platform == 'linux' and sys.version_info >= (3, 11), 'LINUX_PYTHON_311_REQUIRED')
    require(os.geteuid() != 0 and os.getuid() == os.geteuid() and os.getgid() == os.getegid(),
            'UNPRIVILEGED_LINUX_USER_REQUIRED')


def _decode_mount_path(value):
    # mountinfo uses exactly these octal escapes for whitespace and backslash.
    result = re.sub(r'\\(040|011|012|134)', lambda m: chr(int(m[1], 8)), value)
    require('\\' not in re.sub(r'\\(040|011|012|134)', '', value), 'MOUNTINFO_INVALID')
    return result


def _mount_for(path, mountinfo):
    matches = []
    for line in mountinfo.splitlines():
        left, sep, right = line.partition(' - ')
        fields, tail = left.split(), right.split()
        require(sep and len(fields) >= 6 and len(tail) >= 3, 'MOUNTINFO_INVALID')
        root, mount = map(_decode_mount_path, fields[3:5])
        require(mount.startswith('/') and root.startswith('/'), 'MOUNTINFO_INVALID')
        mount = Path(mount)
        if path == mount or mount in path.parents:
            matches.append((len(mount.parts), root, tail[0], set(fields[5].split(',')),
                            set(tail[2].split(','))))
    require(bool(matches), 'LOCAL_FILESYSTEM_UNVERIFIED')
    longest = max(row[0] for row in matches)
    matches = [row for row in matches if row[0] == longest]
    require(len(matches) == 1, 'AMBIGUOUS_MOUNT_DENIED')
    _, root, kind, options, super_options = matches[0]
    require(root == '/' and kind in {'ext4', 'xfs'} and 'rw' in options and
            'rw' in super_options and 'ro' not in options | super_options,
            'LOCAL_WRITABLE_EXT4_OR_XFS_REQUIRED')
    return kind


def inspect_private_path(path, *, directory=False):
    require_linux_user()
    path = Path(path)
    require(path.is_absolute() and '..' not in path.parts, 'ABSOLUTE_CANONICAL_PATH_REQUIRED')
    mountinfo = Path('/proc/self/mountinfo').read_text()
    _mount_for(path, mountinfo)
    uid = os.geteuid()
    for index, current in enumerate((path, *path.parents)):
        info = current.lstat()  # Never resolve through a symlink.
        require(not stat.S_ISLNK(info.st_mode), 'SYMLINK_DENIED')
        expected_dir = directory if index == 0 else True
        require(stat.S_ISDIR(info.st_mode) if expected_dir else stat.S_ISREG(info.st_mode),
                'REGULAR_FILE_OR_DIRECTORY_REQUIRED')
        require(info.st_uid in ({uid} if index == 0 else {uid, 0}), 'TRUSTED_PATH_OWNER_REQUIRED')
        require(not info.st_mode & (stat.S_ISUID | stat.S_ISGID | stat.S_ISVTX), 'SPECIAL_MODE_DENIED')
        require(not info.st_mode & (0o077 if index == 0 else 0o022), 'PRIVATE_PATH_MODE_REQUIRED')
        if not expected_dir:
            require(info.st_nlink == 1 and not info.st_mode & 0o111, 'PRIVATE_SINGLE_LINK_FILE_REQUIRED')
        # Mode bits do not alone describe POSIX ACL access. Unsupported inspection
        # also fails closed; SELinux labels are allowed and are never changed.
        attributes = os.listxattr(current, follow_symlinks=False)
        require(not any(name in attributes for name in
                        ('system.posix_acl_access', 'system.posix_acl_default')),
                'EXTENDED_POSIX_ACL_DENIED')
    return path


def inspect_profile(directory):
    directory = inspect_private_path(directory, directory=True)
    for path in directory.iterdir():
        inspect_private_path(path)
    return directory
