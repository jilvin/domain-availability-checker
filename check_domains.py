import csv
import os
import sys
import time
import socket
import urllib.request
import urllib.error
import argparse
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Tuple
from datetime import datetime

# Set a timeout for socket connections
socket.setdefaulttimeout(3.0)

def is_valid_domain(domain: str) -> bool:
    """Check to ensure domain looks valid according to RFC standards."""
    domain = domain.strip()
    # Handle optional trailing dot
    if domain.endswith("."):
        domain = domain[:-1]
    if not domain:
        return False
    parts = domain.split(".")
    if len(parts) < 2:
        return False
    for part in parts:
        if not part or len(part) > 63 or part.startswith("-") or part.endswith("-"):
            return False
        if not all(c.isalnum() or c == "-" for c in part):
            return False
    return True

def resolve_dns(domain: str) -> bool:
    """
    Attempts to resolve the domain via DNS (A or AAAA records).
    Returns True if resolution succeeds (domain is registered/active).
    Returns False if resolution fails (domain might be available).
    """
    try:
        # getaddrinfo performs standard resolution for A/AAAA records
        socket.getaddrinfo(domain, None)
        return True
    except socket.gaierror:
        # gaierror is thrown when name resolution fails (e.g. NXDOMAIN)
        return False
    except Exception:
        # Any other network or socket error
        return False

def check_rdap(domain: str, max_retries: int = 3, rate_limit_state: dict = None) -> Tuple[str, str]:
    """
    Queries the public RDAP bootstrap server to determine registration status.
    Returns a tuple of (Status, Detail).
    """
    url = f"https://rdap.org/domain/{domain}"
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) DomainAvailabilityChecker/1.0"}
    )
    
    retries = 0
    backoff = 2.0  # Initial backoff in seconds
    
    while True:
        if rate_limit_state is not None:
            cooldown_period = rate_limit_state.get("cooldown_period", 30.0)
            while True:
                # 1. Check and sleep for cooldown outside the lock
                with rate_limit_state["rate_limit_lock"]:
                    now_time = time.time()
                    cooldown_remaining = cooldown_period - (now_time - rate_limit_state.get("last_rate_limit_time", 0.0))
                
                if cooldown_remaining > 0:
                    time.sleep(cooldown_remaining)
                    continue
                
                # 2. Try to acquire launch slot
                with rate_limit_state["rate_limit_lock"]:
                    now_time = time.time()
                    # Double-check if cooldown was updated while waiting for the lock
                    cooldown_remaining = cooldown_period - (now_time - rate_limit_state.get("last_rate_limit_time", 0.0))
                    if cooldown_remaining > 0:
                        # A cooldown was triggered, release lock and loop back to sleep
                        continue
                    
                    # No cooldown, enforce normal request launch spacing
                    elapsed = now_time - rate_limit_state["last_request_time"]
                    current_delay = rate_limit_state["delay"]
                    if elapsed < current_delay:
                        sleep_time = current_delay - elapsed
                        time.sleep(sleep_time)
                        rate_limit_state["last_request_time"] = time.time()
                    else:
                        rate_limit_state["last_request_time"] = now_time
                    break

        try:
            # We perform a GET request. A 200 OK means the domain is registered.
            with urllib.request.urlopen(req, timeout=5.0) as response:
                if response.status == 200:
                    return "Registered", "Registered (Inactive DNS)"
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return "Available", "Unregistered (404 Not Found)"
            elif e.code == 403:
                print(
                    f"HTTP Error 403: Forbidden checking '{domain}'. Blocked due to abuse or other misbehaviour. Exiting...",
                    file=sys.stderr,
                    flush=True
                )
                return "Blocked", "Blocked (HTTP 403 Forbidden)"
            elif e.code == 429:
                if rate_limit_state is not None:
                    with rate_limit_state["rate_limit_lock"]:
                        now_time = time.time()
                        last_rl = rate_limit_state.get("last_rate_limit_time", 0.0)
                        cooldown_period = rate_limit_state.get("cooldown_period", 30.0)
                        # Only scale delay if we haven't already scaled it in the last cooldown period
                        if now_time - last_rl >= cooldown_period:
                            old_delay = rate_limit_state.get("delay", 1.5)
                            new_delay = old_delay + 0.1
                            print(
                                f"Rate limit hit! Dynamically scaling query delay from {old_delay:.2f}s to {new_delay:.2f}s.",
                                file=sys.stderr,
                                flush=True
                            )
                            rate_limit_state["delay"] = new_delay
                            rate_limit_state["last_rate_limit_time"] = now_time
                
                if retries < max_retries:
                    retries += 1
                    retry_wait = rate_limit_state.get("cooldown_period", 30.0) if rate_limit_state else 30.0
                    print(
                        f"Rate limited checking '{domain}'. Waiting {retry_wait:.1f}s before retry {retries}/{max_retries}...",
                        file=sys.stderr,
                        flush=True
                    )
                    time.sleep(retry_wait)
                    continue
                else:
                    return "Rate Limited", "RDAP registry rate-limited requests"
            else:
                return "Unknown", f"HTTP Error {e.code}"
        except urllib.error.URLError as e:
            return "Error", f"Network error: {e.reason}"
        except Exception as e:
            return "Error", f"Unexpected error: {str(e)}"
        
        return "Unknown", "Could not determine status"

