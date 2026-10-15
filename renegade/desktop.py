"""Desktop session model. All client/native access stays on the worker thread."""
import asyncio
import json
import logging
from pathlib import Path
import queue
import threading

from .client import RenegadeClient, server_url
from .data import MISSIONS, enabled_objectives, objective_id, objective_title, objective_kind, ITEM_IDS, ITEM_FLAGS, REPORT, SUPPLIES, DATA_DISCS, disc_id, disc_title


def connection_settings(server, slot, gate=True, theme="light", transfers="all"):
    if not isinstance(server, str) or not isinstance(slot, str):
        raise ValueError("Server and slot name must be text")
    server = server.strip()
    server_url(server)
    slot = slot.strip()
    if not slot:
        raise ValueError("Enter your Archipelago slot name")
    if theme not in ("light", "dark"):
        raise ValueError("Invalid theme")
    if transfers not in ("all", "you", "off"):
        raise ValueError("Invalid item transfer scope")
    # Legacy settings may contain gate=False. Launcher sessions always enforce it.
    return dict(server=server, slot=slot, gate=True, theme=theme, transfers=transfers)


def save_settings(path, settings):
    # An allowlist prevents passwords or client state being persisted here.
    clean = connection_settings(settings["server"], settings["slot"], settings.get("gate", True), settings.get("theme", "light"), settings.get("transfers", "all"))
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(clean, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def load_settings(path):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("Settings must be an object")
        theme = data.get("theme", "light")
        return connection_settings(data["server"], data["slot"], data.get("gate", True), theme if theme in ("light", "dark") else "light", data.get("transfers", "all"))
    except (OSError, ValueError, TypeError, KeyError):
        return dict(server="localhost:38281", slot="Havoc", gate=True, theme="light", transfers="all")


def preview_missions():
    """Browsable mission catalogue until a room supplies its check settings."""
    return [dict(map=m.map_name, title=m.label, state='Unknown', checks='â€”', pending=0,
                 objectives=[dict(id=oid, title=title, state='Unknown', checks='â€”')
                             for oid, title in [('victory', 'Complete mission')]+
                             [(oid, objective_label(m.map_name, oid)) for oid in enabled_objectives(m.map_name, True, True)]+
                             [('disc_'+str(oid), disc_title(m.map_name, oid)) for oid in DATA_DISCS[m.map_name]]])
            for m in MISSIONS]


def objective_label(map_name, oid):
    title = objective_title(map_name, oid)
    return f'{title} ({objective_kind(map_name, oid).title()} Objective)'


def snapshot(client, native):
    ready = client.ready
    unlocked_maps = client.unlocked_maps if ready else set()
    count = client.config["checks_per_mission"] if client.config else None
    completed = client.progress.completed if client.progress else set()
    rows = []
    logic_checks = set()
    for mission in MISSIONS if client.config else ():
        attempt = getattr(client, 'attempt', None) or {}
        failed = set(attempt.get('failed', ())) if attempt.get('map') == mission.map_name else set()
        def check_state(check, available=mission.map_name in unlocked_maps):
            if not ready:
                return 'Unknown'
            return ('Complete' if check in client.acknowledged else
                    'Pending sync' if check in completed else
                    'Available' if available else 'Locked')
        checks = {mission.check_id(i) for i in range(1, count + 1)} if count else set()
        objectives = [dict(id='victory', title='Complete mission', state=check_state(mission.check_id()),
                           checks=f'{len(checks & client.acknowledged)}/{count}' if count else 'â€”')]
        for oid in enabled_objectives(mission.map_name, client.config.get('secondary_objectives', False),
                                      client.config.get('primary_objectives', False)):
            check = objective_id(mission.map_name, oid)
            checks.add(check)
            state = check_state(check)
            if state == 'Available' and oid in failed:
                state = 'Missed this attempt'
            objectives.append(dict(id=oid, title=objective_label(mission.map_name, oid), state=state,
                                   checks='1/1' if check in client.acknowledged else '0/1'))
        if client.config.get('data_discs', False):
            for oid in DATA_DISCS[mission.map_name]:
                check = disc_id(mission.map_name, oid)
                if check not in client.locations:
                    continue
                checks.add(check)
                objectives.append(dict(id='disc_'+str(oid), title=disc_title(mission.map_name, oid),
                    state=check_state(check), checks='1/1' if check in client.acknowledged else '0/1'))
        confirmed = len(checks & client.acknowledged)
        if ready and mission.map_name in unlocked_maps:
            logic_checks.update(checks)
        pending = len((checks & completed) - client.acknowledged)
        # The parent summarizes every enabled check; victory remains a separate
        # child so a beaten mission with objectives/discs left is still orange.
        state = ('Unknown' if not ready else
                 'Complete' if checks and checks <= client.acknowledged else
                 'Pending sync' if pending else
                 'Available' if mission.map_name in unlocked_maps else 'Locked')
        rows.append(dict(map=mission.map_name, title=mission.label, state=state,
                         checks=f"{confirmed}/{len(checks)}" if count else "â€”", pending=pending, objectives=objectives))
    return dict(connection="Connected" if ready else "Synchronizing" if client.connected else "Connecting / reconnecting",
        seed=client.seed, slot=client.slot, confirmed=len(client.acknowledged), total=len(client.locations),
        pending=len(completed - client.acknowledged), unlocked=len(unlocked_maps) if ready else None,
        logic_confirmed=len(logic_checks & client.acknowledged) if ready else None,
        logic_total=len(logic_checks) if ready else None,
        goal=client.goal_sent, native={key: value.copy() for key, value in native.items()},
        missions=rows if client.config else preview_missions(),
        items=[dict(name=name, flags=ITEM_FLAGS[name], count=client.inventory.count(i),
                    available=len(client.supply_indexes(name)) if name in SUPPLIES else None)
               for name,i in ITEM_IDS.items() if name != REPORT],
        supply_pending=client.supply_request,
        recovery=[dict(index=int(k),name=next(n for n,i in ITEM_IDS.items() if i == client.progress.received[int(k)].item))
                  for k,v in client.progress.activations.items() if v['status']=='uncertain'] if client.progress else [],
        mission_launch=client.mission_launch, mission_confirmation=client.mission_confirmation)


class QueueLog(logging.Handler):
    def __init__(self, publish):
        super().__init__()
        self.publish = publish
        self.setFormatter(logging.Formatter("%(asctime)s  %(levelname)s  %(message)s", "%H:%M:%S"))

    def emit(self, record):
        text = self.format(record)
        parts = getattr(record, 'item_parts', ())
        message = record.getMessage()
        spans = []
        if parts and ''.join(part for part, _ in parts) == message and text.endswith(message):
            start = len(text) - len(message)
            for part, flag in parts:
                if flag is not None:
                    spans.append((start, start + len(part), flag))
                start += len(part)
        self.publish("log", dict(text=text, spans=spans, level=record.levelno))


class DesktopSession:
    def __init__(self, state_dir, *, tt_core=False):
        self.state_dir = Path(state_dir)
        self.tt_core = tt_core
        self.events = queue.Queue(maxsize=500)
        self._publish_lock = threading.Lock()
        self.thread = None
        self.loop = None
        self.client = None
        self.stopping = threading.Event()

    @property
    def running(self):
        return self.thread is not None and self.thread.is_alive()

    def publish(self, kind, value):
        # Logs and worker snapshots can arrive concurrently. Keep eviction and
        # insertion together so another producer cannot fill the freed slot.
        with self._publish_lock:
            try:
                self.events.put_nowait((kind, value))
            except queue.Full:
                # This queue is display data, never the AP progress outbox.
                try:
                    self.events.get_nowait()
                except queue.Empty:
                    pass
                self.events.put_nowait((kind, value))

    def start(self, settings, password=None):
        if self.running:
            raise RuntimeError("Disconnect the active session first")
        clean = connection_settings(**settings)
        self.stopping.clear()
        while not self.events.empty():
            self.events.get_nowait()
        self.thread = threading.Thread(target=self._worker, args=(clean, password), daemon=False)
        self.thread.start()

    def stop(self):
        self.stopping.set()
        loop, client = self.loop, self.client
        if loop and client and not loop.is_closed():
            try:
                loop.call_soon_threadsafe(client.stop.set)
            except RuntimeError:
                pass  # Worker completed concurrently.

    def _dispatch(self, action):
        loop, client = self.loop, self.client
        if loop is None or client is None or self.stopping.is_set():
            return
        def request():
            if self.client is client and not self.stopping.is_set():
                action(client)
        try:
            loop.call_soon_threadsafe(request)
        except RuntimeError:
            pass  # Disconnect may close the loop between the click and dispatch.

    def use_supply(self, name):
        self._dispatch(lambda client: client.request_supply(name))

    def launch_mission(self, map_name, difficulty, continuing=False, checkpoint=None):
        def request(client):
            if client.ready and client.mission_request is None:
                client.mission_request = (map_name, difficulty, continuing)
                if checkpoint is not None:
                    client.mission_request += (None, dict(checkpoint))
        self._dispatch(request)

    def confirm_mission_restart(self, confirmation_id, restart):
        def request(client):
            if client.stop.is_set() or not client.ready or client.mission_request is not None:
                return
            confirmation = client.mission_confirmation
            if not confirmation or confirmation['id'] != confirmation_id:
                return
            if restart:
                client.mission_request = (confirmation['map'], confirmation['difficulty'], True, confirmation_id)
                if confirmation.get('checkpoint') is not None:
                    client.mission_request += (dict(confirmation['checkpoint']),)
            else:
                client.mission_confirmation = None
        self._dispatch(request)

    def resolve_items(self, refund):
        self._dispatch(lambda client: client.resolve_uncertain_items(refund))

    def set_transfers(self, scope):
        self._dispatch(lambda client: client.notifications.configure({'scope': scope}))

    def _worker(self, settings, password):
        logger = logging.getLogger("Renegade")
        handler = QueueLog(self.publish)
        logger.addHandler(handler)
        error = None
        try:
            asyncio.run(self._run(settings, password))
        except Exception as exc:
            error = str(exc)
            self.publish("error", error)
        finally:
            logger.removeHandler(handler)
            self.loop = self.client = None
            self.publish("stopped", error)

    async def _run(self, settings, password):
        from RenegadeClient import run
        self.loop = asyncio.get_running_loop()
        self.client = RenegadeClient(settings["server"], settings["slot"], password, self.state_dir)
        self.client.notifications.configure({'scope': settings.get('transfers', 'all')})
        if self.stopping.is_set():
            return
        native = dict(observer=dict(state="waiting", detail="Waiting for game"),
                      services=dict(state="waiting", detail="Waiting for game"))
        def native_status(component, state, detail):
            native[component] = dict(state=state, detail=detail)
        options = {'tt_core': True} if self.tt_core else {}
        task = asyncio.create_task(run(self.client, False, gate_missions=True, native_status=native_status, **options))
        try:
            while not task.done():
                self.publish("snapshot", snapshot(self.client, native))
                await asyncio.sleep(0.2)
            await task
        finally:
            if not task.done():
                task.cancel()
                await task

