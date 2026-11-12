# Rapportera till Artportalen

Det här är en verktygslåda till ArcGIS Pro som för över artfynd från NVI-leveranser i GPKG-format (eller Ekologigruppens egna lager artinventering_generell), till rätt format för att kunna laddas upp i bulk till Artportalen. För att importen ska vara korrekt och fel lätt kunna åtgärdas, så kontrolleras artnamn mot SLUs Dyntaxa-API, och dubbletter av artfynd kontrolleras mot Artportalens API (SOS, species observation system). När det uppstår oklarheter (ett artnamn hittas inte, eller innehåller frågetecken, eller innehåller flera olika artnamn) ges användaren ges möjlighet att granska och korrigera fynd som inte med säkerhet kan flyttas direkt till Artportalens leveransark (.xls-mallen). 

## Verktyg

Toolboxen innehåller tre separata verktyg/skript:
1. **Förbered rapport till Artportalen** läser lagret och skriver
   - en rapporttabell med Artportalens obligatoriska kolumner, Antal och kommentarer
     (alias = kolumnrubrik)
   - ett granskningslager (punkter) med Status, Orsak, Förslag och Godkänn för varje fynd.
2. **Lägg till granskade fynd** lägger till rader med Godkänn = Ja i rapporttabellen.
3. **Exportera till Artportalens Excelmall** skriver tabellen i mallen, högst 2000 fynd per fil.

Arbetsgången är: 

Peka verktyg 1 på ditt artlager, ange fyndgemensamma parametrar (t.ex lokalnamn). Verktyg 1 ger en tabell med godkända observationer och ett punktlager med observationer för granskning, både lagret och tabellen ligger i din Geodatabase och hamnar automatiskt i din öppna kartvy.

Öppna attributtabellen för granskningslagret, filtrera granskningslagret på `Status = 'Behöver granskning'`. Läs anledningen för granskning, kontrollera om den föreslagna arten är rätt för observationen. Är den det kan fältet godkänn sättas till Ja. Gör detta för alla objekt, och kör sedan verktyg 2 för att lägga till de godkända observationerna i rapportarket. 

Kör sedan verktyg 3 för att exportera alla dina godkända fynd från GIS till Artportalens excelark.

## Krav

