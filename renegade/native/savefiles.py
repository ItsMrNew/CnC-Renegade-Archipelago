"""Read/relabel Renegade save metadata without changing the saved world chunks."""
import hashlib
import struct
from ..data import mission_for_map

INFO = 1011991648
DATA = INFO + 1


def chunks(raw):
    offset = 0
    while offset < len(raw):
        if offset + 8 > len(raw):
            raise ValueError("Truncated save chunk header")
        kind, size = struct.unpack_from("<II", raw, offset)
        end = offset + 8 + (size & 0x7FFFFFFF)
        if end > len(raw):
            raise ValueError("Truncated save chunk")
        yield kind, size, raw[offset + 8:end]
        offset = end


def fields(raw):
    offset = 0
    while offset < len(raw):
        if offset + 2 > len(raw):
            raise ValueError("Truncated save metadata")
        kind, size = raw[offset:offset + 2]
        end = offset + 2 + size
        if end > len(raw):
            raise ValueError("Truncated save metadata field")
        yield kind, raw[offset + 2:end]
        offset = end


def inspect_save(raw):
    parts = list(chunks(raw))
    if [p[0] for p in parts] != [INFO, DATA] or not parts[1][2]:
        raise ValueError("Expected Renegade save info and world data")
    metadata = dict(fields(parts[0][2]))
    name = metadata[1].rstrip(b"\0").decode("ascii")
    description = metadata.get(3, b"").decode("utf-16-le").rstrip("\0")
    return dict(map_name=mission_for_map(name).map_name, description=description,
                bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest(),
                world_sha256=hashlib.sha256(parts[1][2]).hexdigest())


def saved_difficulty(raw):
    """Read CombatManager's saved difficulty through its exact chunk hierarchy."""
    # Save world / CombatSaveLoad / CombatManager / variables.
    for expected in (DATA, 0x40000, 916991655, 916991715):
        matches = [payload for kind, _, payload in chunks(raw) if kind == expected]
        if len(matches) != 1:
            raise ValueError("Save lacks a unique combat difficulty chunk")
        raw = matches[0]
    entries = list(fields(raw))
    first = [value for key, value in entries if key == 1]
    difficulty = [value for key, value in entries if key == 2]
    if first != [b'\x00'] or len(difficulty) != 1 or len(difficulty[0]) != 4:
        raise ValueError("Save does not contain an initialized campaign difficulty")
    value = int.from_bytes(difficulty[0], 'little')
    if value not in (0, 1, 2):
        raise ValueError("Unknown saved difficulty")
    return value


def saved_objectives(raw):
    """Read the objective manager for diagnostics; never infer missing checks."""
    for expected in (DATA, 0x40000, 0x36A82EAE):
        matches = [payload for kind, _, payload in chunks(raw) if kind == expected]
        if len(matches) != 1:
            raise ValueError('Save lacks a unique objective manager chunk')
        raw = matches[0]
    result = []
    for kind, _, payload in chunks(raw):
        if kind != 629001432:
            continue
        variables = [data for ident, _, data in chunks(payload) if ident == 629001440]
        if len(variables) != 1:
            raise ValueError('Invalid saved objective entry')
        data = dict(fields(variables[0]))
        if any(len(data.get(key, b'')) != 4 for key in (1, 2, 3, 7)):
            raise ValueError('Invalid saved objective fields')
        result.append(dict(zip(('id', 'type', 'status', 'title_id'),
                               (int.from_bytes(data[key], 'little') for key in (1, 2, 3, 7)))))
    return result


def relabel(raw, description):
    inspect_save(raw)
    encoded = (description + "\0").encode("utf-16-le")
    if len(encoded) > 255:
        raise ValueError("Save description exceeds a microchunk")
    output = bytearray()
    for kind, size, payload in chunks(raw):
        if kind == INFO:
            entries = list(fields(payload))
            if sum(k == 3 for k, _ in entries) != 1:
                raise ValueError("Expected one save description")
            payload = b"".join(bytes((k, len(encoded if k == 3 else v))) + (encoded if k == 3 else v)
                               for k, v in entries)
            size = (size & 0x80000000) | len(payload)
        output += struct.pack("<II", kind, size) + payload
    return bytes(output)
