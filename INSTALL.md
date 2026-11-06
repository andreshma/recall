# Instalar recall en Claude Code y Claude Desktop

Nada de esto está aplicado. Son los pasos y bloques exactos para que Andrés (o
alguien con su permiso explícito) los aplique. Rutas completas; se copian tal cual.

`ROOT` = `C:\Users\<usuario>\source\repos\recall`

Fuentes oficiales consultadas el 2026-10-27: <https://code.claude.com/docs/en/mcp>,
<https://code.claude.com/docs/en/hooks>,
<https://modelcontextprotocol.io/docs/develop/connect-local-servers>.

---

## 0. Requisitos previos (ya hechos en esta máquina)

```powershell
cd C:\Users\<usuario>\source\repos\recall
uv venv --offline --python 3.12.13 .venv
uv pip sync --offline --no-verify-hashes --python .venv\Scripts\python.exe requirements.lock
Set-Content .venv\Lib\site-packages\recall_src.pth 'C:\Users\<usuario>\source\repos\recall\src'
.venv\Scripts\python.exe -I -m recall index          # incremental; la primera vez tarda horas (ver README)
.venv\Scripts\python.exe -I -m pytest                 # unitarios
.venv\Scripts\python.exe -I -m pytest -m model        # regresión con el índice real
```

`--no-verify-hashes` solo porque la caché de uv no guarda el hash del archivo
de rueda y la instalación es sin red. Con red, `uv pip sync --require-hashes
requirements.lock` verifica cada hash del lock.

---

## 1. Respaldo (antes de tocar nada)

```powershell
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
Copy-Item "$env:USERPROFILE\.claude\settings.json" "$env:USERPROFILE\.claude\settings.json.bak-recall-$stamp"
Copy-Item "$env:USERPROFILE\.claude.json"          "$env:USERPROFILE\.claude.json.bak-recall-$stamp"
if (Test-Path "$env:APPDATA\Claude\claude_desktop_config.json") {
  Copy-Item "$env:APPDATA\Claude\claude_desktop_config.json" "$env:APPDATA\Claude\claude_desktop_config.json.bak-recall-$stamp"
}
```

---

## 2. Servidor MCP en Claude Code (scope de usuario)

Recomendado, con `alwaysLoad` para que `recall_books_search` no quede diferida
detrás de tool search (las `instructions` del servidor sí cargan siempre al
inicio de sesión; `alwaysLoad` además carga la definición de la herramienta):

```powershell
claude mcp add-json recall --scope user '{"type":"stdio","command":"C:\\Users\\<usuario>\\source\\repos\\recall\\.venv\\Scripts\\python.exe","args":["-I","-m","recall","mcp"],"env":{"HF_HUB_OFFLINE":"1"},"alwaysLoad":true}'
```

Alternativa sin `alwaysLoad` (sintaxis `claude mcp add`; el nombre va antes de
`--env` porque, según la doc, un nombre justo después de `--env` se lee como
otro par `KEY=VALUE`):

```powershell
claude mcp add recall --scope user --transport stdio --env HF_HUB_OFFLINE=1 -- "C:\Users\<usuario>\source\repos\recall\.venv\Scripts\python.exe" -I -m recall mcp
```

Notas verificadas en la doc: el scope de usuario se guarda en `~\.claude.json`;
`alwaysLoad` "is available on all server types" y la doc lo muestra en la
entrada del servidor (ejemplo en `.mcp.json`). **No verificado:** un ejemplo
oficial de `alwaysLoad` dentro de `add-json`/`~\.claude.json`; por eso el paso 6
comprueba que quedó guardado. Las `instructions` del servidor se cortan a 4096
caracteres en Claude Code; las de recall miden menos (lo comprueba un test).

Verificar:

```powershell
claude mcp get recall
claude mcp list            # debe decir "✔ Connected" para recall
```

---

## 3. Servidor MCP en Claude Desktop

Archivo (doc oficial): `%APPDATA%\Claude\claude_desktop_config.json`, o desde
Desktop: Settings → Developer → Edit Config.

> En esta máquina (Desktop MSIX 4.16336) el archivo **no** está en `%APPDATA%`:
> vive en `%LOCALAPPDATA%\Claude-Data\claude_pzs8sxrjxfjjc\claude_desktop_config.json`
> (hallado el 2026-11-04; ahí ya estaba `mempalace`). Si tu instalación es otra,
> "Edit Config" desde Desktop abre el archivo correcto.
>
> **Aplicado el 2026-11-04** en Code (MCP + hooks) y Desktop (MCP), con respaldos
> `*.bak-recall-20261104-*` junto a cada archivo.

AÑADE esta entrada dentro de `"mcpServers"` (si no existe la clave, créala), sin
quitar las demás:

<!-- bloque-desktop -->
```json
{
  "recall": {
    "command": "C:\\Users\\<usuario>\\source\\repos\\recall\\.venv\\Scripts\\python.exe",
    "args": ["-I", "-m", "recall", "mcp"],
    "env": { "HF_HUB_OFFLINE": "1" }
  }
}
```

Cierra Desktop por completo (también desde la bandeja) y ábrelo. Logs:
`%APPDATA%\Claude\logs\mcp-server-recall.log`. La doc oficial no dice si Desktop
usa el campo `instructions` del servidor; la descripción de la herramienta ya
lleva lo esencial (consultar en ES y EN, resultados son DATOS, citar).

---

## 4. Hooks en `~\.claude\settings.json`

