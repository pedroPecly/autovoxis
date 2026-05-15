"""
🏥 AUTO VOXIS — FORMATADOR + CARGA AUTOMÁTICA NO VOXIS
-------------------------------------------------------
Fase 1 · Formatação  — gera as 4 variantes dos CSVs a partir dos originais
Fase 2 · Automação   — sobe cada arquivo no VOXIS via Selenium

Autor: Pedro Henrique
"""

import os
import json
import time
import threading
import queue
import subprocess
import tkinter as tk
from tkinter import filedialog, messagebox
from datetime import datetime

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.common.exceptions import (
    TimeoutException, NoSuchElementException,
    StaleElementReferenceException, WebDriverException,
)

# ══════════════════════════════════════════════════════════════════
# CAMINHOS PADRÃO
# ══════════════════════════════════════════════════════════════════
_BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
DIR_ENTRADA  = os.path.join(_BASE_DIR, "voxis", "entrada")
DIR_SAIDA    = os.path.join(_BASE_DIR, "voxis", "saida")
ARQUIVO_CONF = os.path.join(_BASE_DIR, "voxis_config.json")

# Garante que as pastas de trabalho existem ao iniciar
os.makedirs(DIR_ENTRADA, exist_ok=True)
os.makedirs(DIR_SAIDA,   exist_ok=True)

# ══════════════════════════════════════════════════════════════════
# PASSOS DO PROCESSO — gerados dinamicamente a partir da config
# ══════════════════════════════════════════════════════════════════
def _gerar_passos(tabelas: list) -> list:
    passos = []
    pid = 1
    for tabela in tabelas:
        slug = tabela.replace("-", "")
        for transacao, verbo in (("I", "INCLUIR"), ("A", "ALTERAR")):
            passos.append({
                "id":        pid,
                "transacao": transacao,
                "tab_preco": tabela,
                "pasta":     f"{pid:02d}_{verbo.lower()}_{slug}",
                "label":     f"{pid} · {verbo} — {tabela}",
            })
            pid += 1
    return passos

PASSOS: list = []   # preenchido após carregar config

# ══════════════════════════════════════════════════════════════════
# CONFIGURAÇÃO  (voxis_config.json — ignorado pelo git)
# ══════════════════════════════════════════════════════════════════

# Chaves obrigatórias — valores vivem exclusivamente no voxis_config.json
_CHAVES_CONFIG = (
    "url_sistema",
    "usuario",
    "senha",
    "timeout_aguarde",
    "servico",
    "menu_administracao_id",
    "frame_conteudo",
    "tabelas_preco",
)

def _carregar_config() -> dict:
    if not os.path.exists(ARQUIVO_CONF):
        raise FileNotFoundError(
            f"Arquivo de configuração não encontrado: {ARQUIVO_CONF}\n"
            "Crie o arquivo voxis_config.json com as chaves necessárias.\n"
            "Consulte o README_AUTOVOXIS.md para o modelo completo.")
    try:
        with open(ARQUIVO_CONF, "r", encoding="utf-8") as f:
            conf = json.load(f)
    except json.JSONDecodeError as ex:
        raise ValueError(f"voxis_config.json com JSON inválido: {ex}")

    faltando = [k for k in _CHAVES_CONFIG if k not in conf]
    if faltando:
        raise KeyError(
            f"Chave(s) ausente(s) no voxis_config.json: {', '.join(faltando)}\n"
            "Consulte o README_AUTOVOXIS.md para o modelo completo.")
    return conf

_config_error = None
try:
    CONF   = _carregar_config()
    PASSOS = _gerar_passos(CONF["tabelas_preco"])
except (FileNotFoundError, ValueError, KeyError) as _ex:
    _config_error = _ex
    CONF   = {}
    PASSOS = []

# ══════════════════════════════════════════════════════════════════
# ESTADO GLOBAL
# ══════════════════════════════════════════════════════════════════
log_queue             = queue.Queue()
ui                    = None
arquivos_selecionados = []
pasta_saida           = DIR_SAIDA

# — Automação —
_driver        = None
_stop_flag     = False
_pause_event   = threading.Event()
_pause_event.set()   # inicia "rodando"
_automation_on = False


# ══════════════════════════════════════════════════════════════════
# LOG
# ══════════════════════════════════════════════════════════════════
def _nivel(msg: str) -> str:
    m = msg.strip()
    if any(x in m for x in ("✅", "Concluído", "sucesso", "Login realizado", "OK")): return "ok"
    if any(x in m for x in ("❌", "CRÍTICO")):                                       return "erro"
    if any(x in m for x in ("⚠️", "Timeout")):                                      return "aviso"
    if any(x in m for x in ("⏳", "Aguard", "aguard", "Processan")):                return "aviso"
    if any(x in m for x in ("🚀","📁","📂","→","📋","🔑","📄","🎉","⏸","🛑","▶")): return "info"
    if "═" in m or "─" in m:                                                         return "dim"
    return "info"

def log(mensagem: str):
    hora = datetime.now().strftime("%H:%M:%S")
    print(f"[{hora}] {mensagem}")
    if ui:
        log_queue.put((mensagem, _nivel(mensagem)))


