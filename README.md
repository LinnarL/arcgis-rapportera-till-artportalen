# Rapportera till Artportalen

ArcGIS Pro-verktygslåda som gör om artfynd i ett lager med Ekologigruppens schema
Artinventering_generell till en fil för Artportalens Excelimport. Artnamnen kontrolleras mot
Dyntaxa, dubbletter söks i Artportalen och andra källor via SOS, och oklara namn får förslag
som granskas i ArcGIS.

## Verktyg

1. **Förbered rapport till Artportalen** läser lagret och skriver
   - en rapporttabell med kolumnerna i mallens blad "Alla arter" (alias = kolumnrubrik)
   - ett granskningslager (punkter) med Status, Orsak, Förslag och Godkänn för varje fynd.
2. **Lägg till granskade fynd** lägger till rader med Godkänn = Ja i rapporttabellen.
3. **Exportera till Artportalens Excelmall** skriver tabellen i mallen, högst 2000 fynd per fil.

Arbetsgång: kör 1, filtrera granskningslagret på `Status = 'Behöver granskning'`, välj Ja eller
Nej i Godkänn (ändra gärna Förslag eller Lokalnamn), kör 2, kör 3 och importera filen i
Artportalen under Rapportera, Importera fynd från Excel.

## Krav

- ArcGIS Pro 3.x (testad på 3.6). Inga paket utöver dem som följer med Pro.
- API-nycklar från [api-portal.artdatabanken.se](https://api-portal.artdatabanken.se/):
  Dyntaxa Taxon Service, och Species Observation System för dubblettkontrollen.
- Artportalens importmall `ap2_template_sv`, sparad som .xlsx (öppna .xls-filen i Excel och
  spara som Excel-arbetsbok).
- I Artportalen, Mina sidor: koordinatsystem SWEREF 99 TM och samma artnamnsspråk som i
  verktyget. Mallen kräver att inställningarna stämmer med filen.

## Lägg till i ArcGIS Pro

Katalogfönstret, Verktygslådor, högerklicka och välj Lägg till verktygslåda, välj
`RapporteraTillArtportalen.pyt`.

## Parametrar

### 1. Förbered rapport till Artportalen

| Parameter | Standard | Beskrivning |
|---|---|---|
| Artinventeringslager | | Punktlager med schemat Artinventering_generell. Urval och definitionsfråga gäller. `taxon_svensktNamn` och `datumForObjektavgr` krävs. |
| Rapporttabell | `Artportalen_rapport` i projektets geodatabas | Tabell med Artportalens kolumner. |
| Granskningslager | `Artportalen_granskning` | Alla fynd med status och namnförslag. Måste ligga i en fil-geodatabas. |
| Artnamnsspråk | Vetenskapligt namn | Dyntaxas rekommenderade vetenskapliga eller svenska namn. |
| Noggrannhet när fältet är tomt | 10 m | Används när `Noggrannhet` saknas eller inte finns i Artportalens lista. |
| Lokalnamn när fältet är tomt | | Tomt: fynd utan `objektnamn` går till granskning. |
| Sök dubbletter i Artportalen och SOS | på | Se Dubbletter nedan. |
| Dubblettradie (m) | 50 | |
| Dyntaxa-nyckel, SOS-nyckel | | Tomt: sparad nyckel, annars miljövariablerna `DYNTAXA_KEY` och `ARTDATABANKEN_API_KEY`. |
| Spara nycklarna på den här datorn | av | Sparar nycklarna i Windows Autentiseringshanteraren efter ett lyckat testanrop. |
| Beslut från tidigare granskning | | Ett granskningslager från en tidigare körning. Förslag och Godkänn används per artnamn. Får inte vara samma lager som utdata. |

### 2. Lägg till granskade fynd

Granskningslager, Rapporttabell (ändras på plats, som med Lägg till/Append) samt artnamnsspråk,
dubblettsökning och nycklar som ovan.

### 3. Exportera till Artportalens Excelmall

| Parameter | Beskrivning |
|---|---|
| Rapporttabell | Tabellen från verktyg 1. |
| Artportalens Excelmall (.xlsx) | Mallen ändras inte. |
| Excelfil för import | Fler än 2000 fynd ger `_del1`, `_del2` och så vidare. |

## Fältöversättning

| Artportalen | Artinventering_generell |
|---|---|
| Artnamn | `taxon_svensktNamn`, uppslaget i Dyntaxa |
| Antal | `Antal` |
| Lokalnamn | `objektnamn` (högst 75 tecken) |
| Ost, Nord | geometrin, omräknad till SWEREF 99 TM |
| Noggrannhet | `Noggrannhet` |
| Startdatum, Slutdatum | `datumForObjektavgr` |
| Publik kommentar | `objektbeskrivning` och `kvantifiering` |
| Privat kommentar | `utforare`, Dyntaxa-id och eventuell omtolkning vid granskning |
| Andrahand | `Andrahand` |
| Externid | `GlobalID` |

## Regler för artnamn

- Pekar ett namn på både art och underart används arten (t.ex. snöbär).
- Till granskning med förslag: frågetecken, "a/b" eller "eller", felstavning (likhetssökning i
  Dyntaxa), flera möjliga taxa, och taxa som Dyntaxa undantar från rapportering (förslaget blir
  arten).
- Namn som slutar på "sp." skippas.
- Ska verktyg 1 köras om efter en granskning: ge det nya granskningslagret ett annat namn och
  peka ut det gamla under Beslut från tidigare granskning. Ramverket raderar ett befintligt
  utdata innan verktyget körs, så besluten i ett lager som skrivs över går inte att läsa.

## Dubbletter

Ett fynd skippas om SOS redan har ett fynd av samma taxon (inklusive underarter) samma datum
inom dubblettradien, från vilken källa som helst. Det fångar fynd som kopierats från
Artportalen och fynd som redan har importerats. Kontrollen tar ungefär en minut per 300 fynd.

## Bra att veta

- NVI-mallens fält `Noggrannhet` har standardvärdet 5 m, så nya punkter får 5 m om ingen ändrar
  det.
- Dyntaxa-svar cachas i `%LOCALAPPDATA%\ArcGIS-RapporteraTillArtportalen`.
- Avsluta redigeringen av granskningslagret innan verktyg 2 körs.
