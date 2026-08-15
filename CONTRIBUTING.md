# Cómo se trabaja en AgenticESP32Labs

## Ramas — gitflow

| Rama | Vive | Qué contiene |
|------|------|--------------|
| `master` | permanente | Solo versiones liberadas. Cada commit tiene tag `vX.Y.Z`. Nunca se commitea directo. |
| `develop` | permanente | Integración. De aquí sale todo y aquí vuelve todo. |
| `feature/*` | temporal | Nace de `develop`, muere en `develop`. |
| `release/*` | temporal | Nace de `develop`, muere en `master` **y** `develop`. Solo correcciones y bump de versión. |
| `hotfix/*` | temporal | Nace de `master`, muere en `master` **y** `develop`. Únicamente para producción rota. |

Regla dura: los merges a `develop` y `master` son siempre `--no-ff`. La
burbuja de merge es la unidad de reversión — sin ella se pierde la
trazabilidad de qué feature introdujo qué.

### Ciclo de una feature

```bash
git checkout develop && git pull
git checkout -b feature/hud-pantalla-alertas

# ... commits ...

git checkout develop
git merge --no-ff feature/hud-pantalla-alertas
git branch -d feature/hud-pantalla-alertas
```

Con `git-flow` instalado: `git flow feature start hud-pantalla-alertas`
y `git flow feature finish hud-pantalla-alertas`. El repo ya tiene los
prefijos configurados en `.git/config`.

### Ciclo de un release

```bash
git flow release start 0.4.0     # o: git checkout -b release/0.4.0 develop
# ajustes finales, changelog, bump
git flow release finish 0.4.0    # mergea a master, taggea v0.4.0, vuelve a develop
```

## Commits — Conventional Commits con scope

```
<tipo>(<scope>): <resumen en imperativo, minúscula, sin punto final>

<cuerpo: por qué, no qué. El diff ya dice qué.>
```

**Tipos:** `feat` · `fix` · `refactor` · `perf` · `style` · `docs` · `test` · `build` · `ci` · `chore`

**Scopes de este repo:**

| Scope | Cubre |
|-------|-------|
| `firmware` | cambios transversales al ESP32 |
| `hud` | `components/hud/hud.c` — pantallas y render |
| `ui` | `components/hud/ui.c` — widgets y táctil |
| `net` | WiFi, SNTP, clima, mDNS |
| `voice` | WebSocket de audio |
| `audio` | I2S, micrófono, altavoz |
| `display` | driver del panel |
| `touch` | driver capacitivo |
| `ajustes` | persistencia NVS |
| `servidor` | todo `servidor/` |
| `board` | pinout y hardware |

Ejemplos válidos:

```
feat(hud): agrega pantalla de alertas del digital twin
fix(net): reintenta SNTP cuando el primer sync falla
refactor(servidor): extrae el pool de MCP del agente
```

Un commit = un cambio coherente. Si el resumen necesita una "y", son dos
commits. El commit `fe3e919` original tenía 2083 líneas y 24 archivos:
eso no se revierte, no se bisecta y no se revisa.

Plantilla local:

```bash
git config commit.template .gitmessage
```

## Configuración local — nunca en git

Credenciales WiFi, host del servidor y coordenadas se editan con
`idf.py menuconfig` y quedan en `sdkconfig`, que está en `.gitignore`.
Las API keys del servidor son variables de entorno expandidas desde
`servidor/config.yaml` (`${GROQ_API_KEY}`), nunca literales.

Si vas a agregar un parámetro configurable, va a `main/Kconfig.projbuild`.
No a un `#define`.

## Antes de abrir un PR

- [ ] `idf.py build` limpio, sin warnings nuevos
- [ ] Probado en placa si toca `display`, `touch`, `audio` o `board`
- [ ] Ningún literal de credencial, IP fija o API key en el diff
- [ ] Commits en Conventional Commits, sin `wip` ni `fix typo` sueltos