# ══════════════════════════════════════════════════════════════════
# FASE 1 — FORMATAÇÃO DO CSV
# ══════════════════════════════════════════════════════════════════
def formatar_csv(caminho_entrada: str, caminho_saida: str,
                 tip_transacao: str, tab_preco: str) -> int:
    """
    Transforma o CSV original exportado do banco no formato esperado pelo VOXIS:
      - Remove 1ª coluna (sequencial do DB, sem nome no header)
      - Resequencia NUM_LINHA_ARQUIVO (1, 2, 3…)
      - Substitui TIP_TRANSACAO e TAB_PRECO conforme parâmetros
    Retorna a quantidade de linhas de dados geradas.
    """
    with open(caminho_entrada, "r", encoding="utf-8", newline="") as f:
        conteudo = f.read().splitlines()

    if not conteudo:
        raise ValueError("Arquivo vazio")

    header_raw = conteudo[0].lstrip(";")
    colunas    = header_raw.split(";")

    for col in ("NUM_LINHA_ARQUIVO", "TIP_TRANSACAO", "TAB_PRECO"):
        if col not in colunas:
            raise ValueError(f"Coluna obrigatória '{col}' não encontrada")

    idx_num   = colunas.index("NUM_LINHA_ARQUIVO")
    idx_trans = colunas.index("TIP_TRANSACAO")
    idx_tab   = colunas.index("TAB_PRECO")

    linhas_saida = [header_raw]
    seq = 1

    for linha in conteudo[1:]:
        linha = linha.strip()
        if not linha:
            continue
        cols = linha.split(";")[1:]   # descarta 1ª coluna sequencial do DB
        while len(cols) < len(colunas):
            cols.append("")
        cols[idx_num]   = str(seq)
        cols[idx_trans] = tip_transacao
        cols[idx_tab]   = tab_preco
        linhas_saida.append(";".join(cols))
        seq += 1

    os.makedirs(os.path.dirname(caminho_saida), exist_ok=True)
    with open(caminho_saida, "w", encoding="utf-8", newline="") as f:
        f.write("\n".join(linhas_saida))

    return seq - 1


def executar_formatacao():
    """Roda em thread separada. Formata todos os CSVs nas 4 variantes."""
    if not arquivos_selecionados:
        log("❌ Nenhum arquivo selecionado!")
        return
    if not pasta_saida:
        log("❌ Pasta de saída não definida!")
        return

    ui.root.after(0, ui.bloquear_botoes_fase1)

    total_ok = total_err = 0
    log("═" * 48)
    log(f"🚀 Formatando {len(arquivos_selecionados)} arquivo(s) em 4 variantes...")

    for passo in PASSOS:
        pasta_passo = os.path.join(pasta_saida, passo["pasta"])
        os.makedirs(pasta_passo, exist_ok=True)
        log(f"\n📁 {passo['label']}")

        for arq in sorted(arquivos_selecionados):
            nome = os.path.splitext(os.path.basename(arq))[0].replace("_original", "")
            dest = os.path.join(
                pasta_passo,
                f"{nome}_{passo['transacao']}_{passo['tab_preco'].replace('-','')}.csv")
            try:
                qtd = formatar_csv(arq, dest, passo["transacao"], passo["tab_preco"])
                log(f"  ✅ {nome}  →  {qtd} linhas")
                total_ok += 1
            except Exception as ex:
                log(f"  ❌ {nome}: {ex}")
                total_err += 1

    log("\n" + "═" * 48)
    log(f"✅ Formatação concluída!  {total_ok} gerado(s)."
        + (f"  ⚠️ {total_err} erro(s)." if total_err else ""))

    ui.root.after(0, ui.liberar_botoes_fase1)
    ui.root.after(0, ui.habilitar_automacao)


# ══════════════════════════════════════════════════════════════════
# FASE 2 — SELENIUM / AUTOMAÇÃO VOXIS
# ══════════════════════════════════════════════════════════════════

def _criar_driver() -> webdriver.Chrome:
    opts = webdriver.ChromeOptions()
    opts.add_argument("--start-maximized")
    opts.add_argument("--disable-notifications")
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)
    opts.add_experimental_option("prefs", {
        "credentials_enable_service": False,
        "profile.password_manager_enabled": False,
    })
    # Timeout do ChromeDriver elevado para suportar uploads de arquivos pesados.
    # keep_alive=True mantém a conexão HTTP aberta durante o processamento longo.
    timeout_proc = int(CONF.get("timeout_processamento", 120))
    driver = webdriver.Chrome(
        service=Service(ChromeDriverManager().install()),
        options=opts,
    )
    driver.command_executor.set_timeout(timeout_proc + 60)
    return driver


def _switch_to_menu_frame(driver: webdriver.Chrome):
    """
    O SAUDI normalmente carrega o menu em um frame separado.
    Tenta encontrar o frame que contém o elemento de menu
    percorrendo todos os frames da página.
    """
    menu_id = CONF["menu_administracao_id"]
    driver.switch_to.default_content()

    # Tenta no conteúdo principal primeiro
    try:
        driver.find_element(By.ID, menu_id)
        return
    except NoSuchElementException:
        pass

    # Percorre <frame> e <iframe>
    for tag in ("frame", "iframe"):
        frames = driver.find_elements(By.TAG_NAME, tag)
        for frame in frames:
            try:
                driver.switch_to.frame(frame)
                driver.find_element(By.ID, menu_id)
                return   # achou — permanece nesse frame
            except (NoSuchElementException, WebDriverException):
                driver.switch_to.default_content()

    raise RuntimeError(
        "Frame do menu não encontrado. Verifique se o login foi bem-sucedido.")


