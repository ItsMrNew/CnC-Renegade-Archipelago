"""Shared DeathLink bridge; the combat thread performs the final control checks."""
import logging

from ..data import MISSIONS
from . import services as s
from .services import DEATH_GENERATION, DEATH_MAP

LOG = logging.getLogger('Renegade.DeathLink')


class NativeDeathLink:
    def __init__(self, memory, control):
        self.memory, self.control = memory, control
        self.deaths = memory.u32(control+s.DEATH_COUNT)
        self.failed = memory.u32(control+s.DEATH_FAILED)

    def update(self, client, *, active=True):
        mem, ctrl = self.memory, self.control
        enabled = client.ready and client.deathlink.enabled
        mem.write_u32(ctrl+s.DEATH_ENABLED, int(enabled))
        deaths = mem.u32(ctrl+s.DEATH_COUNT)
        if deaths != self.deaths:
            self.deaths = deaths
            index = mem.u32(ctrl+DEATH_MAP)
            if enabled and index < len(MISSIONS):
                client.player_died(MISSIONS[index].map_name)
        failed = mem.u32(ctrl+s.DEATH_FAILED)
        if failed != self.failed:
            self.failed = failed
            LOG.error('DeathLink damage did not kill the player; no death was echoed')
        if not enabled:
            mem.write_u32(ctrl+s.DEATH_REQUEST, 0)
            client.deathlink.incoming.clear()
            return
        # Keep session-only incoming deaths queued while paused, in a cinematic,
        # or in a menu. Disconnect clears this queue in the common client.
        if (not active or not client.deathlink.incoming or mem.u32(ctrl+s.DEATH_REQUEST)
                or not mem.u32(ctrl+s.PLAYABLE) or not mem.u32(ctrl+s.ALIVE)):
            return
        player, index = mem.u32(ctrl+s.PLAYER), mem.u32(ctrl+s.MAP_INDEX)
        if not player or index >= len(MISSIONS):
            return
        mem.write_u32(ctrl+s.REQUEST_PLAYER, player)
        mem.write_u32(ctrl+s.REQUEST_MAP, index)
        mem.write_u32(ctrl+DEATH_GENERATION, mem.u32(ctrl+s.GENERATION))
        mem.write_u32(ctrl+s.DEATH_REQUEST, 1)  # Publish only after identity.
        client.deathlink.incoming.clear()  # Coalesce simultaneous room deaths.
