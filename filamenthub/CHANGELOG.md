# Changelog

## 0.3.21

- Hvert slot i AMS'en viser nu, hvor mange gram der er tilbage på rullen – på printertegningen, i slotlisten og under »I printeren«. Uden en valgt rulle er tallet anslået ud fra AMS'ens egen procent.
- Lager viser rullerne, der sidder i AMS'en, øverst for sig selv (i slot-rækkefølge), og resten under »På lager«.
- AMS slots show grams left (estimated from the AMS percentage when no spool is picked); Lager lists the spools in the AMS in their own section at the top.

## 0.3.20

- Kameraet på forsiden starter ikke længere af sig selv – tryk »Vis kamera« for at se det, og »Sluk« for at stoppe det.
- Kameraet vises nu som video fra Home Assistant i stedet for enkeltbilleder, så det ikke hakker (få sekunders forsinkelse). Kan videoen ikke starte, vises billeder som før, men uden at billedet blinker mellem hvert.
- The dashboard camera is off until switched on, and plays Home Assistant's video stream (HLS) instead of still frames; still frames, preloaded, remain the fallback.

## 0.3.19

- Printbiblioteket mister ikke længere et print, når næste plade fra samme fil startes lige efter: det færdige print gemmes, så snart Bambu Cloud viser det. Kort med samme navn viser nu pladenummeret.
- Ny boks »AMS-tørring« på forsiden: FilamentHub ser, når AMS'en tørrer (temperatur, tid tilbage, filament) og måler strøm og pris på printerens stik. Tidligere tørringer kan hentes fra Home Assistants historik.
- Back-to-back plates of one file all reach the library; cards show the plate number. New "AMS-tørring" card: drying sessions with electricity and cost, also imported from Home Assistant history.

## 0.3.18

- Ny boks »Silica« på forsiden: registrér, hvornår silicaen sidst blev tørret – for hver AMS og for de tørrebokse/placeringer, du vælger. »Tørret i dag« eller vælg en dato; efter 30 dage bliver datoen orange.
- New "Silica" card on the dashboard: record when the desiccant was last dried, per AMS unit and per chosen fixed location.

## 0.3.17

- Printerindstillinger på en rulle gemmes nu automatisk, når du har ændret dem – og senest når du lukker rullen. Før gik ændringerne tabt, hvis man ikke trykkede »Gem på filamentet«.
- Under Bibliotek → Filamentprofiler står der nu, at ændringer dér kun bruges til filen eller Bambu Cloud.
- Printer settings on a spool now save by themselves after a change and when the spool is closed; before, edits were lost without pressing "Gem på filamentet".

## 0.3.16

- Printerindstillinger har nu også ironing-hastighed, ironing-flow, flow ratio for første lag og for topfladen. Bambu Studio har dem i procesprofilen, så FilamentHub laver en procesprofil pr. filament (fx »0.20mm Standard @BBL X2D - Elegoo PETG RAPID«), der arver fra Bambus egen og kun ændrer de fire værdier. Den sendes til Bambu Cloud eller hentes som fil sammen med filamentprofilen.
- Gemmer du indstillinger på et filament, får alle filamenter af samme type og farve dem også.
- Værdier, du har ændret manuelt, er markeret med en prik og profilens oprindelige værdi (tryk for at gå tilbage), og rullen viser »Ændret manuelt«.
- Findes profilen allerede i Bambu Cloud, kan den nu erstattes med de nye værdier.
- Printer settings now include ironing speed, ironing flow, first layer and top surface flow ratio, sent as a per-filament process preset next to the filament preset. Saving updates every filament of the same type and colour, values changed by hand are marked, and existing cloud presets can be replaced.

## 0.3.15

- Ny profildatabase under Bibliotek → Filamentprofiler: søg i over 1.600 filamentprofiler (Bambu Lab, Polymaker, Elegoo, SUNLU, eSUN, Generic m.fl.), filtrér på materiale og producent, justér temperaturer og flow, og send profilen til Bambu Studio som fil eller direkte til din Bambu Cloud. Dine egne profiler i Bambu Cloud kan ses og slettes samme sted.
- New profile database under Library → Filament profiles: search 1,600+ filament profiles, filter by material and vendor, adjust temperatures and flow, and send the profile to Bambu Studio as a file or straight to your Bambu Cloud. Your own cloud presets can be listed and deleted there too.

## 0.3.14

- Mellemrum mellem procent og navn på AMS-mærkerne.
- Space between percentage and name on the AMS tags.

## 0.3.13

- Printerbilledet viser lågen åben, når printeren melder det (X1, H2D og X2D har en lågesensor).
- Modellen af printet står tydeligere, og firkanten/stregen, der viser det aktuelle lag, er dæmpet.
- Mærkerne på AMS-slots har plads til procent og navn på en telefon.
- The printer picture shows the door open when the printer reports it (X1, H2D, X2D door sensor).
- The print's model is clearer and the current-layer outline is subtler.

