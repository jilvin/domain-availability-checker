# Domain Availability Checker

A fast, lightweight, and rate-limit-friendly command-line script to check domain availability in bulk. 

Unlike traditional checkers that scrape websites or require developer registration with registrars (like GoDaddy or Namecheap), this tool runs **completely anonymously** and **requires no API keys**.

## Features

- **No Registrar Accounts / API Keys Required:** Runs 100% out of the box using public standards.
- **DNS Pre-filtering:** Instantly filters out active/registered domains using concurrent DNS resolution, saving CPU and network resources.
- **Multithreaded Execution:** Leverages Python's thread pool concurrency (`ThreadPoolExecutor`) for both DNS resolving and RDAP checking. The degree of concurrency is configurable via `--threads`.
- **RDAP Verification:** Queries the Registration Data Access Protocol (RDAP) API for candidate domains (lacking DNS records) to verify availability.
- **Auto-Tuning Dynamic Rate Limiter:** Built-in feedback loop that detects registry rate limits (HTTP 429), dynamically scales the launch delay, and sleeps all threads during the cooldown.
- **Jitter-Proof Launch Spacing:** Guarantees sequential, thread-safe spacing between requests and retries to prevent concurrent spikes from triggering server blocks.
- **Smart Resume Caching:** Reads the output file or an optional cache CSV (`-c` / `--cache`) on startup. Skips completed results, but automatically queues previous failures (`Rate Limited`, `Error`, `Unknown`) directly back to the RDAP check, bypassing redundant DNS resolution. The cache parser supports standard datetimes as well as Excel serial dates.
- **Live Thread-Safe Writing:** Appends results to the CSV file one by one using a thread lock to prevent data loss if the run is interrupted.
- **Real-Time Progress & ETA:** Displays log indices, lookup statuses, and a dynamically calculated human-readable Estimated Time to Completion (ETA).

## How It Works

```mermaid
graph TD
    A[Input: domains.txt] --> B{Clean & Validate Domain}
    B -->|Valid| C{DNS Resolution}
    B -->|Invalid| H[Skip Domain]
    C -->|Resolves to IP| D[Status: Registered / Active DNS]
    C -->|NXDOMAIN| E[Candidate for RDAP]
    E --> F[Query RDAP Server with delay]
    F -->|HTTP 200| G[Status: Registered / Inactive DNS]
    F -->|HTTP 404| I[Status: Available]
    F -->|HTTP 429| J[Status: Rate Limited]
    D --> K[Write to CSV]
    G --> K
    I --> K
    J --> K
```

## Setup & Requirements

The script uses Python's standard library. There are **no external dependencies** to install.

1. Clone this repository.
2. Create an input text file (e.g., `domains.txt`) listing the domains you wish to check, with one domain per line.

## Usage

The script is executed from the command line by passing positional arguments and optional flags:

```bash
python check_domains.py <input_file> <output_file> [delay] [options]
```

### Command Line Options

| Argument / Flag | Type | Description | Default |
| :--- | :--- | :--- | :--- |
| `input_file` | Positional | Path to the text file listing domains to check (one per line). | *(Required)* |
| `output_file` | Positional | Path to the CSV file where results will be saved. | *(Required)* |
| `delay` | Positional (Optional) | Base launch delay spacing (in seconds) between RDAP requests. | `1.2` |
| `-t, --threads` | Flag (Optional) | Number of concurrent worker threads. *Note: Using > 2 threads prints a warning since overlapping concurrent requests can trigger 403 Forbidden blocks on rdap.org.* | `1` |
| `-r, --retries` | Flag (Optional) | Maximum retries allowed for rate-limited requests before marking as `Rate Limited`. | `3` |
| `-c, --cache` | Flag (Optional) | Path to an optional cache CSV file to skip already checked domains. | *None* |

---

### Usage Examples

#### 1. Basic Bulk Check (Safe and Recommended)
Checks the list using a safe `1.5s` delay and limits the worker threads to `2` to avoid concurrent connection blocks:
```bash
python check_domains.py domains.txt results.csv 1.5 --threads 2
```

#### 2. Optimized Speed Check
Launches the checker with a fine-tuned `1.15s` delay using `2` threads:
```bash
python check_domains.py domains.txt results.csv 1.15 --threads 2
```

#### 3. Single-Threaded Sequential Check
Ensures absolute zero overlapping connection concurrency (safest method for long runs):
```bash
python check_domains.py domains.txt results.csv 1.2 --threads 1
```

#### 4. Resilient Check (High Retries)
Allows rate-limited requests (HTTP 429) to retry up to `5` times:
```bash
python check_domains.py domains.txt results.csv 1.15 --threads 2 --retries 5
```

### Rate Limits & HTTP 403 Forbidden Blocks
The public RDAP bootstrap service is protected by Cloudflare, which enforces rate limits on both request frequency and connection concurrency.

* **Rate Limit (HTTP 429)**: Limit is roughly **10 requests in 10 seconds** per IP. If triggered, the script automatically scales query delays up by `0.1s` and sleeps all threads during a 30-second cooldown.
* **Abuse / Concurrency Block (HTTP 403)**: Triggered when too many concurrent requests are sent simultaneously from the same IP (which happens when `--threads` is set > 2).
  * **Immediate Termination**: If a 403 is detected, the script saves all current results and exits immediately with status `1`, since waiting or retrying will not resolve the block.
  * **Ban Lifespan**: Ban durations are managed by Cloudflare/rdap.org. For standard residential or commercial IPs, bans typically last **1 to 24 hours**. For datacenter, VPN, or cloud provider IP ranges, the ban may be **permanent**.
  * **Prevention**: Always use `--threads 2` or `--threads 1` to ensure requests are sequential and do not overlap. If you get blocked, wait 24 hours or run the script using a different IP address (e.g., via a VPN or proxy).

## Output Format

The output CSV file contains the following columns:

| Column | Description |
| :--- | :--- |
| **Domain** | The domain name checked. |
| **Status** | `Available`, `Registered`, `Rate Limited`, `Error`, or `Unknown`. |
| **Details** | Additional context (e.g., `Registered (Active DNS)`, `Unregistered (404 Not Found)`). |
| **LastChecked** | Date and time the domain status was verified. |

## Running Tests

This project includes a test suite under `test_check_domains.py` using Python's standard `unittest` library. It covers domain validation, cache loading and skip behavior, resumption logic, and mock DNS/RDAP resolution.

To run all tests:

```bash
python -m unittest test_check_domains.py
```

