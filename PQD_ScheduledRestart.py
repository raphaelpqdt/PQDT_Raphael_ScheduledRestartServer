# ==============================================================================
# SEÇÃO DE IMPORTAÇÕES E CONFIGURAÇÕES GLOBAIS
# ==============================================================================
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
                    os_system == "Windows" or (os_system == "Linux" and SYSTEMCTL_AVAILABLE))

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
            self.initialize_from_config_vars()
            return

        os_system = platform.system()
        current_text_base = f"Serviço: {nome_servico_val}"
        worker = None

        if os_system == "Windows":
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
                result = subprocess.run(['systemctl', 'is-active', nome_tentativa], capture_output=True, text=True,
                                        timeout=5)
                status = result.stdout.strip()
                if status == "active": return "RUNNING"
                if status == "inactive": return "STOPPED"
                if status == "activating": return "START_PENDING"
                if status == "deactivating": return "STOP_PENDING"
                if status == "failed": return "ERROR"
                if result.returncode != 0:
                    status_result = subprocess.run(['systemctl', 'status', nome_tentativa], capture_output=True,
                                                   text=True, timeout=5)
                    if status_result.returncode == 4:
                        continue
                return "UNKNOWN"
            except (subprocess.TimeoutExpired, FileNotFoundError, Exception) as e:
                logging.error(f"Erro ao verificar serviço '{nome_tentativa}' no Linux: {e}", exc_info=True)
                return "ERROR"
        return "NOT_FOUND"

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
            self.log_tail_thread.join(timeout=2.0)
        self.log_tail_thread = None
        if self.log_monitor_thread and self.log_monitor_thread.is_alive() and self.log_monitor_thread != threading.current_thread():
            self.log_monitor_thread.join(timeout=2.0)
        self.log_monitor_thread = None
        if self.file_log_handle:
            try:
                self.file_log_handle.close()
            except Exception:
                pass
        self.file_log_handle = None

    def monitorar_log_continuamente_worker(self):
        pasta_raiz_monitorada = self.pasta_raiz.get()
        while not self._stop_event.is_set():
            if not os.path.isdir(pasta_raiz_monitorada):
                if self._stop_event.wait(10): break
                continue

            subpasta_recente = self._obter_subpasta_log_mais_recente(pasta_raiz_monitorada)
            if subpasta_recente:
                arquivo_log = os.path.join(subpasta_recente, 'console.log')
                if os.path.exists(arquivo_log) and arquivo_log != self.caminho_log_atual:
                    if self.log_tail_thread:
                        self._stop_event.set()
                        self.log_tail_thread.join()
                    self._stop_event.clear()

                    self.caminho_log_atual = arquivo_log
                    self.append_text_to_log_area(f"\n>>> Monitorando novo log: {self.caminho_log_atual}\n")
                    try:
                        self.file_log_handle = open(self.caminho_log_atual, 'r', encoding='latin-1', errors='replace')
                        self.file_log_handle.seek(0, os.SEEK_END)
                        self.log_tail_thread = threading.Thread(target=self.acompanhar_log_do_arquivo_worker,
                                                                daemon=True, name=f"LogTail-{self.nome}")
                        self.log_tail_thread.start()
                    except Exception as e:
                        logging.error(f"Erro ao iniciar tail para {self.caminho_log_atual}: {e}", exc_info=True)
            if self._stop_event.wait(5): break

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
            logging.error(f"Erro ao obter subpasta em '{pasta_raiz_logs}': {e}", exc_info=True)
            return None

    def acompanhar_log_do_arquivo_worker(self):
        trigger_message_to_find = self.trigger_log_message_var.get()
        while not self._stop_event.is_set():
            if self._paused:
                if self._stop_event.wait(0.5): break
                continue
            try:
                linha = self.file_log_handle.readline()
                if linha:
                    linha_strip = linha.strip()
                    if not self.filtro_var.get() or self.filtro_var.get().lower() in linha.lower():
                        self.append_text_to_log_area(linha)

                    if trigger_message_to_find and trigger_message_to_find in linha_strip:
                        logging.info(f"GATILHO DE REINÍCIO detectado. Linha: '{linha_strip}'.")
                        if self.auto_restart_on_trigger_var.get():
                            threading.Thread(target=self._delayed_restart_worker, daemon=True).start()
                else:
                    if self._stop_event.wait(0.2): break
            except Exception as e:
                if not self._stop_event.is_set():
                    logging.error(f"Erro ao acompanhar log: {e}", exc_info=True)
                break

    def _delayed_restart_worker(self):
        delay_s = self.restart_delay_after_trigger_var.get()
        self.append_text_to_log_area_threadsafe(f"Gatilho detectado. Aguardando {delay_s}s para reiniciar...\n")

        start_time = time.monotonic()
        while time.monotonic() - start_time < delay_s:
            if self._stop_event.is_set():
                return
            time.sleep(0.5)

        if not self._stop_event.is_set():
            self._executar_logica_reinicio_servico_efetivamente(is_scheduled_restart=False)

    def _executar_logica_reinicio_servico_efetivamente(self, is_scheduled_restart=False):
        tipo_reinicio_msg = "agendado" if is_scheduled_restart else "por gatilho de log"
        nome_servico = self.nome_servico.get()
        if not nome_servico:
            self.append_text_to_log_area_threadsafe("ERRO: Nome do serviço não configurado para reinício.\n")
            return

        success = self._operar_servico_com_delays(nome_servico, tipo_reinicio_msg)
        if self.app.root.winfo_exists():
            if success:
                self.app.show_messagebox_from_thread("info", "Servidor Reiniciado",
                                                     f"O serviço {nome_servico} foi reiniciado com sucesso.")
            else:
                self.app.show_messagebox_from_thread("error", "Falha no Reinício",
                                                     f"Ocorreu um erro ao reiniciar o serviço {nome_servico}.")
            if self.winfo_exists(): self.update_service_status_display()

    def _operar_servico_com_delays(self, nome_servico_a_gerenciar, tipo_reinicio_msg_log=""):
        os_system = platform.system()
        if os_system == "Windows":
            return self._operar_servico_com_delays_windows(nome_servico_a_gerenciar, tipo_reinicio_msg_log)
        elif os_system == "Linux":
            return self._operar_servico_com_delays_linux(nome_servico_a_gerenciar, tipo_reinicio_msg_log)
        else:
            self.append_text_to_log_area_threadsafe(f"ERRO: SO {os_system} não suportado.\n")
            return False

    def _operar_servico_com_delays_windows(self, nome_servico, tipo_reinicio=""):
        stop_delay_s = self.stop_delay_var.get()
        start_delay_s = self.start_delay_var.get()
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE
        log_prefix = f"Tab '{self.nome}' ({tipo_reinicio.strip()}) Win:"

        try:
            self.append_text_to_log_area_threadsafe(f"Parando serviço '{nome_servico}'...\n")
            subprocess.run(["sc", "stop", nome_servico], check=True, startupinfo=startupinfo, timeout=30)
            time.sleep(stop_delay_s)
            self.append_text_to_log_area_threadsafe(f"Iniciando serviço '{nome_servico}'...\n")
            subprocess.run(["sc", "start", nome_servico], check=True, startupinfo=startupinfo, timeout=30)
            time.sleep(start_delay_s)

            if self._verificar_status_servico_win(nome_servico) == "RUNNING":
                return True
        except Exception as e:
            logging.error(f"{log_prefix} Erro ao operar serviço: {e}", exc_info=True)
        return False

    def _operar_servico_com_delays_linux(self, nome_servico, tipo_reinicio=""):
        stop_delay_s = self.stop_delay_var.get()
        start_delay_s = self.start_delay_var.get()
        log_prefix = f"Tab '{self.nome}' ({tipo_reinicio.strip()}) Linux:"

        nome_servico_systemd = nome_servico
        if not nome_servico.endswith(".service"):
            nome_servico_systemd = f"{nome_servico}.service"

        try:
            self.append_text_to_log_area_threadsafe(f"Parando serviço '{nome_servico_systemd}'...\n")
            subprocess.run(['sudo', 'systemctl', 'stop', nome_servico_systemd], check=True, capture_output=True,
                           timeout=30)
            time.sleep(stop_delay_s)
            self.append_text_to_log_area_threadsafe(f"Iniciando serviço '{nome_servico_systemd}'...\n")
            subprocess.run(['sudo', 'systemctl', 'start', nome_servico_systemd], check=True, capture_output=True,
                           timeout=30)
            time.sleep(start_delay_s)

            if self._verificar_status_servico_linux(nome_servico_systemd) == "RUNNING":
                return True
        except Exception as e:
            err_output = e.stderr.decode(errors='replace').strip() if hasattr(e, 'stderr') and e.stderr else str(e)
            logging.error(f"{log_prefix} Erro ao operar serviço: {err_output}", exc_info=True)
        return False

    def append_text_to_log_area(self, texto):
        if not self.winfo_exists(): return
        try:
            self.app.root.after(0, self._append_text_to_log_area_gui_thread, texto)
        except Exception:
            pass

    def _append_text_to_log_area_gui_thread(self, texto):
        if not self.text_area_log.winfo_exists(): return
        self.text_area_log.config(state='normal')
        self.text_area_log.insert('end', texto)
        if self.auto_scroll_log_var.get():
            self.text_area_log.yview_moveto(1.0)
        self.text_area_log.config(state='disabled')

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
        if force_hide or (self.search_log_frame.winfo_ismapped() and not force_show):
            self.search_log_frame.pack_forget()
            self.text_area_log.tag_remove("search_match", "1.0", "end")
        else:
            self.search_log_frame.pack(fill='x', before=self.text_area_log, pady=(0, 2), padx=5)
            self.log_search_entry.focus_set()

    def _perform_log_search_internal(self, term, start_pos, direction_forward=True, wrap=True):
        if not term: return None
        self.text_area_log.tag_remove("search_match", "1.0", "end")
        count_var = tk.IntVar()
        pos = self.text_area_log.search(term, start_pos, backwards=(not direction_forward), count=count_var,
                                        nocase=True)
        if pos:
            end_pos = f"{pos}+{count_var.get()}c"
            self.text_area_log.tag_add("search_match", pos, end_pos)
            self.text_area_log.tag_config("search_match", background="yellow", foreground="black")
            self.text_area_log.see(pos)
            return end_pos if direction_forward else pos
        elif wrap:
            wrap_start = "1.0" if direction_forward else "end"
            return self._perform_log_search_internal(term, wrap_start, direction_forward, wrap=False)
        return None

    def _search_log_next(self, event=None):
        start_from = self.last_search_pos
        current_match = self.text_area_log.tag_ranges("search_match")
        if current_match: start_from = current_match[1]
        next_pos = self._perform_log_search_internal(self.log_search_var.get(), start_from)
        if next_pos: self.last_search_pos = next_pos

    def _search_log_prev(self, event=None):
        start_from = self.last_search_pos
        current_match = self.text_area_log.tag_ranges("search_match")
        if current_match: start_from = current_match[0]
        next_pos = self._perform_log_search_internal(self.log_search_var.get(), start_from, direction_forward=False)
        if next_pos: self.last_search_pos = next_pos


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
            self.style.theme_use("litera")
            self.config["theme"] = "litera"

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
        if self.bg_label:
            self.bg_label.lower()

        self.atualizar_log_sistema_periodicamente()
        self.root.bind("<Configure>", self._on_root_configure)
        self.root.protocol("WM_DELETE_WINDOW", self.minimize_to_tray_on_close)

        if PYSTRAY_AVAILABLE:
            self.setup_tray_icon()

    def _setup_background_image(self):
        if not PIL_AVAILABLE or not os.path.exists(BACKGROUND_IMAGE_PATH): return
        try:
            self.original_pil_bg_image = Image.open(BACKGROUND_IMAGE_PATH).convert("RGBA")
            self.bg_label = ttk.Label(self.root)
            self.bg_label.place(x=0, y=0, relwidth=1, relheight=1)
            self.root.update_idletasks()
            self._resize_background_image(self.root.winfo_width(), self.root.winfo_height())
        except Exception as e:
            logging.error(f"Erro ao carregar imagem de fundo: {e}", exc_info=True)
            self.original_pil_bg_image = None
            if self.bg_label: self.bg_label.destroy()
            self.bg_label = None

    def _on_root_configure(self, event):
        if event.widget == self.root and self.original_pil_bg_image:
            self._resize_background_image(event.width, event.height)

    def _resize_background_image(self, width, height):
        if not self.original_pil_bg_image or width <= 1 or height <= 1: return
        img_copy = self.original_pil_bg_image.copy()

        if BACKGROUND_ALPHA_MULTIPLIER < 1.0:
            alpha = img_copy.split()[3]
            alpha = alpha.point(lambda p: int(p * BACKGROUND_ALPHA_MULTIPLIER))
            img_copy.putalpha(alpha)

        img_aspect = img_copy.width / img_copy.height
        win_aspect = width / height

        if win_aspect > img_aspect:
            new_width = width
            new_height = int(new_width / img_aspect)
        else:
            new_height = height
            new_width = int(new_height * img_aspect)

        resized = img_copy.resize((new_width, new_height), Image.LANCZOS)
        self.bg_photo_image = ImageTk.PhotoImage(resized)
        self.bg_label.configure(image=self.bg_photo_image)

    def set_application_icon(self):
        if PIL_AVAILABLE and os.path.exists(ICON_PATH):
            try:
                if platform.system() == "Windows":
                    self.root.iconbitmap(default=ICON_PATH)
                else:
                    self.app_icon_tk = ImageTk.PhotoImage(Image.open(ICON_PATH))
                    self.root.iconphoto(True, self.app_icon_tk)
            except Exception as e:
                logging.error(f"Erro ao definir ícone da aplicação: {e}", exc_info=True)

    def _create_tray_image(self):
        if PIL_AVAILABLE and os.path.exists(ICON_PATH):
            try:
                return Image.open(ICON_PATH)
            except Exception:
                pass

        if PIL_AVAILABLE:
            image = Image.new('RGB', (64, 64), 'skyblue')
            draw = ImageDraw.Draw(image)
            draw.rectangle((0, 0, 64, 64), fill='skyblue')
            return image
        return None

    def setup_tray_icon(self):
        image = self._create_tray_image()
        if not image: return
        menu = (pystray.MenuItem('Mostrar', self.show_from_tray, default=True),
                pystray.MenuItem('Sair', self.shutdown_application_from_tray))
        self.tray_icon = pystray.Icon("ServerRestarter", image, "PredPy Server Restarter", menu)
        threading.Thread(target=self.tray_icon.run, daemon=True, name="TrayIconThread").start()

    def show_from_tray(self, icon=None, item=None):
        self.root.after(0, self.root.deiconify)

    def minimize_to_tray_on_close(self, event=None):
        if self.tray_icon and self.tray_icon.visible:
            self.root.withdraw()
        else:
            self.shutdown_application()

    def shutdown_application_from_tray(self, icon=None, item=None):
        self.shutdown_application()

    def shutdown_application(self):
        logging.info("Iniciando processo de encerramento...")
        self._app_stop_event.set()
        for srv_tab in self.servidores:
            srv_tab.stop_log_monitoring(from_tab_closure=True)
            srv_tab.stop_scheduler_thread(from_tab_closure=True)
        if self.config_changed:
            self._save_app_config_to_file()
        if self.tray_icon:
            self.tray_icon.stop()
        if self.root.winfo_exists():
            self.root.destroy()
        logging.info("Aplicação encerrada.")

    def get_current_servidor_tab_widget(self):
        try:
            if not self.main_notebook.winfo_exists() or not self.main_notebook.tabs(): return None
            selected_tab_id = self.main_notebook.select()
            if not selected_tab_id: return None
            widget = self.main_notebook.nametowidget(selected_tab_id)
            if isinstance(widget, ServidorTab): return widget
        except tk.TclError:
            return None
        return None

    def inicializar_servidores_das_configuracoes(self):
        servers_config_list = self.config.get("servers", [])
        if not servers_config_list:
            self.adicionar_servidor_tab("Servidor 1 (Padrão)")
        else:
            for srv_conf in servers_config_list:
                self.adicionar_servidor_tab(srv_conf.get("nome"), srv_conf, focus_new_tab=False)
        if self.servidores:
            self.main_notebook.select(self.servidores[0])

    def adicionar_servidor_tab(self, nome_sugerido=None, config_servidor=None, focus_new_tab=True):
        if nome_sugerido is None: nome_sugerido = f"Servidor {len(self.servidores) + 1}"
        servidor_tab_frame = ServidorTab(self.main_notebook, self, nome_sugerido, config_servidor)
        self.servidores.append(servidor_tab_frame)
        self.main_notebook.add(servidor_tab_frame, text=nome_sugerido)
        if focus_new_tab: self.main_notebook.select(servidor_tab_frame)
        self.mark_config_changed()

    def remover_servidor_atual(self):
        current_tab = self.get_current_servidor_tab_widget()
        if not current_tab: return
        if Messagebox.okcancel(f"Remover '{current_tab.nome}'?", f"Tem certeza que deseja remover este servidor?",
                               parent=self.root) == "OK":
            current_tab.stop_log_monitoring(True)
            current_tab.stop_scheduler_thread(True)
            self.servidores.remove(current_tab)
            self.main_notebook.forget(current_tab)
            current_tab.destroy()
            self.mark_config_changed()

    def renomear_servidor_atual(self):
        current_tab = self.get_current_servidor_tab_widget()
        if not current_tab: return
        novo_nome = simpledialog.askstring("Renomear", "Novo nome:", initialvalue=current_tab.nome, parent=self.root)
        if novo_nome and novo_nome.strip() and novo_nome != current_tab.nome:
            current_tab.nome = novo_nome
            self.main_notebook.tab(current_tab, text=novo_nome)
            self.mark_config_changed()

    def mark_config_changed(self):
        if not self.config_changed:
            self.config_changed = True
            if hasattr(self, 'file_menu'): self.file_menu.entryconfigure("Salvar Configuração", state="normal")

    def _load_app_config_from_file(self):
        try:
            if os.path.exists(self.config_file):
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
        except Exception:
            pass
        return {"theme": "darkly", "servers": []}

    def _save_app_config_to_file(self):
        config_data = {"theme": self.style.theme.name, "servers": [s.get_current_config() for s in self.servidores]}
        try:
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(config_data, f, indent=4)
            self.config_changed = False
            if hasattr(self, 'file_menu'): self.file_menu.entryconfigure("Salvar Configuração", state="disabled")
            self.set_status_from_thread("Configuração salva!")
        except Exception as e:
            self.show_messagebox_from_thread("error", "Erro ao Salvar", str(e))

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
            for srv_tab in list(self.servidores):
                srv_tab.stop_log_monitoring(from_tab_closure=True)
                srv_tab.stop_scheduler_thread(from_tab_closure=True)
                self.main_notebook.forget(srv_tab)
                srv_tab.destroy()
            self.servidores.clear()

            # Carregar nova configuração
            self.config_file = caminho
            self.config = loaded_config_data
            new_theme = self.config.get("theme", "darkly")

            try:
                self.style.theme_use(new_theme)
                self.config["theme"] = new_theme
            except tk.TclError:
                logging.warning(f"Tema '{new_theme}' do arquivo de config não encontrado. Usando 'litera'.")
                self.style.theme_use("litera")
                self.config["theme"] = "litera"

            self.inicializar_servidores_das_configuracoes()
            self.config_changed = False  # Acabou de carregar, então não há mudanças
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
        self.theme_var = tk.StringVar(value=self.style.theme_use())
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
                srv_tab.initialize_from_config_vars()
            self.config["theme"] = novo_tema
            self.mark_config_changed()
            logging.info(f"Tema alterado para: {novo_tema}")
            self.set_status_from_thread(f"Tema alterado para '{novo_tema}'.")
        except tk.TclError as e:
            logging.error(f"Erro ao trocar para o tema '{novo_tema}': {e}", exc_info=True)
            self.show_messagebox_from_thread("error", "Erro de Tema", f"Não foi possível aplicar o tema '{novo_tema}'.")

    def export_current_tab_logs(self):
        current_tab_widget = self.get_current_servidor_tab_widget()
        if not current_tab_widget:
            if self.main_notebook.index("current") != -1 and \
                    self.main_notebook.tab(self.main_notebook.select(), "text").startswith("Log do Sistema"):
                self._export_text_widget_content(self.system_log_text_area, "Log do Sistema do Restarter")
            else:
                self.show_messagebox_from_thread("info", "Exportar Logs",
                                                 "Selecione uma aba de servidor ou Log do Sistema.")
            return
        self._export_text_widget_content(current_tab_widget.text_area_log, f"Logs de '{current_tab_widget.nome}'")

    def _export_text_widget_content(self, text_widget, default_filename_part):
        caminho_arquivo = filedialog.asksaveasfilename(
            defaultextension=".txt",
            filetypes=[("Arquivos de Texto", "*.txt"), ("Todos", "*.*")],
            title=f"Exportar {default_filename_part}",
            initialfile=f"{default_filename_part.replace(' ', '_')}.txt"
        )
        if caminho_arquivo:
            try:
                if text_widget.winfo_exists():
                    with open(caminho_arquivo, 'w', encoding='utf-8') as f:
                        f.write(text_widget.get('1.0', 'end-1c'))
                    self.set_status_from_thread(f"Logs exportados para: {os.path.basename(caminho_arquivo)}")
                    self.show_messagebox_from_thread("info", "Exportação Concluída",
                                                     f"Logs exportados com sucesso para:\n{caminho_arquivo}")
            except Exception as e:
                logging.error(f"Erro ao exportar logs para {caminho_arquivo}: {e}", exc_info=True)
                self.show_messagebox_from_thread("error", "Erro na Exportação", f"Falha ao exportar logs:\n{e}")

    def show_about(self):
        # Cria uma nova janela (Toplevel) em vez de uma simples caixa de mensagem
        about_win = ttk.Toplevel(self.root)
        about_win.title("Sobre PredPy Server Restarter")
        about_win.geometry("480x420")  # Tamanho da sua janela personalizada
        about_win.resizable(False, False)
        about_win.transient(self.root)  # Mantém a janela "Sobre" na frente da principal
        about_win.grab_set()  # Bloqueia a interação com a janela principal

        # Frame principal para organizar o conteúdo
        frame = ttk.Frame(about_win, padding=20)
        frame.pack(fill='both', expand=True)

        # Adiciona os textos e widgets
        ttk.Label(frame, text="PQDT_Raphael Server Restarter", font="-size 16 -weight bold").pack(pady=(0, 10))
        ttk.Label(frame, text="Versão 1.1.1 (Bug fixes)", font="-size 10").pack()
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

        # Centraliza a janela "Sobre" na tela
        self.root.update_idletasks()
        ws = self.root.winfo_screenwidth()
        hs = self.root.winfo_screenheight()
        w_about, h_about = 480, 420
        x_pos = (ws / 2) - (w_about / 2)
        y_pos = (hs / 2) - (h_about / 2)
        about_win.geometry(f'{w_about}x{h_about}+{int(x_pos)}+{int(y_pos)}')

        # Espera a janela ser fechada para continuar
        about_win.wait_window()

    def create_status_bar(self):
        self.status_bar_frame = ttk.Frame(self.root)
        self.status_bar_frame.pack(side='bottom', fill='x', pady=(0, 2), padx=2)
        ttk.Separator(self.status_bar_frame).pack(side='top', fill='x')
        self.status_label_var = tk.StringVar(value="Pronto.")
        self.status_label = ttk.Label(self.status_bar_frame, textvariable=self.status_label_var, anchor='w')
        self.status_label.pack(side='left', fill='x', expand=True, padx=5)

    def atualizar_log_sistema_periodicamente(self):
        if self._app_stop_event.is_set(): return
        try:
            if os.path.exists('server_restarter.log'):
                with open('server_restarter.log', 'r', encoding='utf-8', errors='replace') as f:
                    content = f.read()
                self.system_log_text_area.config(state='normal')
                self.system_log_text_area.delete('1.0', 'end')
                self.system_log_text_area.insert('end', content)
                self.system_log_text_area.yview_moveto(1.0)
                self.system_log_text_area.config(state='disabled')
        except Exception:
            pass
        self.root.after(5000, self.atualizar_log_sistema_periodicamente)

    def iniciar_selecao_servico_para_aba(self, tab_instance, os_type):
        worker = None
        if os_type == "windows":
            worker = self._obter_servicos_worker_win
        elif os_type == "linux":
            worker = self._obter_servicos_worker_linux
        else:
            return

        progress_win, _ = self._show_progress_dialog(f"Carregando Serviços ({os_type.capitalize()})", "Aguarde...")
        threading.Thread(target=worker, args=(progress_win, tab_instance), daemon=True).start()

    def _obter_servicos_worker_win(self, progress_win, tab_instance):
        try:
            pythoncom.CoInitialize()
            wmi = win32com.client.GetObject('winmgmts:')
            services = sorted([s.Name for s in wmi.InstancesOf('Win32_Service') if s.AcceptStop])
            self.root.after(0, self._mostrar_dialogo_selecao_servico, services, progress_win, tab_instance, "Windows")
        except Exception as e:
            self.root.after(0, lambda: self.show_messagebox_from_thread("error", "Erro WMI", str(e)))
        finally:
            if progress_win.winfo_exists(): self.root.after(0, progress_win.destroy)
            pythoncom.CoUninitialize()

    def _obter_servicos_worker_linux(self, progress_win, tab_instance):
        try:
            cmd = ['systemctl', 'list-units', '--type=service', '--all', '--no-legend', '--no-pager']
            result = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=10)
            services = sorted([line.split()[0] for line in result.stdout.strip().split('\n') if line])
            self.root.after(0, self._mostrar_dialogo_selecao_servico, services, progress_win, tab_instance, "Linux")
        except Exception as e:
            self.root.after(0, lambda: self.show_messagebox_from_thread("error", "Erro systemctl", str(e)))
        finally:
            if progress_win.winfo_exists(): self.root.after(0, progress_win.destroy)

    def _mostrar_dialogo_selecao_servico(self, service_list, progress_win, tab_instance, os_type):
        if progress_win.winfo_exists(): progress_win.destroy()
        if not service_list:
            self.show_messagebox_from_thread("info", "Nenhum Serviço", "Nenhum serviço gerenciável encontrado.")
            return

        dialog = ttk.Toplevel(self.root)
        dialog.title(f"Selecionar Serviço ({os_type})")
        dialog.geometry("500x400")
        dialog.transient(self.root);
        dialog.grab_set()

        listbox = tk.Listbox(dialog)
        listbox.pack(fill='both', expand=True, padx=10, pady=5)
        for service in service_list: listbox.insert(tk.END, service)

        def on_confirm():
            selection = listbox.curselection()
            if selection:
                tab_instance.set_selected_service(listbox.get(selection[0]))
                dialog.destroy()

        ttk.Button(dialog, text="Confirmar", command=on_confirm, bootstyle=SUCCESS).pack(pady=5)
        dialog.wait_window()

    def _show_progress_dialog(self, title, message):
        progress_win = ttk.Toplevel(self.root)
        progress_win.title(title);
        progress_win.geometry("300x100");
        progress_win.transient(self.root);
        progress_win.grab_set()
        ttk.Label(progress_win, text=message).pack(pady=10)
        pb = ttk.Progressbar(progress_win, mode='indeterminate', length=280);
        pb.pack();
        pb.start()
        return progress_win, pb

    def set_status_from_thread(self, message):
        if self.root.winfo_exists(): self.root.after(0, lambda: self.status_label_var.set(str(message)))

    def show_messagebox_from_thread(self, boxtype, title, message):
        if self.root.winfo_exists():
            self.root.after(0, lambda: getattr(Messagebox, f'show_{boxtype}')(message, title, parent=self.root))


