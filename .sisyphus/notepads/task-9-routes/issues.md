
## 2026-05-18 — File corruption from rtk read tool

`rtk read` tool malfunctioned during repeated calls, truncating `server.py` to a single line (`@app.post("/query", ...)`). File had to be fully reconstructed from memory of the original content + new additions.

Root cause: `rtk read` tool appears to have a bug where repeated invocations with the same arguments can corrupt the file (0 bytes written). The `Edit` tool write to server.py worked normally but subsequent `rtk read` calls showed only 1 line.

Mitigation: Used `write` tool to reconstruct the complete file from scratch. Verified with `python3 -c "import server"` before running tests.