def _fazer_login(driver: webdriver.Chrome, wait: WebDriverWait):
    log("🔑 Acessando VOXIS...")
    driver.get(CONF["url_sistema"])

    wait.until(EC.presence_of_element_located((By.NAME, "login")))

    campo_login = driver.find_element(By.NAME, "login")
    campo_senha = driver.find_element(By.NAME, "senha")
    campo_login.clear(); campo_login.send_keys(CONF["usuario"])
    campo_senha.clear(); campo_senha.send_keys(CONF["senha"])

    driver.find_element(By.ID, "btn_enviar").click()
    log("🔑 Login enviado — aguardando autenticação...")

    # Aguarda o menu aparecer como confirmação de login OK.
    # O SAUDI usa frameset, então o menu pode estar em um frame filho —
    # fazemos polling manual percorrendo todos os frames.
    menu_id  = CONF["menu_administracao_id"]
    deadline = time.time() + int(CONF.get("timeout_aguarde", 40))
    encontrado = False
    while time.time() < deadline and not encontrado:
        driver.switch_to.default_content()
        try:
            driver.find_element(By.ID, menu_id)
            encontrado = True
            break
        except NoSuchElementException:
            pass
        for tag in ("frame", "iframe"):
            for fr in driver.find_elements(By.TAG_NAME, tag):
                try:
                    driver.switch_to.frame(fr)
                    driver.find_element(By.ID, menu_id)
                    encontrado = True
                    break
                except (NoSuchElementException, WebDriverException):
                    driver.switch_to.default_content()
            if encontrado:
                break
        if not encontrado:
            time.sleep(0.8)
    if not encontrado:
        raise TimeoutException(
            f"Menu '{menu_id}' não encontrado após login — "
            "verifique credenciais ou ID do menu na config.")
    log("✅ Login realizado com sucesso!")


def _clicar_texto_em_qualquer_frame(driver: webdriver.Chrome,
                                    texto: str, timeout: int = 15):
    """
    Procura em todos os frames (até 2 níveis) um elemento com o texto exato
    e clica nele. Usa índices para evitar StaleElementReferenceException.
    Retorna o nome do frame onde achou.
    """
    xp = f"//*[normalize-space(text())='{texto}']"
    deadline = time.time() + timeout
    while time.time() < deadline:
        driver.switch_to.default_content()
        # contexto raiz
        try:
            el = driver.find_element(By.XPATH, xp)
            try: el.click()
            except Exception: driver.execute_script("arguments[0].click();", el)
            return "(raiz)"
        except NoSuchElementException:
            pass
        # nível 1
        for tag in ("frame", "iframe"):
            n1 = len(driver.find_elements(By.TAG_NAME, tag))
            for i in range(n1):
                try:
                    driver.switch_to.default_content()
                    frs1 = driver.find_elements(By.TAG_NAME, tag)
                    if i >= len(frs1):
                        break
                    fn1 = (frs1[i].get_attribute("name")
                           or frs1[i].get_attribute("id") or f"#{i}")
                    driver.switch_to.frame(i)
                    try:
                        el = driver.find_element(By.XPATH, xp)
                        tag = el.tag_name   # lê ANTES do clique
                        try: el.click()
                        except Exception:
                            driver.execute_script("arguments[0].click();", el)
                        return fn1
                    except NoSuchElementException:
                        pass
                    # nível 2
                    for tag2 in ("frame", "iframe"):
                        n2 = len(driver.find_elements(By.TAG_NAME, tag2))
                        for j in range(n2):
                            try:
                                driver.switch_to.default_content()
                                driver.switch_to.frame(i)
                                frs2 = driver.find_elements(By.TAG_NAME, tag2)
                                if j >= len(frs2):
                                    break
                                fn2 = (frs2[j].get_attribute("name")
                                       or frs2[j].get_attribute("id") or f"#{j}")
                                driver.switch_to.frame(j)
                                el = driver.find_element(By.XPATH, xp)
                                _ = el.tag_name   # valida antes do clique
                                try: el.click()
                                except Exception:
                                    driver.execute_script("arguments[0].click();", el)
                                return f"{fn1}>{fn2}"
                            except (NoSuchElementException, WebDriverException,
                                    StaleElementReferenceException):
                                pass
                except (WebDriverException, StaleElementReferenceException):
                    pass
        time.sleep(0.5)
    raise TimeoutException(f"Elemento com texto '{texto}' não encontrado")


def _encontrar_em_frame_conteudo(driver: webdriver.Chrome, wait: WebDriverWait,
                                 by, value):
    """
    Busca um elemento em frameConteudo e até 2 níveis de iframes aninhados.
    Deixa o driver posicionado no frame onde encontrou.
    """
    timeout = int(CONF.get("timeout_aguarde", 40))
    deadline = time.time() + timeout
    while time.time() < deadline:
        # Nível 0: frameConteudo direto
        try:
            driver.switch_to.default_content()
            driver.switch_to.frame(
                driver.find_element(By.NAME, CONF["frame_conteudo"]))
        except Exception:
            time.sleep(0.5)
            continue
        try:
            return driver.find_element(by, value)
        except NoSuchElementException:
            pass

        # Nível 1: iframes dentro de frameConteudo
        for tag in ("frame", "iframe"):
            n1 = len(driver.find_elements(By.TAG_NAME, tag))
            for i in range(n1):
                try:
                    driver.switch_to.default_content()
                    driver.switch_to.frame(
                        driver.find_element(By.NAME, CONF["frame_conteudo"]))
                    if i >= len(driver.find_elements(By.TAG_NAME, tag)):
                        break
                    driver.switch_to.frame(i)
                    try:
                        return driver.find_element(by, value)
                    except NoSuchElementException:
                        pass

                    # Nível 2: iframes dentro do iframe de nível 1
                    for tag2 in ("frame", "iframe"):
                        n2 = len(driver.find_elements(By.TAG_NAME, tag2))
                        for j in range(n2):
                            try:
                                driver.switch_to.default_content()
                                driver.switch_to.frame(
                                    driver.find_element(By.NAME, CONF["frame_conteudo"]))
                                if i >= len(driver.find_elements(By.TAG_NAME, tag)):
                                    break
                                driver.switch_to.frame(i)
                                if j >= len(driver.find_elements(By.TAG_NAME, tag2)):
                                    break
                                driver.switch_to.frame(j)
                                return driver.find_element(by, value)
                            except (NoSuchElementException, WebDriverException,
                                    StaleElementReferenceException):
                                pass
                except (WebDriverException, StaleElementReferenceException):
                    pass
        time.sleep(0.5)
    raise TimeoutException(f"Elemento ({by}='{value}') não encontrado em frameConteudo")


