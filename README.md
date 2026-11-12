# Rapportera till Artportalen

ArcGIS Pro-verktygslåda som gör om artfynd i ett lager med Ekologigruppens schema
Artinventering_generell, eller `main.Artforekomst_punkt` i en NVI-leverans (GeoPackage), till en fil för Artportalens Excelimport. Artnamnen kontrolleras mot
Dyntaxa, dubbletter söks i Artportalen och andra källor via SOS, och oklara namn får förslag
som granskas i ArcGIS.

## Verktyg

1. **Förbered rapport till Artportalen** läser lagret och skriver
   - en rapporttabell med Artportalens obligatoriska kolumner, Antal och kommentarer
     (alias = kolumnrubrik)
   - ett granskningslager (punkter) med Status, Orsak, Förslag och Godkänn för varje fynd.
2. **Lägg till granskade fynd** lägger till rader med Godkänn = Ja i rapporttabellen.
3. **Exportera till Artportalens Excelmall** skriver tabellen i mallen, högst 2000 fynd per fil.

Arbetsgång: kör 1, filtrera granskningslagret på `Status = 'Behöver granskning'`, välj Ja eller
Nej i Godkänn (ändra gärna Förslag eller Lokalnamn), kör 2 och kör 3. Öppna .xls-filen,
markera rubrikraden (rad 2) och fynden i bladet "Alla arter", kopiera och klistra in i
Artportalen under Rapportera, Importera fynd.

## Krav

