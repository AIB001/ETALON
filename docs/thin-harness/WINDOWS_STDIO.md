# Windows stdio and lazy native imports

ETALON's stdio entrypoint keeps scientific imports on demand. Reading skills or
memory does not preload NumPy, PyArrow or the molecular execution stack.

On the tested Windows/Conda Python 3.13 environment, a pending synchronous stdin
pipe read stalled the first NumPy native import during `etalon_screen_configure`.
The same failure was reproduced against the prior source revision. Standalone
imports succeeded, and additional client input released the stalled import.
This identifies an input/import interaction, not the particular native lock
responsible for it.

`server.main()` now wraps only Windows pipe input. Its single reader waits with
`PeekNamedPipe` and calls `ReadFile` for at most the bytes already available.
The MCP SDK continues to handle UTF-8 decoding, JSON-RPC and tool scheduling.
No private SDK server API or scientific preload is used. POSIX, regular files
and console input follow the existing path.

The adapter borrows the stdin handle and never closes it. Broken/disconnected
pipes produce EOF; other read errors propagate. A stop signal precedes buffer
cleanup, and the input worker cooperatively checks AnyIO cancellation. This
does not change the cancellation semantics of scientific tool workers.

There must be exactly one stdin reader and no competing blocking read on the
same pipe. Microsoft documents that `PeekNamedPipe` itself can block on a
synchronous handle in a multithreaded application; this is a bounded workaround
verified for ETALON's single-reader stdio lifecycle, not a general Windows I/O
guarantee. Idle input checks wait 10 ms between probes and use the SDK's existing
reader thread. See [Microsoft's API contract](https://learn.microsoft.com/en-us/windows/win32/api/namedpipeapi/nf-namedpipeapi-peeknamedpipe)
and [AnyIO's cooperative thread cancellation](https://anyio.readthedocs.io/en/stable/threads.html#reacting-to-cancellation-in-worker-threads).

`tests/test_mcp_cold_start.py` exercises a real cold MCP process with a 20-second
request timeout and a 40-second outer process timeout. It observes `sys.modules`
before and after configure, verifies split UTF-8/CRLF, multiple messages, final
partial lines, EOF, invalid handles, cancellation and input restoration.

Windows configure support does not imply Windows durable workflow execution.
Durable workers retain their existing POSIX file-lock requirement. The full
workflow CPU fixture is skipped on unsupported platforms; the cold configure
test still runs on Windows. The CPU property fixture makes no biological claim.
