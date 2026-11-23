# Changelog

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