def parse_date(date_str: str) -> datetime:
    """
    Parses a date string from various common formats (including ISO and standard CSV formats).
    Returns a datetime object if successful, or None if parsing fails.
    """
    formats = [
        "%Y-%m-%d %H:%M:%S",
        "%d,%m,%Y %H:%M",
        "%d/%m/%Y %H:%M",
        "%Y/%m/%d %H:%M:%S",
        "%d-%m-%Y %H:%M:%S",
        "%d/%m/%Y %H:%M:%S",
        "%Y-%m-%d"
    ]
    for fmt in formats:
        try:
            return datetime.strptime(date_str.strip().strip('"'), fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(date_str.strip().strip('"'))
    except ValueError:
        return None

def process_domains(input_file: str, output_file: str, cache_file: str = None, delay: float = 1.2, retries: int = 3, threads: int = 20, cooldown_period: float = 30.0):
    """
    Processes a list of domains from input_file, checking their availability.
    
    The process consists of:
      1. Loading any existing results from cache_file and output_file.
      2. Skipping domains that have recent cached results (less than 30 days old).
      3. Performing concurrent DNS pre-filtering on the remaining domains.
      4. Querying the public RDAP bootstrap server concurrently for domains that fail DNS resolution.
      5. Enforcing launch delay and dynamic rate-limiting retry/backoff for RDAP requests.
      6. Writing progress and final results to output_file thread-safely, ensuring progress is
         preserved even if the script is interrupted.
    """
    # Read domains from file
    try:
        with open(input_file, 'r', encoding='utf-8') as f:
            raw_domains = f.read().splitlines()
    except FileNotFoundError:
        print(f"Error: Input file '{input_file}' not found.")
        sys.exit(1)
    except Exception as e:
        print(f"Error reading input file '{input_file}': {e}")
        sys.exit(1)

    # Filter and clean domains
    domains = []
    for d in raw_domains:
        cleaned = d.strip().lower()
        if cleaned and is_valid_domain(cleaned):
            domains.append(cleaned)
        elif cleaned:
            print(f"Skipping invalid domain format: '{d}'")

    if not domains:
        print("No valid domains found to process.")
        sys.exit(0)

    # Load existing results if they exist
    existing = {}
    if cache_file:
        try:
            with open(cache_file, 'r', encoding='utf-8') as csvfile:
                reader = csv.DictReader(csvfile)
                for row in reader:
                    domain = row.get("Domain", "").strip().lower()
                    if domain:
                        status = row.get("Status", "")
                        details = row.get("Details", "")
                        last_checked = row.get("LastChecked", "")
                        existing[domain] = (status, details, last_checked)
        except FileNotFoundError:
            pass
        except Exception as e:
            print(f"Warning: Could not read cache file '{cache_file}': {e}")

    if output_file:
        try:
            with open(output_file, 'r', encoding='utf-8') as csvfile:
                reader = csv.DictReader(csvfile)
                for row in reader:
                    domain = row.get("Domain", "").strip().lower()
                    if domain:
                        status = row.get("Status", "")
                        details = row.get("Details", "")
                        last_checked = row.get("LastChecked", "")
                        if status not in ("Unchecked", "Not checked yet", "") or domain not in existing:
                            existing[domain] = (status, details, last_checked)
        except FileNotFoundError:
            pass
        except Exception as e:
            print(f"Warning: Could not read existing output file '{output_file}': {e}")

    now = datetime.now()
    results = {}
    to_check = []
    candidates = []

    for domain in domains:
        if domain in existing:
            status, detail, last_checked = existing[domain]
            if status in ("Unchecked", "Rate Limited", "Error", "Unknown"):
                # Already verified as NXDOMAIN (Candidate) in a previous run, skip DNS check and queue for retry
                candidates.append(domain)
                continue

            days_old = 9999
            if last_checked:
                dt = parse_date(last_checked)
                if dt:
                    days_old = (now - dt).days
            
            if days_old < 30:
                print(f"Skipping {domain} (cached result is {days_old} days old: {status} - {detail})")
                results[domain] = (status, detail, last_checked)
                continue
        
        to_check.append(domain)

    if not to_check:
        print("All domains have recent cached results. No checks needed.")
    else:
        print(f"Loaded {len(domains)} domains. Checking {len(to_check)} domains (skipping {len(domains) - len(to_check)} cached domains).")

    csv_write_lock = threading.Lock()

    def save_results(quiet=False):
        with csv_write_lock:
            try:
                with open(output_file, 'w', newline='', encoding='utf-8') as csvfile:
                    writer = csv.writer(csvfile)
                    writer.writerow(["Domain", "Status", "Details", "LastChecked"])
                    for d in domains:
                        if d in results:
                            status, detail, last_checked = results[d]
                        elif d in existing:
                            status, detail, last_checked = existing[d]
                        else:
                            status, detail, last_checked = "Unchecked", "Not checked yet", ""
                        writer.writerow([d, status, detail, last_checked])
                if not quiet:
                    print(f"\nProgress saved to: {output_file}")
            except Exception as e:
                print(f"\nError writing to output file '{output_file}': {e}", file=sys.stderr)

    try:
        # Step 1: DNS Pre-filtering
        # (candidates list already contains Unchecked domains from cache)

        if to_check:
            print(f"Starting concurrent DNS pre-filtering using {threads} threads...")
            dns_resolved_count = 0
            
            def check_dns_worker(domain):
                resolved = resolve_dns(domain)
                return domain, resolved

            with ThreadPoolExecutor(max_workers=threads) as executor:
                for domain, resolved in executor.map(check_dns_worker, to_check):
                    if resolved:
                        print(f"Checking DNS for {domain}... RESOLVED (Registered)", flush=True)
                        results[domain] = ("Registered", "Registered (Active DNS)", now.strftime("%Y-%m-%d %H:%M:%S"))
                        dns_resolved_count += 1
                    else:
                        print(f"Checking DNS for {domain}... NXDOMAIN (Candidate)", flush=True)
                        candidates.append(domain)

            print(f"\nDNS pre-filtering complete.")
            print(f"Registered (Active DNS): {dns_resolved_count}")
            print(f"Candidates for RDAP lookup: {len(candidates)}")
            
            # Commit DNS checks to cache right away
            save_results()

        if candidates:
            rate_limit_state = {
                "delay": delay,
                "last_request_time": 0.0,
                "last_rate_limit_time": 0.0,
                "cooldown_period": cooldown_period,
                "rate_limit_lock": threading.Lock()
            }
            print(f"\nStarting concurrent RDAP lookups for {len(candidates)} candidates using {threads} threads with a {rate_limit_state['delay']}s launch delay...")
            
            rdap_start_time = time.time()

            def format_eta(seconds: float) -> str:
                if seconds < 60:
                    return f"{int(seconds)}s"
                elif seconds < 3600:
                    minutes = int(seconds // 60)
                    secs = int(seconds % 60)
                    return f"{minutes}m {secs}s"
                else:
                    hours = int(seconds // 3600)
                    minutes = int((seconds % 3600) // 60)
                    return f"{hours}h {minutes}m"

            completed_lock = threading.Lock()
            completed_count = 0

            def check_rdap_worker(domain):
                nonlocal completed_count
                status, detail = check_rdap(domain, max_retries=retries, rate_limit_state=rate_limit_state)
                
                with completed_lock:
                    completed_count += 1
                    current_completed = completed_count
                
                # Calculate ETA based on elapsed time and candidates remaining
                elapsed = time.time() - rdap_start_time
                avg_time = elapsed / current_completed
                remaining = len(candidates) - current_completed
                eta_str = format_eta(avg_time * remaining) if remaining > 0 else "0s"
                
                print(f"[{current_completed}/{len(candidates)}] Querying RDAP for {domain}... {status.upper()} ({detail}) [ETA: {eta_str}]", flush=True)
                
                results[domain] = (status, detail, now.strftime("%Y-%m-%d %H:%M:%S"))
                
                # Save progress progressively to prevent loss if terminated
                save_results(quiet=True)
                
                if status == "Blocked":
                    os._exit(1)

            with ThreadPoolExecutor(max_workers=threads) as executor:
                list(executor.map(check_rdap_worker, candidates))
    finally:
        save_results()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Bulk Domain Availability Checker")
    parser.add_argument("input_file", help="Input text file containing domains")
    parser.add_argument("output_file", help="Output CSV file for results")
    parser.add_argument("delay", type=float, nargs="?", default=1.2, help="Delay between RDAP queries in seconds")
    parser.add_argument("-r", "--retries", type=int, default=3, help="RDAP rate limit retries count")
    parser.add_argument("-t", "--threads", type=int, default=20, help="Number of concurrent threads")
    parser.add_argument("-c", "--cache", help="Optional cache CSV file to skip already checked domains")
    
    args = parser.parse_args()
    
    if args.delay < 0:
        print("Delay cannot be negative. Defaulting to 1.2 seconds.", file=sys.stderr)
        args.delay = 1.2
        
    if args.retries < 0:
        print("Retries cannot be negative. Defaulting to 3.", file=sys.stderr)
        args.retries = 3
        
    if args.threads <= 0:
        print("Threads must be a positive integer. Defaulting to 20.", file=sys.stderr)
        args.threads = 20

    if args.threads > 2:
        print(
            "\033[91mWarning: Using more than 2 threads may lead to concurrent requests, which can trigger abuse blocks (403 Forbidden) on rdap.org.\033[0m",
            file=sys.stderr,
            flush=True
        )

    process_domains(
        input_file=args.input_file,
        output_file=args.output_file,
        cache_file=args.cache,
        delay=args.delay,
        retries=args.retries,
        threads=args.threads
    )
