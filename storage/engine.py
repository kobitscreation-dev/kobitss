"""
ACID Transactional Key-Value Storage Engine
============================================
Implements:
  1. Append-Only Write-Ahead Log (WAL) with CRC32 integrity
  2. In-memory B-Tree index using bisect (stdlib only)
  3. Atomic transactions (BEGIN/PUT/DELETE/COMMIT/ROLLBACK)
  4. Recovery manager that replays committed WAL records on startup
  5. Context manager and clean-shutdown support
"""

import os
import struct
import binascii
import threading
import uuid
import bisect
from typing import Optional, List, Tuple, Dict, Any


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class TransactionError(Exception):
    """Raised when an invalid operation is attempted on a transaction."""


# ---------------------------------------------------------------------------
# WAL constants
# ---------------------------------------------------------------------------
# Record layout:  [CRC32:4B][txn_id:8B][op_type:1B][key_len:4B][val_len:4B][key:NB][val:MB]
# The CRC32 is computed over everything AFTER the 4-byte CRC field.

OP_PUT      = 0x01
OP_DELETE   = 0x02
OP_COMMIT   = 0x03
OP_ROLLBACK = 0x04
OP_BEGIN    = 0x05

# Fixed-size header: CRC32(4) + txn_id(8) + op_type(1) + key_len(4) + val_len(4) = 21 bytes
_HDR_FMT    = '>I8sBI I'   # big-endian: uint32, 8s, uint8, uint32, uint32
_HDR_SIZE   = struct.calcsize(_HDR_FMT)   # == 21

_PAYLOAD_FMT = '>8sBI I'   # everything after the CRC32 field (used for CRC computation)
_PAYLOAD_HDR_SIZE = struct.calcsize(_PAYLOAD_FMT)  # == 17


def _encode_txn_id(txn_id: str) -> bytes:
    """Encode a UUID4 string into 8 bytes (first 8 bytes of the UUID int)."""
    uid = uuid.UUID(txn_id)
    return uid.bytes[:8]


def _decode_txn_id(raw: bytes) -> str:
    """Decode 8-byte truncated txn id back to a hex string key."""
    return raw.hex()


def _build_record(txn_id_bytes: bytes, op_type: int, key: bytes, val: bytes) -> bytes:
    """
    Build a binary WAL record with CRC32 checksum.
    Layout: [CRC32:4B][txn_id:8B][op_type:1B][key_len:4B][val_len:4B][key:NB][val:MB]
    """
    key_len = len(key)
    val_len = len(val)
    # Build everything after the CRC32 field
    payload = struct.pack('>8sBI I', txn_id_bytes, op_type, key_len, val_len) + key + val
    crc = binascii.crc32(payload) & 0xFFFFFFFF
    header = struct.pack('>I', crc)
    return header + payload


# ---------------------------------------------------------------------------
# B-Tree Index (sorted list backed, stdlib bisect)
# ---------------------------------------------------------------------------

class _BTreeIndex:
    """
    Thread-safe in-memory ordered index implemented as a sorted list of
    (key, value) tuples backed by Python's bisect module.
    """

    def __init__(self) -> None:
        self._data: List[Tuple[str, str]] = []
        self._lock = threading.RLock()

    # ------------------------------------------------------------------
    # Internal helpers (must be called with lock held)
    # ------------------------------------------------------------------

    def _find_pos(self, key: str) -> int:
        """Return the insertion position for *key* using bisect_left on keys."""
        lo, hi = 0, len(self._data)
        while lo < hi:
            mid = (lo + hi) // 2
            if self._data[mid][0] < key:
                lo = mid + 1
            else:
                hi = mid
        return lo

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get(self, key: str) -> Optional[str]:
        with self._lock:
            pos = self._find_pos(key)
            if pos < len(self._data) and self._data[pos][0] == key:
                return self._data[pos][1]
            return None

    def put(self, key: str, value: str) -> None:
        with self._lock:
            pos = self._find_pos(key)
            if pos < len(self._data) and self._data[pos][0] == key:
                # Update in place
                self._data[pos] = (key, value)
            else:
                self._data.insert(pos, (key, value))

    def delete(self, key: str) -> None:
        with self._lock:
            pos = self._find_pos(key)
            if pos < len(self._data) and self._data[pos][0] == key:
                del self._data[pos]

    def scan(self, start_key: str, end_key: str) -> List[Tuple[str, str]]:
        """Return all (key, value) pairs where start_key <= key <= end_key."""
        with self._lock:
            lo = self._find_pos(start_key)
            result = []
            idx = lo
            while idx < len(self._data) and self._data[idx][0] <= end_key:
                result.append(self._data[idx])
                idx += 1
            return list(result)


