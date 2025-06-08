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
except ImportError:
    PIL_AVAILABLE = False
    PYSTRAY_AVAILABLE = False
    logging.warning(
        "Pillow (PIL) ou pystray não encontrados. Funcionalidades de ícone avançado, imagem de fundo e bandeja estarão limitadas/desabilitadas.")
    pystray = None

try:
    import win32com.client
    import pythoncom

    PYWIN32_AVAILABLE = True
except ImportError:
    PYWIN32_AVAILABLE = False

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - [%(threadName)s] - %(module)s.%(funcName)s:%(lineno)d - %(message)s',
    filename='server_restarter.log',
    filemode='a',
    encoding='utf-8'
)

# --- Constantes para Ícones e Imagens ---
ICON_FILENAME = "predpy.ico"  # Atualizado para o novo ícone
BACKGROUND_IMAGE_FILENAME = "predpy.png"  # Nova imagem de fundo
BACKGROUND_ALPHA_MULTIPLIER = 0.15  # Quão transparente o fundo deve ser (0.0 = invisível, 1.0 = opaco)


def resource_path(relative_path):
    try:
        base_path = sys._MEIPASS
    except AttributeError:
        base_path = os.path.abspath(os.path.dirname(__file__))
    return os.path.join(base_path, relative_path)


ICON_PATH = resource_path(ICON_FILENAME)
BACKGROUND_IMAGE_PATH = resource_path(BACKGROUND_IMAGE_FILENAME)