- ArcGIS Pro 3.x (testad på 3.6). Inga paket utöver dem som följer med Pro.
- API-nycklar från [api-portal.artdatabanken.se](https://api-portal.artdatabanken.se/):
  Dyntaxa Taxon Service, och Species Observation System för dubblettkontrollen.
- Artportalens importmall `ap2_template_sv.xls`, som den laddas ned från Artportalen.
- Excel, för att fylla i .xls-mallen. Verktyg 3 kopierar mallen och fyller i kopian med Excel
  (utan fönster), så att filen är Artportalens egen. En mall sparad som .xlsx fungerar utan
  Excel, men Excel behöver reparera den filen när den öppnas och Artportalen kan neka
  inklistringen.
- I Artportalen, Mina sidor: koordinatsystem SWEREF 99 TM och samma artnamnsspråk som i
  verktyget. Mallen kräver att inställningarna stämmer med filen.

## Lägg till i ArcGIS Pro

Katalogfönstret, Verktygslådor, högerklicka och välj Lägg till verktygslåda, välj
`RapporteraTillArtportalen.pyt`.

## Parametrar

### 1. Förbered rapport till Artportalen

| Parameter | Standard | Beskrivning |
|---|---|---|
| Artlager | | Punkt- eller multipunktlager i kartan med schemat Artinventering_generell eller Artforekomst_punkt (dra in `main.Artforekomst_punkt` från en .gpkg i kartan, så finns det i listan). Urval och definitionsfråga gäller. `datumForObjektavgr` och ett artnamn krävs: `taxon_vetenskapligtNamn` om det är ifyllt, annars `taxon_svensktNamn`. |
| Rapporttabell | `Artportalen_rapport` i projektets geodatabas | Se Kolumner nedan. |
| Granskningslager | `Artportalen_granskning` | Alla fynd med status och namnförslag. Måste ligga i en fil-geodatabas. |
| Artnamnsspråk | Vetenskapligt namn | Dyntaxas rekommenderade vetenskapliga eller svenska namn. |
| Noggrannhet när fältet är tomt | 10 m | Används när `Noggrannhet` saknas eller inte finns i Artportalens lista. |
| Sök dubbletter i Artportalen och SOS | på | Se Dubbletter nedan. |
| Dubblettradie (m) | 50 | |
| Dyntaxa-nyckel, SOS-nyckel | | Tomt: sparad nyckel, annars miljövariablerna `DYNTAXA_KEY` och `ARTDATABANKEN_API_KEY`. |
| Spara nycklarna på den här datorn | av | Sparar nycklarna i Windows Autentiseringshanteraren efter ett lyckat testanrop. |
| Lokalnamn | | Eget lokalnamn. Tomt: `objektnamn` används. |
| Lokalnamn används | Bara där lagret saknar värde | Bara där lagret saknar värde: texten används där `objektnamn` är tomt. Ersätt lagrets värde: alla fynd får texten. |
| Beslut från tidigare granskning | | Ett granskningslager från en tidigare körning. Förslag och Godkänn används per artnamn. Får inte vara samma lager som utdata. |

Utan lokalnamn går fyndet till granskning. I NVI-leveransernas `Artforekomst_punkt` är
`objektnamn` en kategori ("Övrig värdeart", "Rödlistad art"), så skriv in ett lokalnamn och
välj Ersätt lagrets värde.

### 2. Lägg till granskade fynd

Granskningslager, Rapporttabell (ändras på plats, som med Lägg till/Append) samt artnamnsspråk,
dubblettsökning och nycklar som ovan.

### 3. Exportera till Artportalens Excelmall

| Parameter | Beskrivning |
|---|---|
| Rapporttabell | Tabellen från verktyg 1. |
| Artportalens Excelmall (.xls) | `ap2_template_sv.xls`. Mallen ändras inte. |
| Excelfil för import | Samma filtyp som mallen. Fler än 2000 fynd ger `_del1`, `_del2` och så vidare. |

## Kolumner

Rapporten innehåller de kolumner som Artportalen kräver enligt mallens instruktioner, plus
Antal och kommentarer.

| Artportalen | Krav i Artportalen | Från lagret |
|---|---|---|
| Artnamn | måste anges | `taxon_vetenskapligtNamn` eller `taxon_svensktNamn`, uppslaget i Dyntaxa |
| Lokalnamn | måste anges, högst 75 tecken | `objektnamn` eller eget lokalnamn |
| Ost, Nord | måste anges för nya lokaler | geometrin, omräknad till SWEREF 99 TM |
| Noggrannhet | måste anges för nya lokaler | `Noggrannhet`, annars verktygets standard |
| Startdatum, Slutdatum | måste anges | `datumForObjektavgr` |
| Antal | "kan, men bör ej, lämnas blank" | `Antal` om det är ett heltal större än 0, annars tomt |
| Publik kommentar | kan lämnas tom, högst 1000 tecken | `kvantifiering`, t.ex. "Sparsam förekomst" |
| Privat kommentar | kan lämnas tom, högst 1000 tecken | "Utförare: `utforare`. Kartläggning: `FK_KartlaggningBM`" (det som finns) |

Rapporttabellen har också fältet Källans id (`GlobalID`, i en GeoPackage `objektidentitet`) som
verktyg 2 använder för att inte lägga till samma fynd två gånger. Det exporteras inte: mallens
kolumn Externid får bara användas inom projekt. Är id:t inte unikt i lagret, till exempel när
flera GeoPackage-filer slagits ihop, används OBJECTID.

### Antal

Artportalen vill ha ett räknat antal, ett heltal större än 0, och Enhet (plantor/tuvor, exemplar,
m² och så vidare) är valfri. Ett tomt Antal betyder att fyndet inte är räknat, vilket är
sant för det mesta i NVI-data. Därför skriver verktyget aldrig något antal som inte är räknat:

- Artinventering_generell har heltalsfältet `Antal`. Det används när det är större än 0.
- `Artforekomst_punkt` (SIS-TS 199002) saknar antal. `kvantifiering` är en klass (Okänd,
  Sparsam, God eller Mycket god förekomst) som inte går att översätta till ett antal, så Antal
  blir tomt och klassen skrivs i Publik kommentar. Ett fåtal fynd har ett antal i `objektbeskrivning` ("1 buske", "Ca 5 st", "15 m2");
  de går att fylla i för hand i rapporttabellen.
- `kvantifiering` = "Förekommer sannolikt inte" betyder att arten inte hittades. De fynden
  skippas.

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
- Multipunkter med en punkt (vanligt i GeoPackage) går bra. Med flera punkter skippas fyndet.
- Dyntaxa-svar cachas i `%LOCALAPPDATA%\ArcGIS-RapporteraTillArtportalen`.
- Avsluta redigeringen av granskningslagret innan verktyg 2 körs.