# ---------------------------------------------------------------------------
# Storage Engine
# ---------------------------------------------------------------------------

class StorageEngine:
    """
    ACID key-value storage engine.

    Usage::

        with StorageEngine('/tmp/mydb') as engine:
            txn = engine.begin_transaction()
            engine.put(txn, 'hello', 'world')
            engine.commit(txn)
            print(engine.get('hello'))  # 'world'
    """

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        os.makedirs(db_path, exist_ok=True)

        self._wal_path = os.path.join(db_path, 'wal.bin')
        self._index = _BTreeIndex()

        # Pending transactions: txn_id_str -> list of (op_type, key, value)
        self._pending: Dict[str, List[Tuple[int, str, str]]] = {}
        # Track committed/rolled-back txn ids so we can reject double-ops
        self._closed_txns: set = set()
        self._txn_lock = threading.Lock()

        # Recover existing WAL before opening for append
        self._last_valid_commit_offset = self._recover()

        # Open WAL for appending
        self._wal_fd = open(self._wal_path, 'ab+')

    # ------------------------------------------------------------------
    # Context Manager
    # ------------------------------------------------------------------

    def __enter__(self) -> 'StorageEngine':
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        self.close()
        return False  # Do not suppress exceptions

    # ------------------------------------------------------------------
    # WAL primitives
    # ------------------------------------------------------------------

    def _write_wal(self, txn_id_bytes: bytes, op_type: int,
                   key: bytes = b'', val: bytes = b'') -> None:
        record = _build_record(txn_id_bytes, op_type, key, val)
        self._wal_fd.write(record)
        self._wal_fd.flush()

    # ------------------------------------------------------------------
    # Recovery
    # ------------------------------------------------------------------

    def _recover(self) -> int:
        """
        Replay WAL records and populate the in-memory index.

        Returns the file offset of the last valid COMMIT record's end
        (used to truncate partial writes).
        """
        if not os.path.exists(self._wal_path):
            return 0

        txn_ops: Dict[str, List[Tuple[int, str, str]]] = {}
        committed_txns: set = set()

        last_commit_offset = 0
        current_offset = 0

        try:
            with open(self._wal_path, 'rb') as f:
                while True:
                    record_start = f.tell()

                    # Read fixed header
                    raw_hdr = f.read(_HDR_SIZE)
                    if len(raw_hdr) < _HDR_SIZE:
                        # Truncated header — stop here
                        break

                    try:
                        stored_crc, txn_id_bytes, op_type, key_len, val_len = \
                            struct.unpack(_HDR_FMT, raw_hdr)
                    except struct.error:
                        break

                    # Read variable-length key + value
                    key_data = f.read(key_len)
                    val_data = f.read(val_len)

                    if len(key_data) < key_len or len(val_data) < val_len:
                        # Truncated payload — stop
                        break

                    # Verify CRC32 over payload (everything after CRC field)
                    payload = raw_hdr[4:] + key_data + val_data
                    actual_crc = binascii.crc32(payload) & 0xFFFFFFFF
                    if actual_crc != stored_crc:
                        # Corrupted record — skip and continue scanning for valid records
                        # We cannot safely continue without knowing record boundaries,
                        # so we stop here and truncate to last good offset.
                        break

                    current_offset = f.tell()
                    txn_key = txn_id_bytes.hex()

                    if op_type == OP_BEGIN:
                        if txn_key not in txn_ops:
                            txn_ops[txn_key] = []

                    elif op_type == OP_PUT:
                        key_str = key_data.decode('utf-8', errors='replace')
                        val_str = val_data.decode('utf-8', errors='replace')
                        if txn_key not in txn_ops:
                            txn_ops[txn_key] = []
                        txn_ops[txn_key].append((OP_PUT, key_str, val_str))

                    elif op_type == OP_DELETE:
                        key_str = key_data.decode('utf-8', errors='replace')
                        if txn_key not in txn_ops:
                            txn_ops[txn_key] = []
                        txn_ops[txn_key].append((OP_DELETE, key_str, ''))

                    elif op_type == OP_COMMIT:
                        committed_txns.add(txn_key)
                        last_commit_offset = current_offset

                    elif op_type == OP_ROLLBACK:
                        # Discard buffered ops for this txn
                        txn_ops.pop(txn_key, None)

        except (OSError, IOError):
            pass

        # Replay only committed transactions
        for txn_key in committed_txns:
            ops = txn_ops.get(txn_key, [])
            for op_type, key_str, val_str in ops:
                if op_type == OP_PUT:
                    self._index.put(key_str, val_str)
                elif op_type == OP_DELETE:
                    self._index.delete(key_str)

        # Truncate WAL to last valid commit offset if there was trailing garbage
        file_size = os.path.getsize(self._wal_path) if os.path.exists(self._wal_path) else 0
        if last_commit_offset > 0 and current_offset < file_size:
            # There is trailing data beyond the last committed record
            with open(self._wal_path, 'r+b') as f:
                f.truncate(last_commit_offset)
        elif last_commit_offset == 0 and file_size > 0 and current_offset < file_size:
            # No commits found and there's corrupt/partial data
            # Truncate to whatever valid data we read up to (could be 0)
            with open(self._wal_path, 'r+b') as f:
                f.truncate(current_offset)

        return last_commit_offset

    # ------------------------------------------------------------------
    # Transaction API
    # ------------------------------------------------------------------

    def begin_transaction(self) -> str:
        """Start a new transaction, return its UUID4 string txn_id."""
        txn_id = str(uuid.uuid4())
        txn_id_bytes = _encode_txn_id(txn_id)

        with self._txn_lock:
            self._pending[txn_id] = []

        self._write_wal(txn_id_bytes, OP_BEGIN)
        return txn_id

    def _require_open_txn(self, txn_id: str) -> None:
        """Raise TransactionError if txn_id is unknown or already closed."""
        with self._txn_lock:
            if txn_id in self._closed_txns:
                raise TransactionError(
                    f"Transaction {txn_id!r} is already committed or rolled back."
                )
            if txn_id not in self._pending:
                raise TransactionError(
                    f"Unknown transaction {txn_id!r}."
                )

    def put(self, txn_id: str, key: str, value: str) -> None:
        """Buffer a PUT operation in the given transaction."""
        self._require_open_txn(txn_id)
        txn_id_bytes = _encode_txn_id(txn_id)
        key_bytes = key.encode('utf-8')
        val_bytes = value.encode('utf-8')

        with self._txn_lock:
            self._pending[txn_id].append((OP_PUT, key, value))

        self._write_wal(txn_id_bytes, OP_PUT, key_bytes, val_bytes)

    def delete(self, txn_id: str, key: str) -> None:
        """Buffer a DELETE operation in the given transaction."""
        self._require_open_txn(txn_id)
        txn_id_bytes = _encode_txn_id(txn_id)
        key_bytes = key.encode('utf-8')

        with self._txn_lock:
            self._pending[txn_id].append((OP_DELETE, key, ''))

        self._write_wal(txn_id_bytes, OP_DELETE, key_bytes, b'')

    def commit(self, txn_id: str) -> None:
        """Apply all buffered ops to the index, write COMMIT, fsync."""
        self._require_open_txn(txn_id)
        txn_id_bytes = _encode_txn_id(txn_id)

        with self._txn_lock:
            ops = list(self._pending.pop(txn_id, []))
            self._closed_txns.add(txn_id)

        # Apply to B-Tree
        for op_type, key, value in ops:
            if op_type == OP_PUT:
                self._index.put(key, value)
            elif op_type == OP_DELETE:
                self._index.delete(key)

        # Write COMMIT record and fsync
        self._write_wal(txn_id_bytes, OP_COMMIT)
        os.fsync(self._wal_fd.fileno())

    def rollback(self, txn_id: str) -> None:
        """Discard all buffered ops and write ROLLBACK record."""
        self._require_open_txn(txn_id)
        txn_id_bytes = _encode_txn_id(txn_id)

        with self._txn_lock:
            self._pending.pop(txn_id, None)
            self._closed_txns.add(txn_id)

        self._write_wal(txn_id_bytes, OP_ROLLBACK)

    # ------------------------------------------------------------------
    # Direct read API (no transaction needed)
    # ------------------------------------------------------------------

    def get(self, key: str) -> Optional[str]:
        """Read the committed value for key, or None if not present."""
        return self._index.get(key)

    def scan(self, start_key: str, end_key: str) -> List[Tuple[str, str]]:
        """Return all committed (key, value) pairs where start_key <= key <= end_key."""
        return self._index.scan(start_key, end_key)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Flush and close the WAL file descriptor."""
        try:
            if self._wal_fd and not self._wal_fd.closed:
                self._wal_fd.flush()
                self._wal_fd.close()
        except (OSError, IOError):
            pass
