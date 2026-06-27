"""Crash-safe subprocess launching for a mixed macOS / Linux toolkit.

Why this module exists
======================

Every tool in this repo eventually shells out to ``ffmpeg``/``ffprobe``.  Most
of those tools *also* talk to first-party LAN services over HTTP (``httpx``)
before decoding audio.  On macOS that ordering is a loaded gun, and this module
is the safety.

The macOS ``fork()`` poison, end to end
---------------------------------------

POSIX ``fork()`` duplicates the whole address space but only the **calling
thread**.  Any lock another thread was holding at the instant of ``fork()`` --
most importantly the C allocator's ``malloc`` lock -- is copied in the *locked*
state, owned by a thread that no longer exists in the child.  The child may
therefore only call async-signal-safe functions until it ``exec``s; ``malloc``
is not one of them.

CPython's legacy ``subprocess`` path forks and then, **in the child, still
running our process image**, does the ``dup2`` dance and builds ``argv``/``env``
before calling ``execve``.  If the heap lock was poisoned, that pre-``exec`` C
code segfaults and the child dies with signal 11 *before the target program
ever starts*.  Symptoms: return code ``-11`` and empty stderr, intermittently,
and only after an HTTP request has run in the same process.

macOS makes this near-unavoidable for networked tools because DNS resolution,
TLS, and proxy lookup pull in CoreFoundation / libdispatch (GCD), which spin up
process-global worker threads you cannot ``join()`` and flip a *one-way*
"fork-unsafe" latch in the Objective-C runtime.  Closing the ``httpx`` client
(even a synchronous one) does not undo any of this -- those threads and that
state are owned by the OS, not by us.

The fix: use ``posix_spawn`` instead of ``fork``
------------------------------------------------

``posix_spawn`` is, on macOS, effectively an atomic kernel operation: the new
process comes up already running the target image, with no userspace child code
executing in the poisoned address space.  The fork window simply does not exist,
so the poison is irrelevant.

CPython already *prefers* ``posix_spawn`` -- but only when this gate in
``subprocess.py`` passes::

    (not close_fds or _HAVE_POSIX_SPAWN_CLOSEFROM)

``close_fds`` defaults to ``True``.  ``_HAVE_POSIX_SPAWN_CLOSEFROM`` is ``False``
on macOS (no ``closefrom``/``close_range``) and ``True`` on modern Linux glibc.
So:

* **Linux** already takes the ``posix_spawn`` path with the default arguments.
* **macOS** is forced onto the dangerous ``fork`` path with the default
  arguments -- exactly the platform where forking is most dangerous.

Passing ``close_fds=False`` satisfies the gate via its first term and flips
macOS onto ``posix_spawn``.  This is safe: since PEP 446 (Python 3.4) every fd
Python opens is ``O_CLOEXEC`` by default, so the child does not inherit our
sockets regardless of ``close_fds``.

Conservative scope
------------------

We only override the flag on Darwin, leaving Linux's already-correct defaults
byte-for-byte unchanged, and we bow out when a caller uses ``pass_fds`` (which
requires ``close_fds=True``) or sets ``close_fds`` explicitly.

References:
* ``scripts/reproduce_macos_http_subprocess_segv.py``
* ``docs/macos-fork-subprocess.md``
"""

from __future__ import annotations

import subprocess
import sys
from typing import Any

__all__ = ["run", "needs_posix_spawn_workaround"]


def needs_posix_spawn_workaround() -> bool:
    """Return whether this platform forks (unsafely) for ``subprocess`` by default.

    Currently this is macOS only.  Kept as a function so call sites and tests can
    reason about the behavior without re-deriving the platform check, and so the
    condition has exactly one home if Apple ever ships ``closefrom``.
    """

    return sys.platform == "darwin"


def run(args: Any, **kwargs: Any) -> subprocess.CompletedProcess[Any]:
    """``subprocess.run`` that refuses to ``fork()`` on macOS.

    A drop-in replacement for :func:`subprocess.run` for the common
    "launch ``ffmpeg``/``ffprobe`` and wait" pattern.  On macOS it injects
    ``close_fds=False`` so CPython selects ``posix_spawn`` instead of the
    fork-then-exec path that intermittently segfaults after an HTTP request
    (see the module docstring).  On every other platform it is a transparent
    pass-through.

    The override is skipped when the caller sets ``close_fds`` explicitly or
    uses ``pass_fds`` (which mandates ``close_fds=True``); those callers have
    opted into fd semantics we must not silently override.
    """

    if (
        needs_posix_spawn_workaround()
        and "close_fds" not in kwargs
        and not kwargs.get("pass_fds")
    ):
        # Flip the macOS-only gate so subprocess uses posix_spawn, not fork.
        # Harmless to inheritance: Python's own fds are O_CLOEXEC (PEP 446).
        kwargs["close_fds"] = False

    return subprocess.run(args, **kwargs)
