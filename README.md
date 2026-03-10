# 🏥 AUTO VOXIS

Bot de automação para formatação e carga de arquivos CSV de materiais no sistema VOXIS.

---

## O que ele faz

O processo manual que o bot substitui envolve duas etapas repetidas para cada conjunto de 12 arquivos:

1. Exportar CSV do banco de dados
2. Formatar o arquivo manualmente (resequenciar linhas, ajustar colunas)
3. Entrar no VOXIS, navegar até Upload de Arquivos, selecionar serviço, subir o arquivo
4. Repetir isso para cada variante (Incluir/Alterar × cada tabela de preço = N rodadas × M arquivos cada)

O bot automatiza as etapas 2 e 3 integralmente.

---

## Estrutura do projeto

```
aytomacao/
│
├── autovoxis.py          ← bot principal (executar este)
├── voxis_config.json     ← configurações e credenciais (não vai pro git)
│
└── voxis/
    ├── entrada/          ← cole aqui os CSVs originais exportados do banco
    └── saida/            ← gerado automaticamente pelo bot
        ├── 01_incluir_TABELA1/
        ├── 02_alterar_TABELA1/
        ├── 03_incluir_TABELA2/
        └── 04_alterar_TABELA2/
```

---

## Pré-requisitos

- Python 3.10+
- Google Chrome instalado
- Dependências Python:

```bash
pip install selenium webdriver-manager
```

> Se estiver usando o `venv` do projeto:
> ```bash
> .\venv\Scripts\pip install selenium webdriver-manager
> ```

---

## Configuração (`voxis_config.json`)

Antes de rodar pela primeira vez, preencha o arquivo `voxis_config.json` na raiz do projeto.  
> ⚠️ Se o arquivo não existir ou estiver incompleto, o bot exibirá uma mensagem de erro ao iniciar.

```json
{
    "url_sistema":           "https://seu-sistema.com.br/caminho/login",
    "usuario":               "seu_login",
    "senha":                 "sua_senha",
    "timeout_aguarde":       40,
    "servico":               "CODIGO_SERVICO",
    "menu_administracao_id": "id_do_elemento_menu",
    "frame_conteudo":        "nome_do_iframe_conteudo",
    "tabelas_preco": [
        "TABELA-001",
        "TABELA-002"
    ]
}
```

| Campo | Descrição |
|---|---|
| `url_sistema` | URL de acesso ao VOXIS |
| `usuario` / `senha` | Credenciais de login |
| `timeout_aguarde` | Tempo máximo (segundos) que o Selenium aguarda cada elemento |
| `servico` | Código do serviço selecionado no upload (verificar no sistema) |
| `menu_administracao_id` | ID do elemento HTML do menu Administração |
| `frame_conteudo` | Nome do iframe onde o formulário de upload é carregado |
| `tabelas_preco` | Lista de tabelas de preço a processar — cada tabela gera 2 passos (Incluir + Alterar) |

> ⚠️ **Segurança:** `voxis_config.json` está no `.gitignore` e nunca é versionado.

---

## Como usar

### Executando o bot

```bash
# Com o venv ativado:
.\venv\Scripts\python.exe autovoxis.py

# Ou diretamente:
python autovoxis.py
```

Uma janela flutuante aparecerá no canto inferior direito da tela.

---

### Fase 1 — Formatação dos CSVs

Antes de subir qualquer arquivo no VOXIS, os CSVs originais precisam ser formatados.

**O que a formatação faz em cada arquivo:**
- Remove a 1ª coluna (sequencial interno do banco, sem nome no header)
- Renumera a coluna `NUM_LINHA_ARQUIVO` sequencialmente (1, 2, 3…)
- Preenche `TIP_TRANSACAO` com `I` (Incluir) ou `A` (Alterar)
- Preenche `TAB_PRECO` com o nome da tabela configurada

**Passo a passo:**

1. Coloque os CSVs originais exportados do banco em `voxis/entrada/`
2. Clique em **📄 Arquivos** → selecione os arquivos em `voxis/entrada/`
3. Confirme que a **📁 Saída** já está apontando para `voxis/saida/` (padrão)
4. Clique em **⚙️ Formatar**