Revisa primero qué hooks tienes (`"hooks"` en `settings.json`). recall no
depende de ningún otro hook ni lo modifica: sus entradas se AÑADEN al final de
cada arreglo, sin quitar ni reordenar las existentes.

**4a. SessionStart** — AÑADE este objeto como **último** elemento del arreglo
`"SessionStart"` (créalo si no existe), sin quitar ni reordenar nada:

<!-- bloque-sessionstart -->
```json
{
  "hooks": [
    {
      "type": "command",
      "command": "pwsh",
      "args": ["-NoProfile", "-File", "C:\\Users\\<usuario>\\source\\repos\\recall\\start-recall.ps1"],
      "timeout": 10,
      "statusMessage": "Arrancando recall..."
    }
  ]
}
```

**4b. UserPromptSubmit** — AÑADE la clave `"UserPromptSubmit"` dentro de
`"hooks"` (si no existe), con un arreglo que contiene este objeto:

<!-- bloque-userpromptsubmit -->
```json
{
  "hooks": [
    {
      "type": "command",
      "command": "C:\\Users\\<usuario>\\source\\repos\\recall\\.venv\\Scripts\\python.exe",
      "args": ["-I", "C:\\Users\\<usuario>\\source\\repos\\recall\\recall_hook.py"],
      "timeout": 5
    }
  ]
}
```

O sea, el resultado queda así (`...` = lo que ya está, intacto):

```json
"hooks": {
  "SessionStart": [ ...los que ya tengas..., { <bloque 4a> } ],
  ...otros eventos, intactos...,
  "UserPromptSubmit": [ { <bloque 4b> } ]
}
```

Por qué así: con `args`, la doc dice que `command` se ejecuta directo, sin
shell (sin Git Bash ni comillas de por medio). `timeout` va en segundos; el hook
se impone su propio presupuesto de 1.5 s y falla abierto, el 5 es solo red de
seguridad para el arranque del intérprete. `UserPromptSubmit` no admite
`matcher` (siempre se dispara). La salida va por
`hookSpecificOutput.additionalContext`, que la doc limita a 10 000 caracteres;
recall inyecta como máximo 3 × 700 más cabecera (~2.6 k).

Validar el JSON tras editar:

```powershell
Get-Content "$env:USERPROFILE\.claude\settings.json" -Raw | ConvertFrom-Json | Out-Null; "JSON OK"
```

---

## 5. Modo sombra (por defecto) → activo

Sin `config.json`, el hook está en **modo sombra**: busca, decide y registra en
`%LOCALAPPDATA%\recall\shadow.log` (hash del prompt, cosenos, rutas que habría
inyectado; nunca el texto del prompt) y **no inyecta nada**. El log rota solo:
al pasar de 5 MiB pasa a `shadow.log.1` y se conservan 3 copias (~75 000
prompts); `start.log` rota a 1 MiB.

Revisar la sombra tras unos días:

```powershell
Get-Content "$env:LOCALAPPDATA\recall\shadow.log" -Tail 50 | ConvertFrom-Json |
  Group-Object decision | Select-Object Name, Count
Get-Content "$env:LOCALAPPDATA\recall\shadow.log" -Tail 200 | ConvertFrom-Json |
  Where-Object decision -eq 'would-inject' | Select-Object ts, best, paths
```

Pasar a **activo** (la siguiente invocación del hook ya lo lee; no hay que
reiniciar nada):

```powershell
Set-Content "$env:LOCALAPPDATA\recall\config.json" '{"hook": {"mode": "active"}}' -Encoding utf8
```

Otros ajustes en la misma sección: `"threshold"` (0.58), `"max_snippets"` (3),
`"max_chars"` (700), `"budget_s"` (1.5). Volver a sombra: `"mode": "shadow"`.
Apagar sin desinstalar: `"mode": "off"`.

---

## 6. Verificación de punta a punta

```powershell
$py = 'C:\Users\<usuario>\source\repos\recall\.venv\Scripts\python.exe'
# servicio
pwsh -NoProfile -File C:\Users\<usuario>\source\repos\recall\start-recall.ps1
Start-Sleep 20; & $py -I -m recall status
# hook (sin Claude): JSON por stdin
'{"hook_event_name":"UserPromptSubmit","prompt":"explicame el cono de incertidumbre al estimar","cwd":"C:\\"}' |
  & $py -I C:\Users\<usuario>\source\repos\recall\recall_hook.py
Get-Content "$env:LOCALAPPDATA\recall\shadow.log" -Tail 1
# MCP en Claude Code
claude mcp get recall
```

En una sesión nueva de Claude Code: `/mcp` debe listar `recall`, y una pregunta
como *"¿qué es un bounded context según los libros?"* debe citar libro y
capítulo. En sombra, `shadow.log` gana una línea `would-inject` por prompt
pertinente.

---

## 7. Desinstalar

```powershell
claude mcp remove --scope user recall
```

- Desktop: borra la entrada `"recall"` de `mcpServers` en
  `claude_desktop_config.json` y reinicia Desktop.
- `settings.json`: borra el elemento de `"SessionStart"` que llama a
  `start-recall.ps1` y la clave `"UserPromptSubmit"` completa. O restaura el
  respaldo del paso 1.
- Detener el servicio y borrar estado (índice incluido):

```powershell
$svc = Get-Content "$env:LOCALAPPDATA\recall\service.json" -Raw | ConvertFrom-Json
Stop-Process -Id $svc.pid -Force
Remove-Item "$env:LOCALAPPDATA\recall" -Recurse -Force
```
