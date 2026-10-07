"""Modulator UI: MSEG curve editor and assignment dialog.

Modulators are per-track, tempo-synced multi-segment envelopes that can
be assigned to plugin parameters (one-to-many). This is Pulsegrid's
original take on the modulation-graph concept.
"""

import tkinter as tk
from tkinter import ttk


class MSEGCurveEditor(tk.Toplevel):
    """Edit an MSEG's nodes: click to add, drag to move, right-click to delete.

    nodes: [[time_beats, value 0..1], ...]. Calls on_change(nodes) on edit.
    length_bars sets the visible time range.
    """

    def __init__(self, master, title, nodes, length_bars, on_change):
        super().__init__(master)
        self.title(title)
        self._nodes = [list(n) for n in nodes]
        self._length_bars = max(0.25, length_bars)
        self._on_change = on_change
        self._drag_idx = None

        self._canvas = tk.Canvas(self, width=480, height=220, bg="#0d1117",
                                 highlightthickness=0)
        self._canvas.pack(padx=10, pady=10)
        self._canvas.bind("<Button-1>", self._on_click)
        self._canvas.bind("<B1-Motion>", self._on_drag)
        self._canvas.bind("<ButtonRelease-1>", self._on_release)
        self._canvas.bind("<Button-3>", self._on_right_click)

        hint = ttk.Label(self, text="Click: add node | Drag: move node | Right-click: delete node",
                         font=("", 8), foreground="#8b949e")
        hint.pack(pady=(0, 4))

        btns = ttk.Frame(self)
        btns.pack(pady=(0, 10))
        ttk.Button(btns, text="Close", command=self.destroy).pack()

        self._draw()

    def _to_px(self, t, v):
        w = int(self._canvas["width"])
        h = int(self._canvas["height"])
        max_t = self._length_bars * 4.0
        x = 10 + (t / max_t) * (w - 20)
        y = 10 + (1.0 - v) * (h - 20)
        return x, y

    def _from_px(self, x, y):
        w = int(self._canvas["width"])
        h = int(self._canvas["height"])
        max_t = self._length_bars * 4.0
        t = max(0.0, min(max_t, (x - 10) / (w - 20) * max_t))
        v = max(0.0, min(1.0, 1.0 - (y - 10) / (h - 20)))
        return t, v

    def _draw(self):
        c = self._canvas
        c.delete("all")
        w = int(c["width"])
        h = int(c["height"])
        # Grid: bars
        max_t = self._length_bars * 4.0
        for b in range(int(self._length_bars) + 1):
            x, _ = self._to_px(b * 4.0, 0)
            c.create_line(x, 10, x, h - 10, fill="#21262d")
            c.create_text(x, h - 5, text=f"{b+1}", fill="#8b949e", font=("", 7))
        # Curve
        pts = []
        for t, v in sorted(self._nodes):
            x, y = self._to_px(t, v)
            pts.extend([x, y])
        if len(pts) >= 4:
            c.create_line(pts, fill="#58a6ff", width=2)
        elif len(pts) == 2:
            x, y = pts
            c.create_line(x, y, x, y, fill="#58a6ff", width=2)
        # Nodes
        for t, v in self._nodes:
            x, y = self._to_px(t, v)
            c.create_oval(x - 5, y - 5, x + 5, y + 5,
                          fill="#f78166", outline="#f78166")

    def _on_click(self, e):
        # Check if clicking near existing node -> start drag
        for i, (t, v) in enumerate(self._nodes):
            x, y = self._to_px(t, v)
            if abs(e.x - x) < 8 and abs(e.y - y) < 8:
                self._drag_idx = i
                return
        # Otherwise add node
        t, v = self._from_px(e.x, e.y)
        self._nodes.append([t, v])
        self._nodes.sort()
        self._drag_idx = self._nodes.index([t, v])
        self._draw()
        self._on_change([list(n) for n in self._nodes])

    def _on_drag(self, e):
        if self._drag_idx is None:
            return
        t, v = self._from_px(e.x, e.y)
        self._nodes[self._drag_idx] = [t, v]
        self._draw()

    def _on_release(self, e):
        if self._drag_idx is not None:
            self._nodes.sort()
            self._drag_idx = None
            self._draw()
            self._on_change([list(n) for n in self._nodes])

    def _on_right_click(self, e):
        for i, (t, v) in enumerate(self._nodes):
            x, y = self._to_px(t, v)
            if abs(e.x - x) < 8 and abs(e.y - y) < 8:
                if len(self._nodes) > 1:
                    del self._nodes[i]
                    self._draw()
                    self._on_change([list(n) for n in self._nodes])
                return