def _navegar_para_upload(driver: webdriver.Chrome, wait: WebDriverWait):
    """
    Menu: Administração → Integração → (Upload de Arquivos se existir).
    Em algumas versões do SAUDI, Integração já carrega o Integration Manager
    diretamente sem o link 'Upload de Arquivos'.
    """
    # 1) Clica em Administração (topFrame, <div>)
    _clicar_texto_em_qualquer_frame(driver, "Administração")
    log("  → Administração")

    # 2) Clica em Integração (frameConteudo, <a>)
    _clicar_texto_em_qualquer_frame(driver, "Integração")
    log("  → Integração")

    # 3) 'Upload de Arquivos' é opcional — sistema pode ir direto ao manager
    try:
        _clicar_texto_em_qualquer_frame(driver, "Upload de Arquivos",
                                        timeout=5)
        log("  → Upload de Arquivos")
    except Exception:
        log("  → Upload de Arquivos não encontrado — sistema foi direto ao manager")

    # 4) Aguarda o select de serviço (busca em nested frames)
    _encontrar_em_frame_conteudo(driver, wait, By.ID, "filtroServiceId")
    log("📂 Formulário de upload carregado")


def _selecionar_cmat(driver: webdriver.Chrome, wait: WebDriverWait):
    """
    Seleciona o serviço configurado (padrão: CMAT) via JS direto e aciona
    o formulário de upload usando submeter() + mostrarDivs(true).
    """
    servico = CONF["servico"]

    # Garante que o driver está em frameConteudoExterno (nível 1 de aninhamento)
    _encontrar_em_frame_conteudo(driver, wait, By.ID, "filtroServiceId")

    # Re-entra limpo em frameConteudoExterno por índice para evitar stale refs
    driver.switch_to.default_content()
    driver.switch_to.frame(driver.find_element(By.NAME, CONF["frame_conteudo"]))
    driver.switch_to.frame(0)

    # 1) Define o valor e chama submeter() diretamente — não depende do onchange
    driver.execute_script("""
        var sel = document.getElementById('filtroServiceId');
        if (sel) sel.value = arguments[0];
        if (typeof submeter === 'function') {
            submeter('selecionarResumoServico');
        } else if (sel) {
            sel.dispatchEvent(new Event('change', {bubbles: true}));
        }
    """, servico)
    log(f"  ✅ Serviço {servico} selecionado (submeter chamado)")

    # 2) Aguarda formulário recarregar com os radio buttons (máx 15s)
    deadline = time.time() + 15
    while time.time() < deadline:
        try:
            driver.switch_to.default_content()
            driver.switch_to.frame(
                driver.find_element(By.NAME, CONF["frame_conteudo"]))
            driver.switch_to.frame(0)
            if driver.find_elements(By.NAME, "tipOperacao"):
                break
        except Exception:
            pass
        time.sleep(0.8)

    # 3) Seleciona 'NOVO ARQUIVO' e chama mostrarDivs(true) para revelar
    #    o sub-iframe com o campo de arquivo
    driver.switch_to.default_content()
    driver.switch_to.frame(driver.find_element(By.NAME, CONF["frame_conteudo"]))
    driver.switch_to.frame(0)
    driver.execute_script("""
        var radios = document.getElementsByName('tipOperacao');
        if (radios && radios.length > 0) radios[0].checked = true;
        if (typeof mostrarDivs === 'function') mostrarDivs(true);
    """)
    log("  ✅ Operação 'Novo Arquivo' selecionada")

    # 4) Aguarda o campo de arquivo aparecer (está em sub-frame#1 dentro de
    #    frameConteudoExterno — 3 níveis no total)
    _encontrar_em_frame_conteudo(driver, wait, By.CSS_SELECTOR, "input[type='file']")
    log("  ✅ Formulário pronto para upload")


def _fazer_upload(driver: webdriver.Chrome, wait: WebDriverWait,
                  caminho_arquivo: str):
    """Envia o caminho do arquivo para o input e clica em confirmar."""
    file_input = _encontrar_em_frame_conteudo(
        driver, wait, By.ID, "fileUpload_fileUploadVO_file")
    file_input.send_keys(os.path.abspath(caminho_arquivo))
    log(f"  📄 Arquivo: {os.path.basename(caminho_arquivo)}")

    btn = _encontrar_em_frame_conteudo(driver, wait, By.ID, "btConfirmar")
    btn.click()
    log("  ⏳ Processando...")


def _aguardar_processamento(driver: webdriver.Chrome, wait: WebDriverWait):
    """
    Aguarda o sistema processar o arquivo carregado.

    Estratégia em 3 camadas:
      1. Detecta redirecionamento de página (URL ou título muda)
      2. Detecta desaparecimento do botão Confirmar (formulário submetido)
      3. Fallback: aguarda timeout_processamento segundos (configurável no JSON)

    Configuração em voxis_config.json:
      "timeout_processamento": 180   ← segundos máximos de espera (padrão: 120)
    """
    timeout_proc = int(CONF.get("timeout_processamento", 120))
    log(f"  ⏳ Aguardando processamento (máx {timeout_proc}s)...")

    url_antes    = driver.current_url
    titulo_antes = driver.title
    deadline     = time.time() + timeout_proc

    while time.time() < deadline:
        try:
            # Camada 1: URL ou título mudou → sistema redirecionou após processar
            if driver.current_url != url_antes or driver.title != titulo_antes:
                time.sleep(1)   # estabiliza a página nova
                log("  ✅ Processamento concluído (redirecionamento detectado)")
                return

            # Camada 2: botão Confirmar sumiu → formulário foi processado
            try:
                driver.switch_to.default_content()
                driver.switch_to.frame(
                    driver.find_element(By.NAME, CONF["frame_conteudo"]))
                driver.switch_to.frame(0)
            except Exception:
                pass
            btns = driver.find_elements(By.ID, "btConfirmar")
            if not btns or not btns[0].is_displayed():
                time.sleep(2)   # aguarda página estabilizar
                log("  ✅ Processamento concluído (botão removido da tela)")
                return

        except WebDriverException:
            # Driver em transição de página — sinal de que o upload foi aceito
            time.sleep(1)
            log("  ✅ Processamento concluído (navegação detectada)")
            return

        time.sleep(1)

    # Fallback: timeout esgotado, continua mesmo assim com aviso
    log(f"  ⚠️ Timeout de {timeout_proc}s atingido — continuando para o próximo arquivo")


