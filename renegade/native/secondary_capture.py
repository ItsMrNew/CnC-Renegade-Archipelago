"""Retain native primary/secondary evidence until the durable AP outbox accepts it."""
import logging

from ..data import MISSIONS, OBJECTIVES, objective_id, objective_kind
from .memory import MemoryError
from .services import CAPTURED, CALLBACK_CAPTURED, LIST_CAPTURED, MISSING_AT_CALL, PRESENT_AT_CALL, SCRIPT_CAPTURED

LOG = logging.getLogger('Renegade.Services')


class SecondaryCapture:
    def __init__(self):
        self.pending = set()
        self.evidence = {}
        self.errors = {}

    def collect(self, runtime):
        mem, control = runtime.memory, runtime.control
        for index, mission in enumerate(MISSIONS):
            ids = OBJECTIVES[mission.map_name]
            masks = [mem.u32(control+offset+index*4) for offset in
                     (CAPTURED, CALLBACK_CAPTURED, LIST_CAPTURED, MISSING_AT_CALL, PRESENT_AT_CALL, SCRIPT_CAPTURED)]
            if any(mask >> len(ids) for mask in masks):
                raise MemoryError('Corrupt native objective capture evidence')
            captured, callback, table, missing, present, script = masks
            for bit, oid in enumerate(ids):
                flag = 1 << bit
                if not captured & flag:
                    continue
                key = (mission.map_name, oid)
                self.pending.add(key)
                evidence = self.evidence.get(key, 0)
                updated = evidence | sum(value for value, mask in
                    ((1, callback), (2, table), (4, missing), (8, present), (16, script)) if mask & flag)
                if updated != evidence:
                    self.evidence[key] = updated
                    parts = []
                    if updated & 1: parts.append('completion callback')
                    if updated & 2: parts.append('completed objective list (live or loaded save)')
                    if updated & 4: parts.append('entry absent at a completion call')
                    if updated & 8: parts.append('entry present at a completion call')
                    if updated & 16: parts.append('persistent mission-script completion flag')
                    LOG.info('Objective evidence %s objective %d: %s', *key, '; '.join(parts))

    def flush(self, client):
        if not client.capture_ready:
            return
        for key in sorted(self.pending):
            map_name, oid = key
            if not client.config.get(objective_kind(map_name, oid) + '_objectives', False):
                self.pending.discard(key)
                continue
            try:
                accepted = client.objective_finished(map_name, oid)
                if accepted or objective_id(map_name, oid) in client.progress.completed:
                    self.pending.discard(key)
                    self.errors.pop(key, None)
            except OSError as error:
                # Disk failure must not detach native observers and discard a
                # transient completion that never existed in the objective list.
                if self.errors.get(key) != str(error):
                    LOG.error('Could not persist %s objective %d; retaining for retry: %s', *key, error)
                    self.errors[key] = str(error)
