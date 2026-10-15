"""Renegade developer client with simulator and experimental native mission observer."""
import argparse
import asyncio
import getpass
import logging
from pathlib import Path
from renegade.client import RenegadeClient
from renegade.data import OBJECTIVES


async def simulate(client):
    logging.warning("SIMULATOR: synthetic victories and primary/secondary objectives send real checks. Use a disposable test room.")
    while not client.goal_sent:
        for map_name in client.unlocked_maps:
            for objective in OBJECTIVES[map_name]:
                client.objective_finished(map_name, objective)
            client.mission_finished(map_name, True)
        await asyncio.sleep(0.25)
    # Allow the server to process the final status before closing.
    await asyncio.sleep(0.5)
    client.stop.set()


async def run(client, simulation, pid=None, gate_missions=False, native_status=None, *, tt_core=False):
    tasks = [asyncio.create_task(client.network())]
    if not simulation:
        from renegade.tracker_bridge import watch as tracker_watch
        tasks.append(asyncio.create_task(tracker_watch(client)))
    if simulation:
        tasks.append(asyncio.create_task(simulate(client)))
    elif tt_core:
        from renegade.native.tt import watch
        def tt_status(state, detail):
            if native_status:
                native_status('observer', state, detail)
                native_status('services', state,
                    'TT checkpoints, notifications, effect HUD, DeathLink, all traps and bonuses'
                    if state == 'attached' else detail)
        tasks.append(asyncio.create_task(watch(client, pid, status=tt_status)))
    else:
        from renegade.native.bridge import watch
        observer_status = (lambda state, detail: native_status("observer", state, detail)) if native_status else None
        tasks.append(asyncio.create_task(watch(client, pid, status=observer_status)))
        from renegade.native.services import watch_services
        services_status = (lambda state, detail: native_status("services", state, detail)) if native_status else None
        tasks.append(asyncio.create_task(watch_services(client, pid, gate_enabled=gate_missions, status=services_status)))
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
    finally:
        for task in tasks:
            task.cancel()
        results = await asyncio.gather(*tasks, return_exceptions=True)
        errors = [str(r) for r in results if isinstance(r, Exception)]
        if errors:
            raise RuntimeError("Client stopped with an error: " + "; ".join(dict.fromkeys(errors)))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server", required=True)
    parser.add_argument("--slot", default="Havoc")
    parser.add_argument("--ask-password", action="store_true")
    parser.add_argument("--state-dir", type=Path, default=Path(__file__).resolve().parent / ".state")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--simulate", action="store_true", help="Automatically complete unlocked missions in a TEST room")
    mode.add_argument("--native", action="store_true", help="Stock Steam native adapter")
    mode.add_argument("--tt-core", action="store_true", help="Tiberian Technologies 4.8.4 campaign adapter")
    parser.add_argument("--pid", type=int, help="Optional explicit game process ID for native mode")
    parser.add_argument("--gate-missions", action="store_true",
                        help="With --native: pause locked gameplay, show a native message, and return to the menu")
    args = parser.parse_args()
    if args.gate_missions and not args.native:
        parser.error("--gate-missions requires --native")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    if args.native:
        logging.warning("EXPERIMENTAL: mission entry is not blocked. %s Use a development room.",
                        "Locked gameplay popup/menu gate enabled; broader campaign testing pending." if args.gate_missions
                        else "Gameplay gating is disabled.")
    password = getpass.getpass("Room password: ") if args.ask_password else None
    client = RenegadeClient(args.server, args.slot, password, args.state_dir)
    try:
        asyncio.run(run(client, args.simulate, args.pid, args.gate_missions, tt_core=args.tt_core))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
