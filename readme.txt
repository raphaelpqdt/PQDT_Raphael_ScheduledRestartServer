# PQDT_Raphael Server Auto-Restarter

O PredPy Server Auto-Restarter é uma aplicação de interface gráfica (GUI) projetada para monitorar logs de múltiplos servidores e reiniciar automaticamente os serviços associados do Windows com base em mensagens de gatilho nos logs ou em horários agendados.

Ele é construído usando Python com Tkinter e ttkbootstrap para a interface gráfica, e oferece funcionalidades robustas para administradores de servidores que precisam de uma maneira automatizada de garantir a disponibilidade de seus serviços.

## ✨ Funcionalidades Principais

* **Gerenciamento Multi-Servidor:**
    * Configure e monitore múltiplos servidores, cada um em sua própria aba dedicada.
    * Adicione, remova e renomeie configurações de servidor dinamicamente.
* **Monitoramento de Logs em Tempo Real:**
    * Exibe logs de `console.log` (localizados em subpastas como `logs_AAAA-MM-DD_HH-MM-SS`) em tempo real.
    * Filtro de log para exibir apenas linhas relevantes (case-insensitive).
    * Pause/Retome o acompanhamento ao vivo dos logs.
    * Busca de texto dentro da área de log da aba.
    * Rolagem automática opcional para o final do log.
* **Reinício Automático de Serviços (Windows):**
    * **Baseado em Gatilho:**
        * Defina uma mensagem de log específica que, ao ser detectada, acionará o reinício do serviço.
        * Configure um atraso (em segundos) após a detecção do gatilho antes de iniciar o processo de reinício.
        * Defina atrasos para parada e início do serviço durante o ciclo de reinício.
    * **Baseado em Agendamento:**
        * Configure reinícios em horários pré-definidos (de hora em hora).
        * Adicione horários de reinício personalizados (HH:MM).
* **Integração com Serviços do Windows:**
    * Selecione o serviço do Windows associado a cada configuração de servidor.
    * Exibe o status atual do serviço (Rodando, Parado, etc.).
    * Utiliza `sc.exe` para parar e iniciar serviços. (Requer `pywin32`)
* **Interface Gráfica Amigável:**
    * Interface moderna e temática com `ttkbootstrap`.
    * Múltiplos temas visuais selecionáveis.
    * Barra de menu para fácil acesso às funcionalidades.
    * Barra de status para feedback ao usuário.
    * Ícone de aplicação personalizado e imagem de fundo (opcional).
* **Minimizar para a Bandeja do Sistema:**
    * A aplicação pode ser minimizada para a bandeja do sistema ao invés de ser fechada. (Requer `Pillow` e `pystray`)
* **Configuração Persistente:**
    * As configurações da aplicação e dos servidores são salvas em um arquivo `server_restarter_config.json`.
    * Carregue e salve arquivos de configuração.
* **Logging da Aplicação:**
    * A própria aplicação registra suas operações e erros em `server_restarter.log`.
    * Uma aba "Log do Sistema (Restarter)" exibe o conteúdo deste arquivo.
* **Exportação de Logs:**
    * Exporte o conteúdo da área de log da aba atual (servidor ou sistema) para um arquivo de texto.

## 🔧 Pré-requisitos

* Python 3.x
* Bibliotecas Python (podem ser instaladas via pip):
    * `ttkbootstrap` (para a interface gráfica moderna)
    * `Pillow (PIL)` (para ícones personalizados, imagem de fundo e ícone da bandeja)
    * `pystray` (para funcionalidade de ícone da bandeja do sistema)
    * `pywin32` (essencial para gerenciamento de serviços do Windows; opcional em outros sistemas, mas a funcionalidade principal de reinício será desabilitada)



## 🏃 Como Usar

