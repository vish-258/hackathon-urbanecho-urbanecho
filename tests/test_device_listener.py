"""The optional hardware listener shares ingestion but exposes no management UI."""
import importlib.util
from pathlib import Path
import shutil
import ssl

import pytest
from fastapi.testclient import TestClient

from app.device_server import private_lan_address


@pytest.mark.parametrize('address', ['0.0.0.0', '127.0.0.1', '8.8.8.8', '::1', '', '169.254.1.2'])
def test_hardware_listener_requires_explicit_private_lan(address):
    with pytest.raises(ValueError):
        private_lan_address(address)


@pytest.mark.parametrize('address', ['192.168.1.10', '10.0.0.5', '172.16.1.2'])
def test_private_lan_binding(address):
    assert private_lan_address(address) == address


def test_tls_setup_creates_verified_san_and_preserves_existing_keys(tmp_path):
    if not shutil.which('openssl'):
        pytest.skip('OpenSSL CLI unavailable in this runtime; run this check on the host')
    path = Path(__file__).resolve().parents[1] / 'scripts/setup-hardware-tls.py'
    spec = importlib.util.spec_from_file_location('tls_setup', path)
    setup = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(setup)
    destination = tmp_path / 'private-tls'
    with pytest.raises(ValueError):
        setup.prepare('0.0.0.0', destination)
    setup.prepare('192.168.1.10', destination)
    certificate = ssl._ssl._test_decode_cert(str(destination / 'server.crt'))
    assert ('IP Address', '192.168.1.10') in certificate['subjectAltName']
    assert ('IP Address', '127.0.0.1') in certificate['subjectAltName']
    context = ssl.create_default_context(cafile=str(destination / 'ca.crt'))
    assert context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED
    assert destination.stat().st_mode & 0o777 == 0o700
    assert (destination / 'signing-private/ca.key').stat().st_mode & 0o777 == 0o600
    original = (destination / 'server.key').read_bytes()
    with pytest.raises(ValueError, match='already exists'):
        setup.prepare('192.168.1.11', destination)
    assert (destination / 'server.key').read_bytes() == original


def test_tls_setup_reuses_trusted_ca_for_a_new_network(tmp_path):
    if not shutil.which('openssl'):
        pytest.skip('OpenSSL CLI unavailable in this runtime; run this check on the host')
    path = Path(__file__).resolve().parents[1] / 'scripts/setup-hardware-tls.py'
    spec = importlib.util.spec_from_file_location('tls_setup', path)
    setup = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(setup)
    original = setup.prepare('10.22.197.114', tmp_path / 'first')
    with pytest.raises(ValueError):
        setup.prepare('192.168.14.161', tmp_path / 'bad', also=['8.8.8.8'], ca_from=original)
    moved = setup.prepare('192.168.14.161', tmp_path / 'second', also=['10.22.197.114'], ca_from=original)
    assert (moved / 'ca.crt').read_bytes() == (original / 'ca.crt').read_bytes()
    assert not (moved / 'signing-private').exists()
    assert 'HARDWARE_BIND_IP=192.168.14.161' in (moved / 'listener.env').read_text()
    names = ssl._ssl._test_decode_cert(str(moved / 'server.crt'))['subjectAltName']
    assert {('IP Address', '192.168.14.161'), ('IP Address', '10.22.197.114'), ('IP Address', '127.0.0.1')} <= set(names)
    ssl.create_default_context(cafile=str(original / 'ca.crt')).load_verify_locations(cafile=str(moved / 'ca.crt'))


@pytest.mark.integration
def test_device_listener_ingests_via_existing_pipeline_without_admin_routes(client, admin_headers, settings):
    from app.device_api import create_device_app
    from tests.test_integration import location, device, upload, process
    loc = location(client, admin_headers, threshold_type='dbfs_rms', threshold_value=-40)
    dev = device(client, admin_headers, loc['id'])
    other = device(client, admin_headers, loc['id'])
    with TestClient(create_device_app(), raise_server_exceptions=False) as hardware:
        assert hardware.get('/health/live').status_code == 200
        for path in ('/docs', '/openapi.json', '/app', '/locations', '/devices', '/events', '/daily-summaries'):
            assert hardware.get(path).status_code == 404
        assert hardware.post('/app/session').status_code == 404
        assert hardware.post('/audio').status_code == 401
        assert hardware.post('/audio', headers=admin_headers).status_code == 403
        accepted = upload(hardware, dev)
        assert accepted.status_code == 202, accepted.text
        audio_id = accepted.json()['id']
        process(settings)
        headers = {'Authorization': 'Bearer ' + dev['token']}
        row = hardware.get('/audio/' + audio_id, headers=headers).json()
        assert row['location_id'] == loc['id'] and row['status'] == 'completed'
        assert row['measurements'][0]['measurement_type'] == 'dbfs_rms'
        assert hardware.get('/audio/' + audio_id, headers={'Authorization': 'Bearer ' + other['token']}).status_code == 401
        assert hardware.get('/audio/' + audio_id, headers=admin_headers).status_code == 403
        assert hardware.get('/audio/' + audio_id + '/file', headers=headers).status_code == 200
        duplicate = upload(hardware, dev)
        assert duplicate.status_code == 200 and duplicate.json()['duplicate']
