"""Persist the last authenticated native evidence after capture is disarmed."""
import logging

from ..data import mission_for_map, objective_id, disc_id, VICTORY_PRIMARY_OBJECTIVES


def persist_final_captures(client, identity, objectives=(), discs=(), victories=()):
    # stop is deliberately not a guard: shutdown must finish the durable outbox.
    # Disk caches alone cannot authorize capture, nor can evidence cross rooms.
    permissions = getattr(client, 'capture_permissions', None)
    if identity is None or client.identity != identity or permissions is None:
        return
    maps = set(permissions)
    checks = {objective_id(name, oid) for name, oid in objectives if name in maps}
    checks.update(disc_id(name, oid) for name, oid in discs if name in maps)
    for name in victories:
        if name in maps:
            mission = mission_for_map(name)
            checks.update(mission.check_id(i) for i in range(1, client.config['checks_per_mission']+1))
            if client.config.get('primary_objectives'):
                checks.update(objective_id(name, oid) for oid in VICTORY_PRIMARY_OBJECTIVES.get(name, ()))
    checks.intersection_update(client.config['location_ids'])
    checks.difference_update(client.progress.completed)
    if checks:
        client.progress.merge(checks)
        client.changed.set()
        logging.getLogger('Renegade').info('Retained %d final checks for synchronization', len(checks))
