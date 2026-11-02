# Reusing a pinned multisensor archive session

For repeated reads from one 7z archive, use `LeakMultisensorArchiveSession` as a context manager. Opening a session verifies the catalog-pinned archive and inventories the allowlisted sensor members. It reads one requested member at a time into memory and avoids rehashing the 717 MB archive for every file.

Each member receipt is provisional until the context exits successfully. On exit, the archive SHA-256 and archive-tool identity are checked again. Do not treat extracted records as verified outside the session before that final check succeeds. Member paths are matched against exact sensor-family allowlists, special files are rejected, and each extraction has an explicit byte and time budget.

`verify_leak_multisensor_member_source` replays a member receipt against its bytes without assigning physical units, event truth, clock synchronization, or a hydrophone decoder. Numerical and signal-analysis tests remain deferred to Nurion.
