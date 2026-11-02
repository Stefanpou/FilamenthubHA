# FilamentHub

FilamentHub er et filament-lager og et dashboard til **Bambu Lab**-printere (X1C, X1E, P1S, P1P, P2S, A1, A1 mini,
H2D, H2S og X2D). Du kan holde styr på dine spoler (mærke, materiale, farve, resterende vægt, placering), se
printerens live-status og AMS-slots, og få spoler koblet automatisk til det der faktisk printes.

> FilamentHub forbinder **kun via Bambu Cloud**. LAN-tilstand understøttes ikke.

## Installation

1. Åbn **Indstillinger → Tilføjelser → Tilføjelsesbutik**.
2. Tryk på **⋮** (øverst til højre) → **Arkiver**.
3. Indsæt `https://github.com/Stefanpou/FilamenthubHA` og tryk **Tilføj**.
4. Find **FilamentHub** i butikken (genindlæs siden om nødvendigt) og tryk **Installer**.
5. Tryk **Start**. Slå gerne **Start ved opstart** og **Vis i sidepanel** til.
6. Åbn **FilamentHub** i Home Assistants sidepanel.

## Første opstart

Ved første opstart guider en opsætningsguide dig igennem login til Bambu Cloud (e-mail/adgangskode og evt.
verifikationskode). Derefter vælger du din printer, og lageret er klar til brug.

## Indstillinger

| Indstilling | Beskrivelse |
|---|---|
| `log_level` | Detaljeringsgrad i loggen: `debug`, `info`, `warning` eller `error`. Standard: `info`. |

## Adgang uden for Home Assistant (valgfrit)

Under **Netværk** på tilføjelsens konfigurationsside kan du angive en port for 8080/tcp (fx 8080). Så kan
FilamentHub åbnes direkte på `http://<ha-ip>:8080` – praktisk til en kiosk-skærm eller telefon. Porten er
slået fra som standard.

## Backup og data

Alle data (database og indstillinger) ligger i tilføjelsens `/data`-mappe, som automatisk er med i Home
Assistants backups. Gendannes en backup, gendannes FilamentHub også.

## Support

Fejl og forslag: <https://github.com/Stefanpou/FilamenthubHA/issues>

---

## English summary

FilamentHub is a filament inventory and Bambu Lab printer dashboard (Bambu Cloud only). Add
`https://github.com/Stefanpou/FilamenthubHA` under Settings -> Add-ons -> Add-on Store -> menu -> Repositories,
install, start and open it from the sidebar; a first-run wizard asks for your Bambu login. Data lives in `/data`
and is included in Home Assistant backups. Optionally map port 8080/tcp under Network for direct access.
