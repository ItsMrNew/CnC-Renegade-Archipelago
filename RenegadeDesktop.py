"""Native-only Renegade Archipelago desktop client."""
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from datetime import datetime
import queue
import tkinter as tk
from tkinter import ttk, messagebox

from renegade.data import MISSIONS, SUPPLIES, UPGRADES, UPGRADE_LIMIT, ITEM_COLOURS, LIGHT_ITEM_COLOURS
from renegade.desktop import DesktopSession, connection_settings, load_settings, save_settings, preview_missions
from renegade.check_hints import filter_missions, check_description
from renegade.app_paths import resource_root, data_root, state_directory, preferred_build, remember_build

TRANSFER_SCOPES = {"All players": "all", "Your transfers": "you", "Off": "off"}
STATUS_COLOURS = {"Locked": "#FF7777", "Available": "#FFB347", "Complete": "#79D991", "Pending sync": "#FFB347", "Missed this attempt": "#FF7777"}
LIGHT_STATUS_COLOURS = {"Locked": "#AE3030", "Available": "#955500", "Complete": "#237344", "Pending sync": "#955500", "Missed this attempt": "#AE3030"}
HOTKEY_LABELS = dict(zip(SUPPLIES, ("F2", "F3", "F4")))
ROOT = Path(__file__).resolve().parent