# ==============================================================================
# BLOCO DE EXECUÇÃO PRINCIPAL
# ==============================================================================
def main():
    root_window = ttk.Window()
    app = ServerRestarterApp(root_window)
    try:
        root_window.mainloop()
    except KeyboardInterrupt:
        logging.info("Interrupção por teclado. Encerrando...")
    finally:
        if 'app' in locals() and app:
            app.shutdown_application()


def handle_unhandled_thread_exception(args):
    thread_name = args.thread.name if hasattr(args.thread, 'thread') else 'ThreadDesconhecida'
    logging.critical(f"EXCEÇÃO NÃO TRATADA NA THREAD '{thread_name}':",
                     exc_info=(args.exc_type, args.exc_value, args.exc_traceback))


if __name__ == '__main__':
    threading.excepthook = handle_unhandled_thread_exception

    if not PIL_AVAILABLE:
        logging.warning("Pillow (PIL) não está instalado.")
    if platform.system() == "Windows" and not PYWIN32_AVAILABLE:
        logging.warning("pywin32 não instalado. Funcionalidades de serviço Windows desabilitadas.")
    if platform.system() == "Linux" and not SYSTEMCTL_AVAILABLE:
        logging.warning("'systemctl' não encontrado. Funcionalidades de serviço Linux desabilitadas.")

    main()