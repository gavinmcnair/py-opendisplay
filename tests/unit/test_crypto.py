"""Tests for opendisplay.crypto."""

import pytest

from opendisplay.crypto import (
    KEY_LENGTH_BYTES,
    aes_cmac,
    aes_ecb_encrypt,
    compute_challenge_response,
    decrypt_response,
    derive_session_id,
    derive_session_key,
    encrypt_command,
    generate_client_nonce,
    get_nonce,
    parse_encryption_key,
)
from opendisplay.exceptions import AuthenticationError, InvalidEncryptionKeyError, OpenDisplayError

_RFC4493_KEY = bytes.fromhex("2b7e151628aed2a6abf7158809cf4f3c")


class TestAesCmac:
    """RFC 4493 test vectors for AES-CMAC."""

    def test_empty_message(self):
        """RFC 4493 example 1: key=2b7e..., msg=empty → bb1d..."""
        result = aes_cmac(_RFC4493_KEY, b"")
        assert result.hex() == "bb1d6929e95937287fa37d129b756746"

    def test_sixteen_byte_message(self):
        """RFC 4493 example 2: key=2b7e..., msg=6bc1...172a → 070a..."""
        msg = bytes.fromhex("6bc1bee22e409f96e93d7e117393172a")
        result = aes_cmac(_RFC4493_KEY, msg)
        assert result.hex() == "070a16b46b4d4144f79bdd9dd04a287c"

    def test_returns_16_bytes(self):
        """Output is always 16 bytes."""
        result = aes_cmac(bytes(16), b"some data")
        assert len(result) == 16


class TestAesEcbEncrypt:
    """NIST AES-ECB known-answer test."""

    def test_known_vector(self):
        """NIST AESAVS vector: key=2b7e..., pt=6bc1... → 3ad7..."""
        key = bytes.fromhex("2b7e151628aed2a6abf7158809cf4f3c")
        plaintext = bytes.fromhex("6bc1bee22e409f96e93d7e117393172a")
        result = aes_ecb_encrypt(key, plaintext)
        assert result.hex() == "3ad77bb40d7a3660a89ecaf32466ef97"

    def test_returns_16_bytes(self):
        """Output is always 16 bytes for a 16-byte block."""
        result = aes_ecb_encrypt(bytes(16), bytes(16))
        assert len(result) == 16


class TestDeriveSessionKey:
    """Tests for derive_session_key."""

    def test_returns_16_bytes(self):
        """Session key is 16 bytes."""
        key = derive_session_key(bytes(16), bytes(16), bytes(16))
        assert len(key) == 16

    def test_deterministic(self):
        """Same inputs produce the same key."""
        master = bytes(range(16))
        cn = bytes(range(16, 32))
        sn = bytes(range(32, 48))
        assert derive_session_key(master, cn, sn) == derive_session_key(master, cn, sn)

    def test_changes_with_master_key(self):
        """Different master key → different session key."""
        cn = bytes(16)
        sn = bytes(16)
        k1 = derive_session_key(bytes(16), cn, sn)
        k2 = derive_session_key(bytes([1] * 16), cn, sn)
        assert k1 != k2

    def test_changes_with_client_nonce(self):
        """Different client nonce → different session key."""
        master = bytes(16)
        sn = bytes(16)
        k1 = derive_session_key(master, bytes(16), sn)
        k2 = derive_session_key(master, bytes([1] * 16), sn)
        assert k1 != k2

    def test_changes_with_server_nonce(self):
        """Different server nonce → different session key."""
        master = bytes(16)
        cn = bytes(16)
        k1 = derive_session_key(master, cn, bytes(16))
        k2 = derive_session_key(master, cn, bytes([1] * 16))
        assert k1 != k2

    def test_changes_with_device_id(self):
        """Different device_id produces a different session key."""
        master = bytes(range(16))
        cn = bytes(range(16, 32))
        sn = bytes(range(32, 48))
        k1 = derive_session_key(master, cn, sn, bytes([0x00, 0x00, 0x00, 0x01]))
        k2 = derive_session_key(master, cn, sn, bytes([0xDE, 0xAD, 0xBE, 0xEF]))
        assert k1 != k2