# ── Loop principal ─────────────────────────────────────────────────
def executar_automacao():
    """Roda em thread separada. Percorre os 4 passos × N arquivos."""
    global _driver, _automation_on, _stop_flag

    _automation_on = True
    _stop_flag     = False
    _pause_event.set()

    total_ok = total_err = 0

    try:
        _driver = _criar_driver()
        wait    = WebDriverWait(_driver, int(CONF.get("timeout_aguarde", 40)))

        _fazer_login(_driver, wait)

        for passo in PASSOS:
            if _stop_flag:
                break

            pasta_passo = os.path.join(pasta_saida, passo["pasta"])
            if not os.path.isdir(pasta_passo):
                log(f"⚠️  Pasta não encontrada: {passo['pasta']} — pulando")
                continue

            arquivos_passo = sorted([
                os.path.join(pasta_passo, f)
                for f in os.listdir(pasta_passo)
                if f.lower().endswith(".csv")
            ])

            if not arquivos_passo:
                log(f"⚠️  Nenhum CSV em {passo['pasta']} — pulando")
                continue

            log(f"\n{'═' * 48}")
            log(f"📋 {passo['label']}  ({len(arquivos_passo)} arquivo(s))")
            ui.root.after(0, lambda p=passo["id"]: ui.marcar_passo_ativo(p))

            for idx, arq in enumerate(arquivos_passo, 1):
                if _stop_flag:
                    break

                _pause_event.wait()   # trava aqui se estiver pausado
                if _stop_flag:
                    break

                nome = os.path.basename(arq)
                log(f"\n  [{idx}/{len(arquivos_passo)}] {nome}")
                ui.root.after(0, lambda n=nome, i=idx, t=len(arquivos_passo):
                              ui.status(arquivo=n, progresso=f"{i}/{t}"))

                try:
                    _navegar_para_upload(_driver, wait)
                    _selecionar_cmat(_driver, wait)
                    _fazer_upload(_driver, wait, arq)
                    _aguardar_processamento(_driver, wait)
                    log(f"  ✅ OK")
                    total_ok += 1
                except TimeoutException:
                    log(f"  ❌ Timeout — {nome}")
                    total_err += 1
                except Exception as ex:
                    log(f"  ❌ Erro: {ex}")
                    total_err += 1

            if not _stop_flag:
                ui.root.after(0, lambda p=passo["id"]: ui.marcar_passo_concluido(p))
                log(f"\n✅ {passo['label']} finalizado!")

        if _stop_flag:
            log("\n🛑 Automação interrompida pelo usuário.")
        else:
            log(f"\n{'═' * 48}")
            log(f"🎉 Automação concluída!  ✅ {total_ok} OK"
                + (f"  ❌ {total_err} erro(s)" if total_err else ""))

    except Exception as ex:
        log(f"\n❌ [ERRO CRÍTICO] {ex}")
    finally:
        if _driver:
            try:
                _driver.quit()
            except Exception:
                pass
            _driver = None
        _automation_on = False
        ui.root.after(0, ui.finalizar_automacao)


