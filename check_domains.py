import csv
import sys
import time
import socket
import urllib.request
import urllib.error
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

def check_rdap(domain: str) -> Tuple[str, str]:
    """
    Queries the public RDAP bootstrap server to determine registration status.
    Returns a tuple of (Status, Detail).
    """
    url = f"https://rdap.org/domain/{domain}"
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) DomainAvailabilityChecker/1.0"}
    )
    
    try:
        # We perform a GET request. A 200 OK means the domain is registered.
        with urllib.request.urlopen(req, timeout=5.0) as response:
            if response.status == 200:
                return "Registered", "Registered (Inactive DNS)"
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return "Available", "Unregistered (404 Not Found)"
        elif e.code == 429:
            return "Rate Limited", "RDAP registry rate-limited requests"
        else:
            return "Unknown", f"HTTP Error {e.code}"
    except urllib.error.URLError as e:
        return "Error", f"Network error: {e.reason}"
    except Exception as e:
        return "Error", f"Unexpected error: {str(e)}"
    
    return "Unknown", "Could not determine status"

def process_domains(input_file: str, output_file: str, delay: float = 1.5):
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
    try:
        with open(output_file, 'r', encoding='utf-8') as csvfile:
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
        print(f"Warning: Could not read existing output file '{output_file}': {e}")

    now = datetime.now()
    results = {}
    to_check = []

    for domain in domains:
        if domain in existing:
            status, detail, last_checked = existing[domain]
            days_old = 9999
            if last_checked:
                try:
                    dt = datetime.strptime(last_checked, "%Y-%m-%d %H:%M:%S")
                    days_old = (now - dt).days
                except ValueError:
                    try:
                        dt = datetime.fromisoformat(last_checked)
                        days_old = (now - dt).days
                    except ValueError:
                        pass
            
            if days_old < 30:
                print(f"Skipping {domain} (cached result is {days_old} days old: {status} - {detail})")
                results[domain] = (status, detail, last_checked)
                continue
        
        to_check.append(domain)

    if not to_check:
        print("All domains have recent cached results. No checks needed.")
    else:
        print(f"Loaded {len(domains)} domains. Checking {len(to_check)} domains (skipping {len(domains) - len(to_check)} cached domains).")

    # Step 1: DNS Pre-filtering
    candidates = []

    if to_check:
        print("Starting DNS pre-filtering...")
        for domain in to_check:
            print(f"Checking DNS for {domain}... ", end="", flush=True)
            if resolve_dns(domain):
                print("RESOLVED (Registered)")
                results[domain] = ("Registered", "Registered (Active DNS)", now.strftime("%Y-%m-%d %H:%M:%S"))
            else:
                print("NXDOMAIN (Candidate)")
                candidates.append(domain)

        print(f"\nDNS pre-filtering complete.")
        print(f"Registered (Active DNS): {len(to_check) - len(candidates)}")
        print(f"Candidates for RDAP lookup: {len(candidates)}")

    if candidates:
        print(f"\nStarting RDAP lookups for {len(candidates)} candidates with a {delay}s delay to prevent rate limits...")
        
        for idx, domain in enumerate(candidates, start=1):
            print(f"[{idx}/{len(candidates)}] Querying RDAP for {domain}... ", end="", flush=True)
            status, detail = check_rdap(domain)
            print(f"{status.upper()} ({detail})")
            results[domain] = (status, detail, now.strftime("%Y-%m-%d %H:%M:%S"))
            
            # Wait to respect registry rate limits
            if idx < len(candidates):
                time.sleep(delay)

    # Write results to CSV
    try:
        with open(output_file, 'w', newline='', encoding='utf-8') as csvfile:
            writer = csv.writer(csvfile)
            writer.writerow(["Domain", "Status", "Details", "LastChecked"])
            for domain in domains:
                status, detail, last_checked = results[domain]
                writer.writerow([domain, status, detail, last_checked])
        print(f"\nSuccess! Results written to: {output_file}")
    except Exception as e:
        print(f"\nError writing to output file '{output_file}': {e}")

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python check_domains.py <input_txt_file> <output_csv_file> [delay_seconds]")
        print("Example: python check_domains.py domains.txt results.csv 1.5")
        sys.exit(1)

    infile = sys.argv[1]
    outfile = sys.argv[2]
    
    # Optional delay argument
    rdap_delay = 1.5
    if len(sys.argv) >= 4:
        try:
            val = float(sys.argv[3])
            if val < 0:
                print("Delay cannot be negative. Defaulting to 1.5 seconds.")
            else:
                rdap_delay = val
        except ValueError:
            print("Invalid delay value. Defaulting to 1.5 seconds.")

    process_domains(infile, outfile, rdap_delay)