class TestDeriveSessionId:
    """Tests for derive_session_id."""

    def test_returns_8_bytes(self):
        """Session ID is 8 bytes."""
        sid = derive_session_id(bytes(16), bytes(16), bytes(16))
        assert len(sid) == 8

    def test_deterministic(self):
        """Same inputs → same session ID."""
        sk = bytes(range(16))
        cn = bytes(range(16, 32))
        sn = bytes(range(32, 48))
        assert derive_session_id(sk, cn, sn) == derive_session_id(sk, cn, sn)

    def test_changes_with_inputs(self):
        """Different session key → different session ID."""
        cn = bytes(16)
        sn = bytes(16)
        sid1 = derive_session_id(bytes(16), cn, sn)
        sid2 = derive_session_id(bytes([0xFF] * 16), cn, sn)
        assert sid1 != sid2


class TestComputeChallengeResponse:
    """Tests for compute_challenge_response."""

    def test_returns_16_bytes(self):
        """Challenge response is 16 bytes."""
        result = compute_challenge_response(bytes(16), bytes(16), bytes(16))
        assert len(result) == 16

    def test_deterministic(self):
        """Same inputs → same response."""
        master = bytes(range(16))
        sn = bytes(range(16, 32))
        cn = bytes(range(32, 48))
        r1 = compute_challenge_response(master, sn, cn)
        r2 = compute_challenge_response(master, sn, cn)
        assert r1 == r2

    def test_changes_with_key(self):
        """Different master key → different response."""
        sn = bytes(16)
        cn = bytes(16)
        r1 = compute_challenge_response(bytes(16), sn, cn)
        r2 = compute_challenge_response(bytes([0xAB] * 16), sn, cn)
        assert r1 != r2

    def test_changes_with_nonces(self):
        """Different nonces → different response."""
        master = bytes(16)
        r1 = compute_challenge_response(master, bytes(16), bytes(16))
        r2 = compute_challenge_response(master, bytes([1] * 16), bytes(16))
        assert r1 != r2

    def test_matches_direct_cmac(self):
        """compute_challenge_response is CMAC(master, server_nonce || client_nonce || device_id)."""
        master = bytes(range(16))
        sn = bytes(range(16, 32))
        cn = bytes(range(32, 48))
        device_id = bytes([0x00, 0x00, 0x00, 0x01])

        result = compute_challenge_response(master, sn, cn, device_id)
        expected = aes_cmac(master, sn + cn + device_id)
        assert result == expected

    def test_changes_with_device_id(self):
        """Different device_id produces a different challenge response."""
        master = bytes(range(16))
        sn = bytes(range(16, 32))
        cn = bytes(range(32, 48))
        r1 = compute_challenge_response(master, sn, cn, bytes([0x00, 0x00, 0x00, 0x01]))
        r2 = compute_challenge_response(master, sn, cn, bytes([0xDE, 0xAD, 0xBE, 0xEF]))
        assert r1 != r2


class TestEncryptDecryptCommand:
    """Round-trip and format tests for encrypt_command / decrypt_response."""

    def _make_session(self):
        session_key = bytes(range(16))
        session_id = bytes(range(8))
        return session_key, session_id

    def test_round_trip(self):
        """Encrypt then decrypt recovers the original payload."""
        session_key, session_id = self._make_session()
        cmd = bytes([0x00, 0x50])
        payload = b"hello world"
        counter = 1

        encrypted = encrypt_command(session_key, session_id, counter, cmd, payload)
        cmd_code, recovered = decrypt_response(session_key, encrypted)

        assert recovered == payload
        assert cmd_code == 0x0050

    def test_output_format(self):
        """Encrypted output is cmd(2) + nonce_full(16) + ciphertext + tag(12)."""
        session_key, session_id = self._make_session()
        cmd = bytes([0x00, 0x70])
        payload = b"\xab\xcd"
        counter = 42

        encrypted = encrypt_command(session_key, session_id, counter, cmd, payload)

        assert encrypted[:2] == cmd
        # nonce_full at [2:18]
        assert len(encrypted[2:18]) == 16
        # tag is last 12 bytes
        assert len(encrypted[-12:]) == 12
        # total: 2 + 16 + (1+2) + 12 = 33
        assert len(encrypted) == 2 + 16 + (1 + len(payload)) + 12

    def test_nonce_encodes_counter(self):
        """Nonce field contains session_id and counter."""
        session_key, session_id = self._make_session()
        cmd = bytes([0x00, 0x50])
        payload = b""
        counter = 7

        encrypted = encrypt_command(session_key, session_id, counter, cmd, payload)
        nonce_full = encrypted[2:18]

        assert nonce_full[:8] == session_id
        assert nonce_full[8:] == counter.to_bytes(8, "big")

    def test_different_counters_produce_different_output(self):
        """Each counter value produces a distinct ciphertext."""
        session_key, session_id = self._make_session()
        cmd = bytes([0x00, 0x50])
        payload = b"test"

        enc1 = encrypt_command(session_key, session_id, 1, cmd, payload)
        enc2 = encrypt_command(session_key, session_id, 2, cmd, payload)
        assert enc1 != enc2

    def test_decrypt_too_short_raises(self):
        """decrypt_response raises ValueError when data is too short."""
        with pytest.raises(ValueError, match="too short"):
            decrypt_response(bytes(16), b"\x00" * 10)


