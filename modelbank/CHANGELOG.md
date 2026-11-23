# Changelog

## 0.5.2

- Slet direkte fra oversigten: papirkurv på hvert kort (vises, når musen er over kortet; altid på telefon). På en versionsgruppe vælger du, om kun den viste version eller også de ældre skal slettes.
- »Vælg« øverst: klik på flere kort (eller »Vælg alle«) og slet dem på én gang. Filerne flyttes til `.slettet`, så de kan fortrydes.
- Delete from the overview: trash button per card and a select mode for several at once.

## 0.5.1

- Indstillinger → Dubletter viser filer, der ligger flere gange med præcis samme indhold (fx fra tidligere uploads), og »Ryd op« beholder én af hver med samlede tags, noter, favorit og printhistorik. Kopierne flyttes til `.slettet`.
- Settings → Dubletter: list identical files and keep one of each (copies moved to .slettet).

## 0.5.0

- Statusfelt for upload nederst til højre (træk-og-slip, »Upload modeller« og »Find på computeren«): fil X af Y, samlet procent og MB, hastighed, tid tilbage, den fil der er i gang (også mens serveren læser den), fejl og en Stop-knap. Feltet bliver stående, mens du klikker rundt, og siden advarer, hvis du lukker den midt i en upload.
- Samme fil uploades ikke to gange: indholdet sammenlignes (SHA-256), før filen sendes, og igen på serveren. Har filen samme navn, men nyt indhold, er det en nyere version og gemmes som en ny fil i versionsgruppen.
- Upload progress panel with speed and time left; identical files are never stored twice (content hash), changed files become a new version.

## 0.4.1

- »Nyeste version« bruger nu tidspunktet, hvor 3MF-filen blev gemt i Bambu Studio/OrcaSlicer (det står inde i filen), i stedet for upload-tidspunktet. Filer, der er uploadet samtidig, kommer derfor i den rigtige rækkefølge. Eksisterende filer læses igen automatisk.
- Modelbank-siden genindlæser sig selv, når der er installeret en ny version.
- Newest version = save time stored inside the 3MF; the page reloads itself after an update.

## 0.4.0

- Ny side »Find på computeren«: vælg en eller flere mapper på den computer, du sidder ved (fx Dokumenter, Downloads eller BambuStudio-mappen). Modelbank gennemsøger dem med undermapper, viser hvilke .3mf/.stl der er nye, og hvilke der allerede findes (samme navn og størrelse), og uploader dem, du vælger – med eller uden mappestrukturen. Skjulte mapper, AppData og systemmapper springes over.
- I Chrome og Edge huskes mapperne, så »Scan gemte mapper igen« finder nye filer næste gang. En webside kan af sikkerhedshensyn ikke selv gennemsøge hele computeren; du vælger mapperne.
- New "Find på computeren" page: scan chosen folders on the client for .3mf/.stl, compare with the library and upload the new ones.

## 0.3.0

- Versioner af samme model grupperes: filer i samme mappe, hvis navne starter med de samme ord (fx `yubikey_5c_nfc_v2`, `yubikey_5c_nfc_lock`, `yubikey_noglering`), vises som ét kort med den nyeste version og »N versioner«. Inde på modellen ses alle versioner, nyeste først. Generiske ord som »Bambu Lab X2D« eller »holder« tæller ikke.
- Du kan tage en fil ud af en gruppe, eller lægge den sammen med en anden model under Oplysninger. »Grupér versioner« øverst slår det fra.
- Upload beholder filens egen dato, så »nyeste« er den nyeste fil.
- Versions of a model are grouped (shared leading words in the file name, same folder); newest shown, all versions on the model page; manual override per file.

## 0.2.0

- Fotoet, FilamentHub tager, når et print er færdigt, bliver nu modellens billede. Printer du den samme fil flere gange (eller flere plader af den), gemmes alle fotos i Modelbank og vises i et galleri på modellen – også selvom printet senere slettes i FilamentHub.
- Lægger du en fil ind, efter den er printet, kobles den automatisk til de prints (og deres fotos). Navne sammenlignes uden tegn og mellemrum, og Bambus »projekt_pladenavn«-titler genkendes, så alle plader af en fil kommer med.
- Nye prints kobles på inden for et par minutter.
- Finish photos from FilamentHub become the model picture; all photos of every print are kept in a gallery. Files added after printing are linked to those prints.

## 0.1.0

- Første udgave: upload (træk filer ind eller vælg dem), mapper og tags, søgning, 3D-visning og billeder af modellerne.
- Filerne ligger i `/share/modelbank`, så du også kan lægge dem ind via Samba; de findes af sig selv inden for 10 minutter.
- Koblet til FilamentHub (API-token): lagerstatus for hvert filament i modellen, printhistorik med fotos, og pris
  beregnet som i FilamentHubs prisberegner (rullepris, målt strøm, elpris i printvinduet og avance).
- First release: STL/3MF library with 3D view, folders, tags and FilamentHub stock, print history and price.
