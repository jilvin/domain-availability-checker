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

        # 2. Output file should contain all domains (newly checked and cached)
        self.assertTrue(os.path.exists(self.output_file))
        with open(self.output_file, "r", encoding="utf-8") as csvfile:
            reader = csv.DictReader(csvfile)
            rows = list(reader)

        output_domains = [row["Domain"] for row in rows]
        self.assertIn("cached.com", output_domains)
        self.assertIn("new-active.com", output_domains)
        self.assertIn("new-inactive.com", output_domains)
        self.assertEqual(len(rows), 3)

        # 3. Verify cached values are retained correctly in the output file
        cached_row = next(row for row in rows if row["Domain"] == "cached.com")
        self.assertEqual(cached_row["Status"], "Registered")
        self.assertEqual(cached_row["Details"], "Registered (Active DNS)")
        self.assertEqual(cached_row["LastChecked"], "2026-07-12 10:58:48")

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

if __name__ == "__main__":
    unittest.main()
