import os
import re
import time
import threading
import json
import subprocess
import logging
import platform
import tkinter as tk
from tkinter import simpledialog, messagebox
import sys
import webbrowser
from datetime import datetime
import shutil  # Usado para shutil.which para encontrar systemctl

import ttkbootstrap as ttk
from ttkbootstrap.constants import *
from ttkbootstrap.tooltip import ToolTip
from ttkbootstrap.dialogs import Messagebox
from tkinter import filedialog
from tkinter.scrolledtext import ScrolledText

try:
    import pystray
    from PIL import Image, ImageTk, ImageDraw

    PIL_AVAILABLE = True
    PYSTRAY_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False
    PYSTRAY_AVAILABLE = False
    pystray = None
    logging.warning(
        "Pillow (PIL) ou pystray não encontrados. Funcionalidades de ícone, imagem de fundo e bandeja estarão limitadas/desabilitadas.")

try:
    import win32com.client
    import pythoncom

    PYWIN32_AVAILABLE = True
except ImportError:
    PYWIN32_AVAILABLE = False

# Checagem mais robusta para systemctl no Linux
SYSTEMCTL_AVAILABLE = platform.system() == "Linux" and shutil.which('systemctl') is not None

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - [%(threadName)s] - %(module)s.%(funcName)s:%(lineno)d - %(message)s',
    filename='server_restarter.log',
    filemode='a',
    encoding='utf-8'
)

# --- Constantes para Ícones e Imagens ---
ICON_FILENAME = "predpy.ico"
BACKGROUND_IMAGE_FILENAME = "predpy.png"
BACKGROUND_ALPHA_MULTIPLIER = 0.15


def resource_path(relative_path):
    try:
        base_path = sys._MEIPASS
    except AttributeError:
        base_path = os.path.abspath(os.path.dirname(__file__))
    return os.path.join(base_path, relative_path)


ICON_PATH = resource_path(ICON_FILENAME)
BACKGROUND_IMAGE_PATH = resource_path(BACKGROUND_IMAGE_FILENAME)