## 0.3.12

- Printerbilledet på forsiden viser nu, hvilket filament der sidder i hvert AMS-slot og på den eksterne holder. Tryk på et mærke for at vælge eller skifte rulle.
- The printer picture on the dashboard now shows which filament sits in each AMS slot and on the external holder; tap a tag to pick or change the spool.

## 0.3.11

- "Nulstil forbrug" er igen et lille link efter "% tilbage".
- "Reset usage" is a small link after "% left" again.

## 0.3.10

- "Nulstil forbrug" er nu en tydelig knap under rullens vægt.
- "Reset usage" is now a clear button below the spool's weight.

## 0.3.9

- Nyt: faste placeringer med et antal pladser (Indstillinger > Placeringer). På en rulle vælger du stedet og en ledig plads, eller lader den tage den første ledige. Lageret kan filtreres og grupperes efter placering.
- Nyt: "Nulstil forbrug" på en rulle. Den starter forfra på den vægt, den har nu, og vises som 100 % – fx når en ny rulle vejede lidt under det, der står på etiketten. Prisen pr. kg ændres ikke.
- New: fixed locations with a number of places (Settings > Placeringer). On a spool, pick the location and a free place, or let it take the first free one. Stock can be filtered and grouped by location.
- New: "Reset usage" on a spool starts it over at its current weight (100 %), e.g. a new spool that weighed a little under its label. The price per kg stays.

## 0.3.8

- Nyt: live-kamera på forsiden. Slå det til under Indstillinger > Funktioner og vælg printerens kamera fra Home Assistant. Tryk på billedet for fuld skærm.
- New: live camera on the dashboard. Switch it on under Settings > Features and pick the printer's Home Assistant camera; tap the picture for full screen.

## 0.3.7

- Når Bambu Cloud kun har et billede af printet, prøver FilamentHub igen efter 10 minutter og efter en time at hente 3D-modellen, som du kan dreje.
- When Bambu Cloud only has a picture of the print, FilamentHub tries again after 10 minutes and after an hour to fetch the rotatable 3D model.

## 0.3.6

- Når du sletter en rulle, fjerner FilamentHub også den rulle, den selv oprettede i Bambu Filament Manager (Bambu Studio og Handy). Gamle rester af slettede ruller ryddes op. Ruller, du selv har oprettet i Bambu, røres ikke.
- Deleting a spool also removes the row FilamentHub created for it in Bambu Filament Manager (Bambu Studio and Handy); leftovers of spools deleted earlier are cleaned up. Rows you created in Bambu yourself are left alone.

## 0.3.5

- Lageret kan sorteres på rullenummer (laveste eller højeste først), og tabellen har en kolonne med rullenummeret.
- Inventory can be sorted by spool number (lowest or highest first), and the table has a spool number column.

## 0.3.4

- AMS-slots og rullerne i dem vises igen efter en genstart, også mens printeren er slukket eller sover. Du kan vælge ruller til slots som før.
- AMS slots and their spools show again after a restart, also while the printer is off or asleep, and spools can be picked for them.

## 0.3.3

- Ruller med flere farver (fx trefarvet silke) vises med alle farverne i listen, tabellen og på farvevæggen.
- Multi-colour spools (e.g. tri-colour silk) show all their colours in the list, table and colour wall.

## 0.3.2

- Når appen har ligget i baggrunden i Home Assistant, viser den ikke længere login-siden. Den venter på, at Home Assistant forbinder igen, og fortsætter selv.
- After the app has been in the background in Home Assistant it no longer shows the login page; it waits for Home Assistant to reconnect and carries on.

## 0.3.1

- Finpudsning af det nye udseende: forbrugsgrafen i Historik viser søjlerne igen, rullevinduet er grafitgråt, menuen på telefonen viser hele ordet "Indstillinger", og prisberegneren følger samme stil.
- Polish of the new look: the usage chart shows its bars again, the spool sheet is graphite, the phone menu shows full labels, and the price calculator matches the style.

## 0.3.0

- Nyt, roligere udseende: grafitgrå flader uden glød og bevægelige baggrunde, ny skrift (IBM Plex), en sidebjælke med tekst på computer og en enklere menulinje på telefonen. Filamentfarverne er nu den eneste kraftige farve på skærmen.
- New calmer look: graphite surfaces without glows or moving backdrops, a new typeface (IBM Plex), a labelled sidebar on desktop and a simpler bottom bar on phones. The filament colours are now the only strong colour on screen.

## 0.2.4

- Ruller, der blev vejet over deres nominelle vægt i 0.2.2, regner igen kr/kg ud fra filamentets vægt (fx 15 € for 1 kg = 112 kr/kg). Omregningen fra euro står nu under prisfeltet.
- Spools weighed above their nominal weight in 0.2.2 are priced per the filament's weight again. The euro conversion now shows below the price field.

