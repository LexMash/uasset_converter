# -*- coding: utf-8 -*-
"""
Слой 0: интерфейс конвертера на tkinter.

Ничего своего не считает — редактирует config.json и запускает те же шаги, что
и CLI. Всё, что можно сделать мышкой, воспроизводится командой
`python convert.py --cli --step ...`.
"""
import json
import os
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

import convert
import i18n
from i18n import t

HERE = os.path.dirname(os.path.abspath(__file__))

# Ключ локализации хранится рядом с ключом конфига: подписи меняются вместе с
# языком, а в config.json уезжает по-прежнему технический ключ.
EXPORT_ITEMS = [
    ("textures", "gui.export.textures"),
    ("static_meshes", "gui.export.static_meshes"),
    ("skeletal_meshes", "gui.export.skeletal_meshes"),
    ("animations", "gui.export.animations"),
    ("materials", "gui.export.materials"),
    ("material_graphs", "gui.export.material_graphs"),
]

SCOPE_ITEMS = [
    ("test", "gui.scope.test"),
    ("selected", "gui.scope.selected"),
    ("all", "gui.scope.all"),
]

SHADER_OUTPUT_ITEMS = [
    ("hlsl", "gui.shader_output.hlsl"),
    ("shadergraph", "gui.shader_output.shadergraph"),
    ("both", "gui.shader_output.both"),
]


class ConverterApp:
    def __init__(self, root, config):
        self.root = root
        self.config = config
        self.messages = queue.Queue()
        self.worker = None
        self.stop_flag = threading.Event()

        root.title(t("gui.title"))
        root.geometry("980x800")
        root.minsize(820, 660)

        self.project_var = tk.StringVar(value=config["paths"]["ue_project"])
        # Если в конфиге пусто — сразу показываем найденный в реестре движок,
        # а не пустое поле: искать его руками пользователю незачем.
        self.engine_var = tk.StringVar(
            value=config["paths"].get("ue_engine_dir") or convert.find_engine_dir(config) or "")
        self.output_var = tk.StringVar(value=config["paths"]["output_dir"])
        self.pipeline_var = tk.StringVar(value=config.get("pipeline", "urp"))
        self.scope_var = tk.StringVar(value=config["scope"].get("mode", "test"))
        self.flip_var = tk.BooleanVar(value=config["textures"].get("flip_normal_green", True))
        self.avatar_var = tk.StringVar(value=config["unity"].get("avatar_type", "Generic"))
        self.shader_output_var = tk.StringVar(value=config["shader_gen"].get("output", "hlsl"))
        self.export_vars = {
            key: tk.BooleanVar(value=config["export"].get(key, True))
            for key, _ in EXPORT_ITEMS
        }

        self.languages = i18n.available() or [(i18n.DEFAULT_LANGUAGE, i18n.DEFAULT_LANGUAGE)]
        self.language_names = {name: code for code, name in self.languages}
        self.language_var = tk.StringVar(value=i18n.language_name(i18n.current_language()))

        self.status_var = tk.StringVar(value=t("gui.status.ready"))
        self.folders_status = tk.StringVar(value="")
        self.selected_folders = set(config["scope"].get("include_paths", []))
        self.tree_paths = {}

        self._build_ui()
        self.root.after(100, self._drain_messages)

    # -- построение интерфейса ----------------------------------------------

    def _build_ui(self):
        outer = ttk.Frame(self.root, padding=10)
        outer.pack(fill="both", expand=True)

        self._build_header(outer)
        self._build_paths(outer)
        middle = ttk.Frame(outer)
        middle.pack(fill="x", pady=(8, 0))
        self._build_export(middle)
        self._build_options(middle)
        self._build_folders(outer)
        self._build_actions(outer)
        self._build_log(outer)

    def _build_header(self, parent):
        """Выбор языка держим наверху: он меняет всё остальное окно."""
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=(0, 6))
        ttk.Label(row, text=t("gui.option.language")).pack(side="left")
        combo = ttk.Combobox(row, textvariable=self.language_var, width=18, state="readonly",
                             values=[name for _, name in self.languages])
        combo.pack(side="left", padx=6)
        combo.bind("<<ComboboxSelected>>", self._change_language)

    def _change_language(self, _event=None):
        code = self.language_names.get(self.language_var.get())
        if not code or code == i18n.current_language():
            return
        self.config["language"] = code
        convert.save_config(self.collect_config())
        i18n.set_language(code)
        self._rebuild()

    def _rebuild(self):
        """
        Перевод меняется пересборкой окна, а не обходом виджетов: подписи живут
        в конструкторах, и держать на каждую из них отдельную ссылку ради смены
        языка — больше кода, чем сама пересборка.
        """
        state = {
            "log": self.log_widget.get("1.0", "end-1c"),
            "status": self.status_var.get(),
        }
        for widget in self.root.winfo_children():
            widget.destroy()
        self.root.title(t("gui.title"))
        self._build_ui()
        self.status_var.set(state["status"])
        if state["log"]:
            self.log_widget.configure(state="normal")
            self.log_widget.insert("end", state["log"])
            self.log_widget.see("end")
            self.log_widget.configure(state="disabled")

    def _build_paths(self, parent):
        box = ttk.LabelFrame(parent, text=t("gui.box.paths"), padding=8)
        box.pack(fill="x")
        box.columnconfigure(1, weight=1)

        rows = [
            (t("gui.path.project"), self.project_var, self._pick_project),
            (t("gui.path.engine"), self.engine_var, self._pick_engine),
            (t("gui.path.output"), self.output_var, self._pick_output),
        ]
        for row, (label, variable, command) in enumerate(rows):
            ttk.Label(box, text=label).grid(row=row, column=0, sticky="w", pady=2)
            ttk.Entry(box, textvariable=variable).grid(row=row, column=1, sticky="ew", padx=6)
            ttk.Button(box, text=t("gui.browse"), command=command, width=12).grid(row=row, column=2)

    def _build_export(self, parent):
        box = ttk.LabelFrame(parent, text=t("gui.box.export"), padding=8)
        box.pack(side="left", fill="both", expand=True)
        for key, label_key in EXPORT_ITEMS:
            ttk.Checkbutton(box, text=t(label_key), variable=self.export_vars[key]).pack(anchor="w")

    def _build_options(self, parent):
        box = ttk.LabelFrame(parent, text=t("gui.box.options"), padding=8)
        box.pack(side="left", fill="both", expand=True, padx=(8, 0))

        ttk.Label(box, text=t("gui.option.pipeline")).pack(anchor="w")
        row = ttk.Frame(box)
        row.pack(anchor="w", pady=(0, 6))
        for value, label in (("urp", "URP"), ("hdrp", "HDRP")):
            ttk.Radiobutton(row, text=label, value=value,
                            variable=self.pipeline_var).pack(side="left", padx=(0, 12))

        ttk.Label(box, text=t("gui.option.scope")).pack(anchor="w")
        for value, label_key in SCOPE_ITEMS:
            ttk.Radiobutton(box, text=t(label_key), value=value, variable=self.scope_var,
                            command=self._sync_tree_state).pack(anchor="w")

        ttk.Separator(box).pack(fill="x", pady=6)

        ttk.Label(box, text=t("gui.option.shader_output")).pack(anchor="w")
        for value, label_key in SHADER_OUTPUT_ITEMS:
            ttk.Radiobutton(box, text=t(label_key), value=value,
                            variable=self.shader_output_var).pack(anchor="w")

        ttk.Separator(box).pack(fill="x", pady=6)
        ttk.Checkbutton(box, text=t("gui.option.flip_normal"),
                        variable=self.flip_var).pack(anchor="w")

        row = ttk.Frame(box)
        row.pack(anchor="w", pady=(6, 0))
        ttk.Label(row, text=t("gui.option.avatar")).pack(side="left")
        ttk.Combobox(row, textvariable=self.avatar_var, width=10, state="readonly",
                     values=("Generic", "Humanoid")).pack(side="left", padx=6)

    def _build_folders(self, parent):
        """
        Дерево папок /Game с галочками. Строится обходом файлов на диске, без
        запуска Unreal — выбрать объём надо ДО долгого шага экспорта.
        """
        box = ttk.LabelFrame(parent, text=t("gui.box.folders"), padding=8)
        box.pack(fill="both", expand=False, pady=(8, 0))

        row = ttk.Frame(box)
        row.pack(fill="x", pady=(0, 4))
        ttk.Button(row, text=t("gui.folders.reload"), command=self.reload_folders).pack(side="left")
        ttk.Button(row, text=t("gui.folders.clear"), command=self._clear_folders).pack(side="left", padx=6)
        ttk.Label(row, textvariable=self.folders_status).pack(side="left", padx=10)

        self.tree = ttk.Treeview(box, height=7, columns=(), show="tree")
        scrollbar = ttk.Scrollbar(box, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="left", fill="y")
        self.tree.bind("<<TreeviewSelect>>", self._toggle_folder)

        self.reload_folders()
        self._sync_tree_state()

    def reload_folders(self):
        """Заполняет дерево папками Content, полученными из общей с CLI функции."""
        for item in self.tree.get_children(""):
            self.tree.delete(item)
        self.tree_paths.clear()

        self.config["paths"]["ue_project"] = self.project_var.get()
        folders = convert.list_content_folders(self.config)
        if folders is None:
            self.folders_status.set(t("gui.folders.no_content"))
            return

        nodes = {}
        for ue_path in folders:
            parent_path = ue_path.rsplit("/", 1)[0]
            parent = nodes.get(parent_path, "")
            node = self.tree.insert(parent, "end", text=self._label(ue_path))
            nodes[ue_path] = node
            self.tree_paths[node] = ue_path
        self._update_folders_status()

    def _label(self, ue_path):
        mark = "[x]" if ue_path in self.selected_folders else "[  ]"
        return "%s %s" % (mark, ue_path.rsplit("/", 1)[-1])

    def _toggle_folder(self, _event=None):
        for item in self.tree.selection():
            ue_path = self.tree_paths.get(item)
            if not ue_path:
                continue
            if ue_path in self.selected_folders:
                self.selected_folders.remove(ue_path)
            else:
                self.selected_folders.add(ue_path)
            self.tree.item(item, text=self._label(ue_path))
        self.tree.selection_remove(*self.tree.selection())
        self._update_folders_status()

    def _clear_folders(self):
        self.selected_folders.clear()
        for item, ue_path in self.tree_paths.items():
            self.tree.item(item, text=self._label(ue_path))
        self._update_folders_status()

    def _update_folders_status(self):
        count = len(self.selected_folders)
        self.folders_status.set(t("gui.folders.selected", count=count) if count
                                else t("gui.folders.none_selected"))

    def _sync_tree_state(self):
        """Дерево имеет смысл только в режиме «Выбранные папки»."""
        state = "normal" if self.scope_var.get() == "selected" else "disabled"
        try:
            self.tree.state(("!disabled",) if state == "normal" else ("disabled",))
        except tk.TclError:
            pass

    def _build_actions(self, parent):
        box = ttk.Frame(parent)
        box.pack(fill="x", pady=8)

        self.buttons = {}
        actions = [
            ("scan", "gui.action.scan", self.action_scan),
            ("export", "gui.action.export", lambda: self.run_step("export")),
            ("shaders", "gui.action.shaders", lambda: self.run_step("shaders")),
            ("postprocess", "gui.action.postprocess", lambda: self.run_step("postprocess")),
            ("all", "gui.action.all", lambda: self.run_step("all")),
        ]
        for key, label_key, command in actions:
            button = ttk.Button(box, text=t(label_key), command=command)
            button.pack(side="left", padx=(0, 6))
            self.buttons[key] = button

        self.stop_button = ttk.Button(box, text=t("gui.action.stop"), command=self.action_stop,
                                      state="disabled")
        self.stop_button.pack(side="left", padx=(6, 0))
        ttk.Button(box, text=t("gui.action.open_output"),
                   command=self.action_open_output).pack(side="right")
        ttk.Button(box, text=t("gui.action.install_package"),
                   command=self.action_install_importer).pack(side="right", padx=(0, 6))

    def _build_log(self, parent):
        box = ttk.LabelFrame(parent, text=t("gui.box.log"), padding=8)
        box.pack(fill="both", expand=True)

        self.progress = ttk.Progressbar(box, mode="determinate")
        self.progress.pack(fill="x", pady=(0, 4))
        ttk.Label(box, textvariable=self.status_var).pack(anchor="w")

        self.log_widget = ScrolledText(box, height=18, wrap="word", state="disabled",
                                       font=("Consolas", 9))
        self.log_widget.pack(fill="both", expand=True, pady=(4, 0))

    # -- вспомогательное -----------------------------------------------------

    def emit(self, text):
        self.messages.put(("log", text))

    def _drain_messages(self):
        """
        Виджеты tkinter трогаем только из главного потока, поэтому рабочий
        поток кладёт сообщения в очередь, а разбираем их здесь.
        """
        try:
            while True:
                kind, payload = self.messages.get_nowait()
                if kind == "log":
                    self.log_widget.configure(state="normal")
                    self.log_widget.insert("end", payload + "\n")
                    self.log_widget.see("end")
                    self.log_widget.configure(state="disabled")
                elif kind == "progress":
                    done, total, label = payload
                    self.progress.configure(maximum=max(total, 1), value=done)
                    self.status_var.set(t("gui.progress.of", done=done, total=total, label=label))
                elif kind == "status":
                    self.status_var.set(payload)
                elif kind == "done":
                    self._set_running(False)
                    self.status_var.set(payload)
        except queue.Empty:
            pass
        self.root.after(100, self._drain_messages)

    def _set_running(self, running):
        state = "disabled" if running else "normal"
        for button in self.buttons.values():
            button.configure(state=state)
        self.stop_button.configure(state="normal" if running else "disabled")

    def collect_config(self):
        """Переносит состояние интерфейса в конфиг и сохраняет его на диск."""
        self.config["paths"]["ue_project"] = self.project_var.get()
        self.config["paths"]["ue_engine_dir"] = self.engine_var.get()
        self.config["paths"]["output_dir"] = self.output_var.get()
        self.config["pipeline"] = self.pipeline_var.get()
        self.config["scope"]["mode"] = self.scope_var.get()
        if self.selected_folders:
            self.config["scope"]["include_paths"] = sorted(self.selected_folders)
        self.config["textures"]["flip_normal_green"] = self.flip_var.get()
        self.config["unity"]["avatar_type"] = self.avatar_var.get()
        self.config["shader_gen"]["output"] = self.shader_output_var.get()
        for key, variable in self.export_vars.items():
            self.config["export"][key] = variable.get()
        convert.save_config(self.config)
        return self.config

    # -- выбор путей ---------------------------------------------------------

    def _pick_project(self):
        path = filedialog.askopenfilename(title=t("gui.pick.project"),
                                          filetypes=[(t("gui.pick.project_filter"), "*.uproject")])
        if path:
            self.project_var.set(path)

    def _pick_engine(self):
        path = filedialog.askdirectory(title=t("gui.pick.engine"))
        if path:
            self.engine_var.set(path)

    def _pick_output(self):
        path = filedialog.askdirectory(title=t("gui.pick.output"))
        if path:
            self.output_var.set(path)

    # -- действия ------------------------------------------------------------

    def action_scan(self):
        """
        Быстрая разведка по файлам, без запуска Unreal: сколько чего лежит в
        проекте. Нужна, чтобы понимать масштаб до долгого экспорта.
        """
        config = self.collect_config()
        project_dir = os.path.dirname(config["paths"]["ue_project"])
        content_dir = os.path.join(project_dir, "Content")
        if not os.path.isdir(content_dir):
            messagebox.showerror(t("gui.scan.not_found_title"), t("gui.scan.not_found_body"))
            return

        counts, total_bytes = {}, 0
        for root_dir, _, files in os.walk(content_dir):
            for name in files:
                extension = os.path.splitext(name)[1].lower()
                counts[extension] = counts.get(extension, 0) + 1
                try:
                    total_bytes += os.path.getsize(os.path.join(root_dir, name))
                except OSError:
                    pass

        self.emit(t("gui.scan.header"))
        self.emit(t("gui.scan.content", path=content_dir))
        for extension in sorted(counts, key=lambda e: -counts[e])[:8]:
            self.emit("  %-10s %d" % (extension or t("gui.scan.no_extension"), counts[extension]))
        self.emit(t("gui.scan.total_gb", size="%.1f" % (total_bytes / (1024 ** 3))))
        self.emit(t("gui.scan.hint"))

    def run_step(self, step):
        if self.worker and self.worker.is_alive():
            return
        config = self.collect_config()
        self.stop_flag.clear()
        self._set_running(True)
        self.progress.configure(value=0)
        self.status_var.set(t("gui.status.working"))
        self.log_widget.configure(state="normal")
        self.log_widget.delete("1.0", "end")
        self.log_widget.configure(state="disabled")

        def work():
            try:
                code = convert.run_step(
                    step, config,
                    on_line=self.emit,
                    on_progress=lambda d, tt, l: self.messages.put(("progress", (d, tt, l))),
                    should_stop=self.stop_flag.is_set,
                )
                if self.stop_flag.is_set():
                    self.messages.put(("done", t("gui.status.stopped")))
                elif code == 0:
                    self.messages.put(("done", t("gui.status.step_done", step=step)))
                else:
                    self.messages.put(("done", t("gui.status.step_failed", step=step, code=code)))
            except Exception as error:
                self.messages.put(("log", t("gui.error.prefix", error=error)))
                self.messages.put(("done", t("gui.status.aborted")))

        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()

    def action_stop(self):
        self.stop_flag.set()
        self.status_var.set(t("gui.status.stopping"))

    def action_open_output(self):
        path = self.output_var.get()
        os.makedirs(path, exist_ok=True)
        os.startfile(path)

    def action_install_importer(self):
        """
        Прописывает UPM-пакет в выбранный проект Unity.

        Именно прописывает, а не копирует: пакет по ссылке обновляется вместе с
        конвертером, не мусорит в Assets и не попадает в чужие коммиты.
        """
        target = filedialog.askdirectory(title=t("gui.pick.unity_project"))
        if not target:
            return

        manifest_path = os.path.join(target, "Packages", "manifest.json")
        if not os.path.isfile(manifest_path):
            messagebox.showerror(t("gui.install.not_unity_title"),
                                 t("gui.install.not_unity_body"))
            return

        package_dir = os.path.join(HERE, "unity", "com.uasset.converter")
        if not os.path.isfile(os.path.join(package_dir, "package.json")):
            messagebox.showerror(t("gui.install.no_package_title"),
                                 t("gui.install.no_package_body", path=package_dir))
            return

        try:
            with open(manifest_path, "r", encoding="utf-8") as fh:
                manifest = json.load(fh)
            dependencies = manifest.setdefault("dependencies", {})
            reference = "file:" + package_dir.replace("\\", "/")
            previous = dependencies.get("com.uasset.converter")
            dependencies["com.uasset.converter"] = reference
            with open(manifest_path, "w", encoding="utf-8") as fh:
                json.dump(manifest, fh, indent=2, ensure_ascii=False)
                fh.write("\n")
        except (OSError, ValueError) as error:
            messagebox.showerror(t("gui.install.failed_title"),
                                 t("gui.install.failed_body", error=error))
            return

        self.emit(t("gui.install.registered", path=manifest_path))
        if previous and previous != reference:
            self.emit(t("gui.install.replaced", previous=previous))
        messagebox.showinfo(t("gui.install.done_title"), t("gui.install.done_body"))


def launch(config):
    root = tk.Tk()
    try:
        ttk.Style().theme_use("vista")
    except tk.TclError:
        pass
    ConverterApp(root, config)
    root.mainloop()
    return 0
