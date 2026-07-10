# TODO: Domain Availability Checker Improvements

This file tracks the enhancements planned for the bulk domain checking tool.

## Technical Tasks

- [x] **RDAP Rate-Limiting Retry & Backoff (Dynamic Rate Limiting)**
  - Handle HTTP 429 errors in `check_rdap` with exponential backoff.
  - Make maximum retries configurable via `--retries` parameter (default: `3`).
  - Print informative retry messages to standard error.

- [x] **Concurrent DNS Pre-filtering**
  - Implement concurrent DNS lookup using standard library `concurrent.futures.ThreadPoolExecutor`.
  - Add configurable `--threads` parameter (default: `20`).
  - Sort DNS candidates before moving to RDAP checking to maintain structured output.

- [x] **Robust Process Lifecycle (Graceful Shutdown)**
  - Wrap processing loops in `try...finally` to ensure partial progress is written to the output file on `KeyboardInterrupt` (Ctrl+C).
  - Preserving existing cache values in output file for domains not checked during an interrupted run.

- [x] **Argparse CLI Integration**
  - Replace positional argument parsing with standard library `argparse`.
  - Maintain backward compatibility for `input_file`, `output_file`, and `delay`.
  - Implement options:
    - `-t` / `--threads`: Number of concurrent DNS threads.
    - `-r` / `--retries`: RDAP rate limit retries count.

- [x] **Output Enhancements**
  - Live thread-safe writing of results one-by-one.
  - Real-time progress and completion ETA.