- ArcGIS Pro 3.x (testad på 3.6). Inga paket utöver dem som följer med Pro.
- API-nycklar från [api-portal.artdatabanken.se](https://api-portal.artdatabanken.se/):
  Dyntaxa Taxon Service, och Species Observation System för dubblettkontrollen.
- Artportalens importmall `ap2_template_sv.xls`, som den laddas ned från Artportalen.
- Excel, för att fylla i .xls-mallen. Verktyg 3 kopierar mallen och fyller i kopian med Excel
  (utan fönster).
**- I Artportalen, Mina sidor: koordinatsystem SWEREF 99 TM och samma artnamnsspråk som i
  verktyget. Mallen kräver att inställningarna stämmer med filen.**

## Lägg till i ArcGIS Pro

Katalogfönstret, Verktygslådor, högerklicka och välj Lägg till verktygslåda, välj
`RapporteraTillArtportalen.pyt`.

## Vad händer med ett fynd?

Så här går verktyg 1 igenom varje rad i `Artforekomst_punkt`. För Artinventering_generell är
logiken densamma.

### 1. Raden läses

Bara de fynd som lagret visar i kartan behandlas (dvs urval och definition queries används).

| Uppgift | Varifrån |
|---|---|
| Artnamn att slå upp | `taxon_vetenskapligtNamn`. Är det tomt används `taxon_svensktNamn`. |
| Datum | `datumForObjektavgr`. Samma datum blir start- och slutdatum. |
| Koordinater | Punkten räknas om till SWEREF 99 TM, avrundat till hela meter. En multipunkt med en enda punkt går bra. |
| Lokalnamn | Lokalnamnet du anger i verktyget, samma för alla fynd. Fält i lagret används inte. |
| Noggrannhet | `Noggrannhet` om värdet finns i Artportalens lista, annars verktygets standard (10 m). GeoPackage saknar fältet, så där blir det alltid standarden. |
| Antal | `Antal` om det är ett heltal större än 0. GeoPackage saknar fältet, så där blir det tomt. |
| Publik kommentar | `kvantifiering`, t.ex. "Sparsam förekomst". |
| Privat kommentar | "Utförare: `utforare`. Kartläggning: `FK_KartlaggningBM`" |
| Källans id | `objektidentitet` (GlobalID i en geodatabas). Används bara av verktyg 2 internt och exporteras inte. |

### 2. Fynd som inte ska rapporteras skippas

Fyndet får status **Skippad**, med orsaken i fältet Orsak, om

- artnamn saknas
- namnet bara är bestämt till släkte (slutar på "sp.")
- datum saknas
- `kvantifiering` är "Förekommer sannolikt inte", alltså att arten eftersöktes men inte hittades
- geometri saknas, eller punkten är en multipunkt med flera punkter.

### 3. Artnamnet slås upp i Dyntaxa

Verktyget söker accepterade taxa som har exakt det namnet, som svenskt namn, vetenskapligt namn
eller synonym.

- Ett taxon: det används.
- En art och dess underarter eller varieteter: arten används (t.ex. snöbär).
- Flera taxa, men namnet är det rekommenderade för ett av dem: det taxonet används.

Fyndet går till granskning (status **Behöver granskning**) när

- namnet innehåller frågetecken, "/" eller "eller" ("lerkroksmossa?", "blomsterlupin/sandlupin").
  Du som användare får ta ställning till osäkerheter i granskningssteget.
- namnet inte finns i Dyntaxa, ofta en felstavning. Det mest lika namnet föreslås.
- namnet passar flera taxa som inte är art och underart till varandra.
- Dyntaxa undantar taxonet från rapportering. Arten föreslås.

Förslaget skrivs i fälten Förslag och skälet i Orsak i granskningslagret.

### 4. Namnet i rapporten bestäms

Artnamn blir Dyntaxas rekommenderade namn på språket du valt i verktyget, oavsett vad som stod
i fältdata. Artportalens import söker dock bland alla namn i Dyntaxa, även äldre namn och
synonymer, och avvisar fyndet om namnet passar flera taxa (`TaxonConversionError_MultipleTaxons`).
Hör namnet till fler än ett taxon skrivs därför taxonets Dyntaxa-id i stället, vilket mallen
tillåter. Exempel: *Centaurea scabiosa* är både arten väddklint och ett äldre namn på underarten
subsp. *scabiosa*, så väddklint skrivs som 264738. Körloggen listar vilka namn det gäller. Med
svenska namn blir det fler, t.ex. snöbär och backsippa.

### 5. Dubblettkontroll

Verktyget frågar Artdatabankens fynddatabas (SOS) om det redan finns ett fynd av samma taxon
(inklusive underarter), samma datum, inom dubblettradien (standard 50 m), från vilken källa som
helst. Finns det skippas raden. Det fångar fynd som kopierats från Artportalen in i leveransen
och fynd som redan har importerats.

### 6. Resultat

Ett fynd som klarar alla steg får status **Klar** och skrivs till rapporttabellen. Alla fynd
finns i granskningslagret med status och orsak, så det går att se varför ett fynd inte kom med.

Verktyg 2 tar raderna med Godkänn = Ja och gör om steg 3–5 med namnet i Förslag. Ändringar i
granskningslagret, t.ex. ett annat lokalnamn, följer med.

### Exempel

| Fält i GeoPackage | Värde |
|---|---|
| taxon_vetenskapligtNamn | (tomt) |
| taxon_svensktNamn | väddklint |
| datumForObjektavgr | 2025-11-17 |
| kvantifiering | Okänd förekomst |
| utforare | Maya Edlund, Frida Bäckström-Wood |
| FK_KartlaggningBM | Artinventering Albano |

Verktyget körs med lokalnamnet "NVI Akademiska Hus Albano" och vetenskapliga namn.

1. Namn, datum och geometri finns, och kvantifieringen säger inte att arten saknas.
2. "väddklint" ger ett taxon i Dyntaxa: arten *Centaurea scabiosa* (264738).
3. *Centaurea scabiosa* hör också till en underart, så Artnamn blir 264738.
4. SOS har inget fynd av väddklint samma dag inom 50 m.
5. Raden i rapporten:

| Artnamn | Lokalnamn | Ost | Nord | Noggrannhet | Startdatum | Slutdatum | Publik kommentar | Privat kommentar |
|---|---|---|---|---|---|---|---|---|
| 264738 | NVI Akademiska Hus Albano | 673709 | 6583662 | 10 m | 2025-11-17 | 2025-11-17 | Okänd förekomst | Utförare: Maya Edlund, Frida Bäckström-Wood. Kartläggning: Artinventering Albano |

Hade namnet varit "väddklint?" hade fyndet gått till granskning med förslaget *Centaurea
scabiosa*.

## Parametrar

### 1. Förbered rapport till Artportalen

| Parameter | Standard | Beskrivning |
|---|---|---|
| Artlager | | Punktlager i kartan med schemat Artinventering_generell eller Artforekomst_punkt (dra in `main.Artforekomst_punkt` från en .gpkg i kartan, så finns det i listan). Urval och definition queries gäller. |
| Lokalnamn | | Lokalnamn för alla fynd, t.ex. "NVI Akademiska Hus Albano". Krävs. Kan ändras per fynd i granskningslagret om det behövs. |
| Rapporttabell | `Artportalen_rapport` i projektets geodatabas | Se Kolumner nedan. |
| Granskningslager | `Artportalen_granskning` | Alla fynd med status och namnförslag. Måste ligga i en fil-geodatabas. |
| Artnamnsspråk | Vetenskapligt namn | Dyntaxas rekommenderade vetenskapliga eller svenska namn. |
| Noggrannhet när fältet är tomt | 10 m | Används när `Noggrannhet` saknas eller inte finns i Artportalens lista. |
| Sök dubbletter i Artportalen och SOS | på | Se steg 5 ovan. Tar ungefär en minut per 300 fynd. |
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
| Artportalens Excelmall (.xls) | `ap2_template_sv.xls`. Mallen ändras inte. |
| Excelfil för import | Samma filtyp som mallen. Fler än 2000 fynd ger `_del1`, `_del2` och så vidare, för att inte överskrida Artportalens maxgräns för rapporter. |

## Kolumner

Rapporten innehåller de kolumner som Artportalen kräver enligt mallens instruktioner, plus
Antal och kommentarer.

| Artportalen | Krav i Artportalen | Värde |
|---|---|---|
| Artnamn | måste anges | Dyntaxas namn, eller Dyntaxa-id om namnet är tvetydigt |
| Lokalnamn | måste anges, högst 75 tecken | lokalnamnet från verktyget |
| Ost, Nord | måste anges för nya lokaler | geometrin, omräknad till SWEREF 99 TM |
| Noggrannhet | måste anges för nya lokaler | `Noggrannhet`, annars verktygets standard |
| Startdatum, Slutdatum | måste anges | `datumForObjektavgr` |
| Antal | "kan, men bör ej, lämnas blank" | `Antal` om det är ett heltal större än 0, annars tomt |
| Publik kommentar | kan lämnas tom, högst 1000 tecken | `kvantifiering` |
| Privat kommentar | kan lämnas tom, högst 1000 tecken | utförare och kartläggning |

Rapporttabellen har också fältet Källans id, som verktyg 2 använder för att inte lägga till
samma fynd två gånger. Det exporteras inte: mallens kolumn Externid får bara användas inom
projekt. Är id:t inte unikt i lagret, till exempel när flera GeoPackage-filer slagits ihop,
används OBJECTID.

### Antal

Artportalen vill ha ett räknat antal, ett heltal större än 0, och Enhet (plantor/tuvor,
exemplar, m² och så vidare) är valfri. Ett tomt Antal betyder att fyndet inte är räknat, vilket
oftast är fallet, så verktyget skriver aldrig ett antal som inte är räknat:

- Artinventering_generell har heltalsfältet `Antal`. Det används när det är större än 0.
- `Artforekomst_punkt` (SIS-TS 199002) saknar antal. `kvantifiering` är en klass (Okänd,
  Sparsam, God eller Mycket god förekomst) som inte går att översätta till ett antal, så Antal
  blir tomt och klassen skrivs i Publik kommentar. Ett fåtal fynd har ett antal i
  `objektbeskrivning` ("1 buske", "Ca 5 st", "15 m2"); de går att fylla i för hand i
  rapporttabellen om det behövs.

## Bra att veta

- Ska verktyg 1 köras om efter en granskning: ge det nya granskningslagret ett annat namn och
  peka ut det gamla under Beslut från tidigare granskning. Ramverket raderar ett befintligt
  utdata innan verktyget körs, så besluten i ett lager som skrivs över går inte att läsa.
- NVI-mallens fält `Noggrannhet` har standardvärdet 5 m, så nya punkter får 5 m om ingen ändrar
  det.
- Dyntaxa-svar cachas i `%LOCALAPPDATA%\ArcGIS-RapporteraTillArtportalen` för att inte behöva anropa APIet i onödan.
- Avsluta redigeringen av granskningslagret innan verktyg 2 körs, glöm inte att spara ändringar!