# ... (O restante da classe ServidorTab permanece inalterado) ...
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

        vars_to_trace_str = [
            self.pasta_raiz, self.nome_servico, self.filtro_var,
            self.trigger_log_message_var
        ]
        vars_to_trace_bool = [self.auto_restart_on_trigger_var, self.auto_scroll_log_var]
        vars_to_trace_int = [self.stop_delay_var, self.start_delay_var, self.restart_delay_after_trigger_var]

        for var in vars_to_trace_str:
            var.trace_add("write", lambda *args, v=var: self._value_changed(v.get()))
        for var in vars_to_trace_bool:
            var.trace_add("write", lambda *args, v=var: self._value_changed(v.get()))
        for var in vars_to_trace_int:
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
        ToolTip(self.selecionar_btn,
                text="Seleciona a pasta raiz onde os logs do servidor são armazenados.")

        self.servico_btn = ttk.Button(path_buttons_frame, text="Serviço Win", command=self.selecionar_servico,
                                      bootstyle=SECONDARY)
        self.servico_btn.pack(side='left', padx=2, pady=2)
        ToolTip(self.servico_btn, text="Seleciona o serviço do Windows associado ao servidor.")
        if not PYWIN32_AVAILABLE: self.servico_btn.config(state=DISABLED)

        self.refresh_servico_status_btn = ttk.Button(path_buttons_frame, text="↻",
                                                     command=self.update_service_status_display,
                                                     bootstyle=(TOOLBUTTON, LIGHT), width=2)
        self.refresh_servico_status_btn.pack(side='left', padx=(0, 2), pady=2)
        ToolTip(self.refresh_servico_status_btn, text="Atualizar status do serviço selecionado.")
        if not PYWIN32_AVAILABLE: self.refresh_servico_status_btn.config(state=DISABLED)

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
        ToolTip(self.auto_restart_check,
                "Se marcado, o servidor será reiniciado após o gatilho de log ser detectado.")

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
        if var.get():  # Checkbox marcado
            if hour_str not in self.scheduled_restarts_list:
                self.scheduled_restarts_list.append(hour_str)
                logging.info(f"Tab '{self.nome}': Agendamento pré-definido ADICIONADO: {hour_str}")  # Log modificado
        else:  # Checkbox desmarcado
            if hour_str in self.scheduled_restarts_list:
                self.scheduled_restarts_list.remove(hour_str)
                logging.info(f"Tab '{self.nome}': Agendamento pré-definido REMOVIDO: {hour_str}")  # Log modificado

        self.scheduled_restarts_list = sorted(list(set(self.scheduled_restarts_list)))
        logging.info(
            f"Tab '{self.nome}' _toggle_predefined_schedule: Lista de agendamentos atual: {self.scheduled_restarts_list}")  # <--- NOVO LOG
        self._value_changed()

    def _add_custom_schedule(self):
        time_str = self.custom_schedule_entry_var.get().strip()
        if not time_str:
            self.app.show_messagebox_from_thread("warning", "Horário Inválido",
                                                 "O campo de horário não pode estar vazio.")
            return

        if not re.fullmatch(r"([01]\d|2[0-3]):([0-5]\d)", time_str):
            self.app.show_messagebox_from_thread("error", "Formato Inválido",
                                                 f"Horário '{time_str}' inválido. Use o formato HH:MM (ex: 08:30, 22:15).")
            return

        if time_str in self.scheduled_restarts_list:
            self.app.show_messagebox_from_thread("info", "Horário Duplicado",
                                                 f"O horário '{time_str}' já está na lista.")
            return

        self.scheduled_restarts_list.append(time_str)
        self.scheduled_restarts_list = sorted(list(set(self.scheduled_restarts_list)))
        logging.info(
            f"Tab '{self.nome}': Agendamento personalizado adicionado: {time_str}. Lista atual: {self.scheduled_restarts_list}")  # <--- LOG ATUALIZADO/NOVO
        self._update_scheduled_restarts_ui_from_list()
        self.custom_schedule_entry_var.set("")
        self._value_changed()

    def _remove_selected_custom_schedule(self):
        selection_indices = self.custom_schedules_listbox.curselection()
        if not selection_indices:
            self.app.show_messagebox_from_thread("warning", "Nenhuma Seleção",
                                                 "Selecione um horário personalizado para remover.")
            return

        selected_time_str = self.custom_schedules_listbox.get(selection_indices[0])

        if selected_time_str in self.scheduled_restarts_list:
            self.scheduled_restarts_list.remove(selected_time_str)
            logging.info(
                f"Tab '{self.nome}': Agendamento personalizado removido: {selected_time_str}. Lista atual: {self.scheduled_restarts_list}")  # <--- LOG ATUALIZADO/NOVO
            self._update_scheduled_restarts_ui_from_list()
            self._value_changed()
        else:
            logging.warning(
                f"Tab '{self.nome}': Tentativa de remover horário '{selected_time_str}' que não está na lista interna. Lista atual: {self.scheduled_restarts_list}")  # <--- LOG ATUALIZADO/NOVO
            self._update_scheduled_restarts_ui_from_list()

    def start_scheduler_thread(self):
        if self.scheduler_thread and self.scheduler_thread.is_alive():
            logging.warning(f"Tab '{self.nome}': Tentativa de iniciar scheduler já em execução.")
            return
        self._scheduler_stop_event.clear()
        self.scheduler_thread = threading.Thread(
            target=self._scheduler_worker, daemon=True, name=f"Scheduler-{self.nome}"
        )
        self.scheduler_thread.start()
        logging.info(f"Tab '{self.nome}': Scheduler de reinícios agendados iniciado.")

    def stop_scheduler_thread(self, from_tab_closure=False):
        thread_name = threading.current_thread().name
        logging.debug(f"Tab '{self.nome}' [{thread_name}]: Chamada para stop_scheduler_thread.")
        self._scheduler_stop_event.set()
        if self.scheduler_thread and self.scheduler_thread.is_alive() and self.scheduler_thread != threading.current_thread():
            self.scheduler_thread.join(timeout=2.0)
        self.scheduler_thread = None
        if not from_tab_closure:
            logging.info(f"Tab '{self.nome}' [{thread_name}]: stop_scheduler_thread completado.")

    def _scheduler_worker(self):
        # logging.info(f"Tab '{self.nome}': Thread _scheduler_worker iniciada.") # Esta linha já existe e é boa, mas a de baixo é para um teste mais granular

        # ---> ADICIONE ESTA LINHA EXATAMENTE AQUI <---
        logging.debug(f"Tab '{self.nome}': _scheduler_worker EXECUTANDO - INÍCIO DO MÉTODO.")

        while not self._scheduler_stop_event.is_set():
            try:
                # Logs para depuração (descomentados ou adicionados)
                current_time_obj = datetime.now()  # Movido para dentro do try para o caso de datetime.now() falhar (improvável)
                current_time_str_hh_mm = current_time_obj.strftime("%H:%M")

                logging.debug(
                    f"Tab '{self.nome}' Scheduler Tick: Hora Atual={current_time_str_hh_mm}, Agendamentos={self.scheduled_restarts_list}, ÚltimoProcessadoMinuto={self.last_scheduled_restart_processed_time_str}, ServiçoCfg='{self.nome_servico.get()}'")

                if self.last_scheduled_restart_processed_time_str != current_time_str_hh_mm:
                    logging.debug(
                        f"Tab '{self.nome}' Scheduler: Resetando last_scheduled_restart_processed_time_str (era {self.last_scheduled_restart_processed_time_str}, agora é novo minuto {current_time_str_hh_mm}).")
                    self.last_scheduled_restart_processed_time_str = None

                if not self.scheduled_restarts_list:
                    if self._scheduler_stop_event.wait(20): break
                    continue

                service_to_restart = self.nome_servico.get()
                if not service_to_restart:
                    if self._scheduler_stop_event.wait(20): break
                    continue

                should_restart_now = (current_time_str_hh_mm in self.scheduled_restarts_list and
                                      self.last_scheduled_restart_processed_time_str != current_time_str_hh_mm)

                if should_restart_now:
                    logging.info(
                        f"Tab '{self.nome}' Scheduler: CONDIÇÃO DE REINÍCIO AGENDADO ATINGIDA! Hora: {current_time_str_hh_mm}. Serviço: '{service_to_restart}'. Iniciando processo de reinício.")
                    self.append_text_to_log_area_threadsafe(
                        f"--- REINÍCIO AGENDADO ({current_time_str_hh_mm}) DO SERVIÇO '{service_to_restart}' INICIADO ---\n"
                    )
                    self.app.set_status_from_thread(f"'{self.nome}': Reinício agendado às {current_time_str_hh_mm}...")

                    threading.Thread(
                        target=self._executar_logica_reinicio_servico_efetivamente,
                        args=(True,),
                        daemon=True,
                        name=f"ScheduledRestartExec-{self.nome}-{current_time_str_hh_mm}"
                    ).start()

                    self.last_scheduled_restart_processed_time_str = current_time_str_hh_mm
                    logging.info(
                        f"Tab '{self.nome}' Scheduler: Horário {current_time_str_hh_mm} marcado como processado para este minuto.")
                # else: # Logs de depuração opcionais
                # if not (current_time_str_hh_mm in self.scheduled_restarts_list) and self.scheduled_restarts_list: # Só loga se a lista não estiver vazia
                #    logging.debug(f"Tab '{self.nome}' Scheduler: Hora {current_time_str_hh_mm} NÃO está na lista de agendamentos: {self.scheduled_restarts_list}")
                # elif self.last_scheduled_restart_processed_time_str == current_time_str_hh_mm and self.scheduled_restarts_list: # Só loga se a lista não estiver vazia
                #    logging.debug(f"Tab '{self.nome}' Scheduler: Hora {current_time_str_hh_mm} JÁ FOI PROCESSADA neste minuto.")


            except Exception as e_scheduler:
                logging.error(f"Tab '{self.nome}': Erro no _scheduler_worker: {e_scheduler}", exc_info=True)
                self.append_text_to_log_area_threadsafe(f"ERRO CRÍTICO NO SCHEDULER: {e_scheduler}\n")

            if self._scheduler_stop_event.wait(15):
                break

        logging.info(
            f"Tab '{self.nome}': Thread _scheduler_worker encerrada (evento de parada: {self._scheduler_stop_event.is_set()}).")  # Adicionado info sobre o evento

    # Dentro de ServidorTab
    def initialize_from_config_vars(self):
        default_fg = "black"
        try:
            if hasattr(self.app.style, 'colors') and self.app.style.colors and hasattr(self.app.style.colors, 'fg'):
                default_fg = self.app.style.colors.fg
        except Exception:
            pass

        pasta_raiz_val = self.pasta_raiz.get()
        if pasta_raiz_val and os.path.isdir(pasta_raiz_val):
            self.append_text_to_log_area(f">>> Pasta de logs configurada: {pasta_raiz_val}\n")
            self.log_folder_path_label_var.set(f"Pasta Logs: {os.path.basename(pasta_raiz_val)}")
            self.log_folder_path_label.config(foreground="green")
            self.start_log_monitoring()  # Inicia/reinicia monitor de LOGS
        elif pasta_raiz_val:
            self.log_folder_path_label_var.set(f"Pasta Logs (INVÁLIDA): {os.path.basename(pasta_raiz_val)}")
            self.log_folder_path_label.config(foreground="red")
            # Se a pasta é inválida, o monitor de logs não inicia, mas o scheduler PODE rodar
            # se um serviço estiver configurado. Considerar se o scheduler deve depender da pasta de logs.
            # Por ora, vamos mantê-los independentes.
        else:
            self.log_folder_path_label_var.set("Pasta Logs: Nenhuma")
            self.log_folder_path_label.config(foreground=default_fg)

        nome_servico_val = self.nome_servico.get()
        if nome_servico_val and PYWIN32_AVAILABLE:
            self.update_service_status_display()
        elif not PYWIN32_AVAILABLE:
            self.servico_label_var.set("Serviço: N/A (pywin32)")
            self.servico_label_widget.config(foreground="gray")
        else:
            self.servico_label_var.set("Serviço: Nenhum")
            self.servico_label_widget.config(foreground=default_fg if default_fg != "black" else "orange")

        self._update_scheduled_restarts_ui_from_list()

        # ---> ADICIONE/GARANTA ESTA LINHA <---
        self.start_scheduler_thread()  # Garante que o scheduler seja iniciado/reiniciado aqui

    def selecionar_pasta(self):
        pasta_selecionada = filedialog.askdirectory(
            title=f"Selecione a pasta de logs para '{self.nome}'",
            initialdir=self.pasta_raiz.get() or os.path.expanduser("~")
        )
        if pasta_selecionada:
            if self.pasta_raiz.get() != pasta_selecionada:
                logging.info(f"Tab '{self.nome}': Pasta de logs alterada para '{pasta_selecionada}'")
                self.stop_log_monitoring()
                self.pasta_raiz.set(pasta_selecionada)
                self.initialize_from_config_vars()
            else:
                self.append_text_to_log_area(f">>> Pasta de logs já selecionada: {pasta_selecionada}\n")

    def selecionar_servico(self):
        if not PYWIN32_AVAILABLE:
            self.app.show_messagebox_from_thread("error", "Funcionalidade Indisponível",
                                                 "A biblioteca pywin32 é necessária para listar e gerenciar serviços do Windows.")
            return
        self.app.iniciar_selecao_servico_para_aba(self)

    def set_selected_service(self, service_name):
        if self.nome_servico.get() != service_name:
            self.nome_servico.set(service_name)
            self.update_service_status_display()
            self.app.set_status_from_thread(f"Serviço '{service_name}' selecionado para '{self.nome}'.")
            logging.info(f"Tab '{self.nome}': Serviço selecionado: {service_name}")

    def update_service_status_display(self):
        if not PYWIN32_AVAILABLE:
            self.servico_label_var.set("Serviço: N/A (pywin32)")
            fg_color = "gray"
            try:
                if hasattr(self.app.style, 'colors') and self.app.style.colors:
                    fg_color = self.app.style.colors.get('disabled', 'gray')
            except Exception:
                pass
            self.servico_label_widget.config(foreground=fg_color)
            return

        nome_servico_val = self.nome_servico.get()
        if nome_servico_val:
            current_text_base = f"Serviço: {nome_servico_val}"
            self.servico_label_var.set(f"{current_text_base} (Verificando...)")
            self.servico_label_widget.config(foreground="blue")
            threading.Thread(
                target=self._get_and_display_service_status_thread_worker,
                args=(nome_servico_val, current_text_base),
                daemon=True,
                name=f"ServiceStatusCheck-{self.nome}"
            ).start()
        else:
            self.servico_label_var.set("Serviço: Nenhum")
            default_fg = "orange"
            try:
                if hasattr(self.app.style, 'colors') and self.app.style.colors and hasattr(self.app.style.colors, 'fg'):
                    default_fg = self.app.style.colors.fg if self.app.style.colors.fg != "black" else "orange"
            except Exception:
                pass
            self.servico_label_widget.config(foreground=default_fg)

    def _get_and_display_service_status_thread_worker(self, service_name_to_check, base_text_for_label):
        status = self._verificar_status_servico_win(service_name_to_check)
        status_map_colors = {
            "RUNNING": ("(Rodando)", "green"), "STOPPED": ("(Parado)", "red"),
            "START_PENDING": ("(Iniciando...)", "blue"), "STOP_PENDING": ("(Parando...)", "blue"),
            "NOT_FOUND": ("(Não encontrado!)", "orange"), "ERROR": ("(Erro ao verificar!)", "red"),
            "UNKNOWN": ("(Desconhecido)", "gray")
        }
        display_status_text, color = status_map_colors.get(status, ("(Status ?)", "gray"))
        if self.app.root.winfo_exists() and self.winfo_exists():
            self.app.root.after(0, lambda: (
                self.servico_label_var.set(f"{base_text_for_label} {display_status_text}"),
                self.servico_label_widget.config(foreground=color) if self.servico_label_widget.winfo_exists() else None
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

    def start_log_monitoring(self):
        if self.log_monitor_thread and self.log_monitor_thread.is_alive():
            logging.warning(f"Tab '{self.nome}': Tentativa de iniciar monitoramento de log já em execução.")
            return
        if not self.pasta_raiz.get() or not os.path.isdir(self.pasta_raiz.get()):
            self.append_text_to_log_area(
                f"AVISO: Pasta de logs '{self.pasta_raiz.get()}' inválida. Monitoramento não iniciado.\n")
            return
        self._stop_event.clear()
        self.log_monitor_thread = threading.Thread(
            target=self.monitorar_log_continuamente_worker, daemon=True, name=f"LogMonitor-{self.nome}"
        )
        self.log_monitor_thread.start()
        logging.info(f"Tab '{self.nome}': Monitoramento de logs iniciado para pasta '{self.pasta_raiz.get()}'.")

    def stop_log_monitoring(self, from_tab_closure=False):
        thread_name = threading.current_thread().name
        logging.debug(f"Tab '{self.nome}' [{thread_name}]: Chamada para stop_log_monitoring.")
        self.stop_scheduler_thread(from_tab_closure=from_tab_closure)  # MODIFICADO: Parar scheduler
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
            except Exception as e:
                logging.error(f"Tab '{self.nome}': Erro ao fechar handle do log: {e}", exc_info=True)
            finally:
                self.file_log_handle = None
        self.caminho_log_atual = None
        self.pasta_log_detectada_atual = None
        if not from_tab_closure:
            logging.info(f"Tab '{self.nome}' [{thread_name}]: stop_log_monitoring completado.")

    def monitorar_log_continuamente_worker(self):
        thread_name = threading.current_thread().name
        pasta_raiz_monitorada = self.pasta_raiz.get()
        self.app.set_status_from_thread(
            f"'{self.nome}': Monitorando: {os.path.basename(pasta_raiz_monitorada) if pasta_raiz_monitorada else 'N/A'}")
        logging.info(f"[{thread_name}] Tab '{self.nome}': Iniciando monitoramento: {pasta_raiz_monitorada}")

        while not self._stop_event.is_set():
            if not pasta_raiz_monitorada or not os.path.isdir(pasta_raiz_monitorada):
                if pasta_raiz_monitorada:
                    logging.warning(
                        f"[{thread_name}] Tab '{self.nome}': Pasta logs '{pasta_raiz_monitorada}' inválida.")
                if self._stop_event.wait(10): break
                pasta_raiz_monitorada = self.pasta_raiz.get()
                continue
            try:
                subpasta_log_recente = self._obter_subpasta_log_mais_recente(pasta_raiz_monitorada)
                if not subpasta_log_recente:
                    if self.caminho_log_atual:
                        self.append_text_to_log_area(
                            f"AVISO: Nenhuma subpasta log em '{pasta_raiz_monitorada}'. Verificando...\n")
                        self.caminho_log_atual = None
                        if self.log_tail_thread and self.log_tail_thread.is_alive(): self.log_tail_thread.join(
                            timeout=1.0)
                        if self.file_log_handle: self.file_log_handle.close(); self.file_log_handle = None
                    if self._stop_event.wait(5): break
                    continue
                novo_arquivo_log_path_potencial = os.path.join(subpasta_log_recente, 'console.log')
                if os.path.exists(novo_arquivo_log_path_potencial) and \
                        (novo_arquivo_log_path_potencial != self.caminho_log_atual or not self.caminho_log_atual):
                    logging.info(
                        f"[{thread_name}] Tab '{self.nome}': Novo log detectado: '{novo_arquivo_log_path_potencial}'")
                    self.append_text_to_log_area(f"\n>>> Monitorando novo log: {novo_arquivo_log_path_potencial}\n")
                    if self.log_tail_thread and self.log_tail_thread.is_alive():
                        self.log_tail_thread.join(timeout=1.5)
                    if self.file_log_handle:
                        try:
                            self.file_log_handle.close()
                        except Exception:
                            pass
                        finally:
                            self.file_log_handle = None
                    self.caminho_log_atual = novo_arquivo_log_path_potencial
                    self.pasta_log_detectada_atual = subpasta_log_recente
                    novo_fh_temp = None
                    try:
                        novo_fh_temp = open(self.caminho_log_atual, 'r', encoding='latin-1', errors='replace')
                        novo_fh_temp.seek(0, os.SEEK_END)
                        self.file_log_handle = novo_fh_temp
                        if self.app.root.winfo_exists() and self.winfo_exists():
                            log_file_display_name = os.path.join(os.path.basename(self.pasta_log_detectada_atual),
                                                                 os.path.basename(self.caminho_log_atual))
                            self.app.root.after(0, lambda p=log_file_display_name: self.log_label_display.config(
                                text=f"LOG: {p}") if self.log_label_display.winfo_exists() else None)
                            self.app.set_status_from_thread(f"'{self.nome}': Monitorando: {log_file_display_name}")
                        self.log_tail_thread = threading.Thread(
                            target=self.acompanhar_log_do_arquivo_worker, args=(self.caminho_log_atual,), daemon=True,
                            name=f"LogTail-{self.nome}-{os.path.basename(self.caminho_log_atual)}"
                        )
                        self.log_tail_thread.start()
                    except FileNotFoundError:
                        logging.error(
                            f"[{thread_name}] Tab '{self.nome}': Arquivo {self.caminho_log_atual} não encontrado.")
                        if novo_fh_temp: novo_fh_temp.close()
                        self.file_log_handle = None;
                        self.caminho_log_atual = None
                    except Exception as e_open_new:
                        logging.error(
                            f"[{thread_name}] Tab '{self.nome}': Erro ao abrir/acompanhar {self.caminho_log_atual}: {e_open_new}",
                            exc_info=True)
                        if novo_fh_temp: novo_fh_temp.close()
                        self.file_log_handle = None;
                        self.caminho_log_atual = None
                elif self.caminho_log_atual and not os.path.exists(self.caminho_log_atual):
                    logging.warning(f"[{thread_name}] Tab '{self.nome}': Log {self.caminho_log_atual} não existe mais.")
                    self.append_text_to_log_area(f"AVISO: Log {self.caminho_log_atual} não encontrado. Procurando...\n")
                    if self.log_tail_thread and self.log_tail_thread.is_alive(): self.log_tail_thread.join(timeout=1.0)
                    if self.file_log_handle:
                        try:
                            self.file_log_handle.close()
                        except Exception:
                            pass
                    self.file_log_handle = None;
                    self.caminho_log_atual = None
            except Exception as e_monitor_loop:
                logging.error(f"[{thread_name}] Tab '{self.nome}': Erro no loop de monitoramento: {e_monitor_loop}",
                              exc_info=True)
                self.append_text_to_log_area(f"ERRO CRÍTICO AO MONITORAR LOGS: {e_monitor_loop}\n")
            if self._stop_event.wait(5): break
        logging.info(f"[{thread_name}] Tab '{self.nome}': Thread de monitoramento contínuo encerrada.")
        if self.log_tail_thread and self.log_tail_thread.is_alive(): self.log_tail_thread.join(timeout=1.0)
        if self.file_log_handle:
            try:
                self.file_log_handle.close()
            except Exception:
                pass
        self.file_log_handle = None;
        self.caminho_log_atual = None

    def _obter_subpasta_log_mais_recente(self, pasta_raiz_logs):
        if not pasta_raiz_logs or not os.path.isdir(pasta_raiz_logs): return None
        try:
            entradas = os.listdir(pasta_raiz_logs)
            log_folder_pattern = re.compile(r"^logs_\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}$")
            subpastas_log_validas = [os.path.join(pasta_raiz_logs, nome_entrada) for nome_entrada in entradas
                                     if os.path.isdir(
                    os.path.join(pasta_raiz_logs, nome_entrada)) and log_folder_pattern.match(nome_entrada)]
            if not subpastas_log_validas: return None
            return max(subpastas_log_validas, key=os.path.getmtime)
        except FileNotFoundError:
            logging.warning(f"Tab '{self.nome}': Pasta raiz '{pasta_raiz_logs}' não encontrada.");
            self.pasta_raiz.set("");
            return None
        except PermissionError:
            logging.error(f"Tab '{self.nome}': Permissão negada ao acessar '{pasta_raiz_logs}'.");
            self.pasta_raiz.set("");
            return None
        except Exception as e:
            logging.error(f"Tab '{self.nome}': Erro ao obter subpasta em '{pasta_raiz_logs}': {e}", exc_info=True);
            return None

    def acompanhar_log_do_arquivo_worker(self, caminho_log_designado_para_esta_thread):
        thread_name = threading.current_thread().name
        logging.info(
            f"[{thread_name}] Tab '{self.nome}': Iniciando acompanhamento de: {caminho_log_designado_para_esta_thread}")

        if self._stop_event.is_set():
            logging.info(
                f"[{thread_name}] Tab '{self.nome}': _stop_event setado. Encerrando para {caminho_log_designado_para_esta_thread}.")
            return
        if not self.file_log_handle or self.file_log_handle.closed:
            logging.error(
                f"[{thread_name}] Tab '{self.nome}': ERRO - file_log_handle NULO/FECHADO para '{caminho_log_designado_para_esta_thread}'.")
            return
        try:
            if os.path.normpath(self.file_log_handle.name) != os.path.normpath(caminho_log_designado_para_esta_thread):
                logging.warning(
                    f"[{thread_name}] Tab '{self.nome}': DESCOMPASSO DE HANDLE! Thread para '{caminho_log_designado_para_esta_thread}' mas handle é '{self.file_log_handle.name}'. Encerrando.")
                return
        except Exception as e_check_init_handle:
            logging.error(
                f"[{thread_name}] Tab '{self.nome}': Exceção na verificação handle para '{caminho_log_designado_para_esta_thread}': {e_check_init_handle}. Encerrando.")
            return

        trigger_message_to_find = self.trigger_log_message_var.get()
        if not trigger_message_to_find:
            logging.warning(
                f"[{thread_name}] Tab '{self.nome}': Mensagem de gatilho vazia para '{caminho_log_designado_para_esta_thread}'.")
        logging.debug(
            f"[{thread_name}] Tab '{self.nome}': Mensagem gatilho para '{caminho_log_designado_para_esta_thread}': '{trigger_message_to_find}'")

        while not self._stop_event.is_set():
            if self._paused:
                if self._stop_event.wait(0.5): break
                continue
            if not self.file_log_handle or self.file_log_handle.closed:
                logging.warning(
                    f"[{thread_name}] Tab '{self.nome}': file_log_handle NULO/FECHADO NO LOOP para '{caminho_log_designado_para_esta_thread}'.")
                break
            try:
                if os.path.normpath(self.file_log_handle.name) != os.path.normpath(
                        caminho_log_designado_para_esta_thread):
                    logging.warning(
                        f"[{thread_name}] Tab '{self.nome}': MUDANÇA DE HANDLE DETECTADA! Encerrando para '{caminho_log_designado_para_esta_thread}'.")
                    break
            except Exception as e_check_loop_consistency:
                logging.error(
                    f"[{thread_name}] Tab '{self.nome}': Erro consistência handle no loop para '{caminho_log_designado_para_esta_thread}': {e_check_loop_consistency}.")
                break
            try:
                linha = self.file_log_handle.readline()
                if linha:
                    linha_strip = linha.strip()
                    filtro_atual = self.filtro_var.get().strip().lower()
                    if not filtro_atual or filtro_atual in linha.lower():
                        self.append_text_to_log_area(linha)

                    if trigger_message_to_find and trigger_message_to_find in linha_strip:
                        logging.info(
                            f"[{thread_name}] Tab '{self.nome}': GATILHO DE REINÍCIO detectado em '{caminho_log_designado_para_esta_thread}'. Linha: '{linha_strip}'.")
                        self.app.set_status_from_thread(
                            f"'{self.nome}': Gatilho detectado! Preparando para reiniciar...")
                        self.append_text_to_log_area_threadsafe(
                            f"### GATILHO DE REINÍCIO DETECTADO: {linha_strip} ###\n")

                        if self.auto_restart_on_trigger_var.get():
                            threading.Thread(target=self._delayed_restart_worker, daemon=True,
                                             name=f"DelayedRestart-{self.nome}").start()
                        else:
                            self.append_text_to_log_area_threadsafe(
                                "Reinício automático por gatilho desabilitado. Nenhuma ação.\n")
                            logging.info(
                                f"Tab '{self.nome}': Gatilho detectado, mas reinício automático por gatilho desabilitado.")
                else:
                    if self._stop_event.wait(0.2): break
            except UnicodeDecodeError as ude_loop:
                logging.warning(
                    f"[{thread_name}] Tab '{self.nome}': Erro Unicode ao ler log {caminho_log_designado_para_esta_thread}: {ude_loop}.")
            except ValueError as ve_loop:
                if "closed file" in str(ve_loop).lower():
                    logging.warning(
                        f"[{thread_name}] Tab '{self.nome}': I/O em arquivo fechado ({caminho_log_designado_para_esta_thread}). Encerrando.")
                    break
                else:
                    logging.error(
                        f"[{thread_name}] Tab '{self.nome}': ValueError ao acompanhar log {caminho_log_designado_para_esta_thread}: {ve_loop}",
                        exc_info=True);
                    break
            except Exception as e_tail_loop_inesperado:
                if not self._stop_event.is_set():
                    logging.error(
                        f"[{thread_name}] Tab '{self.nome}': Erro INESPERADO ao acompanhar log {caminho_log_designado_para_esta_thread}: {e_tail_loop_inesperado}",
                        exc_info=True)
                    self.append_text_to_log_area(f"ERRO GRAVE ao ler log: {e_tail_loop_inesperado}\n")
                    self.app.set_status_from_thread(f"'{self.nome}': Erro na leitura do log.")
                break
        logging.info(
            f"[{thread_name}] Tab '{self.nome}': Acompanhamento de '{caminho_log_designado_para_esta_thread}' encerrado.")

    def _delayed_restart_worker(self):
        delay_s = self.restart_delay_after_trigger_var.get()
        nome_servico_reiniciar = self.nome_servico.get()

        if not nome_servico_reiniciar:
            msg = "ERRO: Nome do serviço não configurado para reinício automático por gatilho.\n"
            self.append_text_to_log_area_threadsafe(msg)
            logging.error(f"Tab '{self.nome}': Tentativa de reinício por gatilho sem nome de serviço.")
            self.app.set_status_from_thread(f"'{self.nome}': Erro - Serviço não configurado para reinício (gatilho).")
            return
        if not PYWIN32_AVAILABLE:
            msg = "ERRO: pywin32 não disponível. Não é possível reiniciar o serviço por gatilho.\n"
            self.append_text_to_log_area_threadsafe(msg)
            logging.error(f"Tab '{self.nome}': Tentativa de reinício por gatilho sem pywin32.")
            self.app.set_status_from_thread(f"'{self.nome}': Erro - pywin32 não disponível (gatilho).")
            return

        self.append_text_to_log_area_threadsafe(
            f"Gatilho de log detectado. Aguardando {delay_s}s para reiniciar '{nome_servico_reiniciar}'...\n")
        self.app.set_status_from_thread(
            f"'{self.nome}': Aguardando {delay_s}s para reiniciar {nome_servico_reiniciar} (gatilho)...")
        logging.info(
            f"Tab '{self.nome}': Aguardando {delay_s}s para reiniciar serviço '{nome_servico_reiniciar}' (gatilho).")

        start_time = time.monotonic()
        while time.monotonic() - start_time < delay_s:
            if self._stop_event.wait(0.5):
                logging.info(f"Tab '{self.nome}': Reinício por gatilho cancelado (app/aba fechando durante delay).")
                return
        if self._stop_event.is_set():
            logging.info(f"Tab '{self.nome}': Reinício por gatilho cancelado (app/aba fechando).")
            return
        logging.info(
            f"Tab '{self.nome}': Delay de {delay_s}s (gatilho) concluído. Iniciando reinício de '{nome_servico_reiniciar}'.")
        self._executar_logica_reinicio_servico_efetivamente(is_scheduled_restart=False)

    def _executar_logica_reinicio_servico_efetivamente(self, is_scheduled_restart=False):
        tipo_reinicio_msg = "agendado" if is_scheduled_restart else "por gatilho de log"
        nome_servico_reiniciar = self.nome_servico.get()

        if not PYWIN32_AVAILABLE:
            self.app.show_messagebox_from_thread("error", f"'{self.nome}': Funcionalidade Indisponível",
                                                 f"pywin32 é necessário para reiniciar serviços ({tipo_reinicio_msg}).")
            self.append_text_to_log_area_threadsafe(
                f"ERRO: pywin32 não disponível. Não é possível reiniciar o serviço ({tipo_reinicio_msg}).\n")
            return
        if not nome_servico_reiniciar:
            self.append_text_to_log_area_threadsafe(
                f"ERRO: Nome do serviço não configurado para reinício ({tipo_reinicio_msg}).\n")
            logging.error(
                f"Tab '{self.nome}': Tentativa de reiniciar servidor ({tipo_reinicio_msg}) sem nome de serviço.")
            self.app.set_status_from_thread(
                f"'{self.nome}': Erro - Serviço não configurado para reinício ({tipo_reinicio_msg}).")
            return

        logging.info(
            f"Tab '{self.nome}': Iniciando processo de reinício ({tipo_reinicio_msg}) do serviço '{nome_servico_reiniciar}'.")
        self.app.set_status_from_thread(f"'{self.nome}': Reiniciando {nome_servico_reiniciar} ({tipo_reinicio_msg})...")
        success = self._operar_servico_com_delays(nome_servico_reiniciar, tipo_reinicio_msg)

        if self.app.root.winfo_exists():
            if success:
                msg_success = f"O serviço {nome_servico_reiniciar} foi reiniciado com sucesso ({tipo_reinicio_msg})."
                self.app.show_messagebox_from_thread("info", f"'{self.nome}': Servidor Reiniciado", msg_success)
                self.append_text_to_log_area_threadsafe(f"SUCESSO: {msg_success}\n")
            else:
                msg_fail = f"Ocorreu um erro ao reiniciar ({tipo_reinicio_msg}) o serviço {nome_servico_reiniciar}.\nVerifique os logs."
                self.app.show_messagebox_from_thread("error", f"'{self.nome}': Falha no Reinício", msg_fail)
                self.append_text_to_log_area_threadsafe(f"FALHA: {msg_fail}\n")
            if self.winfo_exists(): self.update_service_status_display()

    def _operar_servico_com_delays(self, nome_servico_a_gerenciar, tipo_reinicio_msg_log=""):
        stop_delay_s = self.stop_delay_var.get()
        start_delay_s = self.start_delay_var.get()
        startupinfo = None
        if platform.system() == "Windows":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = subprocess.SW_HIDE
        log_prefix = f"Tab '{self.nome}' ({tipo_reinicio_msg_log.strip()}):" if tipo_reinicio_msg_log else f"Tab '{self.nome}':"

        try:
            self.app.set_status_from_thread(
                f"'{self.nome}': Parando {nome_servico_a_gerenciar} ({tipo_reinicio_msg_log.strip()})...")
            self.append_text_to_log_area_threadsafe(
                f"Parando serviço '{nome_servico_a_gerenciar}' ({tipo_reinicio_msg_log.strip()})...\n")
            logging.info(f"{log_prefix} Tentando parar o serviço: {nome_servico_a_gerenciar}")
            status_atual = self._verificar_status_servico_win(nome_servico_a_gerenciar)

            if status_atual == "RUNNING" or status_atual == "START_PENDING":
                subprocess.run(["sc", "stop", nome_servico_a_gerenciar], check=True, shell=False,
                               startupinfo=startupinfo)
                self.append_text_to_log_area_threadsafe(f"Comando de parada enviado. Aguardando {stop_delay_s}s...\n")

                # Aguardar interruptivelmente
                wait_start = time.monotonic()
                while time.monotonic() - wait_start < stop_delay_s:
                    if self._scheduler_stop_event.is_set() or self._stop_event.is_set():  # Checar ambos os eventos
                        logging.info(f"{log_prefix} Parada de serviço interrompida durante delay de parada.")
                        return False  # Ou alguma outra forma de indicar interrupção
                    time.sleep(0.1)

                status_apos_parada = self._verificar_status_servico_win(nome_servico_a_gerenciar)
                if status_apos_parada != "STOPPED":
                    logging.warning(
                        f"{log_prefix} Serviço {nome_servico_a_gerenciar} não parou. Status: {status_apos_parada}")
                    self.append_text_to_log_area_threadsafe(
                        f"AVISO: Serviço '{nome_servico_a_gerenciar}' pode não ter parado. Status: {status_apos_parada}\n")
                else:
                    logging.info(f"{log_prefix} Serviço {nome_servico_a_gerenciar} parado.")
            elif status_atual == "STOPPED":
                self.append_text_to_log_area_threadsafe(f"Serviço '{nome_servico_a_gerenciar}' já estava parado.\n")
                logging.info(f"{log_prefix} Serviço {nome_servico_a_gerenciar} já estava parado.")
            elif status_atual == "NOT_FOUND":
                self.append_text_to_log_area_threadsafe(
                    f"ERRO: Serviço '{nome_servico_a_gerenciar}' não encontrado para parada.\n")
                logging.error(f"{log_prefix} Serviço {nome_servico_a_gerenciar} não encontrado para parada.")
                self.app.set_status_from_thread(
                    f"'{self.nome}': Erro - Serviço '{nome_servico_a_gerenciar}' não existe.")
                return False
            else:  # ERROR or UNKNOWN
                self.append_text_to_log_area_threadsafe(
                    f"ERRO: Não foi possível determinar estado do serviço '{nome_servico_a_gerenciar}' ou estado: {status_atual}.\n")
                logging.error(f"{log_prefix} Estado do serviço {nome_servico_a_gerenciar} desconhecido: {status_atual}")
                self.app.set_status_from_thread(
                    f"'{self.nome}': Erro - Estado de '{nome_servico_a_gerenciar}' desconhecido.")
                return False

            self.app.set_status_from_thread(
                f"'{self.nome}': Iniciando {nome_servico_a_gerenciar} ({tipo_reinicio_msg_log.strip()})...")
            self.append_text_to_log_area_threadsafe(
                f"Iniciando serviço '{nome_servico_a_gerenciar}' ({tipo_reinicio_msg_log.strip()})...\n")
            logging.info(f"{log_prefix} Tentando iniciar o serviço: {nome_servico_a_gerenciar}")
            subprocess.run(["sc", "start", nome_servico_a_gerenciar], check=True, shell=False, startupinfo=startupinfo)
            self.append_text_to_log_area_threadsafe(
                f"Comando de início enviado. Aguardando {start_delay_s}s para estabilizar...\n")
            self.app.set_status_from_thread(
                f"'{self.nome}': Aguardando {nome_servico_a_gerenciar} iniciar ({start_delay_s}s)...")

            wait_start = time.monotonic()
            while time.monotonic() - wait_start < start_delay_s:
                if self._scheduler_stop_event.is_set() or self._stop_event.is_set():
                    logging.info(f"{log_prefix} Parada de serviço interrompida durante delay de início.")
                    return False
                time.sleep(0.1)

            status_apos_inicio = self._verificar_status_servico_win(nome_servico_a_gerenciar)
            if status_apos_inicio != "RUNNING":
                logging.error(
                    f"{log_prefix} Serviço {nome_servico_a_gerenciar} falhou ao iniciar. Status: {status_apos_inicio}")
                self.append_text_to_log_area_threadsafe(
                    f"ERRO: Serviço '{nome_servico_a_gerenciar}' falhou ao iniciar. Status: {status_apos_inicio}\n")
                self.app.set_status_from_thread(
                    f"'{self.nome}': Erro - {nome_servico_a_gerenciar} não iniciou. Status: {status_apos_inicio}")
                return False

            logging.info(f"{log_prefix} Serviço {nome_servico_a_gerenciar} iniciado com sucesso.")
            if self.winfo_exists(): self.update_service_status_display()
            self.app.set_status_from_thread(f"'{self.nome}': Servidor reiniciado ({tipo_reinicio_msg_log.strip()}).")
            return True


        except subprocess.CalledProcessError as e_sc:

            err_output = "N/A"

            if e_sc.stderr:

                try:

                    err_output = e_sc.stderr.decode('latin-1', errors='replace')

                except Exception:  # Captura genérica para o decode

                    pass

            elif e_sc.stdout:  # Se não houver stderr, tenta stdout

                try:

                    err_output = e_sc.stdout.decode('latin-1', errors='replace')

                except Exception:  # Captura genérica para o decode

                    pass

            err_msg = f"Erro 'sc' para '{nome_servico_a_gerenciar}': {err_output.strip()}"

            # ... resto do bloco
            self.append_text_to_log_area_threadsafe(f"ERRO: {err_msg}\n")
            logging.error(f"{log_prefix} {err_msg}", exc_info=True)
            self.app.set_status_from_thread(f"'{self.nome}': Erro ao gerenciar serviço: {e_sc.cmd}")
            if self.winfo_exists(): self.update_service_status_display()
            return False
        except FileNotFoundError:
            self.app.show_messagebox_from_thread("error", f"'{self.nome}': Erro de Comando",
                                                 "Comando 'sc.exe' não encontrado.")
            logging.error(f"{log_prefix} Comando 'sc.exe' não encontrado.")
            self.app.set_status_from_thread(f"'{self.nome}': Erro - sc.exe não encontrado.")
            if self.winfo_exists(): self.update_service_status_display()
            return False
        except Exception as e_reinicio_inesperado:
            err_msg = f"Erro inesperado ao reiniciar ({tipo_reinicio_msg_log.strip()}) o servidor '{self.nome}': {e_reinicio_inesperado}"
            self.append_text_to_log_area_threadsafe(f"ERRO: {err_msg}\n")
            logging.error(f"{log_prefix} {err_msg}", exc_info=True)
            self.app.set_status_from_thread(f"'{self.nome}': Erro inesperado no reinício.")
            if self.winfo_exists(): self.update_service_status_display()
            return False

    def append_text_to_log_area(self, texto):
        if not (self.winfo_exists() and self.text_area_log.winfo_exists()):
            logging.debug(f"Tab '{self.nome}': Tentativa add texto log, widget não existe.")
            return
        try:
            self.app.root.after(0, self._append_text_to_log_area_gui_thread, texto)
        except Exception as e:
            logging.warning(f"Tab '{self.nome}': Exceção ao agendar append_text_to_log_area: {e}")

    def _append_text_to_log_area_gui_thread(self, texto):
        if not (self.winfo_exists() and self.text_area_log.winfo_exists()): return
        try:
            current_state = self.text_area_log.cget("state")
            self.text_area_log.configure(state='normal')
            self.text_area_log.insert('end', texto)
            if self.auto_scroll_log_var.get():
                self.text_area_log.yview_moveto(1.0)
            self.text_area_log.configure(state=current_state)
        except tk.TclError as e_tcl:
            logging.debug(f"Tab '{self.nome}': TclError em _append_text_to_log_area_gui_thread: {e_tcl}")
        except Exception as e_append:
            logging.error(f"Tab '{self.nome}': Erro em _append_text_to_log_area_gui_thread: {e_append}", exc_info=True)

    def append_text_to_log_area_threadsafe(self, texto):
        self.append_text_to_log_area(texto)

    def limpar_tela_log(self):
        if self.text_area_log.winfo_exists():
            self.text_area_log.configure(state='normal')
            self.text_area_log.delete('1.0', 'end')
            self.text_area_log.configure(state='disabled')
            self.app.set_status_from_thread(f"Logs de '{self.nome}' limpos.")
            logging.info(f"Tab '{self.nome}': Logs limpos.")

    def toggle_pausa(self):
        self._paused = not self._paused
        btn_text, btn_style = ("▶️ Retomar", SUCCESS) if self._paused else ("⏸️ Pausar", WARNING)
        status_msg = "pausado" if self._paused else "retomado"
        self.pausar_btn.config(text=btn_text, bootstyle=btn_style)
        self.app.set_status_from_thread(f"Monitoramento '{self.nome}' {status_msg}.")
        logging.info(f"Tab '{self.nome}': Monitoramento {status_msg}.")

    def _toggle_log_search_bar(self, event=None, force_hide=False, force_show=False):
        if force_hide or (self.search_log_frame_visible and not force_show):
            if self.search_log_frame.winfo_ismapped():
                self.search_log_frame_visible = False
                self.search_log_frame.pack_forget()
                if self.text_area_log.winfo_exists(): self.text_area_log.focus_set()
                self.text_area_log.tag_remove("search_match", "1.0", "end")
        elif force_show or not self.search_log_frame_visible:
            if not self.search_log_frame.winfo_ismapped():
                self.search_log_frame_visible = True
                self.search_log_frame.pack(fill='x', before=self.text_area_log, pady=(0, 2), padx=5)
                if self.log_search_entry.winfo_exists(): self.log_search_entry.focus_set(); self.log_search_entry.select_range(
                    0, 'end')
        self.last_search_pos = "1.0"

    def _perform_log_search_internal(self, term, start_pos, direction_forward=True, wrap=True):
        if not term or not self.text_area_log.winfo_exists():
            if self.text_area_log.winfo_exists(): self.text_area_log.tag_remove("search_match", "1.0", "end")
            return None
        self.text_area_log.tag_remove("search_match", "1.0", "end")
        count_var = tk.IntVar()
        original_state = self.text_area_log.cget("state")
        self.text_area_log.config(state="normal")
        pos = None
        search_args = {"stopindex": "end" if direction_forward else "1.0", "count": count_var, "nocase": True}
        if not direction_forward: search_args["backwards"] = True
        pos = self.text_area_log.search(term, start_pos, **search_args)
        if not pos and wrap:
            wrap_start_pos = "1.0" if direction_forward else "end"
            search_args["stopindex"] = start_pos  # Don't re-find same if no other
            pos = self.text_area_log.search(term, wrap_start_pos, **search_args)
        if pos:
            end_pos = f"{pos}+{count_var.get()}c"
            self.text_area_log.tag_add("search_match", pos, end_pos)
            self.text_area_log.tag_config("search_match", background="yellow", foreground="black")
            self.text_area_log.see(pos)
            self.text_area_log.config(state=original_state)
            return end_pos if direction_forward else pos
        else:
            self.text_area_log.config(state=original_state)
            self.app.set_status_from_thread(f"'{term}' não encontrado em '{self.nome}'.")
            return None

    def _search_log_next(self, event=None):
        term = self.log_search_var.get()
        if not term: return
        current_match_ranges = self.text_area_log.tag_ranges("search_match")
        start_from = self.last_search_pos
        if current_match_ranges: start_from = current_match_ranges[1]  # Start after current match
        next_start_pos = self._perform_log_search_internal(term, start_from, direction_forward=True, wrap=True)
        if next_start_pos: self.last_search_pos = next_start_pos
        # else: self.last_search_pos = "1.0" # Reset if not found, or rely on wrap handling in _perform_log_search_internal

    def _search_log_prev(self, event=None):
        term = self.log_search_var.get()
        if not term: return
        current_match_ranges = self.text_area_log.tag_ranges("search_match")
        start_from = self.last_search_pos
        if current_match_ranges: start_from = current_match_ranges[0]  # Start before current match
        new_match_start_pos = self._perform_log_search_internal(term, start_from, direction_forward=False, wrap=True)
        if new_match_start_pos: self.last_search_pos = new_match_start_pos
        # else: self.last_search_pos = "end"


class ServerRestarterApp:
    # DENTRO da classe ServerRestarterApp
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
            logging.warning(f"Tema '{self.config.get('theme')}' não encontrado. Usando 'litera'.")
            self.style.theme_use("litera")
            self.config["theme"] = "litera"

        # 1. Configurar a imagem de fundo primeiro, mas não empacotá-la ainda.
        self._setup_background_image()  # Apenas cria self.bg_label e self.original_pil_bg_image

        self.servidores = []
        self.config_changed = False
        self._app_stop_event = threading.Event()

        self.set_application_icon()
        self.create_menu()

        # 2. Criar a barra de status e o notebook
        self.create_status_bar()  # Cria self.status_bar_frame, ela é packed dentro do método

        self.main_notebook = ttk.Notebook(self.root)
        # Adicionar abas ao notebook
        self.system_log_frame = ttk.Frame(self.main_notebook)
        self.main_notebook.add(self.system_log_frame, text="Log do Sistema (Restarter)")
        self.system_log_text_area = ScrolledText(self.system_log_frame, wrap='word', height=10, state='disabled')
        self.system_log_text_area.pack(fill='both', expand=True, padx=5, pady=5)

        self.inicializar_servidores_das_configuracoes()

        # 3. Agora empacotar o notebook principal
        #    A barra de status já foi empacotada como 'bottom'
        self.main_notebook.pack(fill='both', expand=True, padx=5, pady=5, side=tk.TOP)

        # 4. Se o label de fundo existe, colocá-lo no fundo da pilha.
        if self.bg_label:
            self.bg_label.lower()  # Envia o bg_label para trás de todos os outros widgets na root

        self._system_log_update_error_count = 0
        self.atualizar_log_sistema_periodicamente()

        self.root.bind_all("<Escape>", self.handle_escape_key, add="+")
        self.root.bind("<Configure>", self._on_root_configure, add="+")

        if not PYWIN32_AVAILABLE and platform.system() == "Windows":
            self.show_messagebox_from_thread("warning", "pywin32 Ausente",
                                             "A biblioteca 'pywin32' não foi encontrada...\nInstale com: pip install pywin32")

        self.root.protocol("WM_DELETE_WINDOW", self.minimize_to_tray_on_close)
        if PIL_AVAILABLE and pystray:
            self.setup_tray_icon()
        else:
            logging.warning("Ícone da bandeja não será criado devido à falta de Pillow ou pystray.")
    def _setup_background_image(self):
        global BACKGROUND_IMAGE_PATH, BACKGROUND_ALPHA_MULTIPLIER, PIL_AVAILABLE
        if not PIL_AVAILABLE:
            logging.warning("Pillow não disponível, imagem de fundo não será carregada.")
            return

        if not os.path.exists(BACKGROUND_IMAGE_PATH):
            logging.warning(f"Imagem de fundo '{BACKGROUND_IMAGE_PATH}' não encontrada.")
            return

        try:
            pil_image_original = Image.open(BACKGROUND_IMAGE_PATH)
            pil_image_rgba = pil_image_original.convert("RGBA")

            if BACKGROUND_ALPHA_MULTIPLIER < 1.0 and BACKGROUND_ALPHA_MULTIPLIER >= 0.0:
                alpha = pil_image_rgba.split()[3]  # Pega o canal alfa
                alpha = alpha.point(lambda p: int(p * BACKGROUND_ALPHA_MULTIPLIER))
                pil_image_rgba.putalpha(alpha)

            self.original_pil_bg_image = pil_image_rgba  # Armazena para redimensionamento

            self.bg_label = ttk.Label(self.root)  # Label para o fundo
            self.bg_label.place(x=0, y=0, relwidth=1, relheight=1)  # Fazer o label preencher a janela

            # Primeira chamada para definir a imagem no tamanho inicial da janela
            # Precisa garantir que a janela tenha dimensões antes de chamar
            self.root.update_idletasks()
            self._resize_background_image(self.root.winfo_width(), self.root.winfo_height())

        except Exception as e:
            logging.error(f"Erro ao configurar imagem de fundo: {e}", exc_info=True)
            if self.bg_label:
                self.bg_label.destroy()
            self.bg_label = None
            self.original_pil_bg_image = None

    def _on_root_configure(self, event):
        # Este evento é disparado quando a janela é redimensionada
        # Certifique-se que o widget é a própria root para evitar chamadas de widgets filhos
        if event.widget == self.root:
            if self.original_pil_bg_image and self.bg_label:
                self._resize_background_image(event.width, event.height)

    def _resize_background_image(self, width, height):
        if not self.original_pil_bg_image or not self.bg_label or not self.bg_label.winfo_exists():
            return
        if width <= 1 or height <= 1:  # Evitar divisão por zero ou imagens minúsculas
            return

        try:
            img_to_resize = self.original_pil_bg_image

            # Calcular proporções para "cobrir" a área
            img_aspect = img_to_resize.width / img_to_resize.height
            win_aspect = width / height

            if win_aspect > img_aspect:
                # Janela mais larga que a imagem: altura da imagem = altura da janela
                new_height = height
                new_width = int(new_height * img_aspect)
            else:
                # Janela mais alta ou mesma proporção: largura da imagem = largura da janela
                new_width = width
                new_height = int(new_width / img_aspect)

            # Para "cobrir", pode ser que a imagem precise ser maior que a janela em uma dimensão
            # e depois cortada. Ou, podemos escalar para que uma dimensão bata e a outra seja >=
            # e então centralizar. Para "cover", é mais comum escalar para que ambas as dimensões
            # sejam >= window dimensions, then crop.

            # Ajuste para 'cover': escalar para que a menor dimensão da imagem caiba
            # e a maior exceda ou caiba, depois cortar o excesso do centro.

            # Escala para que a imagem CUBRA a janela
            # Se a janela é mais larga proporcionalmente que a imagem:
            # A altura da imagem redimensionada deve ser a altura da janela.
            # A largura será proporcional.
            # Se a janela é mais alta proporcionalmente:
            # A largura da imagem redimensionada deve ser a largura da janela.
            # A altura será proporcional.

            if width / img_to_resize.width > height / img_to_resize.height:
                # Escalar pela largura da janela
                final_w = width
                final_h = int(img_to_resize.height * (width / img_to_resize.width))
            else:
                # Escalar pela altura da janela
                final_h = height
                final_w = int(img_to_resize.width * (height / img_to_resize.height))

            resized_pil_image = img_to_resize.resize((final_w, final_h), Image.LANCZOS)

            # Agora, criar a PhotoImage e atribuir ao label
            # O PhotoImage será centralizado no bg_label por padrão se o bg_label for maior
            # Se o bg_label é place(relwidth=1, relheight=1), ele tem o tamanho da janela.
            self.bg_photo_image = ImageTk.PhotoImage(resized_pil_image)
            self.bg_label.configure(image=self.bg_photo_image)
            # self.bg_label.image = self.bg_photo_image # Manter referência para evitar garbage collection
            # ttk.Label faz isso internamente, mas não custa.

        except Exception as e:
            logging.error(f"Erro ao redimensionar imagem de fundo: {e}", exc_info=True)

    def set_application_icon(self):
        global ICON_PATH, PIL_AVAILABLE
        if not PIL_AVAILABLE:
            logging.warning(
                "Pillow não disponível, não é possível definir o ícone da aplicação a partir de um arquivo.")
            return
        try:
            if os.path.exists(ICON_PATH):
                if platform.system() == "Windows":
                    self.root.iconbitmap(default=ICON_PATH)
                    logging.info(f"Ícone da aplicação (Windows) definido de: {ICON_PATH}")
                else:
                    pil_icon = Image.open(ICON_PATH)
                    self.app_icon_tk = ImageTk.PhotoImage(pil_icon)
                    self.root.iconphoto(True, self.app_icon_tk)
                    logging.info(f"Ícone da aplicação (não-Windows) definido de: {ICON_PATH}")
            else:
                logging.warning(f"Arquivo de ícone '{ICON_PATH}' não encontrado. Ícone padrão do sistema será usado.")
        except tk.TclError as e_icon_tcl:
            logging.error(f"Erro TclError ao definir ícone da aplicação com '{ICON_PATH}': {e_icon_tcl}")
        except Exception as e_icon_generic:
            logging.error(f"Erro genérico ao definir ícone da aplicação: {e_icon_generic}", exc_info=True)

    def _create_tray_image(self):
        global ICON_PATH, PIL_AVAILABLE
        if not PIL_AVAILABLE:
            return None

        try:
            if os.path.exists(ICON_PATH):  # Usar ICON_PATH (predpy.ico) para a bandeja
                logging.info(f"Carregando ícone da bandeja de: {ICON_PATH}")
                return Image.open(ICON_PATH)
            else:
                logging.warning(f"Arquivo de ícone '{ICON_PATH}' não encontrado para a bandeja. Desenhando um padrão.")
        except Exception as e_load_tray:
            logging.error(f"Erro ao carregar ícone da bandeja de '{ICON_PATH}': {e_load_tray}. Desenhando um padrão.")

        width, height = 64, 64
        try:
            image = Image.new('RGB', (width, height), color='skyblue')
            draw = ImageDraw.Draw(image)
            draw.ellipse((10, 10, width - 10, height - 10), fill='darkblue', outline='white')
            draw.text((width // 2 - 10, height // 2 - 10), "SR", fill="white",
                      font=ImageDraw.truetype("arial.ttf", 20) if os.path.exists(
                          "arial.ttf") else None)  # SR = Server Restarter
            return image
        except Exception as e_draw:
            logging.error(f"Erro ao desenhar ícone padrão para bandeja: {e_draw}")
            return None

    # ... (Restante da classe ServerRestarterApp e ServidorTab como antes, com as modificações anteriores para agendamento)
    # ... métodos setup_tray_icon, show_from_tray, minimize_to_tray_on_close, shutdown_application_from_tray, etc.
    # ... permanecem os mesmos, mas agora _create_tray_image usará predpy.ico

    def setup_tray_icon(self):
        if not (PIL_AVAILABLE and pystray):
            return

        image = self._create_tray_image()
        if image is None:
            logging.error("Não foi possível criar a imagem para o ícone da bandeja.")
            return

        menu = (pystray.MenuItem('Mostrar', self.show_from_tray, default=True),
                pystray.MenuItem('Sair', self.shutdown_application_from_tray))

        self.tray_icon = pystray.Icon("ServerRestarter", image, "PredPy Server Restarter",
                                      menu)  # Titulo da bandeja atualizado
        threading.Thread(target=self.tray_icon.run, daemon=True, name="TrayIconThread").start()
        logging.info("Ícone da bandeja do sistema configurado e iniciado.")

    def show_from_tray(self, icon=None, item=None):
        if self.root.winfo_exists():
            self.root.after(0, self.root.deiconify)
            self.root.after(100, self.root.lift)
            self.root.after(200, self.root.focus_force)

    # Dentro de ServerRestarterApp
    def minimize_to_tray_on_close(self, event=None):
        logging.info(
            f"Verificando condições para minimizar: self.tray_icon é {'None' if self.tray_icon is None else 'Objeto'}, self.tray_icon.visible é {getattr(self.tray_icon, 'visible', 'N/A (tray_icon é None ou sem attr visible)')}")
        if self.tray_icon and self.tray_icon.visible:  # Esta é a condição chave
            self.root.withdraw()
            logging.info("Aplicação minimizada para a bandeja ao fechar janela (X).")
        else:
            logging.warning("Condição para minimizar para bandeja FALHOU. Encerrando aplicação normalmente.")
            self.shutdown_application()

    def shutdown_application_from_tray(self, icon=None, item=None):
        logging.info("Comando 'Sair' da bandeja recebido.")
        self.shutdown_application()

    def shutdown_application(self):
        logging.info("Iniciando processo de encerramento da aplicação...")
        self._app_stop_event.set()

        for srv_tab in self.servidores:
            srv_tab.stop_log_monitoring(from_tab_closure=True)

        if self.config_changed:
            logging.debug("Configuração alterada. Salvando automaticamente antes de sair.")
            try:
                self._save_app_config_to_file()
                logging.info("Configurações salvas automaticamente ao sair.")
            except Exception as e_auto_save:
                logging.error(f"Erro ao salvar automaticamente as configurações ao sair: {e_auto_save}", exc_info=True)

        if self.tray_icon:
            try:
                self.tray_icon.stop()
                logging.info("Ícone da bandeja parado.")
            except Exception as e_tray_stop:  # Pode dar erro se já estiver parando
                logging.debug(f"Erro (possivelmente benigno) ao parar ícone da bandeja: {e_tray_stop}")

        if self.root.winfo_exists():
            self.set_status_from_thread("Encerrando...")
            try:
                self.root.update_idletasks()
            except tk.TclError:
                pass
            self.root.destroy()
        logging.info("Aplicação completamente encerrada.")
        # sys.exit() # Pode não ser necessário se a root.destroy() for o suficiente e as threads forem daemon

    def handle_escape_key(self, event=None):
        current_tab_widget = self.get_current_servidor_tab_widget()
        if current_tab_widget and hasattr(current_tab_widget, '_toggle_log_search_bar'):
            if current_tab_widget.search_log_frame_visible:
                current_tab_widget._toggle_log_search_bar(force_hide=True)
                return "break"
        return None

    def on_tab_changed(self, event):
        try:
            current_tab_widget = self.get_current_servidor_tab_widget()
            if current_tab_widget:
                self.set_status_from_thread(f"Servidor '{current_tab_widget.nome}' selecionado.")
            elif self.main_notebook.index("current") != -1 and \
                    self.main_notebook.tab(self.main_notebook.select(), "text").startswith("Log do Sistema"):
                self.set_status_from_thread("Visualizando Log do Sistema do Restarter.")
        except tk.TclError:
            pass

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
            logging.info("Nenhuma config de servidor. Adicionando um servidor padrão.")
            self.adicionar_servidor_tab("Servidor 1 (Padrão)")
        else:
            for srv_conf in servers_config_list:
                nome = srv_conf.get("nome", f"Servidor {len(self.servidores) + 1}")
                self.adicionar_servidor_tab(nome, srv_conf, focus_new_tab=False)
        if self.servidores and self.main_notebook.tabs():
            try:
                self.main_notebook.select(self.servidores[0])
            except tk.TclError:
                logging.warning("Não foi possível selecionar a primeira aba de servidor durante a inicialização.")

    def adicionar_servidor_tab(self, nome_sugerido=None, config_servidor=None, focus_new_tab=True):
        if nome_sugerido is None: nome_sugerido = f"Servidor {len(self.servidores) + 1}"
        nomes_existentes = [s.nome for s in self.servidores]
        nome_final = nome_sugerido
        count = 1
        while nome_final in nomes_existentes:
            nome_final = f"{nome_sugerido} ({count})"
            count += 1
        servidor_tab_frame = ServidorTab(self.main_notebook, self, nome_final, config_servidor)
        self.servidores.append(servidor_tab_frame)
        self.main_notebook.add(servidor_tab_frame, text=nome_final)
        logging.info(f"Aba '{nome_final}' adicionada.")
        if focus_new_tab and self.main_notebook.tabs():
            try:
                self.main_notebook.select(servidor_tab_frame)
            except tk.TclError:
                logging.warning(f"Não foi possível focar a nova aba '{nome_final}'.")
        self.mark_config_changed()
        return servidor_tab_frame

    def remover_servidor_atual(self):
        current_tab_widget = self.get_current_servidor_tab_widget()
        if not current_tab_widget:
            self.show_messagebox_from_thread("warning", "Remover Servidor", "Nenhuma aba de servidor selecionada.")
            return
        nome_servidor_removido = current_tab_widget.nome
        if Messagebox.okcancel(f"Remover '{nome_servidor_removido}'?",
                               f"Tem certeza que deseja remover '{nome_servidor_removido}'?",
                               parent=self.root, alert=True) == "OK":
            current_tab_widget.stop_log_monitoring(from_tab_closure=True)
            self.servidores.remove(current_tab_widget)
            self.main_notebook.forget(current_tab_widget)
            current_tab_widget.destroy()
            logging.info(f"Aba '{nome_servidor_removido}' removida.")
            self.set_status_from_thread(f"Servidor '{nome_servidor_removido}' removido.")
            self.mark_config_changed()
            if not self.servidores and self.main_notebook.index("end") > 0:  # Se não há mais abas de servidor
                if self.system_log_frame.winfo_exists():
                    self.main_notebook.select(self.system_log_frame)  # Seleciona log do sistema
            elif self.servidores:  # Se ainda há abas de servidor
                try:
                    self.main_notebook.select(self.servidores[0])  # Seleciona a primeira
                except tk.TclError:
                    logging.warning("Não foi possível selecionar a primeira aba após remoção.")

    def renomear_servidor_atual(self):
        current_tab_widget = self.get_current_servidor_tab_widget()
        if not current_tab_widget:
            self.show_messagebox_from_thread("warning", "Renomear Servidor", "Nenhuma aba de servidor selecionada.")
            return
        nome_antigo = current_tab_widget.nome
        novo_nome = simpledialog.askstring("Renomear Servidor", f"Novo nome para '{nome_antigo}':",
                                           initialvalue=nome_antigo, parent=self.root)
        if novo_nome and novo_nome.strip() and novo_nome != nome_antigo:
            nomes_existentes = [s.nome for s in self.servidores if s != current_tab_widget]
            if novo_nome in nomes_existentes:
                self.show_messagebox_from_thread("error", "Nome Duplicado", f"O nome '{novo_nome}' já está em uso.")
                return
            current_tab_widget.nome = novo_nome
            for i, tab_id_str in enumerate(self.main_notebook.tabs()):
                if self.main_notebook.nametowidget(tab_id_str) == current_tab_widget:
                    self.main_notebook.tab(tab_id_str, text=novo_nome)
                    break
            logging.info(f"Servidor '{nome_antigo}' renomeado para '{novo_nome}'.")
            self.set_status_from_thread(f"Servidor '{nome_antigo}' renomeado para '{novo_nome}'.")
            self.mark_config_changed()
        elif novo_nome is not None and not novo_nome.strip():
            self.show_messagebox_from_thread("warning", "Nome Inválido", "Nome do servidor não pode ser vazio.")

    def mark_config_changed(self):
        if not self.config_changed:
            self.config_changed = True
            if hasattr(self, 'file_menu') and self.file_menu.winfo_exists():
                try:
                    self.file_menu.entryconfigure("Salvar Configuração", state="normal")
                except tk.TclError:
                    pass
            logging.debug("Configuração marcada como alterada.")

    def _load_app_config_from_file(self):
        try:
            if os.path.exists(self.config_file):
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    config_data = json.load(f)
                logging.info(f"Configuração carregada de {self.config_file}")
                return config_data
            logging.info(f"Arquivo config {self.config_file} não encontrado. Usando padrões.")
            return {"theme": "darkly", "servers": []}
        except json.JSONDecodeError as e:
            logging.error(f"Erro ao decodificar JSON em {self.config_file}: {e}", exc_info=True)
            return {"theme": "darkly", "servers": []}
        except Exception as e:
            logging.error(f"Erro ao carregar config de {self.config_file}: {e}", exc_info=True)
            return {"theme": "darkly", "servers": []}

    def _save_app_config_to_file(self):
        current_app_config = {"theme": self.style.theme_use(), "servers": []}
        for servidor_tab in self.servidores:
            current_app_config["servers"].append(servidor_tab.get_current_config())
        try:
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(current_app_config, f, indent=4)
            self.config_changed = False
            if hasattr(self, 'file_menu') and self.file_menu.winfo_exists():
                try:
                    self.file_menu.entryconfigure("Salvar Configuração", state="disabled")
                except tk.TclError:
                    pass
            self.set_status_from_thread("Configuração salva!")
            logging.info(f"Configuração salva em {self.config_file}")
        except IOError as e_io:
            self.set_status_from_thread(f"Erro E/S ao salvar: {e_io.strerror}")
            logging.error(f"Erro E/S ao salvar: {e_io}", exc_info=True)
            self.show_messagebox_from_thread("error", "Erro ao Salvar",
                                             f"Não foi possível salvar:\n{self.config_file}\n\n{e_io.strerror}")
        except Exception as e_save:
            self.set_status_from_thread(f"Erro ao salvar: {e_save}")
            logging.error(f"Erro ao salvar: {e_save}", exc_info=True)
            self.show_messagebox_from_thread("error", "Erro ao Salvar", f"Erro ao salvar:\n{e_save}")

    def load_config_from_dialog(self):
        caminho = filedialog.askopenfilename(
            defaultextension=".json", filetypes=[("Arquivos JSON", "*.json"), ("Todos", "*.*")],
            title="Selecionar arquivo de configuração", initialdir=os.path.dirname(self.config_file) or os.getcwd()
        )
        if not caminho: return
        try:
            with open(caminho, 'r', encoding='utf-8') as f:
                loaded_config_data = json.load(f)
            for srv_tab in list(self.servidores):  # iterate over a copy
                srv_tab.stop_log_monitoring(from_tab_closure=True)
                self.main_notebook.forget(srv_tab)
                srv_tab.destroy()
            self.servidores.clear()
            self.config_file = caminho
            self.config = loaded_config_data
            new_theme = self.config.get("theme", "darkly")
            try:
                self.style.theme_use(new_theme)
                self.config["theme"] = new_theme
            except tk.TclError:
                logging.warning(f"Tema '{new_theme}' não encontrado. Usando 'litera'.")
                self.style.theme_use("litera");
                self.config["theme"] = "litera"
            self.inicializar_servidores_das_configuracoes()
            self.config_changed = False  # Config recém carregada
            if hasattr(self, 'file_menu') and self.file_menu.winfo_exists():
                try:
                    self.file_menu.entryconfigure("Salvar Configuração", state="disabled")
                except tk.TclError:
                    pass
            self.set_status_from_thread(f"Configuração carregada de {os.path.basename(caminho)}")
            logging.info(f"Configuração carregada de {caminho}")
            self.show_messagebox_from_thread("info", "Configuração Carregada", f"Carregada de:\n{caminho}")
        except json.JSONDecodeError as e_json_load:
            logging.error(f"Erro JSON em {caminho}: {e_json_load}", exc_info=True)
            self.show_messagebox_from_thread("error", "Erro Config",
                                             f"Falha ao carregar '{os.path.basename(caminho)}':\nJSON inválido.\n{e_json_load}")
        except Exception as e_load:
            logging.error(f"Erro ao carregar {caminho}: {e_load}", exc_info=True)
            self.show_messagebox_from_thread("error", "Erro Config",
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
            # Atualizar cores de elementos que não são ttk widgets se necessário (ex: labels de status ServidorTab)
            for srv_tab in self.servidores: srv_tab.initialize_from_config_vars()  # Re-inicializa para pegar cores do tema
            self.config["theme"] = novo_tema
            self.mark_config_changed()
            logging.info(f"Tema alterado para: {novo_tema}")
            self.set_status_from_thread(f"Tema alterado para '{novo_tema}'.")
        except tk.TclError as e_theme_tcl:
            logging.error(f"Erro TclError ao trocar tema '{novo_tema}': {e_theme_tcl}", exc_info=True)
            self.show_messagebox_from_thread("error", "Erro de Tema",
                                             f"Não foi possível aplicar tema '{novo_tema}'.\n{e_theme_tcl}")
            try:  # Tenta voltar para um tema padrão seguro
                self.style.theme_use("litera");
                self.theme_var.set("litera");
                self.config["theme"] = "litera"
                for srv_tab in self.servidores: srv_tab.initialize_from_config_vars()
            except Exception:
                pass

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
            defaultextension=".txt", filetypes=[("Arquivos de Texto", "*.txt"), ("Todos", "*.*")],
            title=f"Exportar {default_filename_part}", initialfile=f"{default_filename_part.replace(' ', '_')}.txt"
        )
        if caminho_arquivo:
            try:
                if text_widget.winfo_exists():
                    with open(caminho_arquivo, 'w', encoding='utf-8') as f:
                        f.write(
                            text_widget.get('1.0', 'end-1c'))  # end-1c para não pegar o newline final do ScrolledText
                    self.set_status_from_thread(
                        f"{default_filename_part} exportados para: {os.path.basename(caminho_arquivo)}")
                    logging.info(f"{default_filename_part} exportados para: {caminho_arquivo}")
                    self.show_messagebox_from_thread("info", "Exportação Concluída",
                                                     f"{default_filename_part} exportados para:\n{caminho_arquivo}")
                else:
                    self.show_messagebox_from_thread("error", "Erro Exportação", "Área de texto não encontrada.")
            except Exception as e_export:
                self.set_status_from_thread(f"Erro ao exportar {default_filename_part}: {e_export}")
                logging.error(f"Erro exportar {default_filename_part} para {caminho_arquivo}: {e_export}",
                              exc_info=True)
                self.show_messagebox_from_thread("error", "Erro Exportação",
                                                 f"Falha ao exportar {default_filename_part}:\n{e_export}")

    def create_status_bar(self):
        self.status_bar_frame = ttk.Frame(self.root)  # Salva referência
        self.status_bar_frame.pack(side='bottom', fill='x', pady=(0, 2), padx=2)
        ttk.Separator(self.status_bar_frame, orient='horizontal').pack(side='top', fill='x')
        self.status_label_var = tk.StringVar(value="Pronto.")
        self.status_label = ttk.Label(self.status_bar_frame, textvariable=self.status_label_var, anchor='w')
        self.status_label.pack(side='left', fill='x', expand=True, padx=5, pady=(2, 0))

    def atualizar_log_sistema_periodicamente(self):
        try:
            if not self.root.winfo_exists() or not hasattr(self,
                                                           'system_log_text_area') or not self.system_log_text_area.winfo_exists(): return
            log_file_path = 'server_restarter.log'
            if os.path.exists(log_file_path):
                with open(log_file_path, 'r', encoding='utf-8', errors='replace') as f:
                    conteudo = f.read()
                self.system_log_text_area.configure(state='normal')
                pos_atual_scroll_y, _ = self.system_log_text_area.yview()
                self.system_log_text_area.delete('1.0', 'end')
                self.system_log_text_area.insert('end', conteudo)
                if pos_atual_scroll_y >= 0.99:
                    self.system_log_text_area.yview_moveto(1.0)  # Smart scroll
                else:
                    self.system_log_text_area.yview_moveto(pos_atual_scroll_y)
                self.system_log_text_area.configure(state='disabled')
            else:  # Se o arquivo não existe, limpa e informa
                self.system_log_text_area.configure(state='normal')
                self.system_log_text_area.delete('1.0', 'end')
                self.system_log_text_area.insert('end', f"Arquivo de log '{log_file_path}' não encontrado.")
                self.system_log_text_area.configure(state='disabled')
        except tk.TclError as e_tcl_syslog:
            if "invalid command name" not in str(e_tcl_syslog).lower():  # Ignora erro comum ao fechar
                logging.debug(f"TclError ao atualizar log sistema (provavelmente ao fechar): {e_tcl_syslog}")
        except Exception as e_syslog_update:
            if not hasattr(self,
                           "_system_log_update_error_count") or self._system_log_update_error_count < 5:  # Limita logs de erro repetidos
                logging.error(f"Erro ao atualizar log sistema: {e_syslog_update}",
                              exc_info=False)  # exc_info=False para não poluir tanto
                self._system_log_update_error_count = getattr(self, "_system_log_update_error_count", 0) + 1
        if not self._app_stop_event.is_set() and self.root.winfo_exists():  # Verifica se app não está parando
            self.root.after(3000, self.atualizar_log_sistema_periodicamente)

    def iniciar_selecao_servico_para_aba(self, servidor_tab_instance):
        if not PYWIN32_AVAILABLE:
            servidor_tab_instance.app.show_messagebox_from_thread("error", "Indisponível", "pywin32 necessário.")
            return
        progress_win, _ = self._show_progress_dialog("Serviços", "Carregando serviços do Windows...")
        if not (progress_win and progress_win.winfo_exists()):
            logging.error("Falha ao criar janela progresso para selecionar serviço.")
            return
        if self.root.winfo_exists(): self.root.update_idletasks()  # Garante que a janela de progresso apareça
        threading.Thread(
            target=self._obter_servicos_worker, args=(progress_win, servidor_tab_instance),
            daemon=True, name=f"ServicoWMI-{servidor_tab_instance.nome}"
        ).start()

    def _obter_servicos_worker(self, progress_win, servidor_tab_instance_target):
        if not PYWIN32_AVAILABLE:
            logging.warning("_obter_servicos_worker mas PYWIN32_AVAILABLE é False.")
            if progress_win and progress_win.winfo_exists(): self.root.after(0,
                                                                             lambda: progress_win.destroy() if progress_win.winfo_exists() else None)
            return
        initialized_com = False
        try:
            pythoncom.CoInitialize()
            initialized_com = True
            wmi = win32com.client.GetObject('winmgmts:')
            services_raw = wmi.InstancesOf('Win32_Service')
            nomes_servicos_temp = [s.Name for s in services_raw if
                                   hasattr(s, 'Name') and s.Name and hasattr(s, 'AcceptStop') and s.AcceptStop]
            nomes_servicos_sorted = sorted(nomes_servicos_temp)

            if self.root.winfo_exists():  # Se a janela principal ainda existe
                self.root.after(0, self._mostrar_dialogo_selecao_servico, nomes_servicos_sorted, progress_win,
                                servidor_tab_instance_target)
            elif progress_win and progress_win.winfo_exists():  # Se só a de progresso existe, fecha ela
                progress_win.destroy()

        except pythoncom.com_error as e_com:
            logging.error(f"Tab '{servidor_tab_instance_target.nome}': Erro COM ao listar serviços: {e_com}",
                          exc_info=True)
            error_message = f"Erro COM ({e_com.hresult}): {e_com.strerror}"
            if hasattr(e_com, 'excepinfo') and e_com.excepinfo and len(e_com.excepinfo) > 2:
                error_message += f"\nDetalhes: {e_com.excepinfo[2]}"
            if self.root.winfo_exists():
                self.root.after(0, self._handle_erro_listar_servicos, error_message, progress_win,
                                servidor_tab_instance_target.nome)
            elif progress_win and progress_win.winfo_exists():
                progress_win.destroy()
        except Exception as e_wmi:
            logging.error(f"Tab '{servidor_tab_instance_target.nome}': Erro WMI: {e_wmi}", exc_info=True)
            if self.root.winfo_exists():
                self.root.after(0, self._handle_erro_listar_servicos, str(e_wmi), progress_win,
                                servidor_tab_instance_target.nome)
            elif progress_win and progress_win.winfo_exists():
                progress_win.destroy()
        finally:
            if initialized_com:
                try:
                    pythoncom.CoUninitialize()
                except Exception:
                    pass
            # Garante que a janela de progresso seja fechada se a root não existir mais e a janela de progresso sim
            if progress_win and progress_win.winfo_exists() and not self.root.winfo_exists():
                try:
                    progress_win.destroy()
                except Exception:
                    pass

    def _handle_erro_listar_servicos(self, error_message, progress_win, nome_tab_origem):
        if progress_win and progress_win.winfo_exists():
            try:
                progress_win.destroy()
            except Exception:
                pass
        if self.root.winfo_exists():
            Messagebox.show_error(f"Erro ao obter serviços para '{nome_tab_origem}':\n{error_message}", "Erro WMI",
                                  parent=self.root)

    def _mostrar_dialogo_selecao_servico(self, nomes_servicos, progress_win, servidor_tab_instance_target):
        if progress_win and progress_win.winfo_exists():
            try:
                progress_win.destroy()
            except Exception:
                pass
        if not nomes_servicos:
            if self.root.winfo_exists():
                Messagebox.show_warning(
                    f"Nenhum serviço gerenciável encontrado para '{servidor_tab_instance_target.nome}'.",
                    "Seleção de Serviço", parent=self.root)
            return

        dialog = ttk.Toplevel(self.root)
        dialog.title(f"Selecionar Serviço para '{servidor_tab_instance_target.nome}'")
        dialog.geometry("500x400")
        dialog.transient(self.root);
        dialog.grab_set()
        dialog.protocol("WM_DELETE_WINDOW", dialog.destroy)

        ttk.Label(dialog, text=f"Escolha o serviço para '{servidor_tab_instance_target.nome}':", font="-size 10").pack(
            pady=(10, 5))
        search_frame = ttk.Frame(dialog);
        search_frame.pack(fill='x', padx=10)
        ttk.Label(search_frame, text="Buscar:").pack(side='left')
        search_var = tk.StringVar()
        search_entry = ttk.Entry(search_frame, textvariable=search_var);
        search_entry.pack(side='left', fill='x', expand=True, padx=5)

        list_frame = ttk.Frame(dialog);
        list_frame.pack(fill='both', expand=True, padx=10, pady=5)
        scrollbar = ttk.Scrollbar(list_frame);
        scrollbar.pack(side='right', fill='y')
        listbox = ttk.Treeview(list_frame, columns=("name",), show="headings", selectmode="browse")
        listbox.heading("name", text="Nome do Serviço");
        listbox.column("name", width=450)
        listbox.pack(side='left', fill='both', expand=True)
        listbox.config(yscrollcommand=scrollbar.set);
        scrollbar.config(command=listbox.yview)
        initial_selection = servidor_tab_instance_target.nome_servico.get()

        def _populate_listbox(query=""):
            listbox.delete(*listbox.get_children())
            filter_query = query.lower().strip()
            item_to_select_id = None
            for name in nomes_servicos:
                if name and (not filter_query or filter_query in name.lower()):
                    item_id = listbox.insert("", "end", values=(name,))
                    if name == initial_selection and not query: item_to_select_id = item_id  # Seleciona o atual se não estiver filtrando
            if item_to_select_id: listbox.selection_set(item_to_select_id); listbox.see(item_to_select_id)

        def on_confirm():
            selection = listbox.selection()
            if selection:
                selected_item_values = listbox.item(selection[0], "values")
                if selected_item_values:
                    servidor_tab_instance_target.set_selected_service(selected_item_values[0])
                    dialog.destroy()
                else:  # Deve ser raro
                    if dialog.winfo_exists(): Messagebox.show_warning("Falha ao obter nome do serviço.", parent=dialog)
            else:
                if dialog.winfo_exists(): Messagebox.show_warning("Nenhum serviço selecionado.", parent=dialog)

        search_entry.bind("<KeyRelease>", lambda e: _populate_listbox(search_var.get()))
        listbox.bind("<Double-1>", lambda e: on_confirm())
        _populate_listbox()  # Popula inicialmente

        btn_frame = ttk.Frame(dialog);
        btn_frame.pack(pady=10)
        ttk.Button(btn_frame, text="Confirmar", command=on_confirm, bootstyle=SUCCESS).pack(side='left', padx=5)
        ttk.Button(btn_frame, text="Cancelar", command=dialog.destroy, bootstyle=DANGER).pack(side='left', padx=5)

        self.root.update_idletasks()  # Para obter dimensões corretas para centralizar
        ws, hs = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        w_dialog, h_dialog = dialog.winfo_width(), dialog.winfo_height()
        if w_dialog <= 1 or h_dialog <= 1: w_dialog, h_dialog = 500, 400  # Fallback de tamanho
        x_pos, y_pos = (ws / 2) - (w_dialog / 2), (hs / 2) - (h_dialog / 2)
        dialog.geometry(f'{w_dialog}x{h_dialog}+{int(x_pos)}+{int(y_pos)}')
        search_entry.focus_set()
        dialog.wait_window()

    def _show_progress_dialog(self, title, message):
        progress_win = ttk.Toplevel(self.root)
        progress_win.title(str(title) if title else "Progresso")
        progress_win.geometry("300x100");
        progress_win.resizable(False, False)
        progress_win.transient(self.root);
        progress_win.grab_set()
        ttk.Label(progress_win, text=str(message) if message else "Carregando...", bootstyle=PRIMARY).pack(pady=10)
        pb = ttk.Progressbar(progress_win, mode='indeterminate', length=280);
        pb.pack(pady=10);
        pb.start(10)
        progress_win.update_idletasks()
        try:  # Centralizar
            width, height = progress_win.winfo_width(), progress_win.winfo_height()
            if width <= 1 or height <= 1: width, height = 300, 100
            x_pos = (self.root.winfo_screenwidth() // 2) - (width // 2)
            y_pos = (self.root.winfo_screenheight() // 2) - (height // 2)
            progress_win.geometry(f'{width}x{height}+{int(x_pos)}+{int(y_pos)}')
        except tk.TclError:
            logging.warning("TclError ao tentar centralizar _show_progress_dialog (janela pode estar fechando).")
        return progress_win, pb

    def set_status_from_thread(self, message):
        if self.root.winfo_exists() and hasattr(self, 'status_label_var'):
            self.root.after(0, lambda: self.status_label_var.set(str(message)[:200]))

    def show_messagebox_from_thread(self, boxtype, title, message):
        if self.root.winfo_exists():
            safe_title = str(title) if title is not None else "Notificação"
            safe_message = str(message) if message is not None else ""
            parent_win = self.root  # Default to root
            # Tenta pegar a janela ativa se for um dialog
            # active_window = self.root.focus_get()
            # if isinstance(active_window, tk.Toplevel) and active_window.winfo_exists():
            #     parent_win = active_window

            max_msg_len = 500
            if len(safe_message) > max_msg_len: safe_message = safe_message[:max_msg_len] + "...\n(Mensagem truncada)"

            def _show_mb():
                # Re-check parent_win existence as it might close between scheduling and execution
                if parent_win.winfo_exists():
                    if boxtype == "info":
                        Messagebox.show_info(safe_message, safe_title, parent=parent_win)
                    elif boxtype == "error":
                        Messagebox.show_error(safe_message, safe_title, parent=parent_win)
                    elif boxtype == "warning":
                        Messagebox.show_warning(safe_message, safe_title, parent=parent_win)

            self.root.after(0, _show_mb)

    def show_about(self):
        about_win = ttk.Toplevel(self.root)
        about_win.title("Sobre PredPy Server Restarter")  # Atualizado
        about_win.geometry("480x420")  # Aumentado para novo texto
        about_win.resizable(False, False)
        about_win.transient(self.root);
        about_win.grab_set()
        frame = ttk.Frame(about_win, padding=20);
        frame.pack(fill='both', expand=True)
        ttk.Label(frame, text="PQDT_Raphael Server Restarter", font="-size 16 -weight bold").pack(pady=(0, 10))
        ttk.Label(frame, text="Versão 1.1.1 (Bug fixes)", font="-size 10").pack()  # Atualiza versão
        ttk.Separator(frame).pack(fill='x', pady=10)
        desc = ("Ferramenta para monitorar logs de múltiplos servidores,\n"
                "detectar uma mensagem de gatilho específica e\n"
                "reiniciar o serviço do servidor automaticamente ou em horários agendados.\n\n"
                "Funcionalidades:\n"
                "- Abas para múltiplos servidores\n"
                "- Monitoramento de logs em tempo real\n"
                "- Mensagem de log configurável para gatilho\n"
                "- Reinício de serviço (Windows) por gatilho ou agendado\n"
                "- Configuração de horários de reinício\n"
                "- Ícone personalizado na janela e bandeja\n"
                "- Imagem de fundo personalizável\n"
                "- Minimizar para a bandeja do sistema\n"
                "- Temas visuais (via ttkbootstrap)")
        ttk.Label(frame, text=desc, justify='left').pack(pady=10)
        ttk.Separator(frame).pack(fill='x', pady=10)
        ttk.Button(frame, text="Fechar", command=about_win.destroy, bootstyle=PRIMARY).pack(pady=(15, 0))
        self.root.update_idletasks()
        w_about, h_about = about_win.winfo_width(), about_win.winfo_height()
        if w_about <= 1 or h_about <= 1: w_about, h_about = 480, 420
        x_pos = (self.root.winfo_screenwidth() // 2) - (w_about // 2)
        y_pos = (self.root.winfo_screenheight() // 2) - (h_about // 2)
        about_win.geometry(f'{w_about}x{h_about}+{int(x_pos)}+{int(y_pos)}')
        about_win.wait_window()


def main():
    root_window = ttk.Window()
    app = ServerRestarterApp(root_window)
    try:
        root_window.mainloop()
    except KeyboardInterrupt:
        logging.info("Interrupção por teclado. Encerrando...")
        if app: app.shutdown_application()
    except Exception as e:
        logging.critical(f"Erro não tratado no loop principal: {e}", exc_info=True)
        if app: app.shutdown_application()
    finally:
        logging.info("Aplicação finalizada (bloco finally do main).")


if __name__ == '__main__':
    if not PIL_AVAILABLE:  # Removido pystray da condição principal, pois é checado individualmente
        print(
            "AVISO: Pillow (PIL) não está instalado. Ícone da aplicação, imagem de fundo e funcionalidade de bandeja podem ser limitados ou desabilitados.")
        logging.warning("Pillow (PIL) não está instalado. Ícone, fundo e bandeja limitados.")

    if not PYWIN32_AVAILABLE and platform.system() == "Windows":
        logging.warning("pywin32 não instalado. Funcionalidades de serviço Windows desabilitadas.")


    def handle_unhandled_thread_exception(args):
        thread_name = args.thread.name if hasattr(args, 'thread') and args.thread else 'ThreadDesconhecida'
        logging.critical(f"EXCEÇÃO NÃO CAPTURADA NA THREAD '{thread_name}':",
                         exc_info=(args.exc_type, args.exc_value, args.exc_traceback))


    threading.excepthook = handle_unhandled_thread_exception
    main()