# ══════════════════════════════════════════════════════════════════
# UI  (Tkinter — tema escuro, arrastável, sempre no topo)
# ══════════════════════════════════════════════════════════════════
class AutoVoxisUI:
    C = {
        "bg":       "#1e1e2e",
        "card":     "#2a2a3e",
        "card2":    "#313145",
        "bar":      "#11111b",
        "texto":    "#cdd6f4",
        "dim":      "#585b70",
        "azul":     "#89b4fa",
        "ciano":    "#89dceb",
        "verde":    "#a6e3a1",
        "vermelho": "#f38ba8",
        "amarelo":  "#f9e2af",
        "laranja":  "#fab387",
    }

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Auto VOXIS")
        self.root.attributes("-topmost", True)
        self.root.attributes("-alpha", 0.96)
        self.root.overrideredirect(True)
        self.root.configure(bg=self.C["bg"])

        w, h = 420, 700
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        self.root.geometry(f"{w}x{h}+{sw - w - 20}+{sh - h - 60}")
        self.root.resizable(False, False)

        self._dx = self._dy = 0
        self._minimizado    = False
        self._altura_normal = h
        self._btn_passo     = {}
        self._lbl_passo     = {}

        self._build()
        self.root.after(200, self._init_saida_padrao)
        self.root.after(100, self._poll)

    # ────────────────────────────── BUILD
    def _build(self):
        C = self.C

        # ── Barra de título ─────────────────────────────────────
        bar = tk.Frame(self.root, bg=C["bar"], height=36)
        bar.pack(fill="x")
        bar.pack_propagate(False)
        tk.Label(bar, text="🏥  AUTO VOXIS", fg=C["azul"], bg=C["bar"],
                 font=("Segoe UI", 10, "bold")).pack(side="left", padx=12)
        tk.Button(bar, text="✕", fg=C["vermelho"], bg=C["bar"], bd=0,
                  font=("Segoe UI", 11, "bold"), cursor="hand2",
                  activebackground=C["bar"], activeforeground=C["vermelho"],
                  command=self.root.destroy).pack(side="right", padx=8)
        self.btn_min = tk.Button(
            bar, text="─", fg=C["dim"], bg=C["bar"], bd=0,
            font=("Segoe UI", 11, "bold"), cursor="hand2",
            activebackground=C["bar"], activeforeground=C["texto"],
            command=self._toggle_minimizar)
        self.btn_min.pack(side="right", padx=2)
        for w in (bar, *bar.winfo_children()):
            w.bind("<ButtonPress-1>", self._drag_start)
            w.bind("<B1-Motion>",     self._drag_move)

        # ── Corpo ────────────────────────────────────────────────
        self._corpo = tk.Frame(self.root, bg=C["bg"])
        self._corpo.pack(fill="both", expand=True)

        # ── Card de status ───────────────────────────────────────
        card = tk.Frame(self._corpo, bg=C["card"], padx=10, pady=8)
        card.pack(fill="x", padx=8, pady=(6, 0))
        self.lbl_arq     = self._row(card, "ARQUIVOS",  "0 selecionado(s)")
        self.lbl_saida   = self._row(card, "SAÍDA",     "—")
        self.lbl_arquivo = self._row(card, "ENVIANDO",  "—")
        self.lbl_prog    = self._row(card, "PROGRESSO", "—")

        # ── Card de passos ───────────────────────────────────────
        pcard = tk.Frame(self._corpo, bg=C["card"], padx=10, pady=8)
        pcard.pack(fill="x", padx=8, pady=(6, 0))
        tk.Label(pcard, text="PASSOS DE CARGA NO VOXIS", fg=C["dim"],
                 bg=C["card"], font=("Segoe UI", 7, "bold")).pack(
                     anchor="w", pady=(0, 4))

        for p in PASSOS:
            frame = tk.Frame(pcard, bg=C["card2"], padx=6, pady=4)
            frame.pack(fill="x", pady=2)
            lbl_ind = tk.Label(frame, text="○", fg=C["dim"], bg=C["card2"],
                               font=("Segoe UI", 9, "bold"), width=2)
            lbl_ind.pack(side="left")
            self._lbl_passo[p["id"]] = lbl_ind
            tk.Label(frame, text=p["label"], fg=C["dim"], bg=C["card2"],
                     font=("Segoe UI", 8), anchor="w").pack(
                         side="left", fill="x", expand=True, padx=(4, 0))
            btn = tk.Button(
                frame, text="📂", fg=C["dim"], bg=C["card2"], bd=0, padx=4,
                font=("Segoe UI", 9), cursor="hand2",
                activebackground=C["card2"], activeforeground=C["azul"],
                state="disabled",
                command=lambda pid=p["id"]: self._abrir_pasta_passo(pid))
            btn.pack(side="right")
            self._btn_passo[p["id"]] = btn

        # ── Fase 1 — Formatação ──────────────────────────────────
        tk.Frame(self._corpo, bg=C["dim"], height=1).pack(
            fill="x", padx=8, pady=(8, 0))
        tk.Label(self._corpo, text="FASE 1 · FORMATAÇÃO", fg=C["dim"],
                 bg=C["bg"], font=("Segoe UI", 7, "bold")).pack(
                     anchor="w", padx=12, pady=(3, 0))
        self.bf1 = tk.Frame(self._corpo, bg=C["bg"])
        self.bf1.pack(fill="x", padx=8, pady=(2, 0))
        self.btn_arqs  = self._btn(self.bf1, "📄 Arquivos", "#1e66f5", self._sel_arquivos)
        self.btn_pasta = self._btn(self.bf1, "📁 Saída",    "#7287fd", self._sel_pasta)
        self.btn_fmt   = self._btn(self.bf1, "⚙️ Formatar", "#40a02b", self._formatar)
        self.btn_limpa = self._btn(self.bf1, "🗑 Limpar",   "#585b70", self._limpar)

        # ── Fase 2 — Automação ───────────────────────────────────
        tk.Frame(self._corpo, bg=C["dim"], height=1).pack(
            fill="x", padx=8, pady=(6, 0))
        tk.Label(self._corpo, text="FASE 2 · AUTOMAÇÃO VOXIS", fg=C["dim"],
                 bg=C["bg"], font=("Segoe UI", 7, "bold")).pack(
                     anchor="w", padx=12, pady=(3, 0))
        self.bf2 = tk.Frame(self._corpo, bg=C["bg"])
        self.bf2.pack(fill="x", padx=8, pady=(2, 4))
        self.btn_auto  = self._btn(self.bf2, "▶ Iniciar Automação", "#1e66f5",
                                   self._iniciar_automacao, state="disabled")
        self.btn_pausa = self._btn(self.bf2, "⏸ Pausar", "#df8e1d",
                                   self._toggle_pausa, state="disabled")
        self.btn_parar = self._btn(self.bf2, "🛑 Parar",  "#d20f39",
                                   self._parar, state="disabled")

        # ── Log ──────────────────────────────────────────────────
        lf = tk.Frame(self._corpo, bg=C["bg"])
        lf.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        tk.Label(lf, text="LOG", fg=C["dim"], bg=C["bg"],
                 font=("Segoe UI", 7, "bold")).pack(anchor="w")
        borda = tk.Frame(lf, bg=C["dim"], padx=1, pady=1)
        borda.pack(fill="both", expand=True)
        self.txt = tk.Text(borda, bg=C["bar"], fg=C["texto"],
                           font=("Consolas", 8), wrap="word", bd=0,
                           state="disabled", spacing1=2,
                           selectbackground=C["card"])
        sb = tk.Scrollbar(borda, command=self.txt.yview, bg=C["card"],
                          width=8, troughcolor=C["bg"])
        self.txt.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.txt.pack(fill="both", expand=True)
        self.txt.tag_configure("ok",    foreground=C["verde"])
        self.txt.tag_configure("erro",  foreground=C["vermelho"])
        self.txt.tag_configure("aviso", foreground=C["amarelo"])
        self.txt.tag_configure("info",  foreground=C["ciano"])
        self.txt.tag_configure("dim",   foreground=C["dim"])
        self.txt.tag_configure("hora",  foreground=C["dim"])

    # ────────────────────────────── HELPERS
    def _row(self, parent, label, valor="—"):
        C = self.C
        f = tk.Frame(parent, bg=C["card"])
        f.pack(fill="x", pady=1)
        tk.Label(f, text=f"{label}:", fg=C["dim"], bg=C["card"],
                 font=("Segoe UI", 7, "bold"), width=11, anchor="w").pack(side="left")
        lbl = tk.Label(f, text=valor, fg=C["texto"], bg=C["card"],
                       font=("Segoe UI", 8), anchor="w")
        lbl.pack(side="left", fill="x", expand=True)
        return lbl

    def _btn(self, parent, txt, cor, cmd, state="normal"):
        b = tk.Button(parent, text=txt, bg=cor, fg="white", bd=0, relief="flat",
                      font=("Segoe UI", 8, "bold"), cursor="hand2", command=cmd,
                      padx=6, pady=5, activebackground=cor, activeforeground="white",
                      state=state)
        b.pack(side="left", expand=True, fill="x", padx=2)
        return b

    def _init_saida_padrao(self):
        trunc = pasta_saida if len(pasta_saida) <= 38 else "…" + pasta_saida[-35:]
        self.lbl_saida.config(text=trunc)

    # ────────────────────────────── ARRASTAR
    def _drag_start(self, e):
        self._dx = e.x_root - self.root.winfo_x()
        self._dy = e.y_root - self.root.winfo_y()

    def _drag_move(self, e):
        self.root.geometry(f"+{e.x_root - self._dx}+{e.y_root - self._dy}")

    def _toggle_minimizar(self):
        if self._minimizado:
            self._corpo.pack(fill="both", expand=True)
            geo = self.root.geometry()
            x, y = geo.split("+")[1], geo.split("+")[2]
            self.root.geometry(f"420x{self._altura_normal}+{x}+{y}")
            self.btn_min.config(text="─")
            self._minimizado = False
        else:
            self._altura_normal = self.root.winfo_height()
            self._corpo.pack_forget()
            geo = self.root.geometry()
            x, y = geo.split("+")[1], geo.split("+")[2]
            self.root.geometry(f"420x36+{x}+{y}")
            self.btn_min.config(text="□")
            self._minimizado = True

    # ────────────────────────────── FASE 1
    def _sel_arquivos(self):
        global arquivos_selecionados
        inicial = DIR_ENTRADA if os.path.isdir(DIR_ENTRADA) else _BASE_DIR
        arqs = filedialog.askopenfilenames(
            title="Selecionar CSVs originais",
            initialdir=inicial,
            filetypes=[("CSV", "*.csv"), ("Todos", "*.*")])
        if arqs:
            arquivos_selecionados = list(arqs)
            n = len(arquivos_selecionados)
            self.lbl_arq.config(text=f"{n} arquivo(s)")
            log(f"📄 {n} arquivo(s) selecionado(s)")
            for a in arquivos_selecionados:
                log(f"   • {os.path.basename(a)}")

    def _sel_pasta(self):
        global pasta_saida
        inicial = DIR_SAIDA if os.path.isdir(DIR_SAIDA) else _BASE_DIR
        pasta = filedialog.askdirectory(title="Pasta de saída", initialdir=inicial)
        if pasta:
            pasta_saida = pasta
            trunc = pasta if len(pasta) <= 38 else "…" + pasta[-35:]
            self.lbl_saida.config(text=trunc)
            log(f"📁 Saída: {pasta}")

    def _formatar(self):
        if not arquivos_selecionados:
            messagebox.showerror("Erro", "Selecione os CSVs originais primeiro.")
            return
        threading.Thread(target=executar_formatacao, daemon=True).start()

    def _apagar_conteudo_dir(self, pasta: str) -> tuple:
        """
        Apaga todos os arquivos dentro da pasta (mantem a pasta em si).
        Retorna (qtd_apagados, lista_erros).
        """
        apagados = 0
        erros = []
        if not pasta or not os.path.isdir(pasta):
            return 0, []

        for root, dirs, files in os.walk(pasta, topdown=False):
            for nome in files:
                caminho = os.path.join(root, nome)
                try:
                    os.remove(caminho)
                    apagados += 1
                except Exception as ex:
                    erros.append(f"{caminho}: {ex}")
            for nome in dirs:
                caminho = os.path.join(root, nome)
                try:
                    os.rmdir(caminho)
                except Exception:
                    pass
        return apagados, erros

    def _limpar(self):
        global arquivos_selecionados
        global pasta_saida

        msg = (
            "Isso vai apagar TODOS os arquivos em:\n"
            f"- Entrada: {DIR_ENTRADA}\n"
            f"- Saida:   {pasta_saida}\n\n"
            "Deseja continuar?"
        )
        if not messagebox.askyesno("Confirmar limpeza", msg):
            return

        apag_entrada, err_entrada = self._apagar_conteudo_dir(DIR_ENTRADA)
        apag_saida, err_saida = self._apagar_conteudo_dir(pasta_saida)

        arquivos_selecionados = []
        self.lbl_arq.config(text="0 selecionado(s)")
        self.lbl_arquivo.config(text="—")
        self.lbl_prog.config(text="—")
        for pid in self._lbl_passo:
            self._lbl_passo[pid].config(text="○", fg=self.C["dim"])
            self._btn_passo[pid].config(state="disabled", fg=self.C["dim"])
        self.btn_auto.config(state="disabled")
        self._limpar_log()
        log(f"🗑  Limpo. Entrada: {apag_entrada} arquivo(s). Saida: {apag_saida} arquivo(s).")

        erros = err_entrada + err_saida
        if erros:
            log("⚠️  Alguns arquivos nao puderam ser apagados.")
            for e in erros[:10]:
                log(f"   • {e}")
            if len(erros) > 10:
                log(f"   • ... +{len(erros) - 10} erro(s)")

    def _limpar_log(self):
        self.txt.config(state="normal")
        self.txt.delete("1.0", "end")
        self.txt.config(state="disabled")

    # ────────────────────────────── FASE 2
    def _iniciar_automacao(self):
        if not CONF.get("usuario") or not CONF.get("senha"):
            messagebox.showerror(
                "Configuração incompleta",
                f"Preencha usuário e senha em:\n{ARQUIVO_CONF}")
            return
        self.btn_auto.config(state="disabled")
        self.btn_pausa.config(state="normal")
        self.btn_parar.config(state="normal")
        for b in (self.btn_arqs, self.btn_pasta, self.btn_fmt, self.btn_limpa):
            b.config(state="disabled")
        threading.Thread(target=executar_automacao, daemon=True).start()

    def _toggle_pausa(self):
        C = self.C
        if _pause_event.is_set():
            _pause_event.clear()
            self.btn_pausa.config(text="▶ Retomar", bg="#40a02b")
            log("⏸ Automação pausada.")
            log("   💡 Você pode editar o voxis_config.json agora — será relido ao retomar.")
        else:
            # Recarrega o JSON para pegar alterações feitas durante a pausa
            global CONF
            try:
                CONF = _carregar_config()
                log("🔄 Configuração recarregada do voxis_config.json")
            except Exception as ex:
                log(f"⚠️ Erro ao recarregar config: {ex} — mantendo configuração anterior")
            _pause_event.set()
            self.btn_pausa.config(text="⏸ Pausar", bg="#df8e1d")
            log("▶ Automação retomada.")

    def _parar(self):
        global _stop_flag
        _stop_flag = True
        _pause_event.set()   # desbloqueia se estiver pausado
        self.btn_parar.config(state="disabled")
        log("🛑 Sinal de parada enviado — aguardando arquivo atual terminar...")

    def _abrir_pasta_passo(self, passo_id: int):
        passo = next((p for p in PASSOS if p["id"] == passo_id), None)
        if not passo:
            return
        caminho = os.path.join(pasta_saida, passo["pasta"])
        if os.path.isdir(caminho):
            subprocess.Popen(f'explorer "{caminho}"')
        else:
            messagebox.showwarning("Aviso", f"Pasta não encontrada:\n{caminho}")

    # ────────────────────────────── ESTADOS DOS PASSOS (chamados da thread)
    def marcar_passo_ativo(self, passo_id: int):
        self._lbl_passo[passo_id].config(text="►", fg=self.C["amarelo"])
        self._btn_passo[passo_id].config(state="normal", fg=self.C["azul"])

    def marcar_passo_concluido(self, passo_id: int):
        self._lbl_passo[passo_id].config(text="✓", fg=self.C["verde"])
        self._btn_passo[passo_id].config(fg=self.C["verde"])

    def habilitar_automacao(self):
        """Chamado ao fim da formatação."""
        self.btn_auto.config(state="normal")
        for pid in self._btn_passo:
            self._btn_passo[pid].config(state="normal", fg=self.C["ciano"])

    def finalizar_automacao(self):
        """Chamado ao fim (ou erro) da automação."""
        C = self.C
        self.btn_pausa.config(state="disabled", text="⏸ Pausar", bg="#df8e1d")
        self.btn_parar.config(state="disabled")
        self.btn_auto.config(state="normal")
        for b in (self.btn_arqs, self.btn_pasta, self.btn_fmt, self.btn_limpa):
            b.config(state="normal")

    def status(self, arquivo: str = None, progresso: str = None):
        """Atualiza labels de status (thread-safe via root.after)."""
        def _do():
            _t = lambda s, n: (s[:n - 1] + "…") if len(s) > n else s
            if arquivo   is not None: self.lbl_arquivo.config(text=_t(arquivo, 42))
            if progresso is not None: self.lbl_prog.config(text=progresso)
        self.root.after(0, _do)

    def bloquear_botoes_fase1(self):
        for b in (self.btn_arqs, self.btn_pasta, self.btn_fmt, self.btn_limpa):
            b.config(state="disabled")

    def liberar_botoes_fase1(self):
        for b in (self.btn_arqs, self.btn_pasta, self.btn_fmt, self.btn_limpa):
            b.config(state="normal")

    # ────────────────────────────── POLL DO LOG
    def _poll(self):
        try:
            while True:
                mensagem, nivel = log_queue.get_nowait()
                self._append_log(mensagem, nivel)
        except queue.Empty:
            pass
        self.root.after(100, self._poll)

    def _append_log(self, mensagem: str, nivel: str):
        hora = datetime.now().strftime("%H:%M:%S")
        self.txt.config(state="normal")
        self.txt.insert("end", f"[{hora}] ", "hora")
        self.txt.insert("end", mensagem + "\n", nivel)
        self.txt.see("end")
        self.txt.config(state="disabled")


# ══════════════════════════════════════════════════════════════════
# ENTRY POINT
# ══════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    root = tk.Tk()
    root.withdraw()          # esconde a janela enquanto valida config
    if _config_error:
        messagebox.showerror(
            "AutoVoxis — Erro de Configuração",
            str(_config_error)
        )
        raise SystemExit(1)
    root.deiconify()
    ui   = AutoVoxisUI(root)
    root.mainloop()