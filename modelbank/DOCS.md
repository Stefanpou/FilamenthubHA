# Modelbank

Modelbank er et bibliotek over dine 3D-modeller (**.3mf** og **.stl**) i Home Assistant. Den arbejder sammen med
**FilamentHub** fra samme arkiv.

## Installation

1. Arkivet `https://github.com/Stefanpou/FilamenthubHA` er allerede tilføjet, hvis du har FilamentHub.
2. Find **Modelbank** i tilføjelsesbutikken, tryk **Installer** og derefter **Start**. Slå **Vis i sidepanel** til.

## Filer

Modellerne ligger i Home Assistants delte mappe: `/share/modelbank`. Undermapper bliver til mapper i Modelbank.

- **Upload:** træk filer ind på siden, eller tryk **Upload modeller**. De lægges i den mappe, du står i.
- **Find på computeren:** vælg mapper på den computer, du sidder ved. Modelbank finder alle .3mf/.stl i dem (med
  undermapper), viser hvilke der er nye, og uploader dem, du vælger. Chrome og Edge husker mapperne til næste scan.
- **Samba / fildeling:** læg filer i `share/modelbank`. De dukker op inden for 10 minutter, eller med det samme når du
  trykker **Scan**.
- **Versioner:** filer i samme mappe, hvis navne starter med de samme ord, vises som ét kort med den nyeste version.
  Alle versioner ses inde på modellen. Under **Oplysninger** kan en fil tages ud af gruppen eller lægges sammen med en
  anden model, og **Grupér versioner** øverst slår grupperingen fra.
- **Slet:** filen flyttes til `share/modelbank/.slettet`, så du kan fortryde. Tøm mappen selv, når du er sikker.

Slicede 3MF-filer fra Bambu Studio / OrcaSlicer giver gram pr. filament, farver og printtid. STL-filer og 3MF-projekter,
der ikke er slicet, får gram og tid fra sidste print af modellen, eller fra de tal du selv skriver ind.

## FilamentHub

1. Opdatér FilamentHub til 0.3.27 eller nyere.
2. Åbn FilamentHub → **Indstillinger → Spoolman-kompatibel API** og opret/kopiér **API-token**.
3. Åbn Modelbank → **Indstillinger**, indsæt tokenet og tryk **Gem**. Adressen er udfyldt på forhånd.

Så viser hver model:

- **Filament og lager:** den rulle der passer bedst (samme materiale, nok tilbage, nærmeste farve).
- **Fotos og printhistorik:** prints fra FilamentHubs printbibliotek med samme navn som modellen (også når filen
  lægges ind efter printet). Fotoet fra hvert færdigt print gemmes i Modelbank; det nyeste er modellens billede, og
  alle vises i et galleri. Andre prints kan kobles på med **Kobl print**, og forkerte kan fjernes.
- **Pris:** filament (rullens kg-pris) + strøm (målt effekt × printtid × elprisen i printvinduet) + avance.

## Indstillinger

| Indstilling | Beskrivelse |
|---|---|
| `log_level` | Detaljeringsgrad i loggen: `debug`, `info`, `warning` eller `error`. Standard: `info`. |

Modelbank kan kun åbnes gennem Home Assistant (Ingress) og har ingen åben port.