## 0.2.3

- Prisen for en rulle er fast: kr/kg og printpriser regnes ud fra rullens nominelle vægt, også når den vejer mere end det.
- A spool's price is fixed: price per kg and print costs use the spool's nominal weight, even when it weighs more.

## 0.2.2

- En rulle, der vejer mere end sin nominelle vægt (fx 1050 g på en "1 kg"-rulle), får nu den vejede vægt i stedet for at blive skåret ned til 1000 g.
- Priser kan skrives i euro: tryk på € ved prisfeltet, så regnes de om til kroner med dagens ECB-kurs.
- A spool heavier than its nominal weight now keeps the weighed amount instead of being capped at 1000 g.
- Prices can be typed in euro and are converted to DKK with the daily ECB rate.

## 0.2.1

- Lager-visningerne er finpudset: listen viser navn og materiale på to linjer på telefonen, mørke ruller ses tydeligt på farvevæggen, og placeringen står ikke længere dobbelt i tabellen.
- Polished inventory views: two-line list rows on phones, dark spools visible on the colour wall, no duplicated location in the table.

## 0.2.0

- Lager kan vises på fem måder: kort, kompakt liste, tabel (sortér ved at trykke på kolonnen), grupperet (efter materiale, mærke, placering eller farve) og farvevæg. Valget huskes på enheden.
- The inventory can be shown five ways: cards, compact list, table (tap a column to sort), grouped (by material, brand, location or colour) and colour wall. The choice is remembered per device.

## 0.1.9

- Et færdigt print uden forbrugsdata i Bambu Cloud lægges under "Venter på dig", så du kan indtaste grammene, i stedet for at blive bogført som 0 g.
- Kiosk-visningen virker også via Home Assistant-adressen.
- Biblioteket viser altid det nyeste billede af pladen.
- Overblik prøver at hente printets billede i op til en time, hvis Bambu Cloud er sen.
- A finished print without usage data in Bambu Cloud now waits for you to enter the grams instead of being booked as 0 g.
- The kiosk view also works through the Home Assistant address.
- The library always shows the newest plate photo.
- The dashboard keeps trying to fetch the print picture for up to an hour if Bambu Cloud is late.

## 0.1.8

- Billedet af pladen prøves op til 5 gange over 5 minutter, hvis kameraet ikke svarer lige når printet slutter. Stikket slukkes først, når billedet er taget.
- Print uden projektfil i Bambu Cloud viser printets forsidebillede på Overblik i stedet for ingenting.
- Appen genindlæses automatisk efter en opdatering, også i Home Assistant.
- Kiosk-adressen under Indstillinger peger på den rigtige adresse i Home Assistant.
- Stikket kan også være en `light.`-enhed; kamerafeltet accepterer kun kameraer.
- Adgangskode og login til appen skjules i Home Assistant, hvor HA står for login.
- The plate photo is retried up to 5 times over 5 minutes if the camera does not answer right at the finish; the plug waits for it.
- Prints without a project file in Bambu Cloud show the print's cover picture on the dashboard.
- The app reloads itself after an update, also inside Home Assistant.
- The kiosk address in Settings is correct inside Home Assistant.
- The plug may be a `light.` entity; the camera field only accepts cameras.
- App password and login settings are hidden inside Home Assistant, which handles the login.

## 0.1.7

- Overblik viser, hvad printeren gør før printet starter (opvarmning, nivellering, kalibrering …) og listen over de planlagte trin.
- The dashboard shows what the printer is doing before the print starts (heating, levelling, calibration …) and the list of planned steps.

## 0.1.6

- Siden kan ikke længere scrolles sidelæns på mobil.
- The page can no longer be scrolled sideways on a phone.

## 0.1.5

- Tænd/sluk-knappen til stikket viser den nye tilstand efter ét tryk.
- The plug on/off button shows the new state after one tap.

## 0.1.4

- Strømforbrug måles separat for print med begge dyser (X2D/H2D), og prisberegneren kan regne med det.
- Power use is measured separately for prints that use both nozzles; the price calculator can use it.

## 0.1.3

- Printerens 3D-model og forsidebillede vises nu i Home Assistant.
- The printer 3D model and cover image now load inside Home Assistant.

## 0.1.2

- Data-import virker nu også med store backups.
- Data import now accepts large backups.

## 0.1.1

- Flyt en eksisterende FilamentHub-installation ind i tilføjelsen (data-import på en ny installation).
- Move an existing FilamentHub install into the add-on (data import on a fresh install).

## 0.1.0

- Første udgave som Home Assistant-tilføjelse (ingress, aarch64 + amd64).
- Initial release as a Home Assistant add-on.