class TestGetNonce:
    """Tests for get_nonce."""

    def test_nonce_length(self):
        """Full nonce is 16 bytes."""
        nonce = get_nonce(bytes(8), 0)
        assert len(nonce) == 16

    def test_nonce_structure(self):
        """session_id(8) || counter_be(8)."""
        sid = bytes(range(8))
        counter = 256
        nonce = get_nonce(sid, counter)
        assert nonce[:8] == sid
        assert nonce[8:] == counter.to_bytes(8, "big")


class TestGenerateClientNonce:
    """Tests for generate_client_nonce."""

    def test_returns_16_bytes(self):
        """Client nonce is 16 bytes."""
        assert len(generate_client_nonce()) == 16

    def test_random(self):
        """Two consecutive calls return different values."""
        n1 = generate_client_nonce()
        n2 = generate_client_nonce()
        assert n1 != n2


class TestParseEncryptionKey:
    """The stored-key format: 32 hex characters for a 16-byte AES-128 key."""

    KEY_HEX = "aabbccddee112233aabbccddee112233"

    def test_none_means_no_key(self) -> None:
        """An unencrypted device stores nothing, which is not an error."""
        assert parse_encryption_key(None) is None

    def test_parses_a_valid_key(self) -> None:
        assert parse_encryption_key(self.KEY_HEX) == bytes.fromhex(self.KEY_HEX)
        assert len(parse_encryption_key(self.KEY_HEX)) == KEY_LENGTH_BYTES

    def test_case_is_not_significant(self) -> None:
        """A key pasted from a config tool round-trips whichever way it is cased."""
        assert parse_encryption_key(self.KEY_HEX.upper()) == parse_encryption_key(self.KEY_HEX)

    def test_surrounding_whitespace_is_ignored(self) -> None:
        assert parse_encryption_key(f"  {self.KEY_HEX}\n") == parse_encryption_key(self.KEY_HEX)

    @pytest.mark.parametrize(
        "separated",
        [
            "aa:bb:cc:dd:ee:11:22:33:aa:bb:cc:dd:ee:11:22:33",
            "aa bb cc dd ee 11 22 33 aa bb cc dd ee 11 22 33",
            "AA:BB:CC:DD:EE:11:22:33:AA:BB:CC:DD:EE:11:22:33",
        ],
    )
    def test_separators_are_not_content(self, separated: str) -> None:
        """A key is copied between tools by humans; colons and spaces are noise."""
        assert parse_encryption_key(separated) == parse_encryption_key(self.KEY_HEX)

    @pytest.mark.parametrize(
        "raw",
        [
            "",  # absence is expressed by None, so empty is malformed
            "aabb",  # too short
            "aabbccddee112233aabbccddee1122",  # 30 chars
            "aabbccddee112233aabbccddee1122334",  # 33 chars
            "aabbccddee112233aabbccddee11223344",  # 34 chars
        ],
    )
    def test_wrong_length_is_rejected(self, raw: str) -> None:
        with pytest.raises(InvalidEncryptionKeyError):
            parse_encryption_key(raw)

    @pytest.mark.parametrize(
        "raw",
        [
            "zzbbccddee112233aabbccddee112233",  # not hex at all
            "aabbccddee112233aabbccddee1122g3",  # one bad nibble
            "aabbccddee112233aabbccddee11223!",  # punctuation
        ],
    )
    def test_non_hex_is_rejected(self, raw: str) -> None:
        with pytest.raises(InvalidEncryptionKeyError):
            parse_encryption_key(raw)

    def test_error_is_an_opendisplay_error_not_an_auth_error(self) -> None:
        """Nothing was sent to a device, so this is local config, not a rejection.

        A host should react by asking for the key again, not by treating the
        device as having refused it.
        """
        assert issubclass(InvalidEncryptionKeyError, OpenDisplayError)
        assert not issubclass(InvalidEncryptionKeyError, AuthenticationError)
