# Changelog

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
