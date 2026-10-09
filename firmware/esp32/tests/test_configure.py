from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "configure.py"
SPEC = importlib.util.spec_from_file_location("firmware_configure", SCRIPT)
configure = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(configure)
PUBLIC_TEST_PEM = "-----BEGIN CERTIFICATE-----\nTEST-ONLY-PUBLIC-PLACEHOLDER\n-----END CERTIFICATE-----\n"


class ConfigureTests(unittest.TestCase):
    def setUp(self):
        self.config = configure.compile_check_config()

    def test_valid_profile_emits_matching_contract(self):
        result = configure.generate(self.config, PUBLIC_TEST_PEM)
        self.assertIn("#define UE_FRAMES_PER_RECORDING 16000\n", result)
        self.assertIn("#define UE_I2S_SAMPLE_LSB 8\n", result)
        self.assertIn("#define UE_GPIO_BCLK 26\n", result)
        self.assertIn("#define UE_WIRING_VERIFIED true\n", result)

    def test_unverified_or_mismatched_wiring_rejected(self):
        for key, value in (("wiring_verified", False), ("wiring_verified", 1), ("gpio_bclk", 27),
                           ("channel", "right"), ("model", "ESP32-S3"), ("sample_lsb", 0)):
            with self.subTest(key=key, value=value):
                config = deepcopy(self.config)
                config["board"][key] = value
                with self.assertRaises(configure.ConfigurationError):
                    configure.generate(config, PUBLIC_TEST_PEM)

    def test_plaintext_loopback_and_credential_urls_rejected(self):
        for endpoint in ("http://192.168.1.2:8443", "https://localhost:8443", "https://127.0.0.1:8443",
                         "https://user:secret@host", "https://host/audio", "https://host?token=secret",
                         "https://host:bad", "https://host:0", "https://host:99999", "https://host\r\nInjected"):
            with self.subTest(endpoint=endpoint):
                self.config["endpoint"] = endpoint
                with self.assertRaises(configure.ConfigurationError):
                    configure.generate(self.config, PUBLIC_TEST_PEM)

    def test_credentials_cannot_inject_headers(self):
        self.config["token"] = "X" * 40 + "\r\nInjected: yes"
        with self.assertRaises(configure.ConfigurationError):
            configure.generate(self.config, PUBLIC_TEST_PEM)

    def test_private_key_is_never_accepted_as_ca(self):
        with self.assertRaises(configure.ConfigurationError):
            configure.generate(self.config, PUBLIC_TEST_PEM + "-----BEGIN PRIVATE KEY-----")

    def test_buffer_and_sample_contract(self):
        for field, value in (("recording_duration_seconds", 2), ("max_frames_per_recording", 32000),
                             ("queue_capacity", 3), ("sample_rate", 22050),
                             ("recording_duration_seconds", float("nan")), ("recording_duration_seconds", 0),
                             ("recording_duration_seconds", 0.5)):
            with self.subTest(field=field, value=value):
                config = deepcopy(self.config)
                config[field] = value
                with self.assertRaises(configure.ConfigurationError):
                    configure.generate(config, PUBLIC_TEST_PEM)

    def test_intervals_are_configurable_and_do_not_overlap(self):
        self.config["recording_interval_seconds"] = 2
        self.config["upload_interval_seconds"] = 0.25
        result = configure.generate(self.config, PUBLIC_TEST_PEM)
        self.assertIn("#define UE_RECORDING_INTERVAL_MS 2000\n", result)
        self.assertIn("#define UE_UPLOAD_INTERVAL_MS 250\n", result)
        self.config["recording_interval_seconds"] = 0.5
        with self.assertRaises(configure.ConfigurationError):
            configure.generate(self.config, PUBLIC_TEST_PEM)

    def test_retry_bounds(self):
        for retry in ({"max_attempts": 0}, {"max_attempts": 101}, {"base_ms": 10000, "cap_ms": 5000},
                      {"retry_after_cap_ms": 300001}, {"max_attempts": True}):
            with self.subTest(retry=retry):
                self.config["retry"] = retry
                with self.assertRaises(configure.ConfigurationError):
                    configure.generate(self.config, PUBLIC_TEST_PEM)

    def test_clock_bounds(self):
        for clock in ({"max_sync_age_seconds": 86401}, {"uncertainty_budget_ms": 0}, {"sntp_server": ""}):
            with self.subTest(clock=clock):
                self.config["clock"] = clock
                with self.assertRaises(configure.ConfigurationError):
                    configure.generate(self.config, PUBLIC_TEST_PEM)

    def test_utf8_wifi_and_cpp_quotes(self):
        self.config["wifi"]["ssid"] = 'Café "lab"'
        result = configure.generate(self.config, PUBLIC_TEST_PEM)
        self.assertIn('#define UE_WIFI_SSID "Café \\"lab\\""', result)
        self.config["wifi"]["ssid"] = "é" * 17
        with self.assertRaises(configure.ConfigurationError):
            configure.generate(self.config, PUBLIC_TEST_PEM)

    def test_malformed_objects(self):
        for field in ("board", "wifi", "clock", "retry"):
            config = deepcopy(self.config)
            config[field] = []
            with self.assertRaises(configure.ConfigurationError):
                configure.generate(config, PUBLIC_TEST_PEM)

    def test_cli_writes_private_file_without_printing_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            config = directory / "private.json"
            ca = directory / "ca.crt"
            output = directory / "device_config.h"
            config.write_text(json.dumps(self.config))
            ca.write_text(PUBLIC_TEST_PEM)
            run = subprocess.run([sys.executable, str(SCRIPT), "--config", str(config), "--ca", str(ca),
                                  "--output", str(output)], capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
            self.assertIn(self.config["token"], output.read_text())
            for secret in (self.config["token"], self.config["wifi"]["password"]):
                self.assertNotIn(secret, run.stdout + run.stderr)

    def test_invalid_file_does_not_overwrite_existing_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            private = directory / "invalid.json"
            ca = directory / "ca.crt"
            output = directory / "device_config.h"
            private.write_text('{"token": "secret input" INVALID}')
            ca.write_text(PUBLIC_TEST_PEM)
            output.write_text("previous private configuration")
            run = subprocess.run([sys.executable, str(SCRIPT), "--config", str(private), "--ca", str(ca),
                                  "--output", str(output)], capture_output=True, text=True)
            self.assertEqual(run.returncode, 2)
            self.assertEqual(output.read_text(), "previous private configuration")
            self.assertNotIn("secret input", run.stdout + run.stderr)


if __name__ == "__main__":
    unittest.main()
