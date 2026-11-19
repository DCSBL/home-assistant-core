"""Drop the privileges of the sandbox process.

This runs inside the sandbox process, before the event loop starts and before
any integration code is imported. A process boundary alone stops an integration
from reaching into core's memory, but not from opening files. So when the
sandbox starts as root on Linux, which is how core runs in the Home Assistant
container, it also:

1. Moves into a private mount namespace.
2. Mounts its own data directory over the config directory, so secrets.yaml,
   .storage (auth, config entries of others) and the database disappear. Only
   the code of the integrations of this sandbox is mounted back, read only.
3. Hides other sensitive paths, such as /ssl, /share, /backup and /run, behind
   empty tmpfs mounts.
4. Switches to an unprivileged uid and gid without supplementary groups, which
   also drops all capabilities, and sets no_new_privs.

Where this is not possible (not root, not Linux) the sandbox still runs in its
own process with a clean environment, and reports that files are not isolated.
"""

import ctypes
import ctypes.util
from dataclasses import dataclass
import os
from pathlib import Path
import resource
import shutil
import sys

CONFINEMENT_FULL = "full"
CONFINEMENT_PROCESS = "process"

# Paths of the Home Assistant container that a sandbox must not see.
HIDDEN_PATHS = (
    "/addon_configs",
    "/addons",
    "/backup",
    "/data",
    "/media",
    "/root",
    "/run",
    "/share",
    "/ssl",
    "/tmp",  # noqa: S108
)

_MS_RDONLY = 1
_MS_NOSUID = 2
_MS_NODEV = 4
_MS_REMOUNT = 32
_MS_BIND = 4096
_MS_REC = 16384
_MS_PRIVATE = 1 << 18
_PR_SET_NO_NEW_PRIVS = 38


@dataclass(slots=True)
class ConfineRequest:
    """What the sandbox needs to set up its view of the file system."""

    config_dir: str
    data_dir: str
    integration_dirs: dict[str, str]
    uid: int
    gid: int
    file_isolation: bool


def _libc() -> ctypes.CDLL:
    return ctypes.CDLL(ctypes.util.find_library("c") or None, use_errno=True)


def _mount(
    libc: ctypes.CDLL,
    source: str | None,
    target: str,
    fstype: str | None,
    flags: int,
    data: str | None = None,
) -> None:
    if (
        libc.mount(
            source.encode() if source else None,
            target.encode(),
            fstype.encode() if fstype else None,
            flags,
            data.encode() if data else None,
        )
        != 0
    ):
        errno = ctypes.get_errno()
        raise OSError(errno, f"mount {source} on {target}: {os.strerror(errno)}")


def _prepare_code_view(request: ConfineRequest, *, link: bool) -> None:
    """Create custom_components in the data dir with only our integrations."""
    custom = Path(request.data_dir, "custom_components")
    if custom.is_symlink() or custom.is_file():
        custom.unlink()
    if custom.exists():
        shutil.rmtree(custom)
    custom.mkdir(parents=True)
    for domain, source in request.integration_dirs.items():
        target = custom / domain
        if link:
            target.symlink_to(source, target_is_directory=True)
        else:
            target.mkdir()


def can_confine() -> bool:
    """Return if this process can confine itself."""
    return sys.platform == "linux" and os.geteuid() == 0 and hasattr(os, "unshare")


def apply(request: ConfineRequest) -> str:
    """Confine the current process and return how far that got.

    Must be called while the process has a single thread.
    """
    os.umask(0o077)
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    Path(request.data_dir).mkdir(parents=True, exist_ok=True)

    if not (request.file_isolation and can_confine()):
        _prepare_code_view(request, link=True)
        os.chdir(request.data_dir)
        return CONFINEMENT_PROCESS

    libc = _libc()
    _prepare_code_view(request, link=False)
    for root, dirs, files in os.walk(request.data_dir):
        for name in (root, *(os.path.join(root, item) for item in dirs + files)):
            os.lchown(name, request.uid, request.gid)
    os.chmod(request.data_dir, 0o700)

    os.unshare(os.CLONE_NEWNS)
    _mount(libc, None, "/", None, _MS_REC | _MS_PRIVATE)

    # Our integrations, read only, inside the data dir.
    for domain, source in request.integration_dirs.items():
        target = os.path.join(request.data_dir, "custom_components", domain)
        _mount(libc, source, target, None, _MS_BIND)
        _mount(
            libc,
            None,
            target,
            None,
            _MS_BIND | _MS_REMOUNT | _MS_RDONLY | _MS_NOSUID | _MS_NODEV,
        )

    # The data dir, including the mounts above, replaces the config dir.
    _mount(libc, request.data_dir, request.config_dir, None, _MS_BIND | _MS_REC)

    for path in HIDDEN_PATHS:
        if os.path.isdir(path) and not request.config_dir.startswith(path + "/"):
            _mount(libc, "tmpfs", path, "tmpfs", _MS_NOSUID | _MS_NODEV, "mode=0755")

    os.chdir(request.config_dir)
    os.setgroups([])
    os.setgid(request.gid)
    os.setuid(request.uid)
    if os.getuid() == 0 or os.geteuid() == 0:
        raise RuntimeError("Failed to drop root")
    if libc.prctl(_PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0:
        raise OSError(ctypes.get_errno(), "prctl(PR_SET_NO_NEW_PRIVS)")
    if not os.access(request.config_dir, os.R_OK | os.W_OK | os.X_OK):
        raise PermissionError(
            f"The sandbox user can not reach {request.config_dir}, check that its"
            " parent directories can be traversed by other users"
        )
    return CONFINEMENT_FULL