class DesktopWindow:
    def __init__(self, root, state_dir=ROOT / ".state", *, tt_core=False):
        self.root = root
        self.tt_core = tt_core
        self.state_dir = Path(state_dir)
        self.settings_path = self.state_dir / "desktop.json"
        settings = load_settings(self.settings_path)
        self.session = DesktopSession(self.state_dir, tt_core=tt_core)
        self.closing = False
        self.last_snapshot = None
        self.shown_mission_confirmation = None
        self.mission_rows = []
        self.check_nodes = set()
        self.checkpoint_options = []
        self.checkpoint_map = None
        self.compact = False
        root.title("Renegade Archipelago")
        icon = resource_root() / 'assets' / 'Renegade.ico'
        if icon.is_file():
            root.iconbitmap(str(icon))
        height = max(800, min(960, root.winfo_screenheight() - 100))
        root.geometry(f"920x{height}")
        root.minsize(880, 800)
        root.protocol("WM_DELETE_WINDOW", self.close)
        self.style = style = ttk.Style(root)
        style.theme_use("clam")  # Explicit colors also work on Windows in dark mode.
        style.configure("Title.TLabel", font=("Segoe UI", 19, "bold"))
        style.configure("Treeview", rowheight=25)
        frame = ttk.Frame(root, padding=18)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(5, weight=1)
        ttk.Label(frame, text="RENEGADE / ARCHIPELAGO", style="Title.TLabel").grid(row=0, sticky="w")
        theme_frame = ttk.Frame(frame)
        theme_frame.grid(row=0, sticky="e")
        ttk.Label(theme_frame, text="Theme").pack(side="left", padx=(0, 8))
        self.theme = tk.StringVar(value=settings["theme"].title())
        self.theme_choice = ttk.Combobox(theme_frame, textvariable=self.theme,
                                        values=("Light", "Dark"), state="readonly", width=8)
        self.theme_choice.pack(side="left")
        self.theme_choice.bind("<<ComboboxSelected>>", self.change_theme)
        build_frame = ttk.Frame(frame)
        build_frame.grid(row=1, sticky='ew', pady=(2, 14))
        ttk.Label(build_frame, text='Game version').pack(side='left', padx=(0,8))
        self.build_choice = ttk.Combobox(build_frame, values=('Steam', 'Tiberian Technologies 4.8.4'),
                                         state='readonly', width=30)
        self.build_choice.current(1 if tt_core else 0)
        self.build_choice.pack(side='left')
        self.build_choice.bind('<<ComboboxSelected>>', self.change_build)

        form = ttk.LabelFrame(frame, text="Archipelago room", padding=12)
        form.grid(row=2, sticky="ew")
        for i in range(3):
            form.columnconfigure(i, weight=1)
        self.server = tk.StringVar(value=settings["server"])
        self.slot = tk.StringVar(value=settings["slot"])
        self.password = tk.StringVar()
        self.inputs = []
        for col, (label, var) in enumerate((("Server", self.server), ("Slot name", self.slot), ("Password (optional)", self.password))):
            ttk.Label(form, text=label).grid(row=0, column=col, sticky="w")
            entry = ttk.Entry(form, textvariable=var, show="•" if col == 2 else "", width=24)
            entry.grid(row=1, column=col, sticky="ew", padx=(0, 10), pady=(4, 6))
            self.inputs.append(entry)
        self.connect_button = ttk.Button(form, text="Connect", command=self.toggle)
        self.connect_button.grid(row=1, column=3, sticky="e", pady=(4, 6))
        ttk.Label(form, text="Start Renegade separately. Run this client as administrator if the game is elevated.",
                  wraplength=800).grid(row=2, column=0, columnspan=4, sticky="w", pady=(4, 0))

        status = ttk.Frame(frame, padding=(0, 12, 0, 10))
        status.grid(row=3, sticky="ew")
        self.connection = tk.StringVar(value="Disconnected")
        self.progress = tk.StringVar()
        self.native = tk.StringVar(value="Observer: stopped  |  Mission gate: stopped")
        self.room = tk.StringVar(value="")
        self.connection_label = ttk.Label(status, textvariable=self.connection, wraplength=835)
        self.connection_label.grid(row=0, sticky="w", pady=2)
        for i, var in ((1,self.room), (3,self.native)):
            ttk.Label(status, textvariable=var, wraplength=835).grid(row=i, sticky="w", pady=2)
        metrics = ttk.Frame(status)
        metrics.grid(row=2, sticky="w", pady=2)
        self.metrics = {}
        self.metric_labels = {}
        for key, style in (('total','TotalChecks.TLabel'), ('logic','LogicChecks.TLabel'), ('maps','LogicChecks.TLabel')):
            value = self.metrics[key] = tk.StringVar()
            label = self.metric_labels[key] = ttk.Label(metrics, textvariable=value, style=style)
            label.pack(side='left', padx=(0,18))
        self.show_progress()
        self.item_summary = tk.StringVar()
        ttk.Label(status, textvariable=self.item_summary, wraplength=835, style="Item2.TLabel").grid(row=4, sticky="w", pady=(6,2))
        supply_frame = ttk.Frame(status)
        supply_frame.grid(row=5, sticky="w")
        self.supply_buttons = {}
        for name in SUPPLIES:
            button = ttk.Button(supply_frame, text=f"{HOTKEY_LABELS[name]}: {name} (0)", state="disabled", style="Supply.TButton",
                                command=lambda n=name: self.session.use_supply(n))
            button.pack(side="left", padx=(0,8))
            self.supply_buttons[name] = button
        self.recovery_button = ttk.Button(supply_frame,text='Review item recovery',command=self.review_recovery)
        campaign = ttk.Frame(frame)
        campaign.grid(row=4, sticky='ew', pady=(0,6))
        ttk.Label(campaign, text="Campaign missions", font=("Segoe UI", 11, "bold")).pack(side='left', padx=(0,12))
        table_frame = ttk.Frame(frame)
        table_frame.grid(row=5, sticky="nsew")
        table_frame.columnconfigure(0, weight=1)
        table_frame.rowconfigure(1, weight=1)
        filters = ttk.Frame(campaign)
        filters.pack(side='left', fill='x', expand=True)
        ttk.Label(filters, text='Search').pack(side='left', padx=(0, 6))
        self.search = tk.StringVar()
        self.search_entry = ttk.Entry(filters, textvariable=self.search, width=12)
        self.search_entry.pack(side='left', fill='x', expand=True, padx=(0, 10))
        self.hide_completed = tk.BooleanVar(value=False)
        ttk.Checkbutton(filters, text='Hide completed', variable=self.hide_completed, command=self.change_filters).pack(side='left', padx=(0, 8))
        self.check_types = {}
        for kind, label in (('primary','Primary'), ('secondary','Secondary'), ('disc','Discs'), ('victory','Victory')):
            self.check_types[kind] = tk.BooleanVar(value=True)
            ttk.Checkbutton(filters, text=label, variable=self.check_types[kind], command=self.change_filters).pack(side='left', padx=(0, 4))
        ttk.Button(filters, text='Reset', command=self.reset_filters, padding=(6, 2)).pack(side='left')
        self.table = ttk.Treeview(table_frame, columns=("mission", "status", "checks"), show="tree headings", height=12)
        self.table.column('#0', width=24, minwidth=24, stretch=False)
        for name, title, width in (("mission", "Mission", 485), ("status", "Status", 145), ("checks", "Checks", 75)):
            self.table.heading(name, text=title)
            self.table.column(name, width=width, minwidth=width if name != "mission" else 260, stretch=name == "mission")
        self.table.grid(row=1, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(table_frame, orient="vertical", command=self.table.yview)
        scroll.grid(row=1, column=1, sticky="ns")
        self.table.configure(yscrollcommand=scroll.set)
        for m in MISSIONS:
            self.table.insert("", "end", iid=m.map_name, values=(m.label, "Unknown", "—"))
        self.check_detail = tk.StringVar(value='Select a check for directions or recovery advice.')
        self.detail_label = ttk.Label(table_frame, textvariable=self.check_detail, wraplength=830)
        self.detail_label.grid(row=2, column=0, columnspan=2, sticky='ew', pady=(5, 0))
        self.search.trace_add('write', lambda *_: self.change_filters())
        self.show_missions(preview_missions())
        actions = ttk.Frame(frame)
        actions.grid(row=6,sticky='ew',pady=(7,12))
        controls = ttk.Frame(actions)
        controls.pack(fill='x')
        self.start_button = ttk.Button(controls,text='Start Mission',state='disabled',command=lambda:self.launch_mission(False))
        self.start_button.pack(side='left',padx=(0,8))
        self.continue_button = ttk.Button(controls,text='Continue Mission',state='disabled',command=lambda:self.launch_mission(True))
        self.continue_button.pack(side='left',padx=(0,12))
        self.difficulty = tk.StringVar(value='Soldier')
        ttk.Combobox(controls,textvariable=self.difficulty,values=('Recruit','Soldier','Commando'),state='readonly',width=10).pack(side='left')
        ttk.Label(controls,text='Checkpoint').pack(side='left', padx=(12,6))
        self.checkpoint_choice = ttk.Combobox(controls, state='disabled', width=36)
        self.checkpoint_choice.pack(side='left', fill='x', expand=True)
        self.launch_hint = tk.StringVar(value='Select an unlocked mission at the main menu.')
        ttk.Label(actions,textvariable=self.launch_hint,wraplength=830).pack(anchor='w', pady=(4,0))
        self.table.bind('<<TreeviewSelect>>',lambda event:self.selection_changed())
        transfers = ttk.Frame(frame)
        transfers.grid(row=7, sticky="e")
        ttk.Label(transfers, text="Item transfers").pack(side="left", padx=(0,8))
        self.transfers = tk.StringVar(value=next(k for k,v in TRANSFER_SCOPES.items() if v == settings['transfers']))
        self.transfer_choice = ttk.Combobox(transfers, textvariable=self.transfers,
            values=tuple(TRANSFER_SCOPES), state="readonly", width=16)
        self.transfer_choice.pack(side="left")
        self.transfer_choice.bind("<<ComboboxSelected>>", self.change_transfers)
        ttk.Label(frame, text="Session log", font=("Segoe UI", 11, "bold")).grid(row=7, sticky="w")
        log_frame = ttk.Frame(frame)
        log_frame.grid(row=8, sticky="ew", pady=(5, 0))
        log_frame.columnconfigure(0, weight=1)
        self.log = tk.Text(log_frame, height=7, wrap="word", state="disabled", font=("Consolas", 9))
        self.log.grid(row=0, column=0, sticky="ew")
        log_scroll = ttk.Scrollbar(log_frame, orient="vertical", command=self.log.yview)
        log_scroll.grid(row=0, column=1, sticky="ns")
        self.log.configure(yscrollcommand=log_scroll.set)
        self.apply_theme()
        root.bind('<Configure>', self.resize_layout, add=True)
        root.after(100, self.poll)

    def resize_layout(self, event):
        if event.widget != self.root:
            return
        compact = event.height < 820
        if compact != self.compact:
            self.compact = compact
            self.log.configure(height=4 if compact else 7)

    def apply_theme(self):
        dark = self.theme.get() == "Dark"
        bg, panel, field, fg, muted, border, accent = (
            ("#20242b", "#2c323c", "#171b21", "#f1f4f8", "#a8b0bd", "#4b5563", "#316ea8") if dark else
            ("#f3f5f7", "#e4e9ef", "#ffffff", "#202733", "#627083", "#acb7c5", "#245f9e"))
        style = self.style
        self.root.configure(background=bg)
        style.configure(".", background=bg, foreground=fg, bordercolor=border,
                        lightcolor=border, darkcolor=border, troughcolor=field, focuscolor=accent)
        style.map(".", foreground=[("disabled", muted)])
        style.configure("TButton", background=panel, padding=(10, 5))
        style.map("TButton", background=[("active", accent), ("pressed", accent)],
                  foreground=[("disabled", muted), ("active", "white")])
        style.configure("TEntry", fieldbackground=field, foreground=fg, insertcolor=fg)
        style.map("TEntry", fieldbackground=[("disabled", panel)], foreground=[("disabled", muted)])
        style.configure("TCombobox", fieldbackground=field, background=panel, arrowcolor=fg)
        style.map("TCombobox", fieldbackground=[("readonly", field), ("disabled", panel)],
                  foreground=[("disabled", muted), ("readonly", fg)], selectbackground=[("readonly", field)],
                  selectforeground=[("readonly", fg)])
        style.configure("TCheckbutton", indicatorbackground=field)
        style.map("TCheckbutton", background=[("active", bg)], indicatorbackground=[("selected", accent)])
        style.configure("Treeview", background=field, fieldbackground=field, foreground=fg)
        style.map("Treeview", background=[("selected", "#344252" if dark else "#dce5ef")], foreground=[])
        for state, colour in (STATUS_COLOURS if dark else LIGHT_STATUS_COLOURS).items():
            self.table.tag_configure(state, foreground=colour)
        status_colours = STATUS_COLOURS if dark else LIGHT_STATUS_COLOURS
        style.configure('TotalChecks.TLabel', foreground=status_colours['Locked'])
        style.configure('LogicChecks.TLabel', foreground=status_colours['Available'])
        style.configure('CompleteChecks.TLabel', foreground=status_colours['Complete'])
        style.configure('Alert.TLabel', foreground=status_colours['Locked'])
        style.configure("Treeview.Heading", background=panel, foreground=fg, padding=(6, 5))
        style.map("Treeview.Heading", background=[("active", panel)])
        style.configure("Vertical.TScrollbar", background=panel, arrowcolor=fg)
        self.log.configure(background=field, foreground=fg, insertbackground=fg,
                           selectbackground=accent, selectforeground="white", relief="flat",
                           highlightthickness=1, highlightbackground=border)
        for flag, colour in (ITEM_COLOURS if dark else LIGHT_ITEM_COLOURS).items():
            style.configure(f"Item{flag}.TLabel", foreground=colour)
            self.log.tag_configure(f"item{flag}", foreground=colour)
        self.log.tag_configure('alert', foreground=status_colours['Locked'])
        style.configure('Supply.TButton', foreground=(ITEM_COLOURS if dark else LIGHT_ITEM_COLOURS)[0])
        for option, value in (("background", field), ("foreground", fg),
                              ("selectBackground", accent), ("selectForeground", "white")):
            self.root.option_add("*TCombobox*Listbox." + option, value)
        # Tk creates the dropdown once; recolor it after subsequent theme changes.
        dropdown = self.root.tk.call("ttk::combobox::PopdownWindow", str(self.theme_choice))
        self.root.tk.call(str(dropdown) + ".f.l", "configure", "-background", field,
                          "-foreground", fg, "-selectbackground", accent, "-selectforeground", "white")

    def change_transfers(self, event=None):
        scope = TRANSFER_SCOPES[self.transfers.get()]
        self.session.set_transfers(scope)
        settings = load_settings(self.settings_path)
        settings['transfers'] = scope
        try:
            save_settings(self.settings_path, settings)
        except OSError as error:
            self.append_log("Could not save transfer preference: " + str(error), logging.WARNING)

    def change_theme(self, event=None):
        self.apply_theme()
        settings = load_settings(self.settings_path)
        settings["theme"] = self.theme.get().lower()
        try:
            save_settings(self.settings_path, settings)
        except OSError as error:
            self.append_log("Could not save theme preference: " + str(error), logging.WARNING)

    def append_log(self, text, level=logging.INFO):
        spans = []
        if isinstance(text, dict):
            spans = text.get('spans', [])
            level = text.get('level', level)
            text = text['text']
        alert = ('alert',) if level >= logging.WARNING else ()
        if alert:
            spans = []  # The entire warning/error takes priority over item colours.
        self.log.configure(state="normal")
        cursor = 0
        for first, last, flag in spans:
            if flag in ITEM_COLOURS and cursor <= first < last <= len(text):
                self.log.insert('end', text[cursor:first], ())
                self.log.insert('end', text[first:last], (f'item{flag}',))
                cursor = last
        self.log.insert('end', text[cursor:] + '\n', alert)
        lines = int(self.log.index("end-1c").split(".")[0])
        if lines > 250:
            self.log.delete("1.0", f"{lines - 250}.0")
        self.log.see("end")
        self.log.configure(state="disabled")

    def toggle(self):
        self.connection_label.configure(style='TLabel')
        if self.session.running:
            self.connection.set("Disconnecting — restoring game callbacks…")
            self.connect_button.configure(state="disabled")
            self.session.stop()
            return
        try:
            settings = connection_settings(self.server.get(), self.slot.get(), theme=self.theme.get().lower(),
                                           transfers=TRANSFER_SCOPES[self.transfers.get()])
            save_settings(self.settings_path, settings)
            self.session.start(settings, self.password.get() or None)
            self.password.set("")
            self.room.set("")
            self.show_progress()
            self.native.set("Observer: waiting  |  Mission gate: waiting")
            self.show_missions(preview_missions())
            self.connection.set("Connecting…")
            self.connect_button.configure(text="Disconnect")
            self.build_choice.configure(state='disabled')
            for widget in self.inputs:
                widget.configure(state="disabled")
        except (OSError, ValueError, RuntimeError) as error:
            self.connection.set(str(error))
            self.connection_label.configure(style='Alert.TLabel')
            self.append_log(str(error), logging.ERROR)

    def change_build(self, event=None):
        if self.session.running:
            self.build_choice.current(1 if self.tt_core else 0)
            return
        self.tt_core = self.build_choice.current() == 1
        self.state_dir = state_directory(self.tt_core)
        self.settings_path = self.state_dir / 'desktop.json'
        self.session = DesktopSession(self.state_dir, tt_core=self.tt_core)
        settings = load_settings(self.settings_path)
        self.server.set(settings['server'])
        self.slot.set(settings['slot'])
        self.password.set('')
        self.theme.set(settings['theme'].title())
        self.transfers.set(next(k for k,v in TRANSFER_SCOPES.items() if v == settings['transfers']))
        self.last_snapshot = None
        self.room.set('')
        self.item_summary.set('')
        self.connection.set('Disconnected')
        self.connection_label.configure(style='TLabel')
        self.native.set('Observer and native services stopped.')
        self.show_progress()
        self.show_missions(preview_missions())
        self.update_launch_buttons()
        self.apply_theme()
        try:
            remember_build(self.tt_core)
        except OSError as error:
            self.append_log('Could not save game version preference: ' + str(error), logging.WARNING)

    def show_progress(self, data=None):
        ready = data is not None and data['unlocked'] is not None
        for key, title, current, total, incomplete_style in (
            ('total', 'Total Checks', 'confirmed', 'total', 'TotalChecks.TLabel'),
            ('logic', 'Checks in Logic', 'logic_confirmed', 'logic_total', 'LogicChecks.TLabel'),
            ('maps', 'Maps Available', 'unlocked', None, 'LogicChecks.TLabel'),
        ):
            count = data[current] if ready else 0
            maximum = (data[total] if total else len(MISSIONS)) if ready else 0
            self.metrics[key].set(f'{title}: {count}/{maximum}' if ready else f'{title}: —')
            complete = ready and maximum > 0 and count == maximum
            self.metric_labels[key].configure(style='CompleteChecks.TLabel' if complete else incomplete_style)
        self.progress.set('  •  '.join(value.get() for value in self.metrics.values()))

    def display(self, data):
        if self.session.stopping.is_set():
            return
        self.last_snapshot = data
        self.connection.set(data["connection"])
        self.connection_label.configure(style='TLabel')
        self.room.set(f"Slot: {data['slot']}  |  Seed: {data['seed'] or 'waiting'}")
        self.show_progress(data)
        self.native.set("\n".join(f"{name.title()}: {value['state']} — {value['detail']}" for name, value in data["native"].items()))
        items = data.get('items', [])
        self.item_summary.set(' | '.join(f"{item['name']}: {min(item['count'],UPGRADE_LIMIT)}/{UPGRADE_LIMIT}" for item in items if item['name'] in UPGRADES)
                              + (' | Supply queued — resume an unlocked mission' if data.get('supply_pending') else ''))
        for item in items:
            if item['name'] in self.supply_buttons:
                self.supply_buttons[item['name']].configure(
                    text=f"{HOTKEY_LABELS[item['name']]}: {item['name']} ({item['available']})",
                    state='normal' if item['available'] and not data.get('supply_pending') else 'disabled')
        self.show_missions(data['missions'])
        self.update_launch_buttons()
        if data.get('recovery'):
            self.recovery_button.pack(side='left')
        else:
            self.recovery_button.pack_forget()
        self.confirm_mission_restart(data.get('mission_confirmation'))

    def update_launch_buttons(self):
        data = self.last_snapshot or {}
        launch = data.get('mission_launch') or {}
        selection = self.table.selection()
        map_name = selection[0].split(':')[0] if selection else None
        choices = launch.get('history', {}).get(map_name, [])
        index = self.checkpoint_choice.current()
        selected = self.checkpoint_options[index]['name'] if 0 <= index < len(self.checkpoint_options) else None
        self.checkpoint_options = choices
        if choices:
            labels = [f"{('Latest', 'Previous', 'Oldest')[i]} · {datetime.fromtimestamp(c['modified']):%d %b %H:%M:%S} · {('Recruit','Soldier','Commando')[c['difficulty']]}"
                      for i, c in enumerate(choices)]
            self.checkpoint_choice.configure(values=labels, state='readonly')
            chosen = next((i for i, c in enumerate(choices) if c['name'] == selected), 0) if map_name == self.checkpoint_map else 0
            self.checkpoint_choice.current(chosen)
        else:
            self.checkpoint_choice.configure(values=(), state='disabled')
            self.checkpoint_choice.set('No checkpoint yet')
        self.checkpoint_map = map_name
        allowed = (not self.session.stopping.is_set() and data.get('connection')=='Connected'
                   and map_name in launch.get('unlocked',[]) and (launch.get('menu') or launch.get('can_queue'))
                   and not launch.get('busy'))
        self.start_button.configure(state='normal' if allowed else 'disabled')
        self.continue_button.configure(state='normal' if allowed and map_name in launch.get('checkpoints',[]) else 'disabled')
        self.launch_hint.set('Waiting for difficulty confirmation…' if data.get('mission_confirmation') else
                             (launch.get('status') or 'Loading mission…') if launch.get('busy') else
                             'Continue loads the selected checkpoint. Earlier saves keep your AP checks and items.' if allowed else
                             'Select an unlocked mission.' if launch.get('can_queue') else
                             'Select an unlocked mission at the main menu.')

    def launch_mission(self, continuing):
        self.update_launch_buttons()
        button = self.continue_button if continuing else self.start_button
        if str(button.cget('state')) == 'disabled':
            return
        map_name = self.table.selection()[0].split(':')[0]
        args = (map_name,('Recruit','Soldier','Commando').index(self.difficulty.get()),continuing)
        index = self.checkpoint_choice.current()
        if continuing and 0 <= index < len(self.checkpoint_options):
            choice = self.checkpoint_options[index]
            args += (dict(name=choice['name'], fingerprint=choice['fingerprint']),)
        self.session.launch_mission(*args)
        self.launch_hint.set('Load requested. Return to Renegade to let it run.')
        self.start_button.configure(state='disabled')
        self.continue_button.configure(state='disabled')

    def confirm_mission_restart(self, confirmation):
        if not confirmation or confirmation['id'] == self.shown_mission_confirmation:
            return
        self.shown_mission_confirmation = confirmation['id']
        self.start_button.configure(state='disabled')
        self.continue_button.configure(state='disabled')
        levels = ('Recruit','Soldier','Commando')
        previous, selected = levels[confirmation['saved_difficulty']], levels[confirmation['difficulty']]
        answer = messagebox.askyesno('Restart mission at a different difficulty?',
            f"The checkpoint for {confirmation['title']} was saved on {previous}.\n\n"
            f"You selected {selected}. Continuing will restart this mission from the beginning on {selected}.\n\n"
            'Your Archipelago checks and items will be kept.\n\nAre you sure you want to restart?',
            icon='warning', default='no', parent=self.root)
        self.session.confirm_mission_restart(confirmation['id'], bool(answer))

    def review_recovery(self):
        items = (self.last_snapshot or {}).get('recovery',[])
        if not items:
            return
        choice = messagebox.askyesnocancel('Interrupted item activations',
            'The game did not leave a confirmed receipt for:\n\n'+ '\n'.join(item['name'] for item in items)+
            '\n\nTheir effects may already have happened.\nYes: return these items to inventory.\nNo: mark them as used.\nCancel: decide later.',parent=self.root)
        if choice is not None:
            self.session.resolve_items(choice)

    def change_filters(self):
        self.show_missions(self.mission_rows, expand=True)

    def reset_filters(self):
        self.hide_completed.set(False)
        for value in self.check_types.values():
            value.set(True)
        self.search.set('')
        self.change_filters()

    def selection_changed(self):
        selection = self.table.selection()
        key = selection[0] if selection else None
        check = next(((row['map'], obj) for row in self.mission_rows for obj in row.get('objectives', ())
                      if f"{row['map']}:{obj['id']}" == key), None)
        self.check_detail.set(check_description(*check) if check else 'Select a check for directions or recovery advice.')
        self.detail_label.configure(style='Alert.TLabel' if check and check[1]['state'] == 'Missed this attempt' else 'TLabel')
        if hasattr(self, 'start_button'):
            self.update_launch_buttons()

    def show_missions(self, rows, expand=False):
        self.mission_rows = rows
        expected = {f"{row['map']}:{obj['id']}" for row in rows for obj in row.get('objectives', ())}
        for iid in self.check_nodes - expected:
            if self.table.exists(iid):
                self.table.delete(iid)
        self.check_nodes = expected
        for row in rows:
            self.table.item(row["map"], tags=() if row['state']=='Unknown' else (row["state"],), values=(row["title"], row["state"], row["checks"]))
            objectives = row.get('objectives', [])
            for position, obj in enumerate(objectives):
                iid = f"{row['map']}:{obj['id']}"
                values = (obj['title'], obj['state'], obj['checks'])
                tags = () if obj['state']=='Unknown' else (obj['state'],)
                if self.table.exists(iid):
                    self.table.item(iid, tags=tags, values=values)
                else:
                    self.table.insert(row['map'], 'end', iid=iid, tags=tags, values=values)
        filtered = filter_missions(rows, self.search.get(), self.hide_completed.get(),
                                   {kind for kind, value in self.check_types.items() if value.get()})
        visible_maps = {row['map'] for row in filtered}
        visible_checks = {f"{row['map']}:{obj['id']}" for row in filtered for obj in row['objectives']}
        for m in MISSIONS:
            if m.map_name not in visible_maps:
                self.table.detach(m.map_name)
        for iid in self.check_nodes - visible_checks:
            self.table.detach(iid)
        for position, row in enumerate(filtered):
            self.table.move(row['map'], '', position)
            if expand:
                self.table.item(row['map'], open=True)
            for index, obj in enumerate(row['objectives']):
                self.table.move(f"{row['map']}:{obj['id']}", row['map'], index)
        hidden_selection = [iid for iid in self.table.selection() if iid not in visible_maps | visible_checks]
        if hidden_selection:
            self.table.selection_remove(*hidden_selection)
        self.selection_changed()
        if not filtered:
            self.check_detail.set('No checks match these filters. Reset to show all checks enabled by this room.')

    def poll(self):
        for _ in range(100):
            try:
                kind, value = self.session.events.get_nowait()
            except queue.Empty:
                break
            if kind == "snapshot":
                self.display(value)
            elif kind == "log":
                self.append_log(value)
            elif kind == "error":
                self.append_log("ERROR: " + value, logging.ERROR)
            elif kind == "stopped":
                self.last_snapshot = None
                self.update_launch_buttons()
                self.recovery_button.pack_forget()
                for button in self.supply_buttons.values():
                    button.configure(state='disabled')
                if value:
                    self.closing = False  # Keep cleanup failures visible before the user closes again.
                self.connection.set("Stopped with an error — see session log" if value else "Disconnected")
                self.connection_label.configure(style='Alert.TLabel' if value else 'TLabel')
                self.native.set("Check the error before reconnecting." if value else "Observer and native services stopped.")
                self.show_progress()
                self.show_missions([dict(row, state='Unknown', objectives=[dict(obj, state='Unknown') for obj in row.get('objectives', ())])
                                    for row in self.mission_rows])
                self.connect_button.configure(text="Connect", state="normal")
                self.build_choice.configure(state='readonly')
                for widget in self.inputs:
                    widget.configure(state="normal")
        if self.closing and not self.session.running:
            self.root.destroy()
            return
        self.root.after(100, self.poll)

    def close(self):
        self.closing = True
        if self.session.running:
            self.connection.set("Closing — waiting for native cleanup…")
            self.connect_button.configure(state="disabled")
            self.session.stop()
        else:
            self.root.destroy()


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    builds = parser.add_mutually_exclusive_group()
    builds.add_argument('--tt-core', action='store_true', help='Select Tiberian Technologies 4.8.4')
    builds.add_argument('--steam', action='store_true', help='Select stock Steam')
    parser.add_argument('--self-test', type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.self_test:
        from renegade.packaging_check import check
        try:
            check(args.self_test)
        except Exception:
            import json
            import traceback
            args.self_test.write_text(json.dumps({'passed': False, 'error': traceback.format_exc()}), encoding='utf-8')
            raise SystemExit(1) from None
        return
    tt = args.tt_core or (not args.steam and preferred_build())
    state_dir = state_directory(tt)
    log_dir = data_root() / 'logs'
    log_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, handlers=[RotatingFileHandler(
        log_dir / "desktop.log", maxBytes=2_000_000, backupCount=2, encoding="utf-8")],
        format="%(asctime)s %(name)s %(levelname)s: %(message)s")
    from renegade import __version__
    logging.getLogger('Renegade').info('Renegade Archipelago %s', __version__)
    root = tk.Tk()
    import ctypes
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('Renegade.Archipelago.Launcher')
    DesktopWindow(root, state_dir, tt_core=tt)
    root.mainloop()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback
        error = traceback.format_exc()
        logging.exception('Launcher stopped')
        messagebox.showerror('Renegade Archipelago', 'The launcher could not continue.\n\n' + error)