O bot gera automaticamente **4 subpastas** dentro de `voxis/saida/`, uma para cada variante:

| Pasta | `TIP_TRANSACAO` | `TAB_PRECO` |
|---|---|---|
| `01_incluir_TABELA1/` | `I` | `TABELA-001` |
| `02_alterar_TABELA1/` | `A` | `TABELA-001` |
| `03_incluir_TABELA2/` | `I` | `TABELA-002` |
| `04_alterar_TABELA2/` | `A` | `TABELA-002` |

---

### Fase 2 — Automação do VOXIS

Após a formatação, o botão **▶ Iniciar Automação** é habilitado.

**O que o bot faz automaticamente para cada arquivo:**

```
1. Abre o Chrome e acessa o VOXIS
2. Faz login com as credenciais do voxis_config.json
3. Navega: Administração → Integração → Upload de Arquivos
4. Seleciona o serviço configurado no dropdown
5. Envia o arquivo CSV via campo de upload (sem abrir janela do explorador)
6. Clica em Confirmar e aguarda o processamento
7. Repete para o próximo arquivo
```

**Ordem de execução:**
- Todos os arquivos do passo 1 (INCLUIR — tabela 1)
- Todos os arquivos do passo 2 (ALTERAR — tabela 1)
- Todos os arquivos do passo 3 (INCLUIR — tabela 2)
- Todos os arquivos do passo 4 (ALTERAR — tabela 2)

**Controles durante a automação:**

| Botão | Ação |
|---|---|
| **⏸ Pausar** | Pausa após terminar o arquivo atual |
| **▶ Retomar** | Retoma de onde parou |
| **🛑 Parar** | Interrompe após terminar o arquivo atual |

---

## Painel flutuante

O painel fica fixo no canto da tela e pode ser arrastado.  
O botão **─** minimiza para uma barra fina quando não estiver usando.

```
┌─────────────────────────────┐
│ 🏥  AUTO VOXIS          ─ ✕ │
├─────────────────────────────┤
│ ARQUIVOS:  12 arquivo(s)    │
│ SAÍDA:     …voxis/saida     │
│ ENVIANDO:  matgeral3_I_...  │
│ PROGRESSO: 3/12             │
├─────────────────────────────┤
│ PASSOS DE CARGA NO VOXIS    │
│  ✓  1 · INCLUIR — TABELA-001│
│  ►  2 · ALTERAR — TABELA-001│
│  ○  3 · INCLUIR — TABELA-002│
│  ○  4 · ALTERAR — TABELA-002│
├─────────────────────────────┤
│ FASE 1 · FORMATAÇÃO         │
│ [📄 Arq] [📁 Saída] [⚙️] [🗑]│
├─────────────────────────────┤
│ FASE 2 · AUTOMAÇÃO VOXIS    │
│ [▶ Iniciar] [⏸ Pausar] [🛑] │
├─────────────────────────────┤
│ LOG                         │
│ [13:42:01] → Integração     │
│ [13:42:03] ✅ Serviço selecionado│
│ [13:42:08] ✅ OK             │
└─────────────────────────────┘
```

O botão **📂** ao lado de cada passo abre a pasta correspondente no Explorer.

---

## Indicadores de passo

| Símbolo | Significado |
|---|---|
| `○` | Aguardando |
| `►` | Em execução |
| `✓` | Concluído |

---

## Adicionando mais tabelas de preço

Para incluir uma nova tabela, basta editar o `voxis_config.json`:

```json
"tabelas_preco": [
    "TABELA-001",
    "TABELA-002",
    "NOVA-TABELA"
]
```

O bot criará automaticamente mais 2 passos (Incluir + Alterar) para a nova tabela.

---

## Pendências

- [ ] Implementar detecção de resultado da tela pós-upload (sucesso / erro detalhado)  
      *(aguardando HTML da tela de resultado para refinar `_aguardar_processamento`)*

---

## Autor

Pedro Henrique
