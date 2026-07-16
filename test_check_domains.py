import unittest
from unittest.mock import patch, MagicMock
import os
import csv
import tempfile
import shutil

# Import the module under test
import check_domains

class TestDomainChecker(unittest.TestCase):
    """Unit tests for the bulk domain availability checker."""

    def setUp(self):
        """Set up temporary test directory and file paths."""
        self.test_dir = tempfile.mkdtemp()
        self.input_file = os.path.join(self.test_dir, "input.txt")
        self.output_file = os.path.join(self.test_dir, "output.csv")
        self.cache_file = os.path.join(self.test_dir, "cache.csv")

    def tearDown(self):
        """Clean up temporary test directory."""
        shutil.rmtree(self.test_dir)

    def test_is_valid_domain(self):
        """Test domain format validation against RFC standards."""
        # Valid domains
        self.assertTrue(check_domains.is_valid_domain("google.com"))
        self.assertTrue(check_domains.is_valid_domain("sub.domain.co.uk"))
        self.assertTrue(check_domains.is_valid_domain("domain-with-hyphen.com"))
        # Invalid domains
        self.assertFalse(check_domains.is_valid_domain("google"))
        self.assertFalse(check_domains.is_valid_domain("-google.com"))
        self.assertFalse(check_domains.is_valid_domain("google-.com"))
        self.assertFalse(check_domains.is_valid_domain("google..com"))
        self.assertFalse(check_domains.is_valid_domain("google .com"))
        self.assertFalse(check_domains.is_valid_domain("# comment line"))

    @patch("check_domains.resolve_dns")
    @patch("check_domains.check_rdap")
    def test_process_domains_with_external_cache(self, mock_check_rdap, mock_resolve_dns):
        """Test that cached results from an external cache file are skipped for checks but preserved in the output."""
        # Setup inputs
        domains = ["cached.com", "new-active.com", "new-inactive.com"]
        with open(self.input_file, "w", encoding="utf-8") as f:
            f.write("\n".join(domains) + "\n")

        # Setup cache: cached.com is already checked
        with open(self.cache_file, "w", newline="", encoding="utf-8") as csvfile:
            writer = csv.writer(csvfile)
            writer.writerow(["Domain", "Status", "Details", "LastChecked"])
            writer.writerow(["cached.com", "Registered", "Registered (Active DNS)", "2026-07-12 10:58:48"])

        # Setup mock behavior
        # resolve_dns return values for: new-active.com (True), new-inactive.com (False)
        mock_resolve_dns.side_effect = lambda d: d == "new-active.com"
        # check_rdap return value for new-inactive.com
        mock_check_rdap.return_value = ("Registered", "Registered (Inactive DNS)")

        # Run process_domains
        check_domains.process_domains(
            input_file=self.input_file,
            output_file=self.output_file,
            cache_file=self.cache_file,
            delay=0.01,
            threads=2
        )

        # Assertions
        # 1. resolve_dns should NOT have been called for "cached.com"
        mock_resolve_dns.assert_any_call("new-active.com")
        mock_resolve_dns.assert_any_call("new-inactive.com")
        with self.assertRaises(AssertionError):
            mock_resolve_dns.assert_any_call("cached.com")

        # 2. Output file should contain only newly checked domains (not cached ones)
        self.assertTrue(os.path.exists(self.output_file))
        with open(self.output_file, "r", encoding="utf-8") as csvfile:
            reader = csv.DictReader(csvfile)
            rows = list(reader)

        output_domains = [row["Domain"] for row in rows]
        self.assertNotIn("cached.com", output_domains)
        self.assertIn("new-active.com", output_domains)
        self.assertIn("new-inactive.com", output_domains)
        self.assertEqual(len(rows), 2)

    @patch("check_domains.resolve_dns")
    @patch("check_domains.check_rdap")
    def test_process_domains_without_cache(self, mock_check_rdap, mock_resolve_dns):
        """Test domain checking from scratch when no cache is provided."""
        # Setup inputs
        domains = ["active.com", "inactive.com"]
        with open(self.input_file, "w", encoding="utf-8") as f:
            f.write("\n".join(domains) + "\n")

        mock_resolve_dns.side_effect = lambda d: d == "active.com"
        mock_check_rdap.return_value = ("Registered", "Registered (Inactive DNS)")

        # Run process_domains
        check_domains.process_domains(
            input_file=self.input_file,
            output_file=self.output_file,
            cache_file=None,
            delay=0.01,
            threads=2
        )

        # Assertions
        self.assertTrue(os.path.exists(self.output_file))
        with open(self.output_file, "r", encoding="utf-8") as csvfile:
            reader = csv.DictReader(csvfile)
            rows = list(reader)

        output_domains = [row["Domain"] for row in rows]
        self.assertIn("active.com", output_domains)
        self.assertIn("inactive.com", output_domains)
        self.assertEqual(len(rows), 2)

    @patch("check_domains.resolve_dns")
    @patch("check_domains.check_rdap")
    def test_process_domains_resume_same_file(self, mock_check_rdap, mock_resolve_dns):
        """Test resuming a check where output_file acts as the cache_file itself."""
        # Setup inputs
        domains = ["cached.com", "new.com"]
        with open(self.input_file, "w", encoding="utf-8") as f:
            f.write("\n".join(domains) + "\n")

        # Setup output_file as the cache file itself (resuming)
        with open(self.output_file, "w", newline="", encoding="utf-8") as csvfile:
            writer = csv.writer(csvfile)
            writer.writerow(["Domain", "Status", "Details", "LastChecked"])
            writer.writerow(["cached.com", "Registered", "Registered (Active DNS)", "2026-07-12 10:58:48"])

        mock_resolve_dns.return_value = True

        # Run process_domains (cache_file path equals output_file path)
        check_domains.process_domains(
            input_file=self.input_file,
            output_file=self.output_file,
            cache_file=self.output_file,
            delay=0.01,
            threads=2
        )

        # Assertions
        # 1. resolve_dns should NOT have been called for "cached.com"
        mock_resolve_dns.assert_called_once_with("new.com")

        # 2. Output file should contain BOTH cached.com and new.com (no data loss)
        self.assertTrue(os.path.exists(self.output_file))
        with open(self.output_file, "r", encoding="utf-8") as csvfile:
            reader = csv.DictReader(csvfile)
            rows = list(reader)

        output_domains = [row["Domain"] for row in rows]
        self.assertIn("cached.com", output_domains)
        self.assertIn("new.com", output_domains)
        self.assertEqual(len(rows), 2)

    @patch("urllib.request.urlopen")
    def test_check_rdap_handles_403(self, mock_urlopen):
        """Test check_rdap when HTTP 403 Forbidden is returned."""
        from urllib.error import HTTPError
        import io
        mock_urlopen.side_effect = HTTPError(
            url="https://rdap.org/domain/blocked.com",
            code=403,
            msg="Forbidden",
            hdrs=None,
            fp=io.BytesIO(b"")
        )

        status, detail = check_domains.check_rdap("blocked.com", max_retries=1)
        self.assertEqual(status, "Blocked")
        self.assertEqual(detail, "Blocked (HTTP 403 Forbidden)")

    @patch("os._exit")
    @patch("check_domains.resolve_dns")
    @patch("check_domains.check_rdap")
    def test_process_domains_exits_on_403(self, mock_check_rdap, mock_resolve_dns, mock_os_exit):
        """Test that process_domains exits immediately with status 1 on 403 Blocked."""
        domains = ["blocked.com"]
        with open(self.input_file, "w", encoding="utf-8") as f:
            f.write("\n".join(domains) + "\n")

        mock_resolve_dns.return_value = False
        mock_check_rdap.return_value = ("Blocked", "Blocked (HTTP 403 Forbidden)")

        check_domains.process_domains(
            input_file=self.input_file,
            output_file=self.output_file,
            cache_file=None,
            delay=0.01,
            threads=2
        )

        mock_os_exit.assert_called_once_with(1)

        # Assert output was saved
        self.assertTrue(os.path.exists(self.output_file))
        with open(self.output_file, "r", encoding="utf-8") as csvfile:
            reader = csv.DictReader(csvfile)
            rows = list(reader)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["Domain"], "blocked.com")
        self.assertEqual(rows[0]["Status"], "Blocked")
        self.assertEqual(rows[0]["Details"], "Blocked (HTTP 403 Forbidden)")

    @patch("check_domains.resolve_dns")
    def test_dns_pre_filtering_as_completed_and_sorted(self, mock_resolve_dns):
        """Test that DNS pre-filtering processes domains as_completed and sorts candidates to maintain domain.txt order."""
        import check_domains

        domains = ["second.com", "first.com", "third.com"]
        with open(self.input_file, "w", encoding="utf-8") as f:
            f.write("\n".join(domains) + "\n")

        mock_resolve_dns.return_value = False

        check_domains.process_domains(
            input_file=self.input_file,
            output_file=self.output_file,
            cache_file=None,
            delay=0.01,
            threads=3
        )

        with open(self.output_file, "r", encoding="utf-8") as csvfile:
            reader = csv.DictReader(csvfile)
            rows = list(reader)
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["Domain"], "second.com")
        self.assertEqual(rows[1]["Domain"], "first.com")
        self.assertEqual(rows[2]["Domain"], "third.com")

    @patch("check_domains.resolve_dns")
    @patch("check_domains.check_rdap")
    @patch("check_domains.time.time")
    def test_save_results_throttled(self, mock_time, mock_check_rdap, mock_resolve_dns):
        """Test that save_results respects write_interval throttling."""
        import check_domains
        from unittest.mock import patch

        class TimeStepper:
            def __init__(self):
                self.time_val = 100.0
            def __call__(self, *args, **kwargs):
                self.time_val += 0.001
                return self.time_val

        stepper = TimeStepper()
        mock_time.side_effect = stepper

        domains = ["d1.com", "d2.com", "d3.com"]
        with open(self.input_file, "w", encoding="utf-8") as f:
            f.write("\n".join(domains) + "\n")

        mock_resolve_dns.return_value = False
        mock_check_rdap.return_value = ("Available", "Unregistered (404 Not Found)")

        original_check_rdap = check_domains.check_rdap
        call_count = 0

        def mock_check_rdap_wrapper(domain, *args, **kwargs):
            nonlocal call_count
            call_count += 1
            res = original_check_rdap(domain, *args, **kwargs)
            if call_count == 1:
                # First check: advance time by 1s (so next check is throttled)
                stepper.time_val += 1.0
            elif call_count == 2:
                # Second check: advance by 6s (so third check writes to disk)
                stepper.time_val += 6.0
            return res

        import builtins
        original_open = builtins.open
        write_count = 0

        def mock_open_wrapper(file, mode='r', *args, **kwargs):
            nonlocal write_count
            if file == self.output_file and 'w' in mode:
                write_count += 1
            return original_open(file, mode, *args, **kwargs)

        with patch("check_domains.check_rdap", side_effect=mock_check_rdap_wrapper), \
             patch("check_domains.open", side_effect=mock_open_wrapper, create=True):
            check_domains.process_domains(
                input_file=self.input_file,
                output_file=self.output_file,
                cache_file=None,
                delay=0.01,
                threads=1
            )

        # Expected writes:
        # 1. DNS pre-filtering complete: save_results(force=True) -> write 1. last_write_time = 100.0.
        # 2. Worker 1 (now = 101.0): elapsed = 1.0 < 5.0 -> throttled.
        # 3. Worker 2 (now = 107.0): elapsed = 7.0 > 5.0 -> write 2. last_write_time = 107.0.
        # 4. Worker 3 (now = 107.0): elapsed = 0.0 < 5.0 -> throttled.
        # 5. finally block: save_results(force=True) -> write 3.
        # Total writes to output file should be exactly 3.
        self.assertEqual(write_count, 3)

    def test_cli_threads_warning(self):
        """Test that running the script with > 2 threads prints a red warning to stderr."""
        import subprocess
        import sys

        with open(self.input_file, "w", encoding="utf-8") as f:
            f.write("example.com\n")

        # Run with 3 threads. We expect the warning in stderr.
        result = subprocess.run(
            [sys.executable, "check_domains.py", self.input_file, self.output_file, "-t", "3"],
            capture_output=True,
            text=True
        )

        # Check that stderr contains the red ANSI warning message
        self.assertIn("\033[91mWarning:", result.stderr)

        # Run with 2 threads. We expect no such warning.
        result_ok = subprocess.run(
            [sys.executable, "check_domains.py", self.input_file, self.output_file, "-t", "2"],
            capture_output=True,
            text=True
        )
        self.assertNotIn("\033[91mWarning:", result_ok.stderr)

    @patch("check_domains.time")
    @patch("check_domains.urllib.request.urlopen")
    def test_rate_limiter_does_not_hold_lock_while_sleeping(self, mock_urlopen, mock_time):
        """Test that the rate limiter does not hold the lock while sleeping."""
        import check_domains
        import threading

        lock_was_held_during_sleep = []
        rate_limit_lock = threading.Lock()

        class MockClock:
            def __init__(self):
                self.time_val = 100.0
            def time(self):
                return self.time_val
            def sleep(self, seconds):
                if rate_limit_lock.locked():
                    lock_was_held_during_sleep.append(True)
                else:
                    lock_was_held_during_sleep.append(False)
                self.time_val += seconds

        clock = MockClock()
        mock_time.time.side_effect = clock.time
        mock_time.sleep.side_effect = clock.sleep

        # Mock urlopen to return a mock response
        mock_response = MagicMock()
        mock_response.status = 200
        mock_urlopen.return_value.__enter__.return_value = mock_response

        rate_limit_state = {
            "delay": 1.0,
            "next_request_time": 0.0,
            "last_rate_limit_time": 0.0,
            "cooldown_period": 30.0,
            "rate_limit_lock": rate_limit_lock
        }

        # First query (t=100.0) -> next_request_time becomes 101.0
        check_domains.check_rdap("d1.com", max_retries=1, rate_limit_state=rate_limit_state)
        # Second query (t=100.0) -> next_request_time becomes 102.0, sleeps 1.0s, t becomes 101.0
        check_domains.check_rdap("d2.com", max_retries=1, rate_limit_state=rate_limit_state)

        # Now trigger a cooldown sleep
        rate_limit_state["last_rate_limit_time"] = 101.0
        # Third query (t=101.0) -> next_request_time becomes 102.0. But wait!
        # When checking cooldown at t=101.0, cooldown_remaining = 30 - (101.0 - 101.0) = 30.0s.
        # So it sleeps for 30.0s (t becomes 131.0).
        # On loop back (t=131.0), cooldown has expired. It reserves slot at t=131.0, next_request_time becomes 132.0.
        check_domains.check_rdap("d3.com", max_retries=1, rate_limit_state=rate_limit_state)

        # Verify that sleeps occurred and lock was never held during sleeps
        self.assertTrue(len(lock_was_held_during_sleep) > 0)
        self.assertNotIn(True, lock_was_held_during_sleep)

    @patch("check_domains.resolve_dns")
    @patch("check_domains.check_rdap")
    def test_process_domains_with_directory_cache(self, mock_check_rdap, mock_resolve_dns):
        """Test loading cache from a directory containing multiple CSV files, including merge priority rules."""
        # Create a temp directory for cache
        cache_dir = os.path.join(self.test_dir, "cache_dir")
        os.makedirs(cache_dir, exist_ok=True)

        # We will create two cache files:
        # file1.csv:
        #   - domain1.com: checked, Registered, 2026-07-10 10:00:00
        #   - domain2.com: checked, Registered (Older), 2026-07-09 10:00:00
        #   - domain3.com: Unchecked, Not checked yet, ""
        # file2.csv:
        #   - domain2.com: checked, Registered (Newer), 2026-07-11 10:00:00  (should override domain2.com)
        #   - domain3.com: checked, Available, 2026-07-10 12:00:00          (should override Unchecked)
        #   - domain4.com: checked, Registered, 2026-07-10 10:00:00

        file1_path = os.path.join(cache_dir, "file1.csv")
        with open(file1_path, "w", newline="", encoding="utf-8") as csvfile:
            writer = csv.writer(csvfile)
            writer.writerow(["Domain", "Status", "Details", "LastChecked"])
            writer.writerow(["domain1.com", "Registered", "Active DNS (old)", "2026-07-10 10:00:00"])
            writer.writerow(["domain2.com", "Registered", "Active DNS (old)", "2026-07-09 10:00:00"])
            writer.writerow(["domain3.com", "Unchecked", "Not checked yet", ""])

        file2_path = os.path.join(cache_dir, "file2.csv")
        with open(file2_path, "w", newline="", encoding="utf-8") as csvfile:
            writer = csv.writer(csvfile)
            writer.writerow(["Domain", "Status", "Details", "LastChecked"])
            writer.writerow(["domain2.com", "Registered", "Active DNS (new)", "2026-07-11 10:00:00"])
            writer.writerow(["domain3.com", "Available", "Available (new)", "2026-07-10 12:00:00"])
            writer.writerow(["domain4.com", "Registered", "Active DNS", "2026-07-10 10:00:00"])

        # Input domains:
        # domain1.com, domain2.com, domain3.com, domain4.com, new.com
        domains = ["domain1.com", "domain2.com", "domain3.com", "domain4.com", "new.com"]
        with open(self.input_file, "w", encoding="utf-8") as f:
            f.write("\n".join(domains) + "\n")

        # Mock behaviors
        mock_resolve_dns.side_effect = lambda d: d == "new.com"
        mock_check_rdap.return_value = ("Registered", "Registered (Inactive DNS)")

        # Run process_domains using cache_dir
        check_domains.process_domains(
            input_file=self.input_file,
            output_file=self.output_file,
            cache_file=cache_dir,
            delay=0.01,
            threads=2
        )

        # Assertions
        # 1. DNS resolve should only run for "new.com" (since domain1, domain2, domain3, domain4 are all cached in directory)
        mock_resolve_dns.assert_called_once_with("new.com")

        # 2. Output file check
        self.assertTrue(os.path.exists(self.output_file))
        with open(self.output_file, "r", encoding="utf-8") as csvfile:
            reader = csv.DictReader(csvfile)
            rows = list(reader)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["Domain"], "new.com")
        self.assertEqual(rows[0]["Status"], "Registered")

    @patch("check_domains.resolve_dns")
    @patch("check_domains.check_rdap")
    def test_cache_vs_output_retry_behavior(self, mock_check_rdap, mock_resolve_dns):
        """Test that Blocked/Rate Limited from cache folder are skipped, but from output_file they are retried."""
        # 1. Create a cache file with domain1.com (Blocked, recent) and domain2.com (Rate Limited, recent)
        cache_dir = os.path.join(self.test_dir, "cache_dir_retry")
        os.makedirs(cache_dir, exist_ok=True)
        file_path = os.path.join(cache_dir, "cache.csv")
        with open(file_path, "w", newline="", encoding="utf-8") as csvfile:
            writer = csv.writer(csvfile)
            writer.writerow(["Domain", "Status", "Details", "LastChecked"])
            writer.writerow(["domain1.com", "Blocked", "Blocked (TLD returned 403)", "2026-07-14 10:00:00"])
            writer.writerow(["domain2.com", "Rate Limited", "Rate limited by registry", "2026-07-14 10:00:00"])

        # 2. Create an output file with domain3.com (Blocked, recent) and domain4.com (Rate Limited, recent)
        with open(self.output_file, "w", newline="", encoding="utf-8") as csvfile:
            writer = csv.writer(csvfile)
            writer.writerow(["Domain", "Status", "Details", "LastChecked"])
            writer.writerow(["domain3.com", "Blocked", "Blocked (TLD returned 403)", "2026-07-14 10:00:00"])
            writer.writerow(["domain4.com", "Rate Limited", "Rate limited by registry", "2026-07-14 10:00:00"])

        # Input domains: domain1, domain2, domain3, domain4
        domains = ["domain1.com", "domain2.com", "domain3.com", "domain4.com"]
        with open(self.input_file, "w", encoding="utf-8") as f:
            f.write("\n".join(domains) + "\n")

        # Mock behaviors: resolve fails (NXDOMAIN) so they go to RDAP check
        mock_resolve_dns.return_value = False
        mock_check_rdap.return_value = ("Available", "Unregistered (404 Not Found)")

        # Run process_domains using cache_dir and output_file
        check_domains.process_domains(
            input_file=self.input_file,
            output_file=self.output_file,
            cache_file=cache_dir,
            delay=0.01,
            threads=2
        )

        # Assertions:
        # - domain1.com (Blocked in cache) and domain2.com (Rate Limited in cache) should be SKIPPED (not resolved or checked)
        # - domain3.com (Blocked in output) and domain4.com (Rate Limited in output) should be RETRIED (resolved/checked)
        called_domains = [args[0][0] for args in mock_check_rdap.call_args_list]
        self.assertNotIn("domain1.com", called_domains)
        self.assertNotIn("domain2.com", called_domains)
        self.assertIn("domain3.com", called_domains)
        self.assertIn("domain4.com", called_domains)

        # Output file checks
        with open(self.output_file, "r", encoding="utf-8") as csvfile:
            reader = csv.DictReader(csvfile)
            rows = list(reader)
        output_domains = [row["Domain"] for row in rows]
        self.assertNotIn("domain1.com", output_domains)
        self.assertNotIn("domain2.com", output_domains)
        self.assertIn("domain3.com", output_domains)
        self.assertIn("domain4.com", output_domains)
        self.assertEqual(len(rows), 2)

    @patch("socket.socket")
    def test_check_whois_available(self, mock_socket_class):
        """Test check_whois for a domain that is available."""
        mock_socket = MagicMock()
        mock_socket_class.return_value = mock_socket
        mock_socket.recv.side_effect = [b"Domain not found.\n", b""]

        status, detail = check_domains.check_whois("testavailable.io")
        self.assertEqual(status, "Available")
        self.assertIn("WHOIS io", detail)

    @patch("socket.socket")
    def test_check_whois_registered(self, mock_socket_class):
        """Test check_whois for a domain that is registered."""
        mock_socket = MagicMock()
        mock_socket_class.return_value = mock_socket
        mock_socket.recv.side_effect = [b"Domain Name: sentinel.io\nRegistry Domain ID: 12345\nRegistrar: 101domain\n", b""]

        status, detail = check_domains.check_whois("sentinel.io")
        self.assertEqual(status, "Registered")
        self.assertIn("WHOIS io", detail)

    @patch("socket.socket")
    def test_check_whois_rate_limited(self, mock_socket_class):
        """Test check_whois when rate limited."""
        mock_socket = MagicMock()
        mock_socket_class.return_value = mock_socket
        mock_socket.recv.side_effect = [b"WHOIS query limit exceeded. Please try again later.\n", b""]

        status, detail = check_domains.check_whois("sentinel.io")
        self.assertEqual(status, "Rate Limited")
        self.assertIn("rate-limited", detail)

    @patch("socket.socket")
    def test_get_whois_server_lookup(self, mock_socket_class):
        """Test get_whois_server querying whois.iana.org."""
        mock_socket = MagicMock()
        mock_socket_class.return_value = mock_socket
        mock_socket.recv.side_effect = [b"refer: whois.nic.xyz\nwhois: whois.nic.xyz\n", b""]

        if "xyz" in check_domains.whois_server_cache:
            del check_domains.whois_server_cache["xyz"]

        server = check_domains.get_whois_server("xyz")
        self.assertEqual(server, "whois.nic.xyz")
        self.assertIn("xyz", check_domains.whois_server_cache)

if __name__ == "__main__":
    unittest.main()