class ModAssignmentDialog(tk.Toplevel):
    """Pick a modulation target: FX slot or generator layer, param, amount, polarity.

    targets: [{"kind": "fx"|"gen", "index": int, "label": str,
               "params": [{"id": int, "name": str, "min": float, "max": float}]}]
    Calls on_add(assignment_dict).
    """

    def __init__(self, master, targets, on_add):
        super().__init__(master)
        self.title("Add modulation assignment")
        self._targets = targets
        self._on_add = on_add

        frm = ttk.Frame(self, padding=10)
        frm.pack(fill="both", expand=True)

        ttk.Label(frm, text="Target:").grid(row=0, column=0, sticky="w", pady=2)
        self._target_var = tk.StringVar()
        target_names = [t["label"] for t in targets]
        self._target_combo = ttk.Combobox(frm, textvariable=self._target_var,
                                          values=target_names, state="readonly",
                                          width=30)
        self._target_combo.grid(row=0, column=1, pady=2, sticky="ew")
        if target_names:
            self._target_combo.set(target_names[0])
        self._target_combo.bind("<<ComboboxSelected>>", self._on_target)

        ttk.Label(frm, text="Parameter:").grid(row=1, column=0, sticky="w", pady=2)
        self._param_var = tk.StringVar()
        self._param_combo = ttk.Combobox(frm, textvariable=self._param_var,
                                         state="readonly", width=30)
        self._param_combo.grid(row=1, column=1, pady=2, sticky="ew")

        ttk.Label(frm, text="Amount:").grid(row=2, column=0, sticky="w", pady=2)
        self._amount_var = tk.DoubleVar(value=0.5)
        ttk.Scale(frm, from_=0.0, to=1.0, variable=self._amount_var,
                  orient="horizontal", length=200).grid(row=2, column=1, pady=2)

        ttk.Label(frm, text="Polarity:").grid(row=3, column=0, sticky="w", pady=2)
        self._polarity_var = tk.StringVar(value="positive")
        ttk.Combobox(frm, textvariable=self._polarity_var,
                     values=["positive", "negative", "bipolar"],
                     state="readonly", width=30).grid(row=3, column=1, pady=2)

        btns = ttk.Frame(frm)
        btns.grid(row=4, column=0, columnspan=2, pady=(10, 0))
        ttk.Button(btns, text="Add", command=self._do_add).pack(side="left", padx=5)
        ttk.Button(btns, text="Cancel", command=self.destroy).pack(side="left")

        self._on_target()

    def _on_target(self, _e=None):
        label = self._target_var.get()
        for t in self._targets:
            if t["label"] == label:
                names = [p["name"] for p in t["params"]]
                self._param_combo["values"] = names
                if names:
                    self._param_combo.set(names[0])
                self._current_target = t
                return

    def _do_add(self):
        t = getattr(self, "_current_target", None)
        if not t:
            return
        pname = self._param_var.get()
        param = next((p for p in t["params"] if p["name"] == pname), None)
        if not param:
            return
        self._on_add({
            "target_kind": t["kind"],
            "target_index": t["index"],
            "param_id": int(param["id"]),
            "amount": float(self._amount_var.get()),
            "polarity": self._polarity_var.get(),
            "param_min": float(param["min"]),
            "param_max": float(param["max"]),
        })
        self.destroy()