1.  Execute o programa .exe
    ```

2.  **Interface Principal:**
    * A janela principal exibirá abas. Inicialmente, pode haver uma aba "Servidor 1 (Padrão)" e uma aba "Log do Sistema (Restarter)".
    * Use o menu "Servidores" > "Adicionar Novo Servidor" para criar novas configurações de servidor.
    * Use "Servidores" > "Remover Servidor Atual" ou "Renomear Servidor Atual..." conforme necessário.

3.  **Configurando uma Aba de Servidor:**
    * **Pasta de Logs:** Clique em "Pasta de Logs" para selecionar a pasta raiz onde os logs do seu servidor são armazenados (ex: a pasta que contém as subpastas `logs_AAAA-MM-DD_HH-MM-SS/`).
    * **Serviço Win:** Clique em "Serviço Win" para selecionar o serviço do Windows associado a este servidor (requer `pywin32`). O status do serviço será exibido.
    * **Filtro:** Digite um texto para filtrar as linhas de log exibidas na área de log.
    * **Controles de Log:** Use "Pausar/Retomar" e "Limpar Log" para controlar a exibição.
    * **Opções de Reinício (Gatilho):**
        * Marque "Reiniciar servidor automaticamente..." para habilitar o reinício por gatilho.
        * Configure a "Mensagem de Log para Gatilho" exata que deve ser detectada.
        * Ajuste os "Delays" para o reinício, parada do serviço e início do serviço.
    * **Reinícios Agendados:**
        * Marque os horários pré-definidos (HH:00) desejados.
        * Adicione horários personalizados (HH:MM) na seção correspondente.

4.  **Menu Arquivo:**
    * "Salvar Configuração": Salva o estado atual de todas as abas de servidor e o tema no arquivo `server_restarter_config.json`.
    * "Carregar Configuração...": Permite carregar um arquivo de configuração `.json` previamente salvo.
    * "Sair": Fecha a aplicação.

5.  **Menu Ferramentas:**
    * "Exportar Logs da Aba Atual": Salva o conteúdo da área de log da aba selecionada (servidor ou sistema) em um arquivo de texto.
    * "Mudar Tema": Permite selecionar diferentes temas visuais fornecidos pelo `ttkbootstrap`.

6.  **Minimizar para Bandeja:**
    * Se `Pillow` e `pystray` estiverem instalados, clicar no botão "X" da janela minimizará a aplicação para a bandeja do sistema.
    * Clique com o botão direito no ícone da bandeja para "Mostrar" ou "Sair".

## ⚙️ Configuração

* **Arquivo Principal de Configuração:** `server_restarter_config.json`
    * Este arquivo é criado/atualizado automaticamente quando você salva a configuração pelo menu "Arquivo".
    * Ele armazena as configurações de cada aba de servidor (caminhos, nome do serviço, mensagem de gatilho, delays, agendamentos) e o tema selecionado.
* **Ícones e Imagens:**
    * Ícone da aplicação: `predpy.ico`
    * Imagem de fundo: `predpy.png`
    * Estes arquivos devem estar presentes no mesmo diretório do script ou empacotados corretamente se você criar um executável. O alfa da imagem de fundo pode ser ajustado pela constante `BACKGROUND_ALPHA_MULTIPLIER`.

## 📄 Arquivos de Log

* **Log da Aplicação:** `server_restarter.log`
    * Contém informações sobre as operações do próprio PredPy Server Auto-Restarter, incluindo inicialização, detecção de gatilhos, erros, etc.
* **Logs do Servidor Monitorado:**
    * A aplicação espera encontrar arquivos `console.log` dentro de subpastas com o padrão `logs_AAAA-MM-DD_HH-MM-SS` na "Pasta de Logs" configurada para cada servidor.

A funcionalidade principal de **reinício de serviço** depende da biblioteca `pywin32` e do uso do comando `sc.exe`, o que a torna **primariamente destinada ao Windows**.
A interface gráfica pode rodar em outros sistemas operacionais (Linux, macOS), mas as funcionalidades de gerenciamento de serviços do Windows estarão desabilitadas.

---

Desenvolvido por PQDT_Raphael