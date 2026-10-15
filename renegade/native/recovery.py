"""Recover retained native receipts; never guess whether an ambiguous effect ran."""
import logging

from ..data import ITEM_IDS
from .memory import ProcessMemory

LOG = logging.getLogger('Renegade.Rewards')


def recover_activations(client, opener=ProcessMemory):
    from .rewards import COOKIE, RECEIPT_INDEX, RESULT
    for key, record in tuple(client.progress.activations.items()):
        if record['status'] != 'pending':
            continue
        outcome = 'uncertain'
        memory = None
        try:
            memory = opener(record['pid'])  # Read-only; stale allocations are never executed.
            ctrl = record['control']
            if (memory.read(ctrl+COOKIE, 16).hex() == record['cookie']
                    and memory.u32(ctrl+RECEIPT_INDEX) == int(key)):
                result = memory.u32(ctrl+RESULT)
                if result in (1, 2, 3):
                    outcome = 'applied' if result == 1 else 'refunded'
        except OSError:
            pass
        finally:
            if memory:
                memory.close()
        client.progress.resolve_activation(int(key), outcome)
        name = next(n for n,i in ITEM_IDS.items() if i == client.progress.received[int(key)].item)
        if outcome == 'uncertain':
            LOG.warning('%s: interrupted activation has no confirmed receipt. Use Review item recovery to resolve it.', name)
        else:
            LOG.info('%s: recovered native receipt — %s', name, outcome)