# ==============================================================================
# CLASSE ServidorTab
# ==============================================================================
class ServidorTab(ttk.Frame):
    def __init__(self, master_notebook, app_instance, nome_servidor, config_dict=None):
        super().__init__(master_notebook)
        self.app = app_instance
        self.nome = nome_servidor
        self.config_inicial = config_dict if config_dict else {}

        self.pasta_raiz = tk.StringVar(value=self.config_inicial.get("log_folder", ""))
        self.nome_servico = tk.StringVar(value=self.config_inicial.get("service_name", ""))
        self.trigger_log_message_var = tk.StringVar(
            value=self.config_inicial.get("trigger_log_message", "ServerAdminTools | Event serveradmintools_game_ended")
        )
        self.restart_delay_after_trigger_var = tk.IntVar(
            value=self.config_inicial.get("restart_delay_after_trigger", 10)
        )
        self.auto_restart_on_trigger_var = tk.BooleanVar(
            value=self.config_inicial.get("auto_restart_on_trigger", True)
        )

        self.log_folder_path_label_var = tk.StringVar(value="Pasta Logs: Nenhuma")
        self.servico_label_var = tk.StringVar(value="Serviço: Nenhum")

        self.filtro_var = tk.StringVar(value=self.config_inicial.get("filter", ""))
        self.stop_delay_var = tk.IntVar(value=self.config_inicial.get("stop_delay", 10))
        self.start_delay_var = tk.IntVar(value=self.config_inicial.get("start_delay", 30))
        self.auto_scroll_log_var = tk.BooleanVar(value=self.config_inicial.get("auto_scroll_log", True))

        self.log_search_var = tk.StringVar()
        self.last_search_pos = "1.0"
        self.search_log_frame_visible = False

        self.scheduled_restarts_list = list(self.config_inicial.get("scheduled_restarts", []))
        self.predefined_schedule_vars = {}
        self.custom_schedule_entry_var = tk.StringVar()
        self.last_scheduled_restart_processed_time_str = None

        self._stop_event = threading.Event()
        self._scheduler_stop_event = threading.Event()
        self._paused = False
        self.log_monitor_thread = None
        self.log_tail_thread = None
        self.scheduler_thread = None
        self.file_log_handle = None
        self.caminho_log_atual = None
        self.pasta_log_detectada_atual = None

        self._create_ui_for_tab()
        self.initialize_from_config_vars()
        self._update_scheduled_restarts_ui_from_list()

        vars_to_trace = [
            self.pasta_raiz, self.nome_servico, self.filtro_var,
            self.trigger_log_message_var, self.auto_restart_on_trigger_var,
            self.auto_scroll_log_var, self.stop_delay_var, self.start_delay_var,
            self.restart_delay_after_trigger_var
        ]

        for var in vars_to_trace:
            var.trace_add("write", lambda *args, v=var: self._value_changed(v.get()))

        self.start_scheduler_thread()

    def _value_changed(self, new_value=None):
        self.app.mark_config_changed()

    def get_current_config(self):
        return {
            "nome": self.nome,
            "log_folder": self.pasta_raiz.get(),
            "service_name": self.nome_servico.get(),
            "filter": self.filtro_var.get(),
            "auto_restart_on_trigger": self.auto_restart_on_trigger_var.get(),
            "trigger_log_message": self.trigger_log_message_var.get(),
            "restart_delay_after_trigger": self.restart_delay_after_trigger_var.get(),
            "stop_delay": self.stop_delay_var.get(),
            "start_delay": self.start_delay_var.get(),
            "auto_scroll_log": self.auto_scroll_log_var.get(),
            "scheduled_restarts": sorted(list(set(self.scheduled_restarts_list)))
        }

    def _create_ui_for_tab(self):
        outer_top_frame = ttk.Frame(self)
        outer_top_frame.pack(pady=5, padx=5, fill='x')

        selection_labelframe = ttk.Labelframe(outer_top_frame, text="Configuração de Caminhos e Serviço",
                                              padding=(10, 5))
        selection_labelframe.pack(side='top', fill='x', pady=(0, 5))

        path_buttons_frame = ttk.Frame(selection_labelframe)
        path_buttons_frame.pack(fill='x')

        self.selecionar_btn = ttk.Button(path_buttons_frame, text="Pasta de Logs", command=self.selecionar_pasta,
                                         bootstyle=PRIMARY)
        self.selecionar_btn.pack(side='left', pady=2, padx=(0, 2))
        ToolTip(self.selecionar_btn, text="Seleciona a pasta raiz onde os logs do servidor são armazenados.")

        self.servico_btn = ttk.Button(path_buttons_frame, text="Selecionar Serviço", command=self.selecionar_servico,
                                      bootstyle=SECONDARY)
        self.servico_btn.pack(side='left', padx=2, pady=2)
        ToolTip(self.servico_btn, text="Seleciona o serviço associado ao servidor (Windows ou Linux).")

        self.refresh_servico_status_btn = ttk.Button(path_buttons_frame, text="↻",
                                                     command=self.update_service_status_display,
                                                     bootstyle=(TOOLBUTTON, LIGHT), width=2)
        self.refresh_servico_status_btn.pack(side='left', padx=(0, 2), pady=2)
        ToolTip(self.refresh_servico_status_btn, text="Atualizar status do serviço selecionado.")

        path_labels_frame_line1 = ttk.Frame(selection_labelframe)
        path_labels_frame_line1.pack(fill='x', pady=(5, 2))
        self.log_folder_path_label = ttk.Label(path_labels_frame_line1, textvariable=self.log_folder_path_label_var,
                                               wraplength=450, anchor='w')
        self.log_folder_path_label.pack(side='left', padx=5, fill='x', expand=True)

        self.servico_label_widget = ttk.Label(path_labels_frame_line1, textvariable=self.servico_label_var, anchor='w',
                                              width=30)
        self.servico_label_widget.pack(side='left', padx=(5, 0))

        controls_labelframe = ttk.Labelframe(outer_top_frame, text="Controles de Log", padding=(10, 5))
        controls_labelframe.pack(side='top', fill='x', pady=(5, 0))

        log_controls_subframe = ttk.Frame(controls_labelframe)
        log_controls_subframe.pack(fill='x', expand=True)

        ttk.Label(log_controls_subframe, text="Filtro:").pack(side='left', padx=(0, 5))
        self.filtro_entry = ttk.Entry(log_controls_subframe, textvariable=self.filtro_var, width=20)
        self.filtro_entry.pack(side='left', padx=(0, 5))
        ToolTip(self.filtro_entry, text="Filtra as linhas de log exibidas (case-insensitive).")

        self.pausar_btn = ttk.Button(log_controls_subframe, text="⏸️ Pausar", command=self.toggle_pausa,
                                     bootstyle=WARNING)
        self.pausar_btn.pack(side='left', padx=5)
        ToolTip(self.pausar_btn, text="Pausa ou retoma o acompanhamento ao vivo dos logs.")

        self.limpar_btn = ttk.Button(log_controls_subframe, text="♻️ Limpar Log", command=self.limpar_tela_log,
                                     bootstyle=SECONDARY)
        self.limpar_btn.pack(side='left', padx=5)
        ToolTip(self.limpar_btn, text="Limpa a área de exibição de logs do servidor.")

        self.tab_notebook = ttk.Notebook(self)
        self.tab_notebook.pack(fill='both', expand=True, padx=5, pady=(5, 5))

        log_frame = ttk.Frame(self.tab_notebook)
        self.tab_notebook.add(log_frame, text="Logs do Servidor")
        self.log_label_display = ttk.Label(log_frame, text="LOG AO VIVO DO SERVIDOR", foreground="red")
        self.log_label_display.pack(pady=(5, 0))

        self.search_log_frame = ttk.Frame(log_frame)
        ttk.Label(self.search_log_frame, text="Buscar:").pack(side='left', padx=(5, 2))
        self.log_search_entry = ttk.Entry(self.search_log_frame, textvariable=self.log_search_var)
        self.log_search_entry.pack(side='left', fill='x', expand=True, padx=2)
        self.log_search_entry.bind("<Return>", self._search_log_next)
        search_next_btn = ttk.Button(self.search_log_frame, text="Próximo", command=self._search_log_next,
                                     bootstyle=SECONDARY)
        search_next_btn.pack(side='left', padx=2)
        search_prev_btn = ttk.Button(self.search_log_frame, text="Anterior", command=self._search_log_prev,
                                     bootstyle=SECONDARY)
        search_prev_btn.pack(side='left', padx=2)
        close_search_btn = ttk.Button(self.search_log_frame, text="X", command=self._toggle_log_search_bar,
                                      bootstyle=(SECONDARY, DANGER), width=2)
        close_search_btn.pack(side='left', padx=(2, 5))

        self.text_area_log = ScrolledText(log_frame, wrap='word', height=10, state='disabled')
        self.text_area_log.pack(fill='both', expand=True, pady=(0, 5))
        self.text_area_log.bind("<Control-f>", lambda e: self._toggle_log_search_bar(force_show=True))

        self.auto_scroll_check = ttk.Checkbutton(log_frame, text="Rolar Auto.", variable=self.auto_scroll_log_var)
        self.auto_scroll_check.pack(side='left', anchor='sw', pady=2, padx=5)

        options_frame = ttk.Frame(self.tab_notebook)
        self.tab_notebook.add(options_frame, text="Opções de Reinício (Gatilho)")
        options_inner_frame = ttk.Frame(options_frame, padding=15)
        options_inner_frame.pack(fill='both', expand=True)

        self.auto_restart_check = ttk.Checkbutton(options_inner_frame,
                                                  text="Reiniciar servidor automaticamente ao detectar gatilho no log",
                                                  variable=self.auto_restart_on_trigger_var)
        self.auto_restart_check.grid(row=0, column=0, sticky='w', padx=5, pady=5, columnspan=2)
        ToolTip(self.auto_restart_check, "Se marcado, o servidor será reiniciado após o gatilho de log ser detectado.")

        ttk.Label(options_inner_frame, text="Mensagem de Log para Gatilho de Reinício:").grid(row=1, column=0,
                                                                                              sticky='w', padx=5,
                                                                                              pady=(10, 0))
        trigger_message_entry = ttk.Entry(options_inner_frame, textvariable=self.trigger_log_message_var, width=60)
        trigger_message_entry.grid(row=2, column=0, sticky='ew', padx=5, pady=2, columnspan=2)
        ToolTip(trigger_message_entry, "A linha de log (ou parte dela) que acionará o reinício do servidor.")

        ttk.Label(options_inner_frame, text="Delay para Reiniciar após Gatilho (s):").grid(row=3, column=0, sticky='w',
                                                                                           padx=5, pady=(10, 0))
        restart_delay_spinbox = ttk.Spinbox(options_inner_frame, from_=0, to=300,
                                            textvariable=self.restart_delay_after_trigger_var, width=5)
        restart_delay_spinbox.grid(row=4, column=0, sticky='w', padx=5, pady=2)
        ToolTip(restart_delay_spinbox,
                "Tempo (s) para aguardar ANTES de iniciar o processo de reinício, após o gatilho ser detectado.")

        delay_frame = ttk.Frame(options_inner_frame)
        delay_frame.grid(row=5, column=0, columnspan=2, sticky='ew', pady=(20, 0))
        ttk.Label(delay_frame, text="Delay Parar Serviço (s):").pack(side='left', padx=5)
        stop_delay_spinbox = ttk.Spinbox(delay_frame, from_=1, to=60, textvariable=self.stop_delay_var, width=5)
        stop_delay_spinbox.pack(side='left', padx=5)
        ToolTip(stop_delay_spinbox, "Tempo (s) para aguardar após comando de parada do serviço.")

        ttk.Label(delay_frame, text="Delay Iniciar Serviço (s):").pack(side='left', padx=15)
        start_delay_spinbox = ttk.Spinbox(delay_frame, from_=5, to=180, textvariable=self.start_delay_var, width=5)
        start_delay_spinbox.pack(side='left', padx=5)
        ToolTip(start_delay_spinbox, "Tempo (s) para aguardar o serviço iniciar completamente.")
        options_inner_frame.columnconfigure(0, weight=1)

        self.scheduled_restarts_frame = ttk.Frame(self.tab_notebook, padding=10)
        self.tab_notebook.add(self.scheduled_restarts_frame, text="Reinícios Agendados")
        self._create_scheduled_restarts_ui(self.scheduled_restarts_frame)

    def _create_scheduled_restarts_ui(self, parent_frame):
        predefined_lf = ttk.Labelframe(parent_frame, text="Horários Pré-definidos (HH:00)", padding=10)
        predefined_lf.pack(fill="x", pady=5)

        predefined_grid_frame = ttk.Frame(predefined_lf)
        predefined_grid_frame.pack(fill="x")

        cols = 6
        for i in range(24):
            hour_str = f"{i:02d}:00"
            var = tk.BooleanVar(value=(hour_str in self.scheduled_restarts_list))
            cb = ttk.Checkbutton(predefined_grid_frame, text=hour_str, variable=var,
                                 command=lambda h=i, v=var: self._toggle_predefined_schedule(h, v))
            cb.grid(row=i // cols, column=i % cols, padx=5, pady=2, sticky="w")
            self.predefined_schedule_vars[hour_str] = var

        custom_lf = ttk.Labelframe(parent_frame, text="Horários Personalizados (HH:MM)", padding=10)
        custom_lf.pack(fill="both", expand=True, pady=5)

        custom_add_frame = ttk.Frame(custom_lf)
        custom_add_frame.pack(fill="x", pady=(0, 5))
        ttk.Label(custom_add_frame, text="Novo (HH:MM):").pack(side="left", padx=(0, 5))
        custom_entry = ttk.Entry(custom_add_frame, textvariable=self.custom_schedule_entry_var, width=10)
        custom_entry.pack(side="left", padx=5)
        ToolTip(custom_entry, "Digite o horário no formato HH:MM (ex: 08:30, 22:15)")
        add_btn = ttk.Button(custom_add_frame, text="+ Adicionar", command=self._add_custom_schedule, bootstyle=SUCCESS)
        add_btn.pack(side="left", padx=5)

        custom_list_remove_frame = ttk.Frame(custom_lf)
        custom_list_remove_frame.pack(fill="both", expand=True)

        self.custom_schedules_listbox = tk.Listbox(custom_list_remove_frame, selectmode=tk.SINGLE, height=6)
        self.custom_schedules_listbox.pack(side="left", fill="both", expand=True, padx=(0, 5))
        custom_scroll = ttk.Scrollbar(custom_list_remove_frame, orient="vertical",
                                      command=self.custom_schedules_listbox.yview)
        custom_scroll.pack(side="left", fill="y")
        self.custom_schedules_listbox.config(yscrollcommand=custom_scroll.set)

        remove_btn = ttk.Button(custom_list_remove_frame, text="- Remover Selecionado",
                                command=self._remove_selected_custom_schedule, bootstyle=DANGER)
        remove_btn.pack(side="left", padx=(5, 0), anchor="n")
        ToolTip(remove_btn, "Remove o horário personalizado selecionado na lista.")

    def _update_scheduled_restarts_ui_from_list(self):
        if not hasattr(self, 'predefined_schedule_vars') or not hasattr(self, 'custom_schedules_listbox'):
            return

        for hour_str, var in self.predefined_schedule_vars.items():
            if var.get() != (hour_str in self.scheduled_restarts_list):
                var.set(hour_str in self.scheduled_restarts_list)

        if self.custom_schedules_listbox.winfo_exists():
            self.custom_schedules_listbox.delete(0, tk.END)
            all_times = set(self.scheduled_restarts_list)
            predefined_as_set = {f"{h:02d}:00" for h in range(24)}
            actually_custom_times = sorted(list(all_times - predefined_as_set))
            for time_str in actually_custom_times:
                self.custom_schedules_listbox.insert(tk.END, time_str)

    def _toggle_predefined_schedule(self, hour_int, var):
        hour_str = f"{hour_int:02d}:00"
        if var.get():
            if hour_str not in self.scheduled_restarts_list:
                self.scheduled_restarts_list.append(hour_str)
        else:
            if hour_str in self.scheduled_restarts_list:
                self.scheduled_restarts_list.remove(hour_str)
        self.scheduled_restarts_list = sorted(list(set(self.scheduled_restarts_list)))
        self._value_changed()

    def _add_custom_schedule(self):
        time_str = self.custom_schedule_entry_var.get().strip()
        if not re.fullmatch(r"([01]\d|2[0-3]):([0-5]\d)", time_str):
            self.app.show_messagebox_from_thread("error", "Formato Inválido",
                                                 f"Horário '{time_str}' inválido. Use o formato HH:MM.")
            return
        if time_str in self.scheduled_restarts_list:
            self.app.show_messagebox_from_thread("info", "Horário Duplicado",
                                                 f"O horário '{time_str}' já está na lista.")
            return
        self.scheduled_restarts_list.append(time_str)
        self.scheduled_restarts_list = sorted(list(set(self.scheduled_restarts_list)))
        self._update_scheduled_restarts_ui_from_list()
        self.custom_schedule_entry_var.set("")
        self._value_changed()

    def _remove_selected_custom_schedule(self):
        selection_indices = self.custom_schedules_listbox.curselection()
        if not selection_indices:
            self.app.show_messagebox_from_thread("warning", "Nenhuma Seleção", "Selecione um horário para remover.")
            return
        selected_time_str = self.custom_schedules_listbox.get(selection_indices[0])
        if selected_time_str in self.scheduled_restarts_list:
            self.scheduled_restarts_list.remove(selected_time_str)
            self._update_scheduled_restarts_ui_from_list()
            self._value_changed()

    def start_scheduler_thread(self):
        if self.scheduler_thread and self.scheduler_thread.is_alive():
            return
        self._scheduler_stop_event.clear()
        self.scheduler_thread = threading.Thread(target=self._scheduler_worker, daemon=True,
                                                 name=f"Scheduler-{self.nome}")
        self.scheduler_thread.start()
        logging.info(f"Tab '{self.nome}': Scheduler de reinícios agendados iniciado.")

    def stop_scheduler_thread(self, from_tab_closure=False):
        self._scheduler_stop_event.set()
        if self.scheduler_thread and self.scheduler_thread.is_alive() and self.scheduler_thread != threading.current_thread():
            self.scheduler_thread.join(timeout=2.0)
        self.scheduler_thread = None

    def _scheduler_worker(self):
        while not self._scheduler_stop_event.is_set():
            try:
                current_time_str_hh_mm = datetime.now().strftime("%H:%M")
                if self.last_scheduled_restart_processed_time_str != current_time_str_hh_mm:
                    self.last_scheduled_restart_processed_time_str = None

                service_to_restart = self.nome_servico.get()
                if not service_to_restart or not self.scheduled_restarts_list:
                    if self._scheduler_stop_event.wait(20): break
                    continue

                if (current_time_str_hh_mm in self.scheduled_restarts_list and
                        self.last_scheduled_restart_processed_time_str != current_time_str_hh_mm):
                    logging.info(
                        f"Tab '{self.nome}': Disparando reinício agendado para '{service_to_restart}' às {current_time_str_hh_mm}.")
                    self.append_text_to_log_area_threadsafe(
                        f"--- REINÍCIO AGENDADO ({current_time_str_hh_mm}) INICIADO ---\n")
                    threading.Thread(
                        target=self._executar_logica_reinicio_servico_efetivamente,
                        args=(True,), daemon=True, name=f"ScheduledRestartExec-{self.nome}"
                    ).start()
                    self.last_scheduled_restart_processed_time_str = current_time_str_hh_mm
            except Exception as e_scheduler:
                logging.error(f"Tab '{self.nome}': Erro no _scheduler_worker: {e_scheduler}", exc_info=True)

            if self._scheduler_stop_event.wait(15):
                break
        logging.info(f"Tab '{self.nome}': Thread _scheduler_worker encerrada.")

    def initialize_from_config_vars(self):
        default_fg = "black"
        try:
            if hasattr(self.app.style, 'colors') and hasattr(self.app.style.colors, 'fg'):
                default_fg = self.app.style.colors.fg if self.app.style.colors.fg else "black"
        except Exception:
            pass

        pasta_raiz_val = self.pasta_raiz.get()
        if pasta_raiz_val and os.path.isdir(pasta_raiz_val):
            self.log_folder_path_label_var.set(f"Pasta Logs: {os.path.basename(pasta_raiz_val)}")
            self.log_folder_path_label.config(foreground="green")
            self.start_log_monitoring()
        elif pasta_raiz_val:
            self.log_folder_path_label_var.set(f"Pasta Logs (INVÁLIDA): {os.path.basename(pasta_raiz_val)}")
            self.log_folder_path_label.config(foreground="red")
        else:
            self.log_folder_path_label_var.set("Pasta Logs: Nenhuma")
            self.log_folder_path_label.config(foreground=default_fg)

        os_system = platform.system()
        can_manage_services = (os_system == "Windows" and PYWIN32_AVAILABLE) or (
                os_system == "Linux" and SYSTEMCTL_AVAILABLE)

        if self.servico_btn.winfo_exists():
            self.servico_btn.config(state=NORMAL if can_manage_services else DISABLED)

        nome_servico_val = self.nome_servico.get()
        can_refresh_status = bool(nome_servico_val) and (
                (os_system == "Windows" and PYWIN32_AVAILABLE) or  # Corrigido para PYWIN32_AVAILABLE aqui
                (os_system == "Linux" and SYSTEMCTL_AVAILABLE))

        if self.refresh_servico_status_btn.winfo_exists():
            self.refresh_servico_status_btn.config(state=NORMAL if can_refresh_status else DISABLED)

        if nome_servico_val:
            self.update_service_status_display()
        else:
            service_unavailable_reason = ""
            if not can_manage_services:
                if os_system == "Windows":
                    service_unavailable_reason = "N/A (pywin32)"
                elif os_system == "Linux":
                    service_unavailable_reason = "N/A (systemctl)"
                else:
                    service_unavailable_reason = f"N/A (SO {os_system})"

            if service_unavailable_reason:
                self.servico_label_var.set(f"Serviço: {service_unavailable_reason}")
                self.servico_label_widget.config(foreground="gray")
            else:
                self.servico_label_var.set("Serviço: Nenhum")
                self.servico_label_widget.config(foreground="orange")

        self._update_scheduled_restarts_ui_from_list()
        self.start_scheduler_thread()

    def selecionar_pasta(self):
        pasta_selecionada = filedialog.askdirectory(title=f"Selecione a pasta de logs para '{self.nome}'")
        if pasta_selecionada and self.pasta_raiz.get() != pasta_selecionada:
            self.stop_log_monitoring()
            self.pasta_raiz.set(pasta_selecionada)
            self.initialize_from_config_vars()

    def selecionar_servico(self):
        os_system = platform.system()
        if os_system == "Windows":
            if not PYWIN32_AVAILABLE:
                self.app.show_messagebox_from_thread("error", "Funcionalidade Indisponível",
                                                     "A biblioteca pywin32 é necessária.")
                return
            self.app.iniciar_selecao_servico_para_aba(self, "windows")
        elif os_system == "Linux":
            if not SYSTEMCTL_AVAILABLE:
                self.app.show_messagebox_from_thread("error", "Funcionalidade Indisponível",
                                                     "O comando 'systemctl' é necessário.")
                return
            self.app.iniciar_selecao_servico_para_aba(self, "linux")
        else:
            self.app.show_messagebox_from_thread("warning", "Não Suportado",
                                                 f"Gerenciamento de serviços não suportado em {os_system}.")

    def set_selected_service(self, service_name):
        if self.nome_servico.get() != service_name:
            self.nome_servico.set(service_name)
            self.update_service_status_display()
            self.app.set_status_from_thread(f"Serviço '{service_name}' selecionado para '{self.nome}'.")
            logging.info(f"Tab '{self.nome}': Serviço selecionado: {service_name}")

    def update_service_status_display(self):
        nome_servico_val = self.nome_servico.get()
        if not nome_servico_val:
            self.initialize_from_config_vars()  # Re-avalia o estado dos botões, etc.
            return

        os_system = platform.system()
        current_text_base = f"Serviço: {nome_servico_val}"
        worker = None

        if os_system == "Windows":
            if not PYWIN32_AVAILABLE:  # Checagem adicional
                self.initialize_from_config_vars()
                return
            worker = self._get_and_display_service_status_win_thread_worker
        elif os_system == "Linux":
            if not SYSTEMCTL_AVAILABLE:
                self.initialize_from_config_vars()
                return
            worker = self._get_and_display_service_status_linux_thread_worker
        else:
            self.initialize_from_config_vars()
            return

        self.servico_label_var.set(f"{current_text_base} (Verificando...)")
        self.servico_label_widget.config(foreground="blue")
        threading.Thread(
            target=worker,
            args=(nome_servico_val, current_text_base),
            daemon=True,
            name=f"ServiceStatusCheck-{self.nome}"
        ).start()

    def _get_and_display_service_status_win_thread_worker(self, service_name, base_text):
        status = self._verificar_status_servico_win(service_name)
        status_map = {
            "RUNNING": ("(Rodando)", "green"), "STOPPED": ("(Parado)", "red"),
            "START_PENDING": ("(Iniciando...)", "blue"), "STOP_PENDING": ("(Parando...)", "blue"),
            "NOT_FOUND": ("(Não encontrado!)", "orange"), "ERROR": ("(Erro ao verificar!)", "red"),
            "UNKNOWN": ("(Desconhecido)", "gray")
        }
        display_text, color = status_map.get(status, ("(Status ?)", "gray"))
        if self.app.root.winfo_exists() and self.winfo_exists():
            self.app.root.after(0, lambda: (
                self.servico_label_var.set(f"{base_text} {display_text}"),
                self.servico_label_widget.config(foreground=color)
            ))

    def _get_and_display_service_status_linux_thread_worker(self, service_name, base_text):
        status = self._verificar_status_servico_linux(service_name)
        status_map = {
            "RUNNING": ("(Rodando)", "green"), "STOPPED": ("(Parado)", "red"),
            "START_PENDING": ("(Iniciando...)", "blue"), "STOP_PENDING": ("(Parando...)", "blue"),
            "NOT_FOUND": ("(Não encontrado!)", "orange"), "ERROR": ("(Erro ao verificar!)", "red"),
            "SYSTEMCTL_NOT_FOUND": ("(systemctl N/A)", "gray"), "UNKNOWN": ("(Desconhecido)", "gray")
        }
        display_text, color = status_map.get(status, ("(Status ?)", "gray"))
        if self.app.root.winfo_exists() and self.winfo_exists():
            self.app.root.after(0, lambda: (
                self.servico_label_var.set(f"{base_text} {display_text}"),
                self.servico_label_widget.config(foreground=color)
            ))

    def _verificar_status_servico_win(self, nome_servico_local):
        if not PYWIN32_AVAILABLE: return "ERROR"
        if not nome_servico_local: return "NOT_FOUND"
        try:
            startupinfo = None
            if platform.system() == "Windows":
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                startupinfo.wShowWindow = subprocess.SW_HIDE
            encodings_to_try = ['latin-1', 'utf-8', 'cp850', 'cp1252']
            output_text = None
            for enc in encodings_to_try:
                try:
                    result = subprocess.run(
                        ['sc', 'query', nome_servico_local],
                        capture_output=True, text=False, check=False, startupinfo=startupinfo
                    )
                    stdout_decoded = result.stdout.decode(enc, errors='replace')
                    stderr_decoded = result.stderr.decode(enc, errors='replace')
                    output_text = stdout_decoded + stderr_decoded
                    break
                except UnicodeDecodeError:
                    logging.debug(f"Tab '{self.nome}': Falha decode 'sc query' com {enc} para '{nome_servico_local}'.")
                except Exception as e_run:
                    logging.error(f"Tab '{self.nome}': Erro 'sc query' para '{nome_servico_local}': {e_run}",
                                  exc_info=True)
                    return "ERROR"
            if output_text is None:
                logging.error(f"Tab '{self.nome}': Impossível decodificar 'sc query' para '{nome_servico_local}'.")
                return "ERROR"
            output_lower = output_text.lower()
            service_not_found_errors = [
                "failed 1060", "falha 1060", "o servi‡o especificado nÆo existe como servi‡o instalado",
                "specified service does not exist as an installed service"
            ]
            if any(err_str in output_lower for err_str in service_not_found_errors):
                logging.warning(
                    f"Tab '{self.nome}': Serviço '{nome_servico_local}' não encontrado. Output: {output_text[:100]}")
                return "NOT_FOUND"
            if "state" not in output_lower:
                logging.warning(
                    f"Tab '{self.nome}': Saída 'sc query {nome_servico_local}' inesperada: {output_text[:100]}")
                return "ERROR"
            if "running" in output_lower or "em execu‡Æo" in output_lower: return "RUNNING"
            if "stopped" in output_lower or "parado" in output_lower: return "STOPPED"
            if "start_pending" in output_lower or "pendente deinÝcio" in output_lower: return "START_PENDING"
            if "stop_pending" in output_lower or "pendente deparada" in output_lower: return "STOP_PENDING"
            logging.info(f"Tab '{self.nome}': Status desconhecido para '{nome_servico_local}': {output_text[:100]}")
            return "UNKNOWN"
        except FileNotFoundError:
            logging.error(f"Tab '{self.nome}': 'sc.exe' não encontrado.", exc_info=True)
            return "ERROR"
        except Exception as e:
            logging.error(f"Tab '{self.nome}': Erro ao verificar status do serviço '{nome_servico_local}': {e}",
                          exc_info=True)
            return "ERROR"

    def _verificar_status_servico_linux(self, nome_servico_local):
        if not SYSTEMCTL_AVAILABLE: return "SYSTEMCTL_NOT_FOUND"
        if not nome_servico_local: return "NOT_FOUND"

        nomes_a_tentar = [nome_servico_local]
        if not nome_servico_local.endswith(".service"):
            nomes_a_tentar.append(f"{nome_servico_local}.service")

        for nome_tentativa in nomes_a_tentar:
            try:
                # Usar `sudo` aqui pode não ser ideal se o script inteiro já roda como root.
                # Se o script já é root, `sudo` é redundante e pode até falhar se `sudo` não estiver configurado para root.
                # No entanto, se o script NÃO roda como root, `sudo` é necessário.
                # Para consistência com _operar_servico_com_delays_linux, vamos manter o sudo por enquanto,
                # assumindo que o script pode não estar rodando como root ou que o sudo é inofensivo se já for root.
                cmd = ['sudo', 'systemctl', 'is-active', nome_tentativa]
                if os.geteuid() == 0:  # Se já é root, não precisa de sudo
                    cmd = ['systemctl', 'is-active', nome_tentativa]

                result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
                status = result.stdout.strip()

                if result.returncode == 0:  # Comando bem sucedido, status é a palavra chave
                    if status == "active": return "RUNNING"
                    if status == "inactive": return "STOPPED"
                    if status == "activating": return "START_PENDING"
                    if status == "deactivating": return "STOP_PENDING"
                    # Outros status como "failed" serão tratados abaixo se returncode não for 0
                elif result.returncode == 3:  # Serviço inativo/parado
                    return "STOPPED"
                elif result.returncode == 4:  # Unidade não encontrada
                    # Continua para a próxima tentativa de nome (se houver)
                    continue
                else:  # Outro erro
                    logging.warning(
                        f"Tab '{self.nome}': 'systemctl is-active {nome_tentativa}' retornou código {result.returncode}. Output: {status}. Stderr: {result.stderr.strip()}")
                    # Tenta 'systemctl status' para mais detalhes em caso de 'failed'
                    if status == "failed": return "ERROR"  # Se is-active reporta failed

                    status_cmd = ['sudo', 'systemctl', 'status', nome_tentativa]
                    if os.geteuid() == 0:
                        status_cmd = ['systemctl', 'status', nome_tentativa]

                    status_result = subprocess.run(status_cmd, capture_output=True, text=True, timeout=5)
                    if "Active: failed" in status_result.stdout:
                        return "ERROR"
                    if "Unit " in status_result.stdout and " could not be found." in status_result.stdout:
                        continue  # Unidade não encontrada, tenta próximo nome
                    # Se não for um erro claro de 'não encontrado' ou 'failed', retorna UNKNOWN
                    return "UNKNOWN"

                return "UNKNOWN"  # Se o status não for reconhecido

            except subprocess.TimeoutExpired:
                logging.error(f"Tab '{self.nome}': Timeout ao verificar serviço '{nome_tentativa}' no Linux.",
                              exc_info=True)
                return "ERROR"
            except FileNotFoundError:  # systemctl ou sudo não encontrado
                logging.error(f"Tab '{self.nome}': Comando 'systemctl' ou 'sudo' não encontrado para verificar status.",
                              exc_info=True)
                return "SYSTEMCTL_NOT_FOUND"  # Indica que a ferramenta base está faltando
            except Exception as e:
                logging.error(f"Tab '{self.nome}': Erro ao verificar serviço '{nome_tentativa}' no Linux: {e}",
                              exc_info=True)
                return "ERROR"
        return "NOT_FOUND"  # Se nenhum nome tentado foi encontrado

    def start_log_monitoring(self):
        if self.log_monitor_thread and self.log_monitor_thread.is_alive():
            return
        if not self.pasta_raiz.get() or not os.path.isdir(self.pasta_raiz.get()):
            self.append_text_to_log_area(
                f"AVISO: Pasta de logs '{self.pasta_raiz.get()}' inválida. Monitoramento não iniciado.\n")
            return
        self._stop_event.clear()
        self.log_monitor_thread = threading.Thread(target=self.monitorar_log_continuamente_worker, daemon=True,
                                                   name=f"LogMonitor-{self.nome}")
        self.log_monitor_thread.start()
        logging.info(f"Tab '{self.nome}': Monitoramento de logs iniciado para pasta '{self.pasta_raiz.get()}'.")

    def stop_log_monitoring(self, from_tab_closure=False):
        self._stop_event.set()
        if self.log_tail_thread and self.log_tail_thread.is_alive():
            self.log_tail_thread.join(timeout=1.0)  # Reduzido timeout para desligamento mais rápido
        self.log_tail_thread = None
        if self.log_monitor_thread and self.log_monitor_thread.is_alive() and self.log_monitor_thread != threading.current_thread():
            self.log_monitor_thread.join(timeout=1.0)  # Reduzido timeout
        self.log_monitor_thread = None
        if self.file_log_handle:
            try:
                self.file_log_handle.close()
            except Exception:
                pass
        self.file_log_handle = None
        self.caminho_log_atual = None  # Limpa o caminho do log atual

    def monitorar_log_continuamente_worker(self):
        pasta_raiz_monitorada = self.pasta_raiz.get()
        logging.info(f"Tab '{self.nome}': Iniciando worker de monitoramento para '{pasta_raiz_monitorada}'")
        while not self._stop_event.is_set():
            if not pasta_raiz_monitorada or not os.path.isdir(pasta_raiz_monitorada):
                logging.warning(
                    f"Tab '{self.nome}': Pasta de logs '{pasta_raiz_monitorada}' inválida ou inacessível no loop.")
                if self._stop_event.wait(10): break
                pasta_raiz_monitorada = self.pasta_raiz.get()  # Tenta reobter caso tenha mudado
                continue

            subpasta_recente = self._obter_subpasta_log_mais_recente(pasta_raiz_monitorada)
            novo_arquivo_log = None
            if subpasta_recente:
                novo_arquivo_log = os.path.join(subpasta_recente, 'console.log')

            if novo_arquivo_log and os.path.exists(novo_arquivo_log) and novo_arquivo_log != self.caminho_log_atual:
                logging.info(f"Tab '{self.nome}': Novo arquivo de log detectado: {novo_arquivo_log}")

                # Para a thread de tail existente, se houver
                if self.log_tail_thread and self.log_tail_thread.is_alive():
                    # O _stop_event global já deve parar a thread de tail, mas vamos ser explícitos.
                    # Não precisamos de um evento separado para a tail thread se o _stop_event da aba é suficiente.
                    # Se self._stop_event.set() for chamado, a tail thread atual deve parar.
                    # Apenas esperamos que ela termine.
                    logging.debug(f"Tab '{self.nome}': Aguardando thread de tail anterior finalizar.")
                    self.log_tail_thread.join(timeout=1.0)
                    # self._stop_event.clear() # Não limpar aqui, pois o evento é da aba, não só da tail

                if self.file_log_handle:
                    try:
                        self.file_log_handle.close()
                    except Exception:
                        pass
                    self.file_log_handle = None

                self.caminho_log_atual = novo_arquivo_log
                self.append_text_to_log_area(f"\n>>> Monitorando novo log: {self.caminho_log_atual}\n")
                try:
                    self.file_log_handle = open(self.caminho_log_atual, 'r', encoding='latin-1', errors='replace')
                    self.file_log_handle.seek(0, os.SEEK_END)  # Vai para o fim do arquivo

                    # Cria e inicia nova thread de tail
                    self.log_tail_thread = threading.Thread(target=self.acompanhar_log_do_arquivo_worker,
                                                            daemon=True,
                                                            name=f"LogTail-{self.nome}-{os.path.basename(self.caminho_log_atual)}")
                    self.log_tail_thread.start()
                    logging.info(f"Tab '{self.nome}': Nova thread de tail iniciada para {self.caminho_log_atual}")
                except FileNotFoundError:
                    logging.error(
                        f"Tab '{self.nome}': Arquivo {self.caminho_log_atual} não encontrado ao tentar abrir para tail.")
                    self.caminho_log_atual = None  # Reseta se não conseguiu abrir
                except Exception as e:
                    logging.error(f"Tab '{self.nome}': Erro ao iniciar tail para {self.caminho_log_atual}: {e}",
                                  exc_info=True)
                    self.caminho_log_atual = None  # Reseta
            elif self.caminho_log_atual and not os.path.exists(self.caminho_log_atual):
                logging.warning(
                    f"Tab '{self.nome}': Arquivo de log monitorado {self.caminho_log_atual} não existe mais.")
                self.append_text_to_log_area(
                    f"AVISO: Log {self.caminho_log_atual} não encontrado. Procurando novo log...\n")
                self.caminho_log_atual = None  # Força a busca por um novo log na próxima iteração
                if self.log_tail_thread and self.log_tail_thread.is_alive():
                    self.log_tail_thread.join(timeout=1.0)
                if self.file_log_handle:
                    try:
                        self.file_log_handle.close()
                    except:
                        pass
                    self.file_log_handle = None

            if self._stop_event.wait(5): break  # Espera 5 segundos ou até o evento de parada
        logging.info(f"Tab '{self.nome}': Worker de monitoramento de log encerrado.")

    def _obter_subpasta_log_mais_recente(self, pasta_raiz_logs):
        if not pasta_raiz_logs or not os.path.isdir(pasta_raiz_logs): return None
        try:
            entradas = os.listdir(pasta_raiz_logs)
            log_folder_pattern = re.compile(r"^logs_\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}$")
            subpastas_log_validas = [os.path.join(pasta_raiz_logs, nome) for nome in entradas if
                                     os.path.isdir(os.path.join(pasta_raiz_logs, nome)) and log_folder_pattern.match(
                                         nome)]
            if not subpastas_log_validas: return None
            return max(subpastas_log_validas, key=os.path.getmtime)
        except Exception as e:
            logging.error(f"Tab '{self.nome}': Erro ao obter subpasta em '{pasta_raiz_logs}': {e}", exc_info=True)
            return None

    def acompanhar_log_do_arquivo_worker(self):
        trigger_message_to_find = self.trigger_log_message_var.get()
        current_file_path = self.caminho_log_atual  # Captura o caminho no início da thread
        logging.info(f"Tab '{self.nome}': Iniciando acompanhamento de log para {current_file_path}")

        if not self.file_log_handle or self.file_log_handle.closed:
            logging.error(
                f"Tab '{self.nome}': file_log_handle nulo ou fechado no início de acompanhar_log para {current_file_path}.")
            return

        while not self._stop_event.is_set():
            if self._paused:
                if self._stop_event.wait(0.5): break
                continue

            # Verifica se o arquivo monitorado ainda é o mesmo ou se o handle foi fechado
            if not self.file_log_handle or self.file_log_handle.closed or self.caminho_log_atual != current_file_path:
                logging.info(
                    f"Tab '{self.nome}': Encerrando thread de tail para {current_file_path} devido a mudança de arquivo ou handle fechado.")
                break
            try:
                linha = self.file_log_handle.readline()
                if linha:
                    linha_strip = linha.strip()
                    if not self.filtro_var.get() or self.filtro_var.get().lower() in linha.lower():
                        self.append_text_to_log_area(linha)

                    if trigger_message_to_find and trigger_message_to_find in linha_strip:
                        logging.info(
                            f"Tab '{self.nome}': GATILHO DE REINÍCIO detectado em '{current_file_path}'. Linha: '{linha_strip}'.")
                        if self.auto_restart_on_trigger_var.get():
                            threading.Thread(target=self._delayed_restart_worker, daemon=True,
                                             name=f"DelayedRestart-{self.nome}").start()
                else:  # Fim do arquivo, espera um pouco
                    if self._stop_event.wait(0.2): break
            except ValueError as ve:  # Ex: I/O operation on closed file
                if "closed file" in str(ve).lower():
                    logging.warning(
                        f"Tab '{self.nome}': Tentativa de I/O em arquivo fechado ({current_file_path}). Encerrando tail.")
                else:
                    logging.error(f"Tab '{self.nome}': ValueError ao acompanhar log {current_file_path}: {ve}",
                                  exc_info=True)
                break  # Sai do loop se o arquivo estiver fechado ou outro ValueError
            except Exception as e:
                if not self._stop_event.is_set():  # Só loga se não for um encerramento esperado
                    logging.error(f"Tab '{self.nome}': Erro inesperado ao acompanhar log {current_file_path}: {e}",
                                  exc_info=True)
                break
        logging.info(f"Tab '{self.nome}': Acompanhamento de log para {current_file_path} encerrado.")

    def _delayed_restart_worker(self):
        delay_s = self.restart_delay_after_trigger_var.get()
        self.append_text_to_log_area_threadsafe(f"Gatilho detectado. Aguardando {delay_s}s para reiniciar...\n")

        start_time = time.monotonic()
        while time.monotonic() - start_time < delay_s:
            if self._stop_event.is_set() or self._scheduler_stop_event.is_set():  # Checa ambos
                logging.info(f"Tab '{self.nome}': Reinício atrasado cancelado.")
                return
            time.sleep(0.5)  # Permite que o evento seja checado mais frequentemente

        if not self._stop_event.is_set() and not self._scheduler_stop_event.is_set():
            self._executar_logica_reinicio_servico_efetivamente(is_scheduled_restart=False)
        else:
            logging.info(f"Tab '{self.nome}': Reinício atrasado cancelado antes da execução.")

    def _executar_logica_reinicio_servico_efetivamente(self, is_scheduled_restart=False):
        tipo_reinicio_msg = "agendado" if is_scheduled_restart else "por gatilho de log"
        nome_servico = self.nome_servico.get()
        if not nome_servico:
            self.append_text_to_log_area_threadsafe(
                f"ERRO: Nome do serviço não configurado para reinício ({tipo_reinicio_msg}).\n")
            logging.error(f"Tab '{self.nome}': Tentativa de reinício ({tipo_reinicio_msg}) sem nome de serviço.")
            return

        self.append_text_to_log_area_threadsafe(
            f"--- REINÍCIO {tipo_reinicio_msg.upper()} DO SERVIÇO '{nome_servico}' INICIADO ---\n")
        success = self._operar_servico_com_delays(nome_servico, tipo_reinicio_msg)

        if self.app.root.winfo_exists():  # Só mostra messagebox se a UI ainda existe
            if success:
                self.app.show_messagebox_from_thread("info", f"'{self.nome}': Servidor Reiniciado",
                                                     f"O serviço '{nome_servico}' foi reiniciado com sucesso ({tipo_reinicio_msg}).")
                self.append_text_to_log_area_threadsafe(
                    f"SUCESSO: Serviço '{nome_servico}' reiniciado ({tipo_reinicio_msg}).\n")
            else:
                self.app.show_messagebox_from_thread("error", f"'{self.nome}': Falha no Reinício",
                                                     f"Ocorreu um erro ao reiniciar ({tipo_reinicio_msg}) o serviço '{nome_servico}'.\nVerifique os logs.")
                self.append_text_to_log_area_threadsafe(
                    f"FALHA: Erro ao reiniciar '{nome_servico}' ({tipo_reinicio_msg}). Verifique os logs.\n")

            if self.winfo_exists():  # Atualiza o status na aba
                self.update_service_status_display()

    def _operar_servico_com_delays(self, nome_servico_a_gerenciar, tipo_reinicio_msg_log=""):
        os_system = platform.system()
        if os_system == "Windows":
            if not PYWIN32_AVAILABLE:
                self.append_text_to_log_area_threadsafe(
                    f"ERRO: pywin32 não disponível para operar serviço no Windows.\n")
                return False
            return self._operar_servico_com_delays_windows(nome_servico_a_gerenciar, tipo_reinicio_msg_log)
        elif os_system == "Linux":
            if not SYSTEMCTL_AVAILABLE:
                self.append_text_to_log_area_threadsafe(
                    f"ERRO: systemctl não disponível para operar serviço no Linux.\n")
                return False
            return self._operar_servico_com_delays_linux(nome_servico_a_gerenciar, tipo_reinicio_msg_log)
        else:
            self.append_text_to_log_area_threadsafe(f"ERRO: Operação de serviço não suportada no SO {os_system}.\n")
            return False

    def _operar_servico_com_delays_windows(self, nome_servico, tipo_reinicio=""):
        stop_delay_s = self.stop_delay_var.get()
        start_delay_s = self.start_delay_var.get()
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE
        log_prefix = f"Tab '{self.nome}' ({tipo_reinicio.strip()}) Win:"

        try:
            # Parar o serviço
            status_atual = self._verificar_status_servico_win(nome_servico)
            if status_atual == "RUNNING" or status_atual == "START_PENDING":
                self.append_text_to_log_area_threadsafe(f"Parando serviço '{nome_servico}'...\n")
                subprocess.run(["sc", "stop", nome_servico], check=True, startupinfo=startupinfo, timeout=30)
                self.append_text_to_log_area_threadsafe(f"Comando de parada enviado. Aguardando {stop_delay_s}s...\n")

                wait_start = time.monotonic()
                while time.monotonic() - wait_start < stop_delay_s:
                    if self._stop_event.is_set() or self._scheduler_stop_event.is_set():
                        logging.info(f"{log_prefix} Operação de serviço interrompida durante delay de parada.")
                        return False
                    time.sleep(0.1)

                status_apos_parada = self._verificar_status_servico_win(nome_servico)
                if status_apos_parada != "STOPPED":
                    logging.warning(
                        f"{log_prefix} Serviço {nome_servico} não parou como esperado. Status: {status_apos_parada}")
                    self.append_text_to_log_area_threadsafe(
                        f"AVISO: Serviço '{nome_servico}' pode não ter parado. Status: {status_apos_parada}\n")
            elif status_atual == "STOPPED":
                self.append_text_to_log_area_threadsafe(f"Serviço '{nome_servico}' já estava parado.\n")
            elif status_atual == "NOT_FOUND":
                self.append_text_to_log_area_threadsafe(f"ERRO: Serviço '{nome_servico}' não encontrado para parada.\n")
                return False
            else:
                self.append_text_to_log_area_threadsafe(
                    f"ERRO: Estado do serviço '{nome_servico}' desconhecido ou erro ({status_atual}). Impossível prosseguir com parada segura.\n")
                return False

            # Iniciar o serviço
            self.append_text_to_log_area_threadsafe(f"Iniciando serviço '{nome_servico}'...\n")
            subprocess.run(["sc", "start", nome_servico], check=True, startupinfo=startupinfo, timeout=30)
            self.append_text_to_log_area_threadsafe(
                f"Comando de início enviado. Aguardando {start_delay_s}s para estabilizar...\n")

            wait_start = time.monotonic()
            while time.monotonic() - wait_start < start_delay_s:
                if self._stop_event.is_set() or self._scheduler_stop_event.is_set():
                    logging.info(f"{log_prefix} Operação de serviço interrompida durante delay de início.")
                    return False
                time.sleep(0.1)

            status_final = self._verificar_status_servico_win(nome_servico)
            if status_final == "RUNNING":
                logging.info(f"{log_prefix} Serviço {nome_servico} iniciado com sucesso.")
                return True
            else:
                logging.error(f"{log_prefix} Serviço {nome_servico} falhou ao iniciar. Status: {status_final}")
                self.append_text_to_log_area_threadsafe(
                    f"ERRO: Serviço '{nome_servico}' falhou ao iniciar. Status: {status_final}\n")
                return False

        except subprocess.CalledProcessError as e_sc:
            err_output = "N/A"
            if e_sc.stderr:
                try:
                    err_output = e_sc.stderr.decode('latin-1', errors='replace')
                except:
                    pass
            elif e_sc.stdout:
                try:
                    err_output = e_sc.stdout.decode('latin-1', errors='replace')
                except:
                    pass
            err_msg = f"Erro 'sc' para '{nome_servico}': {err_output.strip()}"
            self.append_text_to_log_area_threadsafe(f"ERRO: {err_msg}\n")
            logging.error(f"{log_prefix} {err_msg}", exc_info=True)
            return False
        except subprocess.TimeoutExpired as e_timeout:
            self.append_text_to_log_area_threadsafe(f"ERRO: Timeout ao operar serviço '{nome_servico}': {e_timeout}\n")
            logging.error(f"{log_prefix} Timeout ao operar serviço '{nome_servico}': {e_timeout}", exc_info=True)
            return False
        except FileNotFoundError:
            self.append_text_to_log_area_threadsafe(f"ERRO: Comando 'sc.exe' não encontrado.\n")
            logging.error(f"{log_prefix} Comando 'sc.exe' não encontrado.")
            return False
        except Exception as e:
            self.append_text_to_log_area_threadsafe(f"ERRO inesperado ao operar serviço '{nome_servico}': {e}\n")
            logging.error(f"{log_prefix} Erro inesperado ao operar serviço '{nome_servico}': {e}", exc_info=True)
            return False

    def _operar_servico_com_delays_linux(self, nome_servico, tipo_reinicio=""):
        stop_delay_s = self.stop_delay_var.get()
        start_delay_s = self.start_delay_var.get()
        log_prefix = f"Tab '{self.nome}' ({tipo_reinicio.strip()}) Linux:"

        nome_servico_systemd = nome_servico
        if not nome_servico.endswith(".service"):
            nome_servico_systemd = f"{nome_servico}.service"

        cmd_prefix = []
        if os.geteuid() != 0:  # Adiciona sudo apenas se não for root
            cmd_prefix = ['sudo']

        try:
            # Parar o serviço
            status_atual = self._verificar_status_servico_linux(nome_servico_systemd)
            if status_atual == "RUNNING" or status_atual == "START_PENDING":
                self.append_text_to_log_area_threadsafe(f"Parando serviço '{nome_servico_systemd}'...\n")
                subprocess.run(cmd_prefix + ['systemctl', 'stop', nome_servico_systemd], check=True,
                               capture_output=True, text=True, timeout=30)
                self.append_text_to_log_area_threadsafe(f"Comando de parada enviado. Aguardando {stop_delay_s}s...\n")

                wait_start = time.monotonic()
                while time.monotonic() - wait_start < stop_delay_s:
                    if self._stop_event.is_set() or self._scheduler_stop_event.is_set():
                        logging.info(f"{log_prefix} Operação de serviço interrompida durante delay de parada.")
                        return False
                    time.sleep(0.1)

                status_apos_parada = self._verificar_status_servico_linux(nome_servico_systemd)
                if status_apos_parada != "STOPPED":
                    logging.warning(
                        f"{log_prefix} Serviço {nome_servico_systemd} não parou como esperado. Status: {status_apos_parada}")
                    self.append_text_to_log_area_threadsafe(
                        f"AVISO: Serviço '{nome_servico_systemd}' pode não ter parado. Status: {status_apos_parada}\n")
            elif status_atual == "STOPPED":
                self.append_text_to_log_area_threadsafe(f"Serviço '{nome_servico_systemd}' já estava parado.\n")
            elif status_atual == "NOT_FOUND":
                self.append_text_to_log_area_threadsafe(
                    f"ERRO: Serviço '{nome_servico_systemd}' não encontrado para parada.\n")
                return False
            else:
                self.append_text_to_log_area_threadsafe(
                    f"ERRO: Estado do serviço '{nome_servico_systemd}' desconhecido ou erro ({status_atual}). Impossível prosseguir com parada segura.\n")
                return False

            # Iniciar o serviço
            self.append_text_to_log_area_threadsafe(f"Iniciando serviço '{nome_servico_systemd}'...\n")
            subprocess.run(cmd_prefix + ['systemctl', 'start', nome_servico_systemd], check=True, capture_output=True,
                           text=True, timeout=30)
            self.append_text_to_log_area_threadsafe(
                f"Comando de início enviado. Aguardando {start_delay_s}s para estabilizar...\n")

            wait_start = time.monotonic()
            while time.monotonic() - wait_start < start_delay_s:
                if self._stop_event.is_set() or self._scheduler_stop_event.is_set():
                    logging.info(f"{log_prefix} Operação de serviço interrompida durante delay de início.")
                    return False
                time.sleep(0.1)

            status_final = self._verificar_status_servico_linux(nome_servico_systemd)
            if status_final == "RUNNING":
                logging.info(f"{log_prefix} Serviço {nome_servico_systemd} iniciado com sucesso.")
                return True
            else:
                logging.error(f"{log_prefix} Serviço {nome_servico_systemd} falhou ao iniciar. Status: {status_final}")
                self.append_text_to_log_area_threadsafe(
                    f"ERRO: Serviço '{nome_servico_systemd}' falhou ao iniciar. Status: {status_final}\n")
                return False

        except subprocess.CalledProcessError as e_sysctl:
            err_output = e_sysctl.stderr.strip() if e_sysctl.stderr else e_sysctl.stdout.strip()
            err_msg = f"Erro 'systemctl' para '{nome_servico_systemd}': {err_output}"
            self.append_text_to_log_area_threadsafe(f"ERRO: {err_msg}\n")
            logging.error(f"{log_prefix} {err_msg}", exc_info=True)
            return False
        except subprocess.TimeoutExpired as e_timeout:
            self.append_text_to_log_area_threadsafe(
                f"ERRO: Timeout ao operar serviço '{nome_servico_systemd}': {e_timeout}\n")
            logging.error(f"{log_prefix} Timeout ao operar serviço '{nome_servico_systemd}': {e_timeout}",
                          exc_info=True)
            return False
        except FileNotFoundError:
            self.append_text_to_log_area_threadsafe(f"ERRO: Comando 'systemctl' ou 'sudo' não encontrado.\n")
            logging.error(f"{log_prefix} Comando 'systemctl' ou 'sudo' não encontrado.")
            return False
        except Exception as e:
            self.append_text_to_log_area_threadsafe(
                f"ERRO inesperado ao operar serviço '{nome_servico_systemd}': {e}\n")
            logging.error(f"{log_prefix} Erro inesperado ao operar serviço '{nome_servico_systemd}': {e}",
                          exc_info=True)
            return False

    def append_text_to_log_area(self, texto):
        if not self.winfo_exists(): return
        try:
            self.app.root.after(0, self._append_text_to_log_area_gui_thread, texto)
        except Exception:  # tk.TclError if root is destroyed
            pass

    def _append_text_to_log_area_gui_thread(self, texto):
        if not self.text_area_log.winfo_exists(): return
        try:
            current_state = self.text_area_log.cget("state")
            self.text_area_log.config(state='normal')
            self.text_area_log.insert('end', texto)
            if self.auto_scroll_log_var.get():
                self.text_area_log.yview_moveto(1.0)
            self.text_area_log.config(state=current_state)  # Restore original state
        except tk.TclError:  # Widget might be destroyed
            pass

    def append_text_to_log_area_threadsafe(self, texto):
        self.append_text_to_log_area(texto)

    def limpar_tela_log(self):
        if self.text_area_log.winfo_exists():
            self.text_area_log.config(state='normal')
            self.text_area_log.delete('1.0', 'end')
            self.text_area_log.config(state='disabled')

    def toggle_pausa(self):
        self._paused = not self._paused
        btn_text, btn_style = ("▶️ Retomar", SUCCESS) if self._paused else ("⏸️ Pausar", WARNING)
        self.pausar_btn.config(text=btn_text, bootstyle=btn_style)

    def _toggle_log_search_bar(self, event=None, force_show=False, force_hide=False):
        # Garante que o frame existe antes de operar sobre ele
        if not hasattr(self, 'search_log_frame') or not self.search_log_frame.winfo_exists():
            return

        if force_hide or (self.search_log_frame.winfo_ismapped() and not force_show):
            self.search_log_frame.pack_forget()
            if self.text_area_log.winfo_exists():
                self.text_area_log.tag_remove("search_match", "1.0", "end")
        elif self.text_area_log.winfo_exists():  # Só mostra se a área de log existir
            self.search_log_frame.pack(fill='x', before=self.text_area_log, pady=(0, 2), padx=5)
            if self.log_search_entry.winfo_exists():
                self.log_search_entry.focus_set()

    def _perform_log_search_internal(self, term, start_pos, direction_forward=True, wrap=True):
        if not term or not self.text_area_log.winfo_exists(): return None

        original_state = self.text_area_log.cget("state")
        self.text_area_log.config(state="normal")
        self.text_area_log.tag_remove("search_match", "1.0", "end")
        count_var = tk.IntVar()

        pos = self.text_area_log.search(
            term,
            start_pos,
            backwards=(not direction_forward),
            count=count_var,
            nocase=True,
            stopindex="1.0" if not direction_forward else "end"  # Evita que a busca continue indefinidamente
        )

        if pos:
            end_pos = f"{pos}+{count_var.get()}c"
            self.text_area_log.tag_add("search_match", pos, end_pos)
            self.text_area_log.tag_config("search_match", background="yellow", foreground="black")
            self.text_area_log.see(pos)
            self.text_area_log.config(state=original_state)
            return end_pos if direction_forward else pos
        elif wrap:
            # Se não encontrou e wrap é True, tenta do início/fim oposto
            wrap_start = "1.0" if direction_forward else "end"
            # Chama recursivamente com wrap=False para evitar loop infinito
            return self._perform_log_search_internal(term, wrap_start, direction_forward, wrap=False)

        self.text_area_log.config(state=original_state)
        return None

    def _search_log_next(self, event=None):
        term = self.log_search_var.get()
        if not term or not self.text_area_log.winfo_exists(): return

        start_from = self.last_search_pos
        current_match_ranges = self.text_area_log.tag_ranges("search_match")
        if current_match_ranges:  # Se existe uma correspondência atual, começa depois dela
            start_from = current_match_ranges[1]

        next_match_end_pos = self._perform_log_search_internal(term, start_from, direction_forward=True, wrap=True)
        if next_match_end_pos:
            self.last_search_pos = next_match_end_pos
        # else: self.last_search_pos = "1.0" # Ou manter o último se nada for encontrado

    def _search_log_prev(self, event=None):
        term = self.log_search_var.get()
        if not term or not self.text_area_log.winfo_exists(): return

        start_from = self.last_search_pos
        current_match_ranges = self.text_area_log.tag_ranges("search_match")
        if current_match_ranges:  # Se existe uma correspondência atual, começa antes dela
            start_from = current_match_ranges[0]

        prev_match_start_pos = self._perform_log_search_internal(term, start_from, direction_forward=False, wrap=True)
        if prev_match_start_pos:
            self.last_search_pos = prev_match_start_pos
        # else: self.last_search_pos = "end"


# ==============================================================================
# CLASSE ServerRestarterApp
# ==============================================================================
class ServerRestarterApp:
    def __init__(self, root):
        self.root = root
        self.root.title("PQDT_Raphael Server Auto-Restarter - Multi-Servidor")
        self.root.geometry("900x750")
        self.tray_icon = None
        self.app_icon_tk = None
        self.original_pil_bg_image = None
        self.bg_photo_image = None
        self.bg_label = None

        self.style = ttk.Style()
        self.config_file = "server_restarter_config.json"
        self.config = self._load_app_config_from_file()

        try:
            self.style.theme_use(self.config.get("theme", "darkly"))
        except tk.TclError:
            self.style.theme_use("litera")  # Fallback theme
            self.config["theme"] = "litera"
            logging.warning(f"Tema '{self.config.get('theme')}' não encontrado. Usando 'litera'.")

        self.servidores = []
        self.config_changed = False
        self._app_stop_event = threading.Event()

        self._setup_background_image()
        self.set_application_icon()
        self.create_menu()
        self.create_status_bar()

        self.main_notebook = ttk.Notebook(self.root)
        self.system_log_frame = ttk.Frame(self.main_notebook)
        self.main_notebook.add(self.system_log_frame, text="Log do Sistema")
        self.system_log_text_area = ScrolledText(self.system_log_frame, wrap='word', height=10, state='disabled')
        self.system_log_text_area.pack(fill='both', expand=True, padx=5, pady=5)

        self.inicializar_servidores_das_configuracoes()

        self.main_notebook.pack(fill='both', expand=True, padx=5, pady=5)
        if self.bg_label and self.bg_label.winfo_exists():  # Check if bg_label exists
            self.bg_label.lower()

        self._system_log_update_error_count = 0
        self.atualizar_log_sistema_periodicamente()  # Corrigido: Chamada de método da classe
        self.root.bind("<Configure>", self._on_root_configure)
        self.root.protocol("WM_DELETE_WINDOW", self.minimize_to_tray_on_close)

        if PYSTRAY_AVAILABLE:
            self.setup_tray_icon()

    def _setup_background_image(self):
        if not PIL_AVAILABLE or not os.path.exists(BACKGROUND_IMAGE_PATH): return
        try:
            pil_image_original = Image.open(BACKGROUND_IMAGE_PATH)
            pil_image_rgba = pil_image_original.convert("RGBA")
            self.original_pil_bg_image = pil_image_rgba  # Armazena para redimensionamento

            self.bg_label = ttk.Label(self.root)
            self.bg_label.place(x=0, y=0, relwidth=1, relheight=1)
            self.root.update_idletasks()  # Garante que a janela tem dimensões
            self._resize_background_image(self.root.winfo_width(), self.root.winfo_height())
        except Exception as e:
            logging.error(f"Erro ao carregar imagem de fundo: {e}", exc_info=True)
            self.original_pil_bg_image = None
            if self.bg_label and self.bg_label.winfo_exists(): self.bg_label.destroy()
            self.bg_label = None

    def _on_root_configure(self, event):
        if event.widget == self.root and self.original_pil_bg_image and self.bg_label and self.bg_label.winfo_exists():
            self._resize_background_image(event.width, event.height)

    def _resize_background_image(self, width, height):
        if not self.original_pil_bg_image or width <= 1 or height <= 1 or \
                not self.bg_label or not self.bg_label.winfo_exists():
            return

        img_to_resize = self.original_pil_bg_image.copy()  # Trabalha com uma cópia

        if BACKGROUND_ALPHA_MULTIPLIER < 1.0 and BACKGROUND_ALPHA_MULTIPLIER >= 0.0:
            try:
                alpha = img_to_resize.split()[3]
                alpha = alpha.point(lambda p: int(p * BACKGROUND_ALPHA_MULTIPLIER))
                img_to_resize.putalpha(alpha)
            except IndexError:  # Sem canal alfa
                pass
            except Exception as e_alpha:
                logging.warning(f"Não foi possível aplicar alfa à imagem de fundo: {e_alpha}")

        # Calcular proporções para "cobrir" a área
        img_aspect = img_to_resize.width / img_to_resize.height
        win_aspect = width / height

        if win_aspect > img_aspect:
            # Janela mais larga que a imagem: altura da imagem = altura da janela, largura proporcional
            new_height = height
            new_width = int(new_height * img_aspect)
        else:
            # Janela mais alta ou mesma proporção: largura da imagem = largura da janela, altura proporcional
            new_width = width
            new_height = int(new_width / img_aspect)

        # Para "cobrir", pode ser que a imagem precise ser maior que a janela em uma dimensão
        # e depois cortada. Ou, podemos escalar para que uma dimensão bata e a outra seja >=
        # e então centralizar.
        # Ajuste para 'cover': escalar para que a menor dimensão da imagem caiba
        # e a maior exceda ou caiba, depois cortar o excesso do centro.

        # Escala para que a imagem CUBRA a janela
        if width / img_to_resize.width > height / img_to_resize.height:
            # Escalar pela largura da janela, a imagem é proporcionalmente mais alta
            final_w = width
            final_h = int(img_to_resize.height * (width / img_to_resize.width))
        else:
            # Escalar pela altura da janela, a imagem é proporcionalmente mais larga
            final_h = height
            final_w = int(img_to_resize.width * (height / img_to_resize.height))

        try:
            resized_pil_image = img_to_resize.resize((final_w, final_h), Image.LANCZOS)
            self.bg_photo_image = ImageTk.PhotoImage(resized_pil_image)
            self.bg_label.configure(image=self.bg_photo_image)
            # self.bg_label.image = self.bg_photo_image # Manter referência, ttk.Label deve fazer isso
        except Exception as e_resize:
            logging.error(f"Erro ao redimensionar ou aplicar imagem de fundo: {e_resize}", exc_info=True)

    def set_application_icon(self):
        if PIL_AVAILABLE and os.path.exists(ICON_PATH):
            try:
                if platform.system() == "Windows":
                    self.root.iconbitmap(default=ICON_PATH)
                else:
                    pil_icon = Image.open(ICON_PATH)
                    self.app_icon_tk = ImageTk.PhotoImage(pil_icon)
                    self.root.iconphoto(True, self.app_icon_tk)
            except Exception as e:
                logging.error(f"Erro ao definir ícone da aplicação: {e}", exc_info=True)

    def _create_tray_image(self):
        if PIL_AVAILABLE and os.path.exists(ICON_PATH):
            try:
                return Image.open(ICON_PATH)
            except Exception as e_load_icon:
                logging.warning(
                    f"Não foi possível carregar ícone da bandeja de {ICON_PATH}: {e_load_icon}. Usando padrão.")
                pass  # Tenta o padrão abaixo

        if PIL_AVAILABLE:  # Se o ícone falhou ou não existia, mas PIL sim, desenha um
            try:
                image = Image.new('RGBA', (64, 64), (0, 0, 0, 0))  # Transparente
                draw = ImageDraw.Draw(image)
                # Exemplo simples: um círculo azul
                draw.ellipse((5, 5, 59, 59), fill='skyblue', outline='blue')
                draw.text((20, 20), "SR", fill="navy", font=None)  # Sem fonte específica para portabilidade
                return image
            except Exception as e_draw:
                logging.error(f"Erro ao desenhar ícone padrão da bandeja: {e_draw}")
        return None

    def setup_tray_icon(self):
        if not PYSTRAY_AVAILABLE:  # Checa se pystray está disponível
            return

        image = self._create_tray_image()
        if not image:
            logging.error("Imagem para ícone da bandeja não pôde ser criada.")
            return

        menu_items = [
            pystray.MenuItem('Mostrar', self.show_from_tray, default=True),
            pystray.MenuItem('Sair', self.shutdown_application_from_tray)
        ]

        try:
            self.tray_icon = pystray.Icon("ServerRestarter", image, "PredPy Server Restarter", tuple(menu_items))
            threading.Thread(target=self.tray_icon.run, daemon=True, name="TrayIconThread").start()
            logging.info("Ícone da bandeja configurado.")
        except Exception as e_tray:
            logging.error(f"Erro ao configurar ícone da bandeja: {e_tray}", exc_info=True)
            self.tray_icon = None

    def show_from_tray(self, icon=None, item=None):
        if self.root.winfo_exists():
            self.root.after(0, self.root.deiconify)  # Traz a janela de volta
            self.root.after(100, self.root.lift)  # Traz para frente
            self.root.after(200, self.root.focus_force)  # Força o foco

    def minimize_to_tray_on_close(self, event=None):
        # Verifica se o ícone da bandeja foi criado e está visível
        if self.tray_icon and hasattr(self.tray_icon, 'visible') and self.tray_icon.visible:
            self.root.withdraw()  # Minimiza para a bandeja
            logging.info("Aplicação minimizada para a bandeja.")
        else:
            # Se não há ícone na bandeja ou não está visível, encerra a aplicação
            logging.info("Ícone da bandeja não disponível/visível. Encerrando aplicação.")
            self.shutdown_application()

    def shutdown_application_from_tray(self, icon=None, item=None):
        logging.info("Comando 'Sair' da bandeja recebido.")
        self.shutdown_application()

    def shutdown_application(self):
        logging.info("Iniciando processo de encerramento...")
        self._app_stop_event.set()
        for srv_tab in self.servidores:
            srv_tab.stop_log_monitoring(from_tab_closure=True)
            srv_tab.stop_scheduler_thread(from_tab_closure=True)

        if self.config_changed:
            try:
                self._save_app_config_to_file()
                logging.info("Configurações salvas automaticamente ao sair.")
            except Exception as e_save:
                logging.error(f"Erro ao salvar configurações ao sair: {e_save}", exc_info=True)

        if self.tray_icon:
            try:
                self.tray_icon.stop()
                logging.info("Ícone da bandeja parado.")
            except Exception as e_tray_stop:
                # Pode dar erro se já estiver parando ou se a thread já terminou
                logging.debug(f"Erro (possivelmente benigno) ao parar ícone da bandeja: {e_tray_stop}")

        if self.root.winfo_exists():
            try:
                self.root.destroy()
            except tk.TclError:  # Pode acontecer se já estiver sendo destruído
                pass
        logging.info("Aplicação encerrada.")

    def get_current_servidor_tab_widget(self):
        try:
            if not self.main_notebook.winfo_exists() or not self.main_notebook.tabs(): return None
            selected_tab_id = self.main_notebook.select()
            if not selected_tab_id: return None
            widget = self.main_notebook.nametowidget(selected_tab_id)
            if isinstance(widget, ServidorTab): return widget
        except tk.TclError:  # Widget pode não existir mais
            return None
        return None

    def inicializar_servidores_das_configuracoes(self):
        servers_config_list = self.config.get("servers", [])
        if not servers_config_list:
            self.adicionar_servidor_tab("Servidor 1 (Padrão)")
        else:
            for idx, srv_conf in enumerate(servers_config_list):
                # Garante nome único se o nome do config estiver em branco ou for duplicado
                nome_base = srv_conf.get("nome", f"Servidor {idx + 1}")
                self.adicionar_servidor_tab(nome_base, srv_conf, focus_new_tab=False)

        if self.servidores and self.main_notebook.tabs():  # Verifica se há abas antes de selecionar
            try:
                self.main_notebook.select(self.servidores[0])
            except tk.TclError:
                logging.warning("Não foi possível selecionar a primeira aba de servidor.")

    def adicionar_servidor_tab(self, nome_sugerido=None, config_servidor=None, focus_new_tab=True):
        if nome_sugerido is None or not nome_sugerido.strip():
            nome_sugerido = f"Servidor {len(self.servidores) + 1}"

        # Garante nome único entre as abas
        final_nome = nome_sugerido
        count = 1
        nomes_existentes = [s.nome for s in self.servidores]
        while final_nome in nomes_existentes:
            final_nome = f"{nome_sugerido} ({count})"
            count += 1

        servidor_tab_frame = ServidorTab(self.main_notebook, self, final_nome, config_servidor)
        self.servidores.append(servidor_tab_frame)
        self.main_notebook.add(servidor_tab_frame, text=final_nome)
        if focus_new_tab and self.main_notebook.tabs():
            try:
                self.main_notebook.select(servidor_tab_frame)
            except tk.TclError:
                logging.warning(f"Não foi possível focar na nova aba '{final_nome}'")
        self.mark_config_changed()

    def remover_servidor_atual(self):
        current_tab = self.get_current_servidor_tab_widget()
        if not current_tab:
            self.show_messagebox_from_thread("warning", "Remover Servidor", "Nenhuma aba de servidor selecionada.")
            return

        nome_servidor = current_tab.nome
        if Messagebox.okcancel(f"Remover '{nome_servidor}'?",
                               f"Tem certeza que deseja remover o servidor '{nome_servidor}'?\nEsta ação não pode ser desfeita.",
                               parent=self.root, alert=True) == "OK":
            logging.info(f"Removendo aba '{nome_servidor}'...")
            current_tab.stop_log_monitoring(from_tab_closure=True)
            current_tab.stop_scheduler_thread(from_tab_closure=True)

            try:
                self.main_notebook.forget(current_tab)
            except tk.TclError:
                logging.warning(
                    f"TclError ao tentar remover aba '{nome_servidor}' do notebook (pode já ter sido removida).")

            if current_tab in self.servidores:
                self.servidores.remove(current_tab)

            current_tab.destroy()  # Destroi o frame da aba
            self.mark_config_changed()
            self.set_status_from_thread(f"Servidor '{nome_servidor}' removido.")
            logging.info(f"Aba '{nome_servidor}' removida.")

            # Seleciona outra aba se possível
            if self.servidores:
                try:
                    self.main_notebook.select(self.servidores[0])
                except tk.TclError:
                    pass  # Ignora se não puder selecionar
            elif self.main_notebook.tabs() and self.system_log_frame.winfo_exists():  # Se só sobrou o log do sistema
                try:
                    self.main_notebook.select(self.system_log_frame)
                except tk.TclError:
                    pass

    def renomear_servidor_atual(self):
        current_tab = self.get_current_servidor_tab_widget()
        if not current_tab:
            self.show_messagebox_from_thread("warning", "Renomear Servidor", "Nenhuma aba de servidor selecionada.")
            return

        nome_antigo = current_tab.nome
        novo_nome = simpledialog.askstring("Renomear Servidor", f"Novo nome para '{nome_antigo}':",
                                           initialvalue=nome_antigo, parent=self.root)

        if novo_nome and novo_nome.strip() and novo_nome != nome_antigo:
            nomes_existentes = [s.nome for s in self.servidores if s != current_tab]
            if novo_nome in nomes_existentes:
                self.show_messagebox_from_thread("error", "Nome Duplicado", f"O nome '{novo_nome}' já está em uso.")
                return

            current_tab.nome = novo_nome
            try:
                # Encontra o ID da aba para renomear no notebook
                for i, tab_id_str in enumerate(self.main_notebook.tabs()):
                    if self.main_notebook.nametowidget(tab_id_str) == current_tab:
                        self.main_notebook.tab(tab_id_str, text=novo_nome)
                        break
                self.mark_config_changed()
                self.set_status_from_thread(f"Servidor '{nome_antigo}' renomeado para '{novo_nome}'.")
                logging.info(f"Servidor '{nome_antigo}' renomeado para '{novo_nome}'.")
            except tk.TclError as e:
                logging.error(f"Erro ao renomear aba no notebook: {e}", exc_info=True)
                self.show_messagebox_from_thread("error", "Erro ao Renomear",
                                                 "Não foi possível atualizar o nome da aba.")
                current_tab.nome = nome_antigo  # Reverte a mudança interna

        elif novo_nome is not None and not novo_nome.strip():
            self.show_messagebox_from_thread("warning", "Nome Inválido", "O nome do servidor não pode ser vazio.")

    def mark_config_changed(self):
        if not self.config_changed:
            self.config_changed = True
            if hasattr(self, 'file_menu') and self.file_menu.winfo_exists():  # Checa se o menu existe
                try:
                    self.file_menu.entryconfigure("Salvar Configuração", state="normal")
                except tk.TclError:  # Pode acontecer se o menu for destruído
                    pass

    def _load_app_config_from_file(self):
        try:
            if os.path.exists(self.config_file):
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    config_data = json.load(f)
                    logging.info(f"Configuração carregada de {self.config_file}")
                    return config_data
        except json.JSONDecodeError as e_json:
            logging.error(f"Erro ao decodificar JSON em {self.config_file}: {e_json}", exc_info=True)
        except Exception as e_load:
            logging.error(f"Erro ao carregar configuração de {self.config_file}: {e_load}", exc_info=True)

        logging.info(f"Arquivo de configuração {self.config_file} não encontrado ou inválido. Usando padrões.")
        return {"theme": "litera", "servers": []}  # Default para um tema que deve existir

    def _save_app_config_to_file(self):
        # Usa self.style.theme.name para pegar o nome do tema atual de forma segura
        current_theme_name = "litera"  # Default
        if hasattr(self.style, 'theme') and hasattr(self.style.theme, 'name'):
            current_theme_name = self.style.theme.name

        config_data = {"theme": current_theme_name,
                       "servers": [s.get_current_config() for s in self.servidores]}
        try:
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(config_data, f, indent=4)
            self.config_changed = False
            if hasattr(self, 'file_menu') and self.file_menu.winfo_exists():
                try:
                    self.file_menu.entryconfigure("Salvar Configuração", state="disabled")
                except tk.TclError:
                    pass
            self.set_status_from_thread("Configuração salva!")
            logging.info(f"Configuração salva em {self.config_file}")
        except IOError as e_io:
            self.show_messagebox_from_thread("error", "Erro ao Salvar", f"Erro de E/S: {e_io}")
            logging.error(f"Erro de E/S ao salvar configuração: {e_io}", exc_info=True)
        except Exception as e:
            self.show_messagebox_from_thread("error", "Erro ao Salvar", f"Erro inesperado: {str(e)}")
            logging.error(f"Erro inesperado ao salvar configuração: {e}", exc_info=True)

    def load_config_from_dialog(self):
        caminho = filedialog.askopenfilename(
            defaultextension=".json", filetypes=[("Arquivos JSON", "*.json"), ("Todos", "*.*")],
            title="Selecionar arquivo de configuração", initialdir=os.path.dirname(self.config_file) or os.getcwd()
        )
        if not caminho:
            return

        try:
            with open(caminho, 'r', encoding='utf-8') as f:
                loaded_config_data = json.load(f)

            # Limpar abas existentes
            for srv_tab in list(self.servidores):  # Itera sobre uma cópia
                srv_tab.stop_log_monitoring(from_tab_closure=True)
                srv_tab.stop_scheduler_thread(from_tab_closure=True)
                if self.main_notebook.winfo_exists():  # Verifica se o notebook ainda existe
                    try:
                        self.main_notebook.forget(srv_tab)
                    except tk.TclError:
                        pass  # Aba pode já ter sido removida
                srv_tab.destroy()
            self.servidores.clear()

            # Carregar nova configuração
            self.config_file = caminho  # Atualiza o arquivo de configuração padrão
            self.config = loaded_config_data
            new_theme = self.config.get("theme", "litera")  # Default para litera

            try:
                self.style.theme_use(new_theme)
                self.config["theme"] = new_theme  # Atualiza o tema na config interna
            except tk.TclError:
                logging.warning(f"Tema '{new_theme}' do arquivo de config não encontrado. Usando 'litera'.")
                self.style.theme_use("litera")
                self.config["theme"] = "litera"
                self.theme_var.set("litera")  # Atualiza a variável do menu de temas

            self.inicializar_servidores_das_configuracoes()
            self.config_changed = False
            if hasattr(self, 'file_menu') and self.file_menu.winfo_exists():
                try:
                    self.file_menu.entryconfigure("Salvar Configuração", state="disabled")
                except tk.TclError:
                    pass

            self.set_status_from_thread(f"Configuração carregada de {os.path.basename(caminho)}")
            logging.info(f"Configuração carregada de {caminho}")
            self.show_messagebox_from_thread("info", "Configuração Carregada", f"Carregada de:\n{caminho}")

        except json.JSONDecodeError as e_json_load:
            logging.error(f"Erro de JSON ao carregar {caminho}: {e_json_load}", exc_info=True)
            self.show_messagebox_from_thread("error", "Erro de Configuração",
                                             f"Falha ao carregar '{os.path.basename(caminho)}':\nJSON inválido.\n{e_json_load}")
        except Exception as e_load:
            logging.error(f"Erro ao carregar {caminho}: {e_load}", exc_info=True)
            self.show_messagebox_from_thread("error", "Erro de Configuração",
                                             f"Falha ao carregar '{os.path.basename(caminho)}':\n{e_load}")

    def create_menu(self):
        menubar = ttk.Menu(self.root)
        self.root.config(menu=menubar)
        self.file_menu = ttk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Arquivo", menu=self.file_menu)
        self.file_menu.add_command(label="Salvar Configuração", command=self._save_app_config_to_file, state="disabled")
        self.file_menu.add_command(label="Carregar Configuração...", command=self.load_config_from_dialog)
        self.file_menu.add_separator()
        self.file_menu.add_command(label="Sair", command=self.shutdown_application)

        server_menu = ttk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Servidores", menu=server_menu)
        server_menu.add_command(label="Adicionar Novo Servidor", command=self.adicionar_servidor_tab)
        server_menu.add_command(label="Remover Servidor Atual", command=self.remover_servidor_atual)
        server_menu.add_command(label="Renomear Servidor Atual...", command=self.renomear_servidor_atual)

        tools_menu = ttk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Ferramentas", menu=tools_menu)
        tools_menu.add_command(label="Exportar Logs da Aba Atual", command=self.export_current_tab_logs)

        theme_menu = ttk.Menu(tools_menu, tearoff=0)
        tools_menu.add_cascade(label="Mudar Tema", menu=theme_menu)

        # Garante que self.style.theme.name está acessível antes de usá-lo
        current_theme_name = "litera"  # Default
        if hasattr(self.style, 'theme') and hasattr(self.style.theme, 'name'):
            current_theme_name = self.style.theme.name
        self.theme_var = tk.StringVar(value=current_theme_name)

        for theme_name in sorted(self.style.theme_names()):
            theme_menu.add_radiobutton(label=theme_name, variable=self.theme_var, command=self.trocar_tema)

        help_menu = ttk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Ajuda", menu=help_menu)
        help_menu.add_command(label="Sobre", command=self.show_about)

    def trocar_tema(self, event=None):
        novo_tema = self.theme_var.get()
        try:
            self.style.theme_use(novo_tema)
            # Re-inicializa abas para que elas peguem as novas cores do tema, se necessário
            for srv_tab in self.servidores:
                if srv_tab.winfo_exists():  # Verifica se a aba ainda existe
                    srv_tab.initialize_from_config_vars()
            self.config["theme"] = novo_tema
            self.mark_config_changed()
            logging.info(f"Tema alterado para: {novo_tema}")
            self.set_status_from_thread(f"Tema alterado para '{novo_tema}'.")
        except tk.TclError as e:
            logging.error(f"Erro ao trocar para o tema '{novo_tema}': {e}", exc_info=True)
            self.show_messagebox_from_thread("error", "Erro de Tema",
                                             f"Não foi possível aplicar o tema '{novo_tema}'.\nVoltando para 'litera'.")
            try:  # Tenta voltar para um tema seguro
                self.style.theme_use("litera")
                self.theme_var.set("litera")
                self.config["theme"] = "litera"
                for srv_tab in self.servidores:
                    if srv_tab.winfo_exists(): srv_tab.initialize_from_config_vars()
            except Exception as e_fallback:
                logging.critical(f"Falha ao voltar para o tema de fallback 'litera': {e_fallback}")

    def export_current_tab_logs(self):
        current_tab_widget = self.get_current_servidor_tab_widget()
        text_widget_to_export = None
        filename_part = ""

        if current_tab_widget:
            text_widget_to_export = current_tab_widget.text_area_log
            filename_part = f"Logs de '{current_tab_widget.nome}'"
        elif self.main_notebook.winfo_exists() and self.main_notebook.tabs():
            try:
                # Verifica se a aba de Log do Sistema está selecionada
                current_tab_text = self.main_notebook.tab(self.main_notebook.select(), "text")
                if current_tab_text == "Log do Sistema":  # Compara com o texto exato da aba
                    text_widget_to_export = self.system_log_text_area
                    filename_part = "Log do Sistema do Restarter"
            except tk.TclError:  # Pode acontecer se nenhuma aba estiver selecionada
                pass

        if text_widget_to_export and text_widget_to_export.winfo_exists():
            self._export_text_widget_content(text_widget_to_export, filename_part)
        else:
            self.show_messagebox_from_thread("info", "Exportar Logs",
                                             "Selecione uma aba de servidor ou a aba 'Log do Sistema' para exportar.")

    def _export_text_widget_content(self, text_widget, default_filename_part):
        # Sanitiza o nome do arquivo
        safe_filename_part = re.sub(r'[^\w\s-]', '', default_filename_part).strip().replace(' ', '_')
        initial_filename = f"{safe_filename_part}.txt" if safe_filename_part else "logs.txt"

        caminho_arquivo = filedialog.asksaveasfilename(
            defaultextension=".txt",
            filetypes=[("Arquivos de Texto", "*.txt"), ("Todos", "*.*")],
            title=f"Exportar {default_filename_part}",
            initialfile=initial_filename
        )
        if caminho_arquivo:
            try:
                if text_widget.winfo_exists():
                    # Salva o estado, habilita, pega o texto, restaura o estado
                    original_state = text_widget.cget("state")
                    text_widget.config(state="normal")
                    content = text_widget.get('1.0', 'end-1c')  # end-1c para não pegar o newline final
                    text_widget.config(state=original_state)

                    with open(caminho_arquivo, 'w', encoding='utf-8') as f:
                        f.write(content)
                    self.set_status_from_thread(f"Logs exportados para: {os.path.basename(caminho_arquivo)}")
                    self.show_messagebox_from_thread("info", "Exportação Concluída",
                                                     f"Logs exportados com sucesso para:\n{caminho_arquivo}")
                    logging.info(f"{default_filename_part} exportados para: {caminho_arquivo}")
            except Exception as e:
                logging.error(f"Erro ao exportar logs para {caminho_arquivo}: {e}", exc_info=True)
                self.show_messagebox_from_thread("error", "Erro na Exportação", f"Falha ao exportar logs:\n{e}")

    def show_about(self):
        about_win = ttk.Toplevel(self.root)
        about_win.title("Sobre PredPy Server Restarter")
        about_win.geometry("480x420")
        about_win.resizable(False, False)
        about_win.transient(self.root)
        about_win.grab_set()

        frame = ttk.Frame(about_win, padding=20)
        frame.pack(fill='both', expand=True)

        ttk.Label(frame, text="PQDT_Raphael Server Restarter", font="-size 16 -weight bold").pack(pady=(0, 10))
        ttk.Label(frame, text="Versão 1.1.2 (Correções e Melhorias)", font="-size 10").pack()  # Versão atualizada
        ttk.Separator(frame).pack(fill='x', pady=10)

        desc = ("Ferramenta para monitorar logs de múltiplos servidores,\n"
                "detectar uma mensagem de gatilho específica e\n"
                "reiniciar o serviço do servidor automaticamente ou em horários agendados.\n\n"
                "Funcionalidades:\n"
                "- Abas para múltiplos servidores\n"
                "- Monitoramento de logs em tempo real\n"
                "- Mensagem de log configurável para gatilho\n"
                "- Reinício de serviço (Windows/Linux) por gatilho ou agendado\n"
                "- Configuração de horários de reinício\n"
                "- Ícone personalizado na janela e bandeja\n"
                "- Imagem de fundo personalizável\n"
                "- Minimizar para a bandeja do sistema\n"
                "- Temas visuais (via ttkbootstrap)")
        ttk.Label(frame, text=desc, justify='left').pack(pady=10)

        ttk.Separator(frame).pack(fill='x', pady=10)
        ttk.Button(frame, text="Fechar", command=about_win.destroy, bootstyle=PRIMARY).pack(pady=(15, 0))

        self.root.update_idletasks()
        ws = self.root.winfo_screenwidth()
        hs = self.root.winfo_screenheight()
        w_about, h_about = about_win.winfo_reqwidth(), about_win.winfo_reqheight()
        if w_about <= 1: w_about = 480
        if h_about <= 1: h_about = 420
        x_pos = (ws / 2) - (w_about / 2)
        y_pos = (hs / 2) - (h_about / 2)
        about_win.geometry(f'{w_about}x{h_about}+{int(x_pos)}+{int(y_pos)}')

        about_win.wait_window()

    def create_status_bar(self):
        self.status_bar_frame = ttk.Frame(self.root)
        self.status_bar_frame.pack(side='bottom', fill='x', pady=(0, 2), padx=2)
        ttk.Separator(self.status_bar_frame, orient=HORIZONTAL).pack(side='top', fill='x')  # Adicionado orient
        self.status_label_var = tk.StringVar(value="Pronto.")
        self.status_label = ttk.Label(self.status_bar_frame, textvariable=self.status_label_var, anchor='w')
        self.status_label.pack(side='left', fill='x', expand=True, padx=5, pady=(2, 0))  # Adicionado pady

    def atualizar_log_sistema_periodicamente(self):  # Corrigido: Indentação para ser método da classe
        if self._app_stop_event.is_set() or not self.root.winfo_exists() or \
                not hasattr(self, 'system_log_text_area') or not self.system_log_text_area.winfo_exists():
            return

        try:
            log_file_path = 'server_restarter.log'
            if os.path.exists(log_file_path):
                with open(log_file_path, 'r', encoding='utf-8', errors='replace') as f:
                    conteudo = f.read()

                self.system_log_text_area.config(state='normal')
                current_scroll_pos_tuple = self.system_log_text_area.yview()  # Retorna tupla (primeiro, ultimo)
                current_scroll_pos = current_scroll_pos_tuple[0]

                self.system_log_text_area.delete('1.0', 'end')
                self.system_log_text_area.insert('end', conteudo)

                if current_scroll_pos >= 0.99:
                    self.system_log_text_area.yview_moveto(1.0)
                else:
                    self.system_log_text_area.yview_moveto(current_scroll_pos)

                self.system_log_text_area.config(state='disabled')
                self._system_log_update_error_count = 0
            else:
                self.system_log_text_area.config(state='normal')
                self.system_log_text_area.delete('1.0', 'end')
                self.system_log_text_area.insert('end', f"Arquivo de log '{log_file_path}' não encontrado.")
                self.system_log_text_area.config(state='disabled')

        except tk.TclError as e_tcl_syslog:
            if "invalid command name" not in str(e_tcl_syslog).lower():
                logging.debug(f"TclError ao atualizar log sistema (provavelmente ao fechar): {e_tcl_syslog}")
        except Exception as e_syslog_update:
            if self._system_log_update_error_count < 5:
                logging.error(f"Erro ao atualizar log sistema: {e_syslog_update}", exc_info=False)
                self._system_log_update_error_count += 1

        if not self._app_stop_event.is_set() and self.root.winfo_exists():
            self.root.after(3000, self.atualizar_log_sistema_periodicamente)

    def iniciar_selecao_servico_para_aba(self, tab_instance, os_type):
        worker = None
        if os_type == "windows":
            if not PYWIN32_AVAILABLE:
                self.show_messagebox_from_thread("error", "Componente Ausente",
                                                 "pywin32 é necessário para listar serviços do Windows.")
                return
            worker = self._obter_servicos_worker_win
        elif os_type == "linux":
            if not SYSTEMCTL_AVAILABLE:
                self.show_messagebox_from_thread("error", "Componente Ausente",
                                                 "systemctl é necessário para listar serviços do Linux.")
                return
            worker = self._obter_servicos_worker_linux
        else:
            self.show_messagebox_from_thread("error", "Erro Interno", f"Tipo de SO desconhecido: {os_type}")
            return

        progress_win, _ = self._show_progress_dialog(f"Carregando Serviços ({os_type.capitalize()})", "Aguarde...")
        threading.Thread(target=worker, args=(progress_win, tab_instance), daemon=True,
                         name=f"ServiceList-{os_type}-{tab_instance.nome}").start()

    def _obter_servicos_worker_win(self, progress_win, tab_instance):
        initialized_com = False
        try:
            pythoncom.CoInitialize()
            initialized_com = True
            wmi = win32com.client.GetObject('winmgmts:')
            # Filtra serviços que podem ser parados (AcceptStop=True) e têm nome
            services = sorted([s.Name for s in wmi.InstancesOf('Win32_Service')
                               if hasattr(s, 'Name') and s.Name and hasattr(s, 'AcceptStop') and s.AcceptStop])

            if self.root.winfo_exists():  # Só chama se a root ainda existir
                self.root.after(0, self._mostrar_dialogo_selecao_servico, services, progress_win, tab_instance,
                                "Windows")
            elif progress_win.winfo_exists():  # Se a root não existe mais, fecha o progresso
                self.root.after(0, progress_win.destroy)

        except pythoncom.com_error as e_com:
            logging.error(f"Erro COM ao listar serviços Windows para '{tab_instance.nome}': {e_com}", exc_info=True)
            if self.root.winfo_exists():
                self.root.after(0, lambda: self.show_messagebox_from_thread("error", "Erro WMI/COM",
                                                                            f"Erro ao listar serviços: {e_com}"))
        except Exception as e:
            logging.error(f"Erro ao listar serviços Windows para '{tab_instance.nome}': {e}", exc_info=True)
            if self.root.winfo_exists():
                self.root.after(0, lambda: self.show_messagebox_from_thread("error", "Erro WMI",
                                                                            f"Erro ao listar serviços: {str(e)}"))
        finally:
            if progress_win.winfo_exists() and not self.root.winfo_exists():  # Garante fechar se a root sumiu
                self.root.after(0, progress_win.destroy)  # Usa after para ser thread-safe
            elif progress_win.winfo_exists() and self.root.winfo_exists():  # Se ambos existem, fecha via after
                self.root.after(0, progress_win.destroy)

            if initialized_com:
                try:
                    pythoncom.CoUninitialize()
                except Exception:
                    pass  # Ignora erros na desinicialização

    def _obter_servicos_worker_linux(self, progress_win, tab_instance):
        try:
            cmd_prefix = []
            if os.geteuid() != 0:  # Adiciona sudo apenas se não for root
                cmd_prefix = ['sudo']
            cmd = cmd_prefix + ['systemctl', 'list-units', '--type=service', '--all', '--no-legend', '--no-pager']
            result = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=15)  # Aumentado timeout
            services = sorted([line.split()[0] for line in result.stdout.strip().split('\n')
                               if line and not line.startswith("@")])  # Ignora templates
            # Filtra para remover a extensão .service para consistência, se presente
            services_cleaned = []
            for s in services:
                if s.endswith(".service"):
                    services_cleaned.append(s[:-len(".service")])
                else:
                    services_cleaned.append(s)
            services = sorted(list(set(services_cleaned)))  # Remove duplicatas e ordena

            if self.root.winfo_exists():
                self.root.after(0, self._mostrar_dialogo_selecao_servico, services, progress_win, tab_instance, "Linux")
            elif progress_win.winfo_exists():
                self.root.after(0, progress_win.destroy)

        except subprocess.CalledProcessError as e_cmd:
            err_msg = e_cmd.stderr.strip() if e_cmd.stderr else e_cmd.stdout.strip()
            logging.error(f"Erro systemctl (CalledProcessError) para '{tab_instance.nome}': {err_msg}", exc_info=True)
            if self.root.winfo_exists():
                self.root.after(0, lambda: self.show_messagebox_from_thread("error", "Erro systemctl",
                                                                            f"Falha ao listar serviços:\n{err_msg}"))
        except subprocess.TimeoutExpired:
            logging.error(f"Timeout ao listar serviços systemctl para '{tab_instance.nome}'.", exc_info=True)
            if self.root.winfo_exists():
                self.root.after(0, lambda: self.show_messagebox_from_thread("error", "Erro systemctl",
                                                                            "Timeout ao listar serviços."))
        except FileNotFoundError:
            logging.error(f"Comando systemctl ou sudo não encontrado para '{tab_instance.nome}'.", exc_info=True)
            if self.root.winfo_exists():
                self.root.after(0, lambda: self.show_messagebox_from_thread("error", "Erro systemctl",
                                                                            "Comando systemctl ou sudo não encontrado."))
        except Exception as e:
            logging.error(f"Erro ao listar serviços Linux para '{tab_instance.nome}': {e}", exc_info=True)
            if self.root.winfo_exists():
                self.root.after(0, lambda: self.show_messagebox_from_thread("error", "Erro systemctl",
                                                                            f"Erro inesperado ao listar serviços:\n{str(e)}"))
        finally:
            if progress_win.winfo_exists() and not self.root.winfo_exists():
                self.root.after(0, progress_win.destroy)
            elif progress_win.winfo_exists() and self.root.winfo_exists():
                self.root.after(0, progress_win.destroy)

    def _mostrar_dialogo_selecao_servico(self, service_list, progress_win, tab_instance,
                                         os_type):  # Corrigido: Indentação
        if progress_win and progress_win.winfo_exists():
            try:
                progress_win.destroy()
            except Exception:
                pass

        if not service_list:
            self.show_messagebox_from_thread("info", "Nenhum Serviço",
                                             f"Nenhum serviço gerenciável encontrado para {os_type}.")
            return

        dialog = ttk.Toplevel(self.root)
        dialog.title(f"Selecionar Serviço para '{tab_instance.nome}' ({os_type})")
        dialog.geometry("500x450")
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.protocol("WM_DELETE_WINDOW", dialog.destroy)

        ttk.Label(dialog, text=f"Escolha o serviço para '{tab_instance.nome}':", font="-size 10").pack(pady=(10, 5))

        search_frame = ttk.Frame(dialog)
        search_frame.pack(fill='x', padx=10, pady=(0, 5))
        ttk.Label(search_frame, text="Buscar:").pack(side='left')
        search_var = tk.StringVar()
        search_entry = ttk.Entry(search_frame, textvariable=search_var)
        search_entry.pack(side='left', fill='x', expand=True, padx=5)

        list_frame = ttk.Frame(dialog)
        list_frame.pack(fill='both', expand=True, padx=10, pady=5)

        scrollbar_y = ttk.Scrollbar(list_frame, orient=VERTICAL)
        scrollbar_y.pack(side='right', fill='y')
        scrollbar_x = ttk.Scrollbar(list_frame, orient=HORIZONTAL)
        scrollbar_x.pack(side='bottom', fill='x')

        treeview = ttk.Treeview(list_frame, columns=("name",), show="headings", selectmode="browse",
                                yscrollcommand=scrollbar_y.set, xscrollcommand=scrollbar_x.set)
        treeview.heading("name", text="Nome do Serviço")
        treeview.column("name", width=450, stretch=tk.YES)
        treeview.pack(side='left', fill='both', expand=True)

        scrollbar_y.config(command=treeview.yview)
        scrollbar_x.config(command=treeview.xview)

        initial_selection_name = tab_instance.nome_servico.get()

        def _populate_treeview(query=""):
            for i in treeview.get_children():
                treeview.delete(i)

            filter_query = query.lower().strip()
            item_to_select_id = None

            for name in service_list:
                if name and (not filter_query or filter_query in name.lower()):
                    item_id = treeview.insert("", "end", values=(name,))
                    if name == initial_selection_name and not query:
                        item_to_select_id = item_id

            if item_to_select_id:
                treeview.selection_set(item_to_select_id)
                treeview.see(item_to_select_id)
            elif treeview.get_children():
                first_item = treeview.get_children()[0]
                treeview.selection_set(first_item)
                treeview.see(first_item)

        def on_confirm():
            selection = treeview.selection()
            if selection:
                selected_item_id = selection[0]
                selected_item_values = treeview.item(selected_item_id, "values")
                if selected_item_values:
                    tab_instance.set_selected_service(selected_item_values[0])
                    dialog.destroy()
                else:
                    if dialog.winfo_exists(): Messagebox.show_warning("Falha ao obter nome do serviço.", parent=dialog)
            else:
                if dialog.winfo_exists(): Messagebox.show_warning("Nenhum serviço selecionado.", parent=dialog)

        search_entry.bind("<KeyRelease>", lambda e: _populate_treeview(search_var.get()))
        treeview.bind("<Double-1>", lambda e: on_confirm())

        _populate_treeview()

        btn_frame = ttk.Frame(dialog)
        btn_frame.pack(pady=10)
        ttk.Button(btn_frame, text="Confirmar", command=on_confirm, bootstyle=SUCCESS).pack(side='left', padx=5)
        ttk.Button(btn_frame, text="Cancelar", command=dialog.destroy, bootstyle=DANGER).pack(side='left', padx=5)

        self.root.update_idletasks()
        ws, hs = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        w_dialog, h_dialog = dialog.winfo_reqwidth(), dialog.winfo_reqheight()
        if w_dialog <= 1: w_dialog = 500
        if h_dialog <= 1: h_dialog = 450
        x_pos = (ws / 2) - (w_dialog / 2)
        y_pos = (hs / 2) - (h_dialog / 2)
        dialog.geometry(f'{w_dialog}x{h_dialog}+{int(x_pos)}+{int(y_pos)}')

        search_entry.focus_set()
        dialog.wait_window()

    def _show_progress_dialog(self, title, message):
        progress_win = ttk.Toplevel(self.root)
        progress_win.title(str(title) if title else "Progresso")
        progress_win.geometry("300x100")
        progress_win.resizable(False, False)
        progress_win.transient(self.root)
        progress_win.grab_set()  # Torna a janela modal
        progress_win.protocol("WM_DELETE_WINDOW", lambda: None)  # Impede fechar pelo X

        ttk.Label(progress_win, text=str(message) if message else "Carregando...", bootstyle=PRIMARY).pack(pady=10,
                                                                                                           padx=10,
                                                                                                           fill='x')
        pb = ttk.Progressbar(progress_win, mode='indeterminate', length=280)
        pb.pack(pady=10, padx=10)
        pb.start(10)  # Intervalo em ms

        # Centralizar
        progress_win.update_idletasks()  # Atualiza para obter dimensões corretas
        try:
            root_x = self.root.winfo_x()
            root_y = self.root.winfo_y()
            root_width = self.root.winfo_width()
            root_height = self.root.winfo_height()

            win_width = progress_win.winfo_width()
            win_height = progress_win.winfo_height()

            if win_width <= 1: win_width = 300  # Fallback
            if win_height <= 1: win_height = 100  # Fallback

            x_pos = root_x + (root_width // 2) - (win_width // 2)
            y_pos = root_y + (root_height // 2) - (win_height // 2)

            # Garante que a janela de progresso não saia da tela
            screen_width = self.root.winfo_screenwidth()
            screen_height = self.root.winfo_screenheight()
            if x_pos + win_width > screen_width: x_pos = screen_width - win_width
            if y_pos + win_height > screen_height: y_pos = screen_height - win_height
            if x_pos < 0: x_pos = 0
            if y_pos < 0: y_pos = 0

            progress_win.geometry(f'{win_width}x{win_height}+{int(x_pos)}+{int(y_pos)}')
        except tk.TclError:  # Pode acontecer se a janela root estiver fechando
            logging.warning("TclError ao centralizar _show_progress_dialog.")
            try:  # Tenta centralizar na tela como fallback
                x_pos = (self.root.winfo_screenwidth() // 2) - (progress_win.winfo_reqwidth() // 2)
                y_pos = (self.root.winfo_screenheight() // 2) - (progress_win.winfo_reqheight() // 2)
                progress_win.geometry(f'+{int(x_pos)}+{int(y_pos)}')
            except Exception:
                pass  # Ignora se nem isso funcionar

        return progress_win, pb

    def set_status_from_thread(self, message):
        if hasattr(self, 'root') and self.root.winfo_exists() and hasattr(self, 'status_label_var'):
            self.root.after(0, lambda: self.status_label_var.set(str(message)[:250]))  # Limita tamanho da msg

    def show_messagebox_from_thread(self, boxtype, title, message):
        if hasattr(self, 'root') and self.root.winfo_exists():
            parent_to_use = self.root
            # Tenta usar a janela ativa se for um Toplevel (diálogo) sobre a root
            try:
                active_window = self.root.focus_get()
                if isinstance(active_window, tk.Toplevel) and active_window.winfo_exists():
                    # Verifica se o Toplevel é filho da root ou um progresso/about
                    if active_window.master == self.root or active_window.transient() == self.root:
                        parent_to_use = active_window
            except Exception:  # Caso focus_get() falhe ou retorne None
                pass

            self.root.after(0, lambda bt=boxtype, t=title, m=message, p=parent_to_use:
            getattr(Messagebox, f'show_{bt}')(m, t, parent=p if p.winfo_exists() else self.root))


# ==============================================================================
# BLOCO DE EXECUÇÃO PRINCIPAL
# ==============================================================================
def main():
    root_window = ttk.Window()
    app = None  # Inicializa app como None
    try:
        app = ServerRestarterApp(root_window)
        root_window.mainloop()
    except KeyboardInterrupt:
        logging.info("Interrupção por teclado. Encerrando...")
    except Exception as e_main:
        logging.critical(f"Erro fatal no loop principal: {e_main}", exc_info=True)
    finally:
        if app:  # Verifica se app foi instanciado
            app.shutdown_application()
        elif root_window.winfo_exists():  # Se app falhou no init mas root existe
            root_window.destroy()
        logging.info("Aplicação finalizada (bloco finally do main).")


def handle_unhandled_thread_exception(args):
    thread_name = args.thread.name if hasattr(args, 'thread') and hasattr(args.thread, 'name') else 'ThreadDesconhecida'
    logging.critical(f"EXCEÇÃO NÃO TRATADA NA THREAD '{thread_name}':",
                     exc_info=(args.exc_type, args.exc_value, args.exc_traceback))


if __name__ == '__main__':
    threading.excepthook = handle_unhandled_thread_exception

    if platform.system() == "Linux" and SYSTEMCTL_AVAILABLE:
        try:
            if os.geteuid() != 0:
                # Tenta elevar privilégios com pkexec para GUI
                # Isso é mais complexo e pode não funcionar em todos os ambientes.
                # A abordagem mais simples é instruir o usuário.
                print("INFO: Tentando elevar privilégios com pkexec (se disponível)...")
                logging.info("Tentando elevar privilégios com pkexec para GUI no Linux.")
                # Constrói o comando para pkexec
                # sys.executable é o interpretador python atual
                # sys.argv são os argumentos passados para o script
                pkexec_cmd = ["pkexec", sys.executable] + sys.argv
                try:
                    os.execvp(pkexec_cmd[0], pkexec_cmd)
                    # Se execvp retornar, significa que falhou.
                    print("ERRO: Falha ao elevar privilégios com pkexec.")
                    print("Por favor, execute o script como root: sudo python3 seu_script.py")
                    logging.error("Falha ao elevar privilégios com pkexec. Pedindo execução manual com sudo.")
                    sys.exit(1)
                except FileNotFoundError:
                    print(
                        "ERRO: pkexec não encontrado. Este script precisa ser executado como root (com sudo) para gerenciar serviços no Linux.")
                    print("Por favor, execute como: sudo python3 seu_script.py")
                    logging.error("pkexec não encontrado. Pedindo execução manual com sudo.")
                    sys.exit(1)
                except Exception as e_pkexec:
                    print(f"ERRO ao tentar usar pkexec: {e_pkexec}")
                    print("Por favor, execute o script como root: sudo python3 seu_script.py")
                    logging.error(f"Erro ao tentar usar pkexec: {e_pkexec}. Pedindo execução manual com sudo.")
                    sys.exit(1)
            else:
                logging.info("Script já está sendo executado como root no Linux.")
        except AttributeError:  # os.geteuid() não existe no Windows
            pass
        except Exception as e_priv_check:  # Qualquer outro erro na checagem de privilégios
            print(f"Erro ao verificar privilégios: {e_priv_check}")
            logging.error(f"Erro ao verificar privilégios no Linux: {e_priv_check}")
            # Não sair aqui, pode ser que SYSTEMCTL_AVAILABLE seja False e não precise de root

    if not PIL_AVAILABLE:
        logging.warning(
            "Pillow (PIL) não está instalado. Ícone da aplicação, imagem de fundo e funcionalidade de bandeja podem ser limitados ou desabilitados.")
        # Você pode optar por mostrar uma messagebox aqui também se for crítico
        # mas como é um warning, o logging pode ser suficiente.
    if platform.system() == "Windows" and not PYWIN32_AVAILABLE:
        logging.warning("pywin32 não instalado. Funcionalidades de serviço Windows desabilitadas.")
    if platform.system() == "Linux" and not SYSTEMCTL_AVAILABLE:
        logging.warning("'systemctl' não encontrado. Funcionalidades de serviço Linux desabilitadas.")

    main()