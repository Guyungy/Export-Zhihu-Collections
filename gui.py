import json
import queue
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Callable, Optional


ROOT_DIR = Path(__file__).resolve().parent
CONFIG_PATH = ROOT_DIR / "config.json"

DEFAULT_CONFIG = {
    "zhihuUrls": [],
    "outputPath": "",
    "os": "",
    "openCollection": False,
}

OS_OPTIONS = [
    "",
    "windows",
    "linux",
    "macos",
    "freebsd",
    "openbsd",
    "netbsd",
    "solaris",
    "aix",
    "cygwin",
    "msys",
]


class ZhihuExporterGUI:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("知乎收藏夹导出工具")
        self.root.geometry("1080x760")
        self.root.minsize(920, 640)

        self.log_queue: queue.Queue[str] = queue.Queue()
        self.current_process: subprocess.Popen[str] | None = None
        self.active_worker: threading.Thread | None = None

        self.output_path_var = tk.StringVar()
        self.os_var = tk.StringVar()
        self.open_collection_var = tk.BooleanVar(value=False)
        self.name_var = tk.StringVar()
        self.url_var = tk.StringVar()
        self.status_var = tk.StringVar(value="就绪")

        self._build_layout()
        self._load_config_into_form()
        self.root.after(100, self._drain_log_queue)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_layout(self) -> None:
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(2, weight=1)
        self.root.rowconfigure(3, weight=1)

        settings_frame = ttk.LabelFrame(self.root, text="基础配置", padding=12)
        settings_frame.grid(row=0, column=0, sticky="nsew", padx=12, pady=(12, 6))
        settings_frame.columnconfigure(1, weight=1)

        ttk.Label(settings_frame, text="输出目录").grid(row=0, column=0, sticky="w")
        ttk.Entry(settings_frame, textvariable=self.output_path_var).grid(
            row=0, column=1, sticky="ew", padx=(8, 8)
        )
        ttk.Button(settings_frame, text="选择目录", command=self._choose_output_path).grid(
            row=0, column=2, sticky="ew"
        )

        ttk.Label(settings_frame, text="系统类型").grid(row=1, column=0, sticky="w", pady=(10, 0))
        self.os_combo = ttk.Combobox(
            settings_frame,
            textvariable=self.os_var,
            values=OS_OPTIONS,
            state="readonly",
        )
        self.os_combo.grid(row=1, column=1, sticky="w", padx=(8, 8), pady=(10, 0))
        ttk.Checkbutton(
            settings_frame,
            text="仅获取我的收藏夹列表（openCollection）",
            variable=self.open_collection_var,
        ).grid(row=1, column=2, sticky="w", pady=(10, 0))

        collection_frame = ttk.LabelFrame(self.root, text="收藏夹列表", padding=12)
        collection_frame.grid(row=1, column=0, sticky="nsew", padx=12, pady=6)
        collection_frame.columnconfigure(0, weight=1)
        collection_frame.rowconfigure(1, weight=1)

        editor_frame = ttk.Frame(collection_frame)
        editor_frame.grid(row=0, column=0, sticky="ew")
        editor_frame.columnconfigure(1, weight=1)
        editor_frame.columnconfigure(3, weight=1)

        ttk.Label(editor_frame, text="名称").grid(row=0, column=0, sticky="w")
        ttk.Entry(editor_frame, textvariable=self.name_var).grid(
            row=0, column=1, sticky="ew", padx=(8, 12)
        )
        ttk.Label(editor_frame, text="URL").grid(row=0, column=2, sticky="w")
        ttk.Entry(editor_frame, textvariable=self.url_var).grid(
            row=0, column=3, sticky="ew", padx=(8, 12)
        )

        ttk.Button(editor_frame, text="新增", command=self._add_collection).grid(row=0, column=4)
        ttk.Button(editor_frame, text="更新选中", command=self._update_selected_collection).grid(
            row=0, column=5, padx=(8, 0)
        )
        ttk.Button(editor_frame, text="删除选中", command=self._delete_selected_collection).grid(
            row=0, column=6, padx=(8, 0)
        )

        table_frame = ttk.Frame(collection_frame)
        table_frame.grid(row=1, column=0, sticky="nsew", pady=(12, 0))
        table_frame.columnconfigure(0, weight=1)
        table_frame.rowconfigure(0, weight=1)

        self.collection_tree = ttk.Treeview(
            table_frame,
            columns=("name", "url"),
            show="headings",
            height=12,
        )
        self.collection_tree.heading("name", text="名称")
        self.collection_tree.heading("url", text="收藏夹 URL")
        self.collection_tree.column("name", width=240, anchor="w")
        self.collection_tree.column("url", width=720, anchor="w")
        self.collection_tree.grid(row=0, column=0, sticky="nsew")
        self.collection_tree.bind("<<TreeviewSelect>>", self._load_selected_collection)

        tree_scrollbar = ttk.Scrollbar(table_frame, orient="vertical", command=self.collection_tree.yview)
        tree_scrollbar.grid(row=0, column=1, sticky="ns")
        self.collection_tree.configure(yscrollcommand=tree_scrollbar.set)

        action_frame = ttk.Frame(collection_frame)
        action_frame.grid(row=2, column=0, sticky="ew", pady=(12, 0))
        action_frame.columnconfigure(4, weight=1)

        self.save_button = ttk.Button(action_frame, text="保存配置", command=self._save_config)
        self.save_button.grid(row=0, column=0, sticky="w")

        self.reload_button = ttk.Button(action_frame, text="重新加载", command=self._load_config_into_form)
        self.reload_button.grid(row=0, column=1, sticky="w", padx=(8, 0))

        self.fetch_button = ttk.Button(
            action_frame, text="获取我的收藏夹", command=self._fetch_collections
        )
        self.fetch_button.grid(row=0, column=2, sticky="w", padx=(8, 0))

        self.export_button = ttk.Button(action_frame, text="开始导出", command=self._start_export)
        self.export_button.grid(row=0, column=3, sticky="w", padx=(8, 0))

        ttk.Label(action_frame, textvariable=self.status_var).grid(row=0, column=4, sticky="e")

        log_frame = ttk.LabelFrame(self.root, text="运行日志", padding=12)
        log_frame.grid(row=2, column=0, sticky="nsew", padx=12, pady=(6, 12))
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)

        self.log_text = tk.Text(log_frame, wrap="word", state="disabled")
        self.log_text.grid(row=0, column=0, sticky="nsew")
        log_scrollbar = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_text.yview)
        log_scrollbar.grid(row=0, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=log_scrollbar.set)

    def _choose_output_path(self) -> None:
        chosen = filedialog.askdirectory(initialdir=str(ROOT_DIR))
        if chosen:
            self.output_path_var.set(chosen)

    def _append_log(self, message: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", message)
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _drain_log_queue(self) -> None:
        while not self.log_queue.empty():
            self._append_log(self.log_queue.get_nowait())
        self.root.after(100, self._drain_log_queue)

    def _queue_log(self, message: str) -> None:
        if not message.endswith("\n"):
            message += "\n"
        self.log_queue.put(message)

    def _read_config_file(self) -> dict:
        if not CONFIG_PATH.exists():
            return DEFAULT_CONFIG.copy()
        with CONFIG_PATH.open("r", encoding="utf-8") as file:
            data = json.load(file)
        config = DEFAULT_CONFIG.copy()
        config.update(data if isinstance(data, dict) else {})
        if not isinstance(config.get("zhihuUrls"), list):
            config["zhihuUrls"] = []
        return config

    def _load_config_into_form(self) -> None:
        try:
            config = self._read_config_file()
        except Exception as exc:
            messagebox.showerror("读取失败", f"读取配置文件失败：\n{exc}")
            return

        self.output_path_var.set(config.get("outputPath", ""))
        self.os_var.set(config.get("os", ""))
        self.open_collection_var.set(bool(config.get("openCollection", False)))

        for item in self.collection_tree.get_children():
            self.collection_tree.delete(item)

        for collection in config.get("zhihuUrls", []):
            name = collection.get("name", "")
            url = collection.get("url", "")
            self.collection_tree.insert("", "end", values=(name, url))

        self.status_var.set("配置已加载")
        self._queue_log("已从 config.json 读取当前配置。")

    def _collect_form_data(self) -> dict:
        collections: list[dict[str, str]] = []
        for item_id in self.collection_tree.get_children():
            name, url = self.collection_tree.item(item_id, "values")
            name = str(name).strip()
            url = str(url).strip()
            if name or url:
                collections.append({"name": name, "url": url})

        return {
            "zhihuUrls": collections,
            "outputPath": self.output_path_var.get().strip(),
            "os": self.os_var.get().strip(),
            "openCollection": bool(self.open_collection_var.get()),
        }

    def _save_config(self) -> bool:
        config = self._collect_form_data()
        try:
            with CONFIG_PATH.open("w", encoding="utf-8") as file:
                json.dump(config, file, ensure_ascii=False, indent=2)
        except Exception as exc:
            messagebox.showerror("保存失败", f"写入 config.json 失败：\n{exc}")
            return False

        self.status_var.set("配置已保存")
        self._queue_log("配置已保存到 config.json。")
        return True

    def _validate_collection_input(self) -> tuple[str, str] | None:
        name = self.name_var.get().strip()
        url = self.url_var.get().strip()
        if not name:
            messagebox.showwarning("缺少名称", "请输入收藏夹名称。")
            return None
        if not url:
            messagebox.showwarning("缺少 URL", "请输入收藏夹 URL。")
            return None
        return name, url

    def _add_collection(self) -> None:
        payload = self._validate_collection_input()
        if payload is None:
            return
        self.collection_tree.insert("", "end", values=payload)
        self.name_var.set("")
        self.url_var.set("")
        self.status_var.set("已新增收藏夹")

    def _update_selected_collection(self) -> None:
        selection = self.collection_tree.selection()
        if not selection:
            messagebox.showinfo("提示", "请先选中一条收藏夹记录。")
            return
        payload = self._validate_collection_input()
        if payload is None:
            return
        self.collection_tree.item(selection[0], values=payload)
        self.status_var.set("已更新选中收藏夹")

    def _delete_selected_collection(self) -> None:
        selection = self.collection_tree.selection()
        if not selection:
            messagebox.showinfo("提示", "请先选中要删除的收藏夹。")
            return
        for item_id in selection:
            self.collection_tree.delete(item_id)
        self.status_var.set("已删除选中收藏夹")

    def _load_selected_collection(self, _event: object | None = None) -> None:
        selection = self.collection_tree.selection()
        if not selection:
            return
        name, url = self.collection_tree.item(selection[0], "values")
        self.name_var.set(str(name))
        self.url_var.set(str(url))

    def _set_running_state(self, running: bool) -> None:
        state = "disabled" if running else "normal"
        for widget in (
            self.save_button,
            self.reload_button,
            self.fetch_button,
            self.export_button,
            self.os_combo,
        ):
            widget.configure(state=state)

    def _run_script(
        self, script_name: str, on_success: Optional[Callable[[], None]] = None
    ) -> None:
        if self.current_process is not None:
            messagebox.showinfo("任务执行中", "当前已有任务在运行，请等待完成。")
            return

        self._set_running_state(True)
        self.status_var.set(f"正在运行 {script_name}")
        self._queue_log(f"\n>>> 启动 {script_name}\n")

        def worker() -> None:
            process: subprocess.Popen[str] | None = None
            try:
                process = subprocess.Popen(
                    [sys.executable, "-u", script_name],
                    cwd=str(ROOT_DIR),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                )
                self.current_process = process
                assert process.stdout is not None
                for line in process.stdout:
                    self._queue_log(line)
                return_code = process.wait()
                if return_code == 0:
                    self.status_var.set(f"{script_name} 执行完成")
                    self._queue_log(f">>> {script_name} 执行完成\n")
                    if on_success is not None:
                        self.root.after(0, on_success)
                else:
                    self.status_var.set(f"{script_name} 执行失败")
                    self._queue_log(f">>> {script_name} 失败，退出码：{return_code}\n")
            except Exception as exc:
                self.status_var.set(f"{script_name} 启动失败")
                self._queue_log(f">>> 启动失败：{exc}\n")
            finally:
                self.current_process = None
                self.root.after(0, lambda: self._set_running_state(False))

        self.active_worker = threading.Thread(target=worker, daemon=True)
        self.active_worker.start()

    def _fetch_collections(self) -> None:
        if not self._save_config():
            return
        self._run_script("fetch_collections.py", on_success=self._load_config_into_form)

    def _start_export(self) -> None:
        if not self.collection_tree.get_children() and not self.open_collection_var.get():
            messagebox.showwarning("没有可导出的收藏夹", "请先添加收藏夹，或先获取我的收藏夹列表。")
            return
        if not self._save_config():
            return
        self._run_script("main.py")

    def _on_close(self) -> None:
        if self.current_process is not None:
            if not messagebox.askyesno("确认退出", "当前任务仍在运行，确定要关闭界面吗？"):
                return
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    try:
        style = ttk.Style()
        if "vista" in style.theme_names():
            style.theme_use("vista")
    except tk.TclError:
        pass

    ZhihuExporterGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
