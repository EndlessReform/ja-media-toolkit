# macOS `fork()` + subprocess SIGSEGV (and why we wrap `subprocess.run`)

## TL;DR

On macOS, calling `subprocess.run([...ffmpeg...])` **after** any `httpx`/HTTP
request in the same process can make the subprocess die with **signal 11
(SIGSEGV)**, intermittently, with **empty stderr** and **return code `-11`**.
It is not an ffmpeg bug, a pipe-buffer bug, or a file-descriptor bug. It is the
classic macOS *"`fork()` in a process that touched Apple's system frameworks"*
crash.

The fix is centralized in **`packages/core/src/ja_media_core/proc.py`**: use
`ja_media_core.proc.run(...)` instead of `subprocess.run(...)` for anything that
launches `ffmpeg`/`ffprobe`/external tools. On macOS it forces CPython onto the
`posix_spawn` path (which does not fork); on Linux it is a transparent
pass-through.

## What actually happens

1. **`fork()` only clones the calling thread.** POSIX `fork()` copies the whole
   address space but exactly one thread. Any lock another thread held at the
   instant of the fork — crucially the C allocator's `malloc` lock — is copied
   **locked**, owned by a thread that does not exist in the child. The child may
   therefore only call async-signal-safe functions until it `exec`s. `malloc`
   is *not* async-signal-safe.

2. **CPython's legacy `subprocess` path runs our code in the forked child.**
   After `fork()`, the child is still our Python process. It performs the
   `dup2` dance, closes/reorders fds, and builds the `argv`/`env` C arrays
   **before** calling `execve`. If the heap lock was poisoned, this pre-`exec`
   C code segfaults — so the child dies *before the target program ever runs*.
   That is why the corpse has return code `-11` and no ffmpeg output.

3. **`httpx`/DNS/TLS poison the process, irreversibly.** On macOS, name
   resolution, TLS, and proxy lookup pull in CoreFoundation / libdispatch
   (GCD). Those frameworks spin up **process-global worker threads you cannot
   `join()`** and flip a **one-way** "fork-unsafe" latch in the Objective-C
   runtime. Using a *synchronous* `httpx.Client` and closing it (`with
   httpx.Client() as c: ...`) does **not** help — those threads and that state
   are owned by the OS, not by the client. (`http.py` already does exactly this
   and still crashed; that is the proof.)

4. **Intermittent because it is a race.** The child only segfaults when the
   `fork()` happens to coincide with a background thread holding the
   malloc/framework lock. Win the race → clean child → ffmpeg runs. Lose it →
   SIGSEGV. This is why "it worked 10/10 times" then "failed 8/10" — none of
   those variants fixed anything; they just perturbed the timing.

## Why `posix_spawn` fixes it

`posix_spawn` on macOS is effectively an atomic kernel operation: the new
process comes up already running the target image. There is **no userspace
child code executing `malloc` in the poisoned address space**, so the fork
window — and the poison — simply do not exist.

CPython already *prefers* `posix_spawn`, gated in `subprocess.py` by:

```python
(not close_fds or _HAVE_POSIX_SPAWN_CLOSEFROM)
```

- `close_fds` defaults to `True`.
- `_HAVE_POSIX_SPAWN_CLOSEFROM` is `False` on macOS (no `closefrom` /
  `close_range`) and `True` on modern Linux glibc (≥ 2.34).

So with default arguments **Linux already uses `posix_spawn`**, while **macOS is
forced onto the dangerous `fork` path** — the worst possible split. Passing
`close_fds=False` satisfies the gate's first term and flips macOS onto
`posix_spawn`.

## Why this is safe, and why it is Darwin-gated

- **`close_fds=False` does not leak fds.** Since PEP 446 (Python 3.4) every fd
  Python opens is `O_CLOEXEC` by default, so the child does not inherit our
  sockets regardless of the flag. `close_fds=True` is belt-and-suspenders
  hygiene whose practical leak surface is ~zero on modern Python.
- **`posix_spawn` is not exotic.** It is already the *default* `subprocess`
  path on modern Linux, and CPython only enables it when the platform reports
  `exec` failures correctly (`_use_posix_spawn()`).
- **We still gate the override to macOS** (`sys.platform == "darwin"`) so Linux
  behavior is byte-for-byte unchanged, and we bow out when a caller uses
  `pass_fds` (which mandates `close_fds=True`) or sets `close_fds` explicitly.

## Rules for this repo

- **Use `ja_media_core.proc.run` for external processes** (`ffmpeg`, `ffprobe`,
  `pbcopy`, …), especially in any tool that also talks to a service over HTTP.
  Do not call `subprocess.run` directly for those.
- `subprocess` constants/exceptions (`PIPE`, `DEVNULL`, `CalledProcessError`)
  are fine to import and use; only the *launch* needs the wrapper.
- The override only helps the `posix_spawn`-eligible cases. If you genuinely
  need `preexec_fn`, `start_new_session`, etc. on macOS in a networked process,
  you are back on the fork path — isolate the HTTP work in a separate process
  instead.

## Reproductions / further reading

- `scripts/reproduce_macos_http_subprocess_segv.py` — minimal HTTP-then-spawn
  matrix, including a `--posix-spawn` toggle.
- `scripts/reproduce_macos_nfs_probe_crash.py` — related NFS/ffprobe variant.
- The famous user-facing symptom of the same root cause is the
  `objc[…] +[… initialize] may have been in progress in another thread when
  fork() was called` abort, and the `OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES`
  workaround — which only silences the objc check and does *not* fix the
  malloc-lock SIGSEGV we hit.
