# -*- coding: utf-8 -*-
"""
Rapportera till Artportalen
===========================

Tre verktyg som tar artfynd från ett punktlager med Ekologigruppens schema
Artinventering_generell, eller Artforekomst_punkt i en NVI-leverans (GeoPackage), och gör
om dem till Artportalens Excelimport.

1. Förbered rapport till Artportalen
   Läser lagret (urval och definitionsfråga gäller), slår upp varje artnamn i
   Dyntaxa, söker dubbletter i Artportalen via SOS och skriver
   - en rapporttabell med Artportalens obligatoriska kolumner, Antal och kommentarer
   - ett granskningslager (punkter) med status och namnförslag för varje fynd.
2. Lägg till granskade fynd
   Tar raderna i granskningslagret som har Godkänn = Ja och lägger till dem i
   rapporttabellen. Godkänn = Nej markerar raden som avvisad.
3. Exportera till Artportalens Excelmall
   Skriver rapporttabellen in i bladet "Alla arter" i Artportalens mall, högst
   2000 fynd per fil (Artportalens gräns per import).

Datakällor
----------
Dyntaxa Taxon Service : https://api.artdatabanken.se/taxonservice/v1
                        GET /taxa/search?searchString=&searchFields=Both&pageSize=
                        GET /taxa/{id}/parentids
Species Observation System (SOS):
                        https://api.artdatabanken.se/species-observation-system/v1
                        POST /Observations/Count (antal fynd som matchar ett filter)
Nycklar               : https://api-portal.artdatabanken.se/ (Ocp-Apim-Subscription-Key).
                        Dyntaxa och SOS är två produkter i portalen; en nyckel fungerar för
                        båda om prenumerationen omfattar båda.

Regler för artnamn
------------------
- Namnet i fältet taxon_svensktNamn (alias Artnamn) slås upp bland accepterade taxa i
  Dyntaxa, både svenska och vetenskapliga namn och synonymer. Auktor efter ett
  vetenskapligt namn stryks.
- Pekar namnet på en art och dess underarter eller varieteter används arten.
- Oklara namn går till granskning med förslag: frågetecken, "a/b" eller "eller",
  felstavning (likhetssökning), flera möjliga taxa, eller ett taxon som Dyntaxa har
  undantagit från rapportering (då föreslås arten).
- Namn som slutar på "sp." är bara bestämda till släkte och skippas.

Dubbletter
----------
Ett fynd räknas som dubblett om SOS redan har ett fynd av samma taxon (inklusive
underliggande taxa) samma datum inom dubblettradien från punkten, från vilken
dataleverantör som helst. Kontrollen fångar också fynd som redan har importerats.

Koordinater
-----------
Ost/Nord skrivs alltid i SWEREF 99 TM (EPSG:3006). Artportalen läser koordinaterna i
det koordinatsystem som är valt under Mina sidor, och artnamnen på det valda
artnamnsspråket, så båda inställningarna måste stämma med filen.

Verktygstipsen skrivs till <verktygslåda>.<verktyg>.pyt.xml från TOOLTIPS när
verktygslådan laddas.
"""
import datetime
import difflib
import json
import os
import re
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from xml.sax.saxutils import escape

import arcpy

# =============================================================================
# Konstanter
# =============================================================================

DYNTAXA_API = "https://api.artdatabanken.se/taxonservice/v1"
SOS_COUNT_URL = "https://api.artdatabanken.se/species-observation-system/v1/Observations/Count"
ENV_DYNTAXA = "DYNTAXA_KEY"
ENV_SOS = "ARTDATABANKEN_API_KEY"  # samma variabel som Sök artfynd i Fynddata
CREDENTIAL_SERVICE = "Artdatabanken API (ArcGIS-verktyg)"
CREDENTIAL_DYNTAXA = "dyntaxa"
CREDENTIAL_SOS = "sos"
HTTP_TIMEOUT = 60
HTTP_RETRIES = 8

APP_DIR = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
                       "ArcGIS-RapporteraTillArtportalen")
CACHE_FILE = os.path.join(APP_DIR, "dyntaxa_cache.json")

SWEREF99TM = 3006
WGS84 = 4326

SPRAK_VET = "Vetenskapligt namn"
SPRAK_SV = "Svenskt namn"

NOGGRANNHET = ["1 m", "5 m", "10 m", "25 m", "50 m", "75 m", "100 m", "125 m", "150 m",
               "200 m", "250 m", "300 m", "400 m", "500 m", "750 m", "1000 m", "1500 m",
               "2000 m", "2500 m", "3000 m", "5000 m"]  # mallens lista Accuracy
DEFAULT_NOGGRANNHET = "10 m"
DEFAULT_RADIE_M = 50
MAX_RADER_PER_IMPORT = 2000
MAX_LOKALNAMN = 75
MALL_BLAD = "Alla arter"
MALL_RUBRIKRAD = 2
MALL_FORSTA_DATARAD = 3

STATUS_KLAR = "Klar"
STATUS_GRANSKA = "Behöver granskning"
STATUS_SKIPPAD = "Skippad"
STATUS_TILLAGD = "Tillagd efter granskning"
STATUS_AVVISAD = "Avvisad vid granskning"
DOMAIN_GODKANN = "RapporteraArtportalen_JaNej"

# Hur lokalnamnet från verktyget förhåller sig till objektnamn i lagret
LAGE_TOM = "Bara där lagret saknar värde"
LAGE_ERSATT = "Ersätt lagrets värde"
LAGEN_LOKAL = [LAGE_TOM, LAGE_ERSATT]

# Fält i Artinventering_generell och Artforekomst_punkt (SIS-TS 199002, NVI-leveransens
# GeoPackage). Namn, inte alias. Ett artnamnsfält och datum krävs. Finns båda artnamnsfälten
# används det vetenskapliga namnet, och det svenska när det vetenskapliga är tomt.
SRC_ARTNAMN = "taxon_svensktNamn"
SRC_VETNAMN = "taxon_vetenskapligtNamn"
SRC_DATUM = "datumForObjektavgr"
SRC_ID = "objektidentitet"  # Källans id när lagret saknar GlobalID (GeoPackage)
SRC_KARTL = "FK_KartlaggningBM"  # kartläggningen fyndet hör till (GeoPackage)
SRC_FALT = [SRC_ARTNAMN, SRC_VETNAMN, SRC_DATUM, "Antal", "kvantifiering", "Noggrannhet",
            "objektnamn", "utforare", SRC_KARTL, SRC_ID]
# kvantifiering (SIS-TS 199002) som betyder att arten inte hittades: rapporteras inte som fynd
EJ_FOREKOMST = re.compile(r"förekommer\s+(sannolikt\s+)?inte", re.I)

# Rapporttabellens fält: (namn, alias = kolumnrubrik i mallen, typ, längd). Artportalen
# kräver Artnamn, Lokalnamn, Ost, Nord, Noggrannhet, Startdatum och Slutdatum (mallens
# instruktioner); Antal kan lämnas tomt men bör anges när det är räknat, och Publik
# kommentar får kvantifiering (förekomstklassen) och Privat kommentar utförare och
# kartläggning. Kalla_id är
# verktygets eget id för fyndet och exporteras inte (Externid får bara användas i projekt).
RAPPORT_FALT = [
    ("Artnamn", "Artnamn", "TEXT", 255),
    ("Antal", "Antal", "LONG", None),
    ("Lokalnamn", "Lokalnamn", "TEXT", MAX_LOKALNAMN),
    ("Ost", "Ost", "LONG", None),
    ("Nord", "Nord", "LONG", None),
    ("Noggrannhet", "Noggrannhet", "TEXT", 10),
    ("Startdatum", "Startdatum", "TEXT", 10),
    ("Slutdatum", "Slutdatum", "TEXT", 10),
    ("Publik_kommentar", "Publik kommentar", "TEXT", 1000),
    ("Privat_kommentar", "Privat kommentar", "TEXT", 1000),
    ("Kalla_id", "Källans id (exporteras inte)", "TEXT", 50),
]
RAPPORT_NAMN = [f[0] for f in RAPPORT_FALT]
EXPORT_FALT = [f for f in RAPPORT_FALT if f[0] != "Kalla_id"]

# Granskningslagrets egna fält, före rapportfälten
GRANSKNING_FALT = [
    ("Status", "Status", "TEXT", 30),
    ("Orsak", "Orsak", "TEXT", 1000),
    ("Namn_i_lagret", "Namn i lagret", "TEXT", 255),
    ("Forslag", "Förslag (ändra vid behov)", "TEXT", 255),
    ("Godkann", "Godkänn", "TEXT", 3),
    ("Dyntaxa_id", "Dyntaxa-id", "LONG", None),
    ("Kalla_OID", "Källans OBJECTID", "LONG", None),
] + RAPPORT_FALT

UNDER_ART = {"Subspecies", "Variety", "Form", "Race", "Population", "Cultivar"}
OKLART = re.compile(r"\?|/|\beller\b", re.I)
SLAKTE = re.compile(r"\bsp{1,2}\.?$", re.I)

# Dyntaxa-taxonId som taxonet ska ha bland sina föräldrar -> artgrupp (används bara i loggen)
ARTGRUPPER = [
    ("Fåglar", {4000104}), ("Fladdermöss", {3000299}), ("Däggdjur", {4000107}),
    ("Grod- och kräldjur", {4000105, 3000297, 3000296}),
    ("Fiskar", {4000103, 4000102, 3000241}), ("Kärlväxter", {6000506}),
    ("Mossor", {5000047, 5000094, 5000089}),
]
DJUR, RYGGSTRANGSDJUR = 5000001, 5000034

TOOLTIPS = {
    "in_layer": (
        "Punktlager i kartan med schemat Artinventering_generell (NVI-projektmallen) eller "
        "Artforekomst_punkt i en NVI-leverans (GeoPackage: lägg till main.Artforekomst_punkt "
        "i kartan först). Lagrets urval och definitionsfråga gäller: bara de fynd lagret "
        "visar behandlas.\n"
        "Datum (datumForObjektavgr) och ett artnamn krävs. Finns både "
        "taxon_vetenskapligtNamn och taxon_svensktNamn används det vetenskapliga namnet, och "
        "det svenska när det vetenskapliga är tomt. Antal, Noggrannhet och objektnamn används "
        "om de finns. Fynd med kvantifiering \"Förekommer sannolikt inte\" skippas, eftersom "
        "arten inte hittades.\n"
        "Rapporten innehåller Artportalens obligatoriska kolumner, Antal, Publik kommentar "
        "(fältet kvantifiering, t.ex. \"Sparsam förekomst\") och Privat kommentar "
        "(\"Utförare: <utforare>. Kartläggning: <FK_KartlaggningBM>\"). GlobalID, "
        "eller objektidentitet i en GeoPackage, sparas som Källans id i rapporttabellen men "
        "exporteras inte. Multipunkter med en punkt går bra; med flera punkter skippas fyndet."
    ),
    "out_table": (
        "Rapporttabell i en geodatabas med Artportalens obligatoriska kolumner (Artnamn, "
        "Lokalnamn, Ost, Nord, Noggrannhet, Startdatum, Slutdatum), Antal, Publik "
        "kommentar (kvantifiering) och Privat kommentar (utförare och kartläggning). "
        "Fältens alias "
        "är kolumnrubrikerna i mallens blad \"Alla arter\". Fältet Källans id pekar ut "
        "punkten i indata och exporteras inte. Bara fynd med "
        "status Klar skrivs hit; granskade fynd läggs till med verktyget Lägg till "
        "granskade fynd.\n"
        "Finns tabellen redan skrivs den över (om Skriv över utdata är på)."
    ),
    "out_review": (
        "Granskningslager (punkter) i en fil-geodatabas, med alla fynd från indata och "
        "fälten Status, Orsak, Namn i lagret, Förslag, Godkänn och Dyntaxa-id, plus "
        "rapportens kolumner.\n"
        "Status Behöver granskning: välj Ja eller Nej i Godkänn och ändra gärna Förslag "
        "eller andra fält (t.ex. Lokalnamn), kör sedan Lägg till granskade fynd.\n"
        "Finns lagret redan skrivs det över, och besluten i det försvinner. Vill du "
        "behålla dem, välj ett annat namn här och peka ut det gamla lagret under Beslut "
        "från tidigare granskning."
    ),
    "tidigare": (
        "Ett granskningslager från en tidigare körning. Förslag och Godkänn läses per artnamn "
        "och används direkt: namn med Godkänn = Ja tolkas som förslaget och kommer med i "
        "rapporten, namn med Godkänn = Nej avvisas. Valfritt.\n"
        "Får inte vara samma lager som utdata, eftersom utdata raderas innan verktyget "
        "körs när Skriv över utdata är på."
    ),
    "sprak": (
        "Språket för artnamnen i rapporten: Dyntaxas rekommenderade vetenskapliga eller "
        "svenska namn. Måste vara samma artnamnsspråk som är valt i dina inställningar "
        "i Artportalen (Mina sidor), annars känner importen inte igen namnen. Taxa utan "
        "svenskt namn får det vetenskapliga."
    ),
    "noggrannhet": (
        "Noggrannhet för fynd där fältet Noggrannhet är tomt eller har ett värde som inte "
        "finns i Artportalens lista. Standard 10 m. Artportalen kräver noggrannhet för "
        "nya lokaler."
    ),
    "lokalnamn": (
        "Lokalnamn att använda i rapporten, till exempel \"Campus Ultuna\". Hur det "
        "förhåller sig till fältet objektnamn i lagret styrs av Lokalnamn används.\n"
        "Lämnas den tom används objektnamn, och fynd utan objektnamn går till granskning, "
        "där lokalnamnet kan fyllas i. Artportalen tillåter högst 75 tecken; längre namn "
        "kortas."
    ),
    "lokalnamn_lage": (
        "Bara där lagret saknar värde: lokalnamnet ovan används för fynd där objektnamn är "
        "tomt. Övriga fynd behåller sitt objektnamn.\n"
        "Ersätt lagrets värde: alla fynd får lokalnamnet ovan, oavsett objektnamn. Tänk på "
        "att Artportalen skapar en ny lokal per unikt namn och koordinat, så ett gemensamt "
        "namn ger många lokaler med samma namn.\n"
        "Används inte när Lokalnamn är tomt."
    ),
    "dubbletter": (
        "Sök efter samma fynd i Artportalen och andra källor i SOS innan fyndet tas med. "
        "Ett fynd räknas som dubblett om det finns ett fynd av samma taxon (inklusive "
        "underarter) samma datum inom dubblettradien. Kräver SOS-nyckeln och tar några "
        "minuter per tusen fynd. Stäng av för en snabb provkörning."
    ),
    "radie": (
        "Avstånd i meter från punkten inom vilket ett fynd av samma taxon samma datum räknas "
        "som dubblett. Standard 50 m."
    ),
    "dyntaxa_key": (
        "Prenumerationsnyckel (Ocp-Apim-Subscription-Key) för Dyntaxa Taxon Service, från "
        "api-portal.artdatabanken.se. Lämna tomt för att använda en sparad nyckel eller "
        "miljövariabeln " + ENV_DYNTAXA + "."
    ),
    "sos_key": (
        "Prenumerationsnyckel för Species Observation System (SOS), från "
        "api-portal.artdatabanken.se. Behövs bara för dubblettkontrollen. Lämna tomt för att "
        "använda en sparad nyckel eller miljövariabeln " + ENV_SOS + ", som även Sök "
        "artfynd i Fynddata använder."
    ),
    "spara_nycklar": (
        "Spara de ifyllda nycklarna i Windows Autentiseringshanteraren för ditt "
        "användarkonto, så att fälten kan lämnas tomma nästa gång. Nyckeln sparas först "
        "när ett testanrop med den har lyckats. Nyckeln lagras krypterad, till skillnad "
        "från en miljövariabel."
    ),
    "in_review": (
        "Granskningslagret från Förbered rapport till Artportalen. Rader med status "
        "Behöver granskning och Godkänn = Ja slås upp på nytt (Förslag, eller Namn i lagret "
        "om Förslag är tomt) och läggs till i rapporttabellen. Godkänn = Nej ger status "
        "Avvisad vid granskning. Lagrets urval gäller."
    ),
    "in_table": (
        "Rapporttabellen som fynden läggs till i. Fynd vars Källans id redan finns i tabellen "
        "läggs inte till igen. Tabellen ändras på plats, som med verktyget Lägg till "
        "(Append)."
    ),
    "out_updated": "Rapporttabellen efter tillägget (samma tabell som indata).",
    "in_report": (
        "Rapporttabellen från Förbered rapport till Artportalen. Raderna skrivs i "
        "OBJECTID-ordning."
    ),
    "mall": (
        "Artportalens Excelmall för import (ap2_template_sv.xls), som den laddas ned från "
        "Artportalen. Verktyget kopierar mallen och fyller i kopian med Excel, så att filen "
        "är Artportalens egen och går att klistra in i importen. Kräver Excel på datorn.\n"
        "Raderna skrivs i bladet \"Alla arter\" från rad 3 och kolumnerna matchas mot "
        "rubrikerna på rad 2. Mallen ändras inte. En mall sparad som .xlsx går också, utan "
        "Excel, men Artportalen kan neka inklistring från en sådan fil."
    ),
    "out_xlsx": (
        "Excelfil att importera i Artportalen, samma filtyp som mallen. Öppna den, markera "
        "rubrikraden och fynden i bladet \"Alla arter\", kopiera och klistra in under "
        "Rapportera, Importera fynd. Har tabellen fler än 2000 rader, vilket är "
        "Artportalens gräns per import, skrivs flera filer med _del1, _del2 och så vidare "
        "i namnet. Finns filen redan skrivs den över."
    ),
}

SUMMARY_FORBERED = (
    "Gör om artfynd i ett lager med schemat Artinventering_generell eller Artforekomst_punkt "
    "(NVI-leverans i GeoPackage) till en tabell för "
    "Artportalens Excelimport. Varje artnamn slås upp i Dyntaxa; pekar ett namn på både art "
    "och underart används arten. Fynd som redan finns i Artportalen eller andra källor i "
    "SOS (samma taxon, samma datum, inom dubblettradien) skippas.\n"
    "Oklara namn (frågetecken, \"a/b\", felstavning, flera möjliga taxa, taxa undantagna "
    "från rapportering) får ett förslag i granskningslagret. Välj Ja eller Nej i Godkänn "
    "och kör Lägg till granskade fynd. Exportera sedan med Exportera till Artportalens "
    "Excelmall.\n"
    "Ost/Nord skrivs i SWEREF 99 TM. Koordinatsystem och artnamnsspråk under Mina sidor i "
    "Artportalen måste stämma med filen."
)
SUMMARY_LAGG_TILL = (
    "Lägger till granskade fynd i rapporttabellen. Rader i granskningslagret med status "
    "Behöver granskning och Godkänn = Ja slås upp i Dyntaxa (Förslag, eller Namn i lagret om "
    "Förslag är tomt), dubblettkontrolleras och läggs till. Godkänn = Nej markeras som "
    "avvisade. Ändringar i andra fält i granskningslagret, t.ex. Lokalnamn, följer med."
)
SUMMARY_EXPORTERA = (
    "Skriver rapporttabellen in i bladet \"Alla arter\" i Artportalens Excelmall, högst "
    "2000 fynd per fil. Filen importeras sedan i Artportalen under Rapportera, Importera "
    "fynd från Excel."
)

# =============================================================================
# Hjälpfunktioner: allmänt
# =============================================================================


def _fmt_duration(seconds):
    seconds = int(round(seconds))
    if seconds < 60:
        return "{} s".format(seconds)
    return "{} min {} s".format(seconds // 60, seconds % 60)


def _text(value):
    """Värde -> trimmad text utan radbrytningar och dubbla blanksteg."""
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def _cut(value, n):
    value = _text(value)
    return value if len(value) <= n else value[:n - 1] + "…"


class _Progress:
    """Förlopp per fynd med uppskattad tid kvar."""

    def __init__(self, total, label, messages):
        self.total = max(total, 1)
        self.label = label
        self.messages = messages
        self.t0 = time.time()
        self.last_log = 0
        arcpy.SetProgressor("step", label, 0, self.total, 1)

    def step(self, k):
        arcpy.SetProgressorPosition(k)
        elapsed = time.time() - self.t0
        text = "{}: {} av {}".format(self.label, k, self.total)
        if k >= 5 and elapsed > 5:
            left = elapsed / k * (self.total - k)
            text += ", ca {} kvar".format(_fmt_duration(left))
        arcpy.SetProgressorLabel(text)
        if time.time() - self.last_log > 30:
            self.messages.addMessage("    " + text)
            self.last_log = time.time()


# =============================================================================
# Hjälpfunktioner: API-nycklar
# =============================================================================


def _saved_key(user):
    try:
        import keyring
        return keyring.get_password(CREDENTIAL_SERVICE, user) or ""
    except Exception:
        return ""


def _key(parameter, user, env):
    """Nyckel i ordningen: fältet, Autentiseringshanteraren, miljövariabeln."""
    value = (parameter.valueAsText or "").strip() if parameter is not None else ""
    return value or _saved_key(user) or os.environ.get(env, "").strip()


def _missing_key_text(what, env):
    return ("Ingen {} angiven. Fyll i fältet, spara en nyckel med Spara nycklarna, eller sätt "
            "miljövariabeln {}. Nyckeln hämtas på api-portal.artdatabanken.se."
            .format(what, env))


def _remember_keys(dyntaxa_key, sos_key, use_sos, messages):
    """Spara nycklar som har klarat ett testanrop (annars ValueError)."""
    import keyring
    saved = []
    if dyntaxa_key:
        _dyntaxa_get("/taxa/search", dyntaxa_key, use_cache=False,
                     searchString="abborre", searchFields="Both", pageSize=1)
        keyring.set_password(CREDENTIAL_SERVICE, CREDENTIAL_DYNTAXA, dyntaxa_key)
        saved.append("Dyntaxa")
    if sos_key and use_sos:
        _sos_count({"taxon": {"ids": [100062]}}, sos_key)
        keyring.set_password(CREDENTIAL_SERVICE, CREDENTIAL_SOS, sos_key)
        saved.append("SOS")
    if saved:
        messages.addMessage("Nyckel för {} sparad i Windows Autentiseringshanteraren."
                            .format(" och ".join(saved)))


# =============================================================================
# Hjälpfunktioner: HTTP, Dyntaxa och SOS
# =============================================================================

_cache = None


def _load_cache():
    global _cache
    if _cache is None:
        try:
            with open(CACHE_FILE, encoding="utf-8") as fh:
                _cache = json.load(fh)
        except (OSError, ValueError):
            _cache = {}
    return _cache


def _save_cache():
    if _cache is None:
        return
    try:
        os.makedirs(APP_DIR, exist_ok=True)
        tmp = CACHE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(_cache, fh, ensure_ascii=False)
        os.replace(tmp, CACHE_FILE)
    except OSError:
        pass


def _http(request, what):
    """Skicka en begäran med omförsök vid 429, 5xx och nätverksfel."""
    for attempt in range(HTTP_RETRIES):
        try:
            with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                raise ValueError("{} godkände inte nyckeln (HTTP {}). Kontrollera nyckeln och "
                                 "att prenumerationen omfattar {} på api-portal.artdatabanken.se."
                                 .format(what, exc.code, what))
            if exc.code not in (408, 429) and exc.code < 500:
                raise ValueError("{} svarade HTTP {}: {}".format(
                    what, exc.code, exc.read()[:300].decode("utf-8", "replace")))
            wait = int(exc.headers.get("Retry-After") or 0) or 2 ** attempt
        except OSError:
            wait = 2 ** attempt
        time.sleep(min(wait, 60))
    raise ValueError("{} svarar inte efter {} försök. Försök igen senare.".format(what, HTTP_RETRIES))


def _dyntaxa_get(path, key, use_cache=True, **query):
    url = DYNTAXA_API + path + ("?" + urllib.parse.urlencode(query) if query else "")
    cache = _load_cache()
    if use_cache and url in cache:
        return cache[url]
    req = urllib.request.Request(url, headers={"Ocp-Apim-Subscription-Key": key,
                                               "User-Agent": "ArcGIS-RapporteraTillArtportalen"})
    data = json.loads(_http(req, "Dyntaxa").decode("utf-8"))
    cache[url] = data
    return data


def _sos_count(search_filter, key):
    req = urllib.request.Request(
        SOS_COUNT_URL, data=json.dumps(search_filter).encode("utf-8"), method="POST",
        headers={"Ocp-Apim-Subscription-Key": key, "Content-Type": "application/json",
                 "User-Agent": "ArcGIS-RapporteraTillArtportalen"})
    return int(_http(req, "SOS"))


class Dyntaxa:
    """Uppslag av artnamn i Dyntaxa med reglerna i modulens dokumentation."""

    def __init__(self, key, sprak):
        self.key = key
        self.sprak = sprak
        self._forslag = {}

    def _search(self, text, page_size=50):
        return self._get("/taxa/search", searchString=text, searchFields="Both",
                         pageSize=page_size)["data"]

    def _get(self, path, **query):
        return _dyntaxa_get(path, self.key, **query)

    def parents(self, taxon_id):
        return set(self._get("/taxa/{}/parentids".format(taxon_id))["taxonIds"])

    @staticmethod
    def vet(t):
        return next((n["name"] for n in t["names"] if n["category"]["value"] == "ScientificName"
                     and n["isRecommended"]), str(t["taxonId"]))

    @staticmethod
    def sv(t):
        return next((n["name"] for n in t["names"] if n["category"]["value"] == "SwedishName"
                     and n["isRecommended"]), "")

    def report_name(self, t):
        return (self.sv(t) or self.vet(t)) if self.sprak == SPRAK_SV else self.vet(t)

    def label(self, t):
        sv = self.sv(t)
        return "{} ({})".format(self.vet(t), sv) if sv else self.vet(t)

    def exact(self, name):
        """Accepterade taxa med exakt detta namn (svenskt, vetenskapligt eller synonym)."""
        name = re.sub(r"^([A-Z][a-z-]+ [a-z-]+)( [A-Z(].*| \w+\.)$", r"\1", _text(name))
        return [t for t in self._search(name)
                if t["status"]["value"] == "Accepted"
                and any(n["name"].strip().lower() == name.lower() for n in t["names"])]

    def species_over_subspecies(self, taxa):
        """Pekar namnen på en art och dess underarter/varieteter -> bara arten."""
        species = [t for t in taxa if t["category"]["value"] == "Species"]
        if len({t["taxonId"] for t in species}) == 1:
            sid = species[0]["taxonId"]
            if all(t["taxonId"] == sid or sid in self.parents(t["taxonId"]) for t in taxa):
                return species[:1]
        return taxa

    def lookup(self, name):
        """Taxon för ett namn, eller ValueError med orsaken."""
        hits = self.exact(name)
        if len({t["taxonId"] for t in hits}) > 1:
            hits = self.species_over_subspecies(hits)
        if len({t["taxonId"] for t in hits}) > 1:
            rec = [t for t in hits if any(n["name"].strip().lower() == _text(name).lower()
                                          and n["isRecommended"] for n in t["names"])]
            hits = rec or hits
        ids = sorted({t["taxonId"] for t in hits})
        if not ids:
            raise ValueError("namnet finns inte i Dyntaxa")
        if len(ids) > 1:
            raise ValueError("namnet matchar flera taxa i Dyntaxa: " +
                             ", ".join(self.label(t) for t in hits))
        t = hits[0]
        if t.get("excludeFromReportingSystem"):
            raise ValueError("taxonet är undantaget från rapportering i Dyntaxa")
        return t

    def group(self, t):
        f = self.parents(t["taxonId"]) | {t["taxonId"]}
        g = next((name for name, ids in ARTGRUPPER if f & ids), None)
        if g:
            return g
        return "Ryggradslösa djur" if DJUR in f and RYGGSTRANGSDJUR not in f else "Övriga"

    def _fuzzy(self, name, n_max=3):
        """Felstavade namn: sök på delsträngar och ranka efter likhet."""
        q = name.lower().replace("é", "e")
        cand = {}
        for k in range(len(q) - 1, 3, -1):
            for part in {q[:k], q[-k:]}:
                for t in self._search(part, page_size=200):
                    if t["status"]["value"] != "Accepted" or t.get("excludeFromReportingSystem"):
                        continue
                    for n in t["names"]:
                        if n["category"]["value"] in ("SwedishName", "ScientificName"):
                            r = difflib.SequenceMatcher(None, q, n["name"].lower()).ratio()
                            if r > cand.get(t["taxonId"], (0,))[0]:
                                cand[t["taxonId"]] = (r, t)
            if any(r >= 0.85 for r, _ in cand.values()):
                break
        best = sorted(cand.values(), key=lambda x: -x[0])[:n_max]
        return [(t, "liknar '{}' ({:.0%})".format(name, r)) for r, t in best if r >= 0.7]

    def suggest(self, name):
        """[(taxon, motivering)], bästa förslaget först. Memoiseras per namn."""
        if name in self._forslag:
            return self._forslag[name]
        clean = _text(name.replace("?", ""))
        parts = [p.strip() for p in re.split(r"/|\beller\b", clean, flags=re.I) if p.strip()]
        out = []
        for part in ([clean] if len(parts) > 1 else []) + parts:
            for t in self.species_over_subspecies(self.exact(part)) if self.exact(part) else []:
                if t.get("excludeFromReportingSystem") and t["category"]["value"] in UNDER_ART:
                    species = " ".join(self.vet(t).split()[:2])
                    out += [(a, "'{}' är en underart som inte får rapporteras; förslaget är arten"
                             .format(part))
                            for a in self.exact(species) if not a.get("excludeFromReportingSystem")]
                elif not t.get("excludeFromReportingSystem"):
                    out.append((t, "'{}' matchar {} i Dyntaxa".format(
                        part, t["category"]["name"].lower())))
            if out and part == clean:
                break  # hela namnet finns, t.ex. som artkomplex
        if not out:
            for part in parts:
                out += self._fuzzy(part)
        out.sort(key=lambda x: x[0]["category"]["value"] not in ("Species", "SpeciesComplex"))
        unique, seen = [], set()
        for t, why in out:
            if t["taxonId"] not in seen:
                seen.add(t["taxonId"])
                unique.append((t, why))
        self._forslag[name] = unique
        return unique


class Dubbletter:
    """Dubblettkontroll mot SOS: samma taxon, samma datum, inom radien."""

    def __init__(self, key, radius_m):
        self.key = key
        self.radius = int(radius_m)
        self._memo = {}

    def count(self, taxon_id, lon, lat, date):
        k = (taxon_id, round(lon, 6), round(lat, 6), date)
        if k not in self._memo:
            self._memo[k] = _sos_count({
                "taxon": {"ids": [taxon_id], "includeUnderlyingTaxa": True},
                "date": {"startDate": date, "endDate": date,
                         "dateFilterType": "OverlappingStartDateAndEndDate"},
                "geographics": {"geometries": [{"type": "point", "coordinates": [lon, lat]}],
                                "maxDistanceFromPoint": self.radius,
                                "considerObservationAccuracy": False},
            }, self.key)
        return self._memo[k]


# =============================================================================
# Hjälpfunktioner: geodata
# =============================================================================


def _field_map(dataset):
    """Fältnamn i gemener -> verkligt namn."""
    return {f.name.lower(): f.name for f in arcpy.ListFields(dataset)}


def _missing_source_fields(layer):
    have = _field_map(layer)
    missing = [] if {SRC_ARTNAMN.lower(), SRC_VETNAMN.lower()} & set(have) else [SRC_ARTNAMN]
    return missing + ([SRC_DATUM] if SRC_DATUM.lower() not in have else [])


def _globalid_field(layer):
    try:
        d = arcpy.Describe(layer)
        return d.globalIDFieldName if getattr(d, "hasGlobalID", False) else ""
    except Exception:
        return ""


def _date_text(value):
    if value is None or value == "":
        return ""
    if isinstance(value, (datetime.datetime, datetime.date)):
        return value.strftime("%Y-%m-%d")
    m = re.match(r"(\d{4}-\d{2}-\d{2})", _text(value))
    return m.group(1) if m else ""


def _is_network(path):
    """Sant om sökvägen ligger på en nätverksenhet eller UNC-sökväg."""
    path = os.path.abspath(path)
    if path.startswith("\\\\"):
        return True
    try:
        import ctypes
        return ctypes.windll.kernel32.GetDriveTypeW(os.path.splitdrive(path)[0] + "\\") == 4
    except Exception:
        return False


def _is_file_gdb(path):
    ws = os.path.dirname(path)
    while ws and not ws.lower().endswith(".gdb"):
        parent = os.path.dirname(ws)
        if parent == ws:
            return False
        ws = parent
    return bool(ws)


def _workspace_of(path):
    ws = os.path.dirname(path)
    while ws and not ws.lower().endswith(".gdb"):
        parent = os.path.dirname(ws)
        if parent == ws:
            return os.path.dirname(path)
        ws = parent
    return ws


def _default_gdb():
    try:
        gdb = arcpy.mp.ArcGISProject("CURRENT").defaultGeodatabase
        if gdb:
            return gdb
    except Exception:
        pass
    ws = arcpy.env.workspace or ""
    return ws if ws.lower().endswith(".gdb") else ""


def _field_specs(specs):
    out = []
    for name, alias, ftype, length in specs:
        out.append([name, ftype, alias, length or ""] if ftype == "TEXT" else [name, ftype, alias])
    return out


def _new_memory(kind, specs, sr=None):
    name = "memory\\rta_" + uuid.uuid4().hex[:12]
    if kind == "table":
        arcpy.management.CreateTable("memory", os.path.basename(name))
    else:
        arcpy.management.CreateFeatureclass("memory", os.path.basename(name), "POINT",
                                            spatial_reference=sr)
    arcpy.management.AddFields(name, _field_specs(specs))
    return name


def _delete(name):
    try:
        arcpy.management.Delete(name)
    except Exception:
        pass


def _same_fields(a, b):
    def fields(ds):
        return {(f.name.lower(), f.type) for f in arcpy.ListFields(ds)
                if f.editable and f.type not in ("OID", "Geometry")}
    return fields(a) == fields(b)


def _write_output(src, out, is_table):
    """
    Skriv utdata. Utdata som visas i en karta går inte att ersätta från Python (Export ger
    ERROR 002869, Delete lämnar det kvar), så finns ett utdata med samma fält töms det och
    fylls på i stället. Lagret behåller då sin plats och symbologi.
    """
    exists = arcpy.Exists(out)
    if exists and arcpy.env.overwriteOutput and _same_fields(src, out):
        arcpy.management.TruncateTable(out)
        arcpy.management.Append(src, out, "NO_TEST")
        return out
    try:
        if is_table:
            arcpy.conversion.ExportTable(src, out)
        else:
            with arcpy.EnvManager(outputCoordinateSystem=arcpy.Describe(src).spatialReference,
                                  extent="MAXOF"):
                arcpy.conversion.ExportFeatures(src, out)
    except arcpy.ExecuteError:
        if exists:
            raise ValueError("{} kunde inte skrivas över. Visas det i en karta med andra fält, "
                             "ta bort lagret ur kartan eller välj ett annat namn.".format(out))
        raise
    return out


def _ensure_domain(fc):
    """Ja/Nej-domän på fältet Godkann, så att valet görs i en lista i attributtabellen."""
    gdb = _workspace_of(fc)
    if not gdb.lower().endswith(".gdb"):
        return
    if DOMAIN_GODKANN not in {d.name for d in arcpy.da.ListDomains(gdb)}:
        arcpy.management.CreateDomain(gdb, DOMAIN_GODKANN, "Ja eller Nej för ett namnförslag",
                                      "TEXT", "CODED")
        for code in ("Ja", "Nej"):
            arcpy.management.AddCodedValueToDomain(gdb, DOMAIN_GODKANN, code, code)
    fld = next((f for f in arcpy.ListFields(fc) if f.name.lower() == "godkann"), None)
    if fld is not None and fld.domain != DOMAIN_GODKANN:
        arcpy.management.AssignDomainToField(fc, fld.name, DOMAIN_GODKANN)


def _previous_decisions(review):
    """Namn i lagret (gemener) -> (Förslag, Godkänn) från ett tidigare granskningslager."""
    if not review or not arcpy.Exists(review):
        return {}
    fm = _field_map(review)
    need = ("namn_i_lagret", "forslag", "godkann")
    if not all(n in fm for n in need):
        return {}
    out = {}
    with arcpy.da.SearchCursor(review, [fm[n] for n in need]) as cur:
        for name, forslag, godkann in cur:
            if _text(godkann) in ("Ja", "Nej") and _text(name):
                out[_text(name).lower()] = (_text(forslag), _text(godkann))
    return out


def _existing_ids(table):
    fm = _field_map(table)
    if "kalla_id" not in fm:
        return set()
    with arcpy.da.SearchCursor(table, [fm["kalla_id"]]) as cur:
        return {_text(r[0]) for r in cur if _text(r[0])}


# =============================================================================
# Kärnan: bedöm ett fynd
# =============================================================================


def _resolve(row, name, decision, dyntaxa, dubbletter, wgs84_xy):
    """
    Bedöm ett fynd. row är en dict med rapportfälten (utom Artnamn). Returnerar dict med
    Status, Orsak, Forslag, Dyntaxa_id, Artnamn och taxon.
    """
    res = {"Status": STATUS_KLAR, "Orsak": "", "Forslag": "", "Dyntaxa_id": None,
           "Artnamn": "", "taxon": None}

    def review(reason):
        res["Status"] = STATUS_GRANSKA
        res["Orsak"] = reason
        if not res["Forslag"] and name:
            sug = dyntaxa.suggest(name)
            if sug:
                res["Forslag"] = dyntaxa.vet(sug[0][0])
                res["Dyntaxa_id"] = sug[0][0]["taxonId"]
                res["Orsak"] = "{}. Förslag: {}".format(reason, sug[0][1])
            else:
                res["Orsak"] = reason + ". Inget förslag hittades, skriv in rätt namn i Förslag"
        return res

    if not name:
        res.update(Status=STATUS_SKIPPAD, Orsak="artnamn saknas")
        return res
    if SLAKTE.search(name):
        res.update(Status=STATUS_SKIPPAD, Orsak="artnamnet är bara bestämt till släkte (sp.)")
        return res
    if not row["Startdatum"]:
        res.update(Status=STATUS_SKIPPAD, Orsak="datum saknas")
        return res
    if row.get("ej_forekomst"):
        res.update(Status=STATUS_SKIPPAD, Orsak="kvantifiering anger att arten inte hittades "
                   "(\"{}\")".format(row["ej_forekomst"]))
        return res
    if wgs84_xy is None:
        res.update(Status=STATUS_SKIPPAD,
                   Orsak="geometri saknas eller är en multipunkt med flera punkter")
        return res

    forslag, godkann = decision or ("", "")
    if godkann == "Nej":
        res.update(Status=STATUS_AVVISAD, Orsak="namnförslaget är avvisat vid granskning",
                   Forslag=forslag)
        return res
    if godkann == "Ja":
        res["Forslag"] = forslag or name
        try:
            taxon = dyntaxa.lookup(res["Forslag"])
        except ValueError as exc:
            return review("godkänt förslag '{}' går inte att använda: {}".format(
                res["Forslag"], exc))
    elif OKLART.search(name):
        return review("osäkert eller dubbelt artnamn")
    else:
        try:
            taxon = dyntaxa.lookup(name)
        except ValueError as exc:
            return review(str(exc))

    res["taxon"] = taxon
    res["Dyntaxa_id"] = taxon["taxonId"]
    res["Artnamn"] = dyntaxa.report_name(taxon)
    if not row["Lokalnamn"]:
        res["Status"] = STATUS_GRANSKA
        res["Orsak"] = "lokalnamn saknas. Fyll i Lokalnamn och sätt Godkänn = Ja"
        res["Forslag"] = res["Forslag"] or dyntaxa.vet(taxon)
        return res
    if dubbletter is not None:
        n = dubbletter.count(taxon["taxonId"], wgs84_xy[0], wgs84_xy[1], row["Startdatum"])
        if n:
            res["Status"] = STATUS_SKIPPAD
            res["Orsak"] = ("dubblett: {} fynd av samma taxon inom {} m samma datum finns redan "
                            "i Artportalen eller annan källa i SOS".format(n, dubbletter.radius))
    return res


def _lokalnamn(layer_value, text, mode):
    """objektnamn i lagret + lokalnamn från verktyget enligt läget (LAGE_*)."""
    if text and (mode == LAGE_ERSATT or not layer_value):
        return text
    return layer_value


# =============================================================================
# Verktyg 1: Förbered rapport
# =============================================================================


def _read_source(layer, noggrannhet, lokalnamn, lokalnamn_lage, messages):
    """Läs lagret (urval och definitionsfråga gäller) -> lista med dicts."""
    fm = _field_map(layer)
    gid = _globalid_field(layer)
    fields = ["OID@", "SHAPE@"] + [fm[f.lower()] if f.lower() in fm else None for f in SRC_FALT]
    use = [f for f in fields if f]
    idx = {f: use.index(fm[f.lower()]) for f in SRC_FALT if f.lower() in fm}
    if gid:
        use.append(gid)
    sr_in = arcpy.Describe(layer).spatialReference
    messages.addMessage("    Lagrets koordinatsystem: {}. Ost/Nord räknas om till SWEREF 99 TM."
                        .format(sr_in.name or "okänt"))
    if not (sr_in.factoryCode or sr_in.exportToString()) or sr_in.name == "Unknown":
        raise ValueError("Lagret saknar koordinatsystem. Definiera det med Definiera projektion "
                         "(Define Projection) först.")
    tm = arcpy.SpatialReference(SWEREF99TM)
    wgs = arcpy.SpatialReference(WGS84)
    missing = [f for f in SRC_FALT if f.lower() not in fm
               and f not in (SRC_ID, SRC_VETNAMN, SRC_ARTNAMN, "kvantifiering", "utforare",
                             SRC_KARTL)]
    if missing:
        messages.addMessage("    Fält som saknas i lagret och lämnas tomma: " + ", ".join(missing))
    if not gid and SRC_ID.lower() in fm:
        messages.addMessage("    Lagret saknar GlobalID. Källans id blir objektidentitet.")
    elif not gid:
        messages.addWarningMessage("Lagret har inget GlobalID. Källans id blir OBJECTID, som "
                                   "inte är stabilt om data kopieras.")

    def get(row, f):
        return row[idx[f]] if f in idx else None

    if lokalnamn:
        messages.addMessage("    Lokalnamn: \"{}\" ({})".format(lokalnamn, lokalnamn_lage.lower()))

    rows = []
    with arcpy.da.SearchCursor(layer, use) as cur:
        for r in cur:
            shape = r[1]
            xy_tm = xy_wgs = None
            # GeoPackage-lager är ofta multipunkt med en punkt per objekt; flera punkter: nej
            if shape is not None and shape.pointCount == 1:
                pt = arcpy.PointGeometry(shape.firstPoint, sr_in)
                p_tm = pt.projectAs(tm).firstPoint
                p_wgs = pt.projectAs(wgs).firstPoint
                xy_tm, xy_wgs = (p_tm.X, p_tm.Y), (p_wgs.X, p_wgs.Y)
            nogg = _text(get(r, "Noggrannhet"))
            antal = get(r, "Antal")
            kvant = _text(get(r, "kvantifiering"))
            row = {
                "Kalla_OID": r[0],
                "Namn_i_lagret": _text(get(r, SRC_VETNAMN)) or _text(get(r, SRC_ARTNAMN)),
                "shape": shape,
                "xy_tm": xy_tm,
                "xy_wgs": xy_wgs,
                "Antal": int(antal) if isinstance(antal, (int, float)) and antal > 0 else None,
                "Lokalnamn": _cut(_lokalnamn(_text(get(r, "objektnamn")), lokalnamn,
                                             lokalnamn_lage), MAX_LOKALNAMN),
                "Ost": round(xy_tm[0]) if xy_tm else None,
                "Nord": round(xy_tm[1]) if xy_tm else None,
                "Noggrannhet": nogg if nogg in NOGGRANNHET else noggrannhet,
                "Startdatum": _date_text(get(r, SRC_DATUM)),
                "Kalla_id": (_text(r[-1]) if gid else _text(get(r, SRC_ID))
                             or "OID {}".format(r[0])),
                "Publik_kommentar": _cut(kvant, 1000),
                "Privat_kommentar": _cut(". ".join(
                    "{}: {}".format(label, _text(get(r, f)))
                    for label, f in (("Utförare", "utforare"), ("Kartläggning", SRC_KARTL))
                    if _text(get(r, f))), 1000),
                "ej_forekomst": kvant if EJ_FOREKOMST.search(kvant) else "",
            }
            row["Slutdatum"] = row["Startdatum"]
            rows.append(row)
    ids = [row["Kalla_id"] for row in rows]
    if len(set(ids)) < len(ids):
        # objektidentitet är bara unik inom en leverans; sammanslagna gpkg-lager krockar
        messages.addWarningMessage(
            "Källans id är inte unikt i lagret (t.ex. objektidentitet från flera "
            "sammanslagna GeoPackage-filer). OBJECTID används i stället, så kör om "
            "verktyget på samma lager om rapporten ska kompletteras.")
        for row in rows:
            row["Kalla_id"] = "OID {}".format(row["Kalla_OID"])
    return rows, sr_in


def _run_forbered(layer, out_table, out_review, sprak=SPRAK_VET,
                  noggrannhet=DEFAULT_NOGGRANNHET, lokalnamn="", dubbletter=True,
                  radie=DEFAULT_RADIE_M, dyntaxa_key="", sos_key="", messages=None,
                  previous=None, lokalnamn_lage=LAGE_TOM):
    messages = messages or _PrintMessages()
    t_start = time.time()
    if not dyntaxa_key:
        raise ValueError(_missing_key_text("Dyntaxa-nyckel", ENV_DYNTAXA))
    if dubbletter and not sos_key:
        raise ValueError(_missing_key_text("SOS-nyckel", ENV_SOS))

    messages.addMessage("Steg 1 av 4: läser fynd")
    arcpy.SetProgressor("default", "Steg 1 av 4: läser fynd")
    rows, sr_in = _read_source(layer, noggrannhet, lokalnamn, lokalnamn_lage, messages)
    messages.addMessage("    {} fynd i lagret (urval och definitionsfråga gäller)".format(len(rows)))
    if not rows:
        raise ValueError("Lagret har inga fynd att behandla (kontrollera urval och "
                         "definitionsfråga).")
    decisions = _previous_decisions(previous)
    if previous:
        messages.addMessage("    Beslut från tidigare granskning används för {} artnamn"
                            .format(len(decisions)))

    messages.addMessage("Steg 2 av 4: slår upp artnamn i Dyntaxa" +
                        (" och söker dubbletter i SOS" if dubbletter else ""))
    dyntaxa = Dyntaxa(dyntaxa_key, sprak)
    dubb = Dubbletter(sos_key, radie) if dubbletter else None
    progress = _Progress(len(rows), "Steg 2 av 4: fynd", messages)
    try:
        for k, row in enumerate(rows, 1):
            name = row["Namn_i_lagret"]
            row["res"] = _resolve(row, name, decisions.get(name.lower()), dyntaxa, dubb,
                                  row["xy_wgs"])
            progress.step(k)
    finally:
        _save_cache()

    messages.addMessage("Steg 3 av 4: skriver rapporttabell och granskningslager")
    arcpy.SetProgressor("default", "Steg 3 av 4: skriver utdata")
    tm = arcpy.SpatialReference(SWEREF99TM)
    mem_table = _new_memory("table", RAPPORT_FALT)
    mem_fc = _new_memory("fc", GRANSKNING_FALT, tm)
    review_names = [f[0] for f in GRANSKNING_FALT]
    counts = {}
    groups = {}
    try:
        with arcpy.da.InsertCursor(mem_table, RAPPORT_NAMN) as ins_t, \
                arcpy.da.InsertCursor(mem_fc, ["SHAPE@XY"] + review_names) as ins_f:
            for row in rows:
                res = row["res"]
                counts[res["Status"]] = counts.get(res["Status"], 0) + 1
                values = dict(row)
                values["Artnamn"] = res["Artnamn"]
                if res["Status"] == STATUS_KLAR:
                    ins_t.insertRow([values[f] for f in RAPPORT_NAMN])
                    g = dyntaxa.group(res["taxon"])
                    groups[g] = groups.get(g, 0) + 1
                values.update({
                    "Status": res["Status"], "Orsak": _cut(res["Orsak"], 1000),
                    "Forslag": res["Forslag"], "Godkann": None, "Dyntaxa_id": res["Dyntaxa_id"],
                })
                if res["Status"] == STATUS_AVVISAD:
                    values["Godkann"] = "Nej"
                if row["xy_tm"] is None:
                    continue  # utan geometri kan raden inte ritas; den finns i loggen
                ins_f.insertRow([row["xy_tm"]] + [values.get(f) for f in review_names])
        _write_output(mem_table, out_table, True)
        _write_output(mem_fc, out_review, False)
        _ensure_domain(out_review)
    finally:
        _delete(mem_table)
        _delete(mem_fc)

    messages.addMessage("Steg 4 av 4: sammanfattning")
    for status in (STATUS_KLAR, STATUS_GRANSKA, STATUS_SKIPPAD, STATUS_AVVISAD):
        if counts.get(status):
            messages.addMessage("    {}: {} fynd".format(status, counts[status]))
    if groups:
        messages.addMessage("    I rapporten per artgrupp: " + ", ".join(
            "{} {}".format(g, n) for g, n in sorted(groups.items(), key=lambda x: -x[1])))
    no_geom = sum(1 for r in rows if r["xy_tm"] is None)
    if no_geom:
        messages.addWarningMessage("{} fynd saknar geometri och finns inte i "
                                   "granskningslagret.".format(no_geom))
    if counts.get(STATUS_GRANSKA):
        messages.addWarningMessage(
            "{} fynd behöver granskning. Öppna attributtabellen för granskningslagret, filtrera "
            "på Status = '{}', välj Ja eller Nej i Godkänn och kör Lägg till granskade fynd."
            .format(counts[STATUS_GRANSKA], STATUS_GRANSKA))
    if counts.get(STATUS_KLAR, 0) > MAX_RADER_PER_IMPORT:
        messages.addMessage("    Artportalen tar högst {} fynd per import; Exportera till "
                            "Artportalens Excelmall delar upp filen.".format(MAX_RADER_PER_IMPORT))
    messages.addMessage("Kontrollera att koordinatsystemet under Mina sidor i Artportalen är "
                        "SWEREF 99 TM och artnamnsspråket {} före importen."
                        .format("vetenskapligt" if sprak == SPRAK_VET else "svenskt"))
    messages.addMessage("Klart på {}.".format(_fmt_duration(time.time() - t_start)))
    return counts


# =============================================================================
# Verktyg 2: Lägg till granskade fynd
# =============================================================================


def _run_lagg_till(review, table, sprak=SPRAK_VET, dubbletter=True, radie=DEFAULT_RADIE_M,
                   dyntaxa_key="", sos_key="", messages=None):
    messages = messages or _PrintMessages()
    t_start = time.time()
    if not dyntaxa_key:
        raise ValueError(_missing_key_text("Dyntaxa-nyckel", ENV_DYNTAXA))
    if dubbletter and not sos_key:
        raise ValueError(_missing_key_text("SOS-nyckel", ENV_SOS))
    fm = _field_map(review)
    names = [f[0] for f in GRANSKNING_FALT]
    missing = [n for n in names if n.lower() not in fm]
    if missing:
        raise ValueError("Granskningslagret saknar fälten {}. Använd lagret som Förbered rapport "
                         "till Artportalen skrev.".format(", ".join(missing)))
    tfm = _field_map(table)
    missing = [n for n in RAPPORT_NAMN if n.lower() not in tfm]
    if missing:
        raise ValueError("Rapporttabellen saknar fälten {}.".format(", ".join(missing)))

    real = [fm[n.lower()] for n in names]
    status_f = arcpy.AddFieldDelimiters(review, fm["status"])
    where = "{} = '{}'".format(status_f, STATUS_GRANSKA)
    sr_review = arcpy.Describe(review).spatialReference
    wgs = arcpy.SpatialReference(WGS84)
    with arcpy.da.SearchCursor(review, ["OID@"], where) as cur:
        total = sum(1 for _ in cur)
    messages.addMessage("{} rader med status {} i granskningslagret (urvalet gäller)"
                        .format(total, STATUS_GRANSKA))
    if not total:
        return {}
    existing = _existing_ids(table)
    dyntaxa = Dyntaxa(dyntaxa_key, sprak)
    dubb = Dubbletter(sos_key, radie) if dubbletter else None
    counts = {"tillagda": 0, "avvisade": 0, "obesvarade": 0, "kvar": 0, "fanns": 0}
    progress = _Progress(total, "Granskade fynd", messages)
    # Läs, bedöm, lägg till och skriv tillbaka i tre steg: en UpdateCursor och en InsertCursor
    # i samma geodatabas samtidigt ger "workspace already in transaction mode".
    updates, new_rows = {}, []
    try:
        with arcpy.da.SearchCursor(review, ["OID@", "SHAPE@"] + real, where) as cur:
            for k, r in enumerate(cur, 1):
                progress.step(k)
                v = dict(zip(names, r[2:]))
                godkann = _text(v["Godkann"])
                if godkann == "Nej":
                    v["Status"] = STATUS_AVVISAD
                    counts["avvisade"] += 1
                    updates[r[0]] = v
                    continue
                if godkann != "Ja":
                    counts["obesvarade"] += 1
                    continue
                row = {n: v[n] for n in RAPPORT_NAMN}
                row["Lokalnamn"] = _cut(row["Lokalnamn"], MAX_LOKALNAMN)
                row["Startdatum"] = _date_text(row["Startdatum"])
                row["Slutdatum"] = _date_text(row["Slutdatum"]) or row["Startdatum"]
                if _text(row["Noggrannhet"]) not in NOGGRANNHET:
                    row["Noggrannhet"] = DEFAULT_NOGGRANNHET
                xy = None
                if r[1] is not None:
                    p = arcpy.PointGeometry(r[1].firstPoint, sr_review).projectAs(wgs).firstPoint
                    xy = (p.X, p.Y)
                name = _text(v["Namn_i_lagret"])
                res = _resolve(row, name, (_text(v["Forslag"]) or name, "Ja"), dyntaxa, dubb, xy)
                v["Orsak"] = _cut(res["Orsak"], 1000)
                v["Dyntaxa_id"] = res["Dyntaxa_id"]
                v["Artnamn"] = res["Artnamn"]
                if res["Status"] != STATUS_KLAR:
                    v["Status"] = res["Status"]
                    counts["kvar"] += 1
                elif _text(row["Kalla_id"]) in existing:
                    v["Status"] = STATUS_TILLAGD
                    v["Orsak"] = "fanns redan i rapporttabellen"
                    counts["fanns"] += 1
                else:
                    row["Artnamn"] = res["Artnamn"]
                    new_rows.append([row[n] for n in RAPPORT_NAMN])
                    existing.add(_text(row["Kalla_id"]))
                    v["Status"] = STATUS_TILLAGD
                    counts["tillagda"] += 1
                updates[r[0]] = v
    finally:
        _save_cache()
    try:
        if new_rows:
            with arcpy.da.InsertCursor(table, [tfm[n.lower()] for n in RAPPORT_NAMN]) as ins:
                for values in new_rows:
                    ins.insertRow(values)
        if updates:
            with arcpy.da.UpdateCursor(review, ["OID@"] + real, where) as upd:
                for r in upd:
                    v = updates.get(r[0])
                    if v is not None:
                        upd.updateRow([r[0]] + [v[n] for n in names])
    except RuntimeError as exc:
        if "edit" in str(exc).lower() or "redigering" in str(exc).lower():
            raise ValueError("Granskningslagret eller rapporttabellen är under redigering. Spara "
                             "och avsluta redigeringen först.")
        raise
    messages.addMessage("Tillagda i rapporten: {}. Avvisade: {}. Utan Ja/Nej: {}. Fortfarande "
                        "kvar att granska eller skippade: {}. Fanns redan: {}."
                        .format(counts["tillagda"], counts["avvisade"], counts["obesvarade"],
                                counts["kvar"], counts["fanns"]))
    messages.addMessage("Klart på {}.".format(_fmt_duration(time.time() - t_start)))
    return counts


# =============================================================================
# Verktyg 3: Exportera till Excelmall
# =============================================================================


# Fyller i en kopia av .xls-mallen med Excel (COM). Bara ASCII här: Windows PowerShell 5.1
# läser skriptfiler utan BOM som ANSI. Datat kommer som UTF-8-JSON. Textkolumner får
# formatet Text först, annars gör Excel om "2025-11-17" till ett datumtal.
PS_FYLL_XLS = r"""
$ErrorActionPreference = 'Stop'
$job = Get-Content -LiteralPath $args[0] -Raw -Encoding UTF8 | ConvertFrom-Json
Add-Type -Namespace W -Name U -MemberDefinition '[DllImport("user32.dll")] public static extern int GetWindowThreadProcessId(IntPtr h, out int p);'
$xl = $null; $wb = $null; $ws = $null; $r = $null; $xlPid = 0
try {
    $xl = New-Object -ComObject Excel.Application
    [void][W.U]::GetWindowThreadProcessId([IntPtr]$xl.Hwnd, [ref]$xlPid)
    $xl.Visible = $false; $xl.DisplayAlerts = $false
    $wb = $xl.Workbooks.Open($job.path)
    $ws = $wb.Worksheets.Item($job.sheet)
    foreach ($c in $job.columns) {
        $vals = @($c.values)
        $n = $vals.Count
        if ($n -eq 0) { continue }
        $a = New-Object 'object[,]' $n, 1
        for ($i = 0; $i -lt $n; $i++) { $a[$i, 0] = $vals[$i] }
        $r = $ws.Range($ws.Cells.Item($job.row, $c.col), $ws.Cells.Item($job.row + $n - 1, $c.col))
        if ($c.text) { $r.NumberFormat = '@' }
        $r.Value2 = $a
    }
    $ws.Activate()
    $wb.Save()
} finally {
    if ($wb) { $wb.Close($false) }
    if ($xl) { $xl.Quit() }
    foreach ($o in @($r, $ws, $wb, $xl)) {
        if ($o) { [void][Runtime.InteropServices.Marshal]::ReleaseComObject($o) }
    }
    $r = $null; $ws = $null; $wb = $null; $xl = $null
    [GC]::Collect(); [GC]::WaitForPendingFinalizers()
    # stop only the Excel instance this script started, never the user's own
    if ($xlPid) { Start-Sleep -Milliseconds 500; Stop-Process -Id $xlPid -Force -ErrorAction SilentlyContinue }
}
"""


def _xls_header(mall):
    import xlrd
    book = xlrd.open_workbook(mall)
    if MALL_BLAD not in book.sheet_names():
        return None
    ws = book.sheet_by_name(MALL_BLAD)
    header = {}
    for c, v in enumerate(ws.row_values(MALL_RUBRIKRAD - 1), 1):
        if _text(v) and _text(v) not in header:
            header[_text(v)] = c
    return header


def _xlsx_header(mall):
    from openpyxl import load_workbook
    wb = load_workbook(mall, read_only=True)
    if MALL_BLAD not in wb.sheetnames:
        return None
    header = {}
    for c in next(wb[MALL_BLAD].iter_rows(min_row=MALL_RUBRIKRAD, max_row=MALL_RUBRIKRAD)):
        if _text(c.value) and _text(c.value) not in header:
            header[_text(c.value)] = c.column
    wb.close()
    return header


def _write_xlsx(mall, path, header, part):
    from openpyxl import load_workbook
    wb = load_workbook(mall)
    ws = wb[MALL_BLAD]
    for i, row in enumerate(part):
        for (name, alias, _, _), value in zip(EXPORT_FALT, row):
            if value not in (None, ""):
                ws.cell(MALL_FORSTA_DATARAD + i, header[alias], value)
    wb.active = wb.sheetnames.index(MALL_BLAD)
    wb.save(path)


def _write_xls(mall, path, header, part):
    """Kopiera .xls-mallen och fyll i den med Excel, så att filen är Artportalens egen."""
    import shutil
    import subprocess
    shutil.copyfile(mall, path)
    columns = []
    for k, (name, alias, ftype, _) in enumerate(EXPORT_FALT):
        values = [None if row[k] in (None, "") else row[k] for row in part]
        if any(v is not None for v in values):
            columns.append({"col": header[alias], "text": ftype == "TEXT", "values": values})
    tmp = tempfile.mkdtemp(prefix="artportalen_")
    job, script = os.path.join(tmp, "data.json"), os.path.join(tmp, "fyll.ps1")
    try:
        with open(job, "w", encoding="utf-8") as f:
            json.dump({"path": os.path.abspath(path), "sheet": MALL_BLAD,
                       "row": MALL_FORSTA_DATARAD, "columns": columns}, f, ensure_ascii=False)
        with open(script, "w", encoding="ascii") as f:
            f.write(PS_FYLL_XLS)
        proc = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-File", script, job],
            capture_output=True, text=True, timeout=600,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        if "80040154" in err or "ComObject" in err:
            raise ValueError("Excel behövs för att fylla i .xls-mallen men gick inte att starta. "
                             "Installera Excel, eller använd mallen sparad som .xlsx.")
        raise ValueError("Excel kunde inte skriva {}: {}".format(path, err[-800:]))


def _run_exportera(table, mall, out_xlsx, messages=None):
    messages = messages or _PrintMessages()
    is_xls = mall.lower().endswith(".xls")
    tfm = _field_map(table)
    names = [f[0] for f in EXPORT_FALT]
    missing = [n for n in names if n.lower() not in tfm]
    if missing:
        raise ValueError("Tabellen saknar fälten {}. Använd rapporttabellen från Förbered rapport "
                         "till Artportalen.".format(", ".join(missing)))
    with arcpy.da.SearchCursor(table, ["OID@"] + [tfm[n.lower()] for n in names]) as cur:
        rows = [list(r[1:]) for r in sorted(cur, key=lambda r: r[0])]
    if not rows:
        raise ValueError("Rapporttabellen är tom.")
    header = _xls_header(mall) if is_xls else _xlsx_header(mall)
    if header is None:
        raise ValueError("Mallen saknar bladet \"{}\". Använd Artportalens mall ap2_template_sv."
                         .format(MALL_BLAD))
    aliases = [f[1] for f in EXPORT_FALT]
    missing = [a for a in aliases if a not in header]
    if missing:
        raise ValueError("Mallens blad \"{}\" saknar kolumnerna {} på rad {}."
                         .format(MALL_BLAD, ", ".join(missing), MALL_RUBRIKRAD))
    parts = [rows[i:i + MAX_RADER_PER_IMPORT] for i in range(0, len(rows), MAX_RADER_PER_IMPORT)]
    base, ext = os.path.splitext(out_xlsx)
    written = []
    for k, part in enumerate(parts, 1):
        arcpy.SetProgressorLabel("Skriver fil {} av {}".format(k, len(parts)))
        path = out_xlsx if len(parts) == 1 else "{}_del{}{}".format(base, k, ext)
        (_write_xls if is_xls else _write_xlsx)(mall, path, header, part)
        written.append(path)
        messages.addMessage("Skrev {} fynd till {}".format(len(part), path))
    messages.addMessage("Öppna filen, markera rubrikraden (rad 2) och fynden i bladet \"{}\", "
                        "kopiera och klistra in i Artportalen under Rapportera, Importera fynd. "
                        "Koordinatsystem (SWEREF 99 TM) och artnamnsspråk under Mina sidor "
                        "måste stämma med filen.".format(MALL_BLAD))
    return written


class _PrintMessages:
    """Meddelanden till konsolen när _run-funktionerna körs utanför ramverket."""

    def addMessage(self, text):
        print(text)

    def addWarningMessage(self, text):
        print("VARNING: " + text)

    def addErrorMessage(self, text):
        print("FEL: " + text)


# =============================================================================
# Verktygstips
# =============================================================================


def _write_tool_metadata(tool_cls, toolbox_alias, summary):
    """
    Skriv verktygets metadatafil med parameterförklaringar från TOOLTIPS.

    Pro läser verktygstipsen i dialogen från <verktygslåda>.<verktyg>.pyt.xml
    (elementet dialogReference per parameter). Det finns inget attribut på
    arcpy.Parameter för detta. Filen skrivs bara om innehållet har ändrats.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    toolbox = os.path.splitext(os.path.basename(__file__))[0]
    path = os.path.join(here, "{}.{}.pyt.xml".format(toolbox, tool_cls.__name__))

    def html(text):
        body = escape(text).replace("\n", "</SPAN></P><P><SPAN>")
        return escape('<DIV STYLE="text-align:Left;"><P><SPAN>{}</SPAN></P></DIV>'.format(body))

    tool = tool_cls()
    params = []
    for p in tool.getParameterInfo():
        tip = TOOLTIPS.get(p.name)
        if not tip:
            continue
        params.append(
            '<param name="{n}" displayname="{d}" type="{t}" direction="{r}">'
            "<dialogReference>{h}</dialogReference>"
            "<pythonReference>{h}</pythonReference></param>".format(
                n=p.name, d=escape(p.displayName, {'"': "&quot;"}),
                t=p.parameterType, r=p.direction, h=html(tip))
        )
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<metadata xml:lang="sv"><Esri><ArcGISFormat>1.0</ArcGISFormat></Esri>'
        '<tool name="{name}" displayname="{label}" toolboxalias="{alias}" xmlns="">'
        "<parameters>{params}</parameters><summary>{summary}</summary></tool>"
        "<dataIdInfo><idCitation><resTitle>{label}</resTitle></idCitation>"
        "<idAbs>{summary}</idAbs></dataIdInfo></metadata>\n"
    ).format(name=tool_cls.__name__, label=escape(tool.label), alias=toolbox_alias,
             params="".join(params), summary=html(summary))
    try:
        with open(path, encoding="utf-8") as fh:
            if fh.read() == xml:
                return
    except OSError:
        pass
    try:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(xml)
    except OSError:
        pass  # skrivskyddad plats: verktyget fungerar ändå, utan verktygstips


# =============================================================================
# Gemensamma parametrar
# =============================================================================


def _param(name, label, datatype, kind="Optional", direction="Input", category=None):
    return arcpy.Parameter(displayName=label, name=name, datatype=datatype, parameterType=kind,
                           direction=direction, category=category)


def _options_params(include_place=True):
    cat = "Inställningar"
    sprak = _param("sprak", "Artnamnsspråk", "GPString", "Required", category=cat)
    sprak.filter.type = "ValueList"
    sprak.filter.list = [SPRAK_VET, SPRAK_SV]
    sprak.value = SPRAK_VET
    out = [sprak]
    if include_place:
        nogg = _param("noggrannhet", "Noggrannhet när fältet är tomt", "GPString", "Required",
                      category=cat)
        nogg.filter.type = "ValueList"
        nogg.filter.list = NOGGRANNHET
        nogg.value = DEFAULT_NOGGRANNHET
        out += [nogg]
    dubb = _param("dubbletter", "Sök dubbletter i Artportalen och SOS", "GPBoolean", category=cat)
    dubb.value = True
    radie = _param("radie", "Dubblettradie (m)", "GPLong", category=cat)
    radie.filter.type = "Range"
    radie.filter.list = [1, 10000]
    radie.value = DEFAULT_RADIE_M
    out += [dubb, radie]
    if include_place:
        cat = "Lokalnamn"
        text = _param("lokalnamn", "Lokalnamn", "GPString", category=cat)
        mode = _param("lokalnamn_lage", "Lokalnamn används", "GPString", "Required",
                      category=cat)
        mode.filter.type = "ValueList"
        mode.filter.list = LAGEN_LOKAL
        mode.value = LAGE_TOM
        out += [text, mode]
    cat = "API-nycklar"
    dk = _param("dyntaxa_key", "Dyntaxa-nyckel", "GPStringHidden", category=cat)
    sk = _param("sos_key", "SOS-nyckel", "GPStringHidden", category=cat)
    save = _param("spara_nycklar", "Spara nycklarna på den här datorn", "GPBoolean", category=cat)
    save.value = False
    return out + [dk, sk, save]


def _p(parameters):
    return {p.name: p for p in parameters}


def _options_update(p):
    if "radie" in p:
        p["radie"].enabled = bool(p["dubbletter"].value)
    if "lokalnamn" in p:
        p["lokalnamn_lage"].enabled = bool(_text(p["lokalnamn"].valueAsText))


def _options_messages(p):
    if not _key(p["dyntaxa_key"], CREDENTIAL_DYNTAXA, ENV_DYNTAXA):
        p["dyntaxa_key"].setErrorMessage(_missing_key_text("Dyntaxa-nyckel", ENV_DYNTAXA))
    if p["dubbletter"].value and not _key(p["sos_key"], CREDENTIAL_SOS, ENV_SOS):
        p["sos_key"].setErrorMessage(_missing_key_text("SOS-nyckel", ENV_SOS) +
                                     " Eller stäng av dubblettsökningen.")


def _keys_and_save(p, messages):
    dk = _key(p["dyntaxa_key"], CREDENTIAL_DYNTAXA, ENV_DYNTAXA)
    sk = _key(p["sos_key"], CREDENTIAL_SOS, ENV_SOS)
    if p["spara_nycklar"].value:
        _remember_keys((p["dyntaxa_key"].valueAsText or "").strip(),
                       (p["sos_key"].valueAsText or "").strip(), True, messages)
    return dk, sk


def _execute(func, messages):
    """Kör func; ValueError blir ett läsbart fel utan kedjad traceback."""
    error = None
    try:
        return func()
    except ValueError as exc:
        error = str(exc)
    finally:
        arcpy.ResetProgressor()
    messages.addErrorMessage(error)
    raise arcpy.ExecuteError


# =============================================================================
# Toolbox
# =============================================================================


class Toolbox:
    def __init__(self):
        self.label = "Rapportera till Artportalen"
        self.alias = "rapportera_artportalen"
        self.tools = [ForberedRapport, LaggTillGranskade, ExporteraExcelmall]
        for cls, summary in ((ForberedRapport, SUMMARY_FORBERED),
                             (LaggTillGranskade, SUMMARY_LAGG_TILL),
                             (ExporteraExcelmall, SUMMARY_EXPORTERA)):
            _write_tool_metadata(cls, self.alias, summary)


class ForberedRapport:
    def __init__(self):
        self.label = "1. Förbered rapport till Artportalen"
        self.description = SUMMARY_FORBERED
        self.canRunInBackground = False

    def getParameterInfo(self):
        lyr = _param("in_layer", "Artlager", "GPFeatureLayer", "Required")
        lyr.filter.list = ["Point", "Multipoint"]
        table = _param("out_table", "Rapporttabell", "DETable", "Required", "Output")
        review = _param("out_review", "Granskningslager", "DEFeatureClass", "Required", "Output")
        prev = _param("tidigare", "Beslut från tidigare granskning", "GPFeatureLayer",
                      category="Inställningar")
        prev.filter.list = ["Point"]
        return [lyr, table, review] + _options_params(True) + [prev]

    def isLicensed(self):
        return True

    def updateParameters(self, parameters):
        p = _p(parameters)
        if p["in_layer"].altered and not p["in_layer"].hasBeenValidated:
            gdb = _default_gdb()
            if gdb:
                if not p["out_table"].altered:
                    p["out_table"].value = os.path.join(gdb, "Artportalen_rapport")
                if not p["out_review"].altered:
                    p["out_review"].value = os.path.join(gdb, "Artportalen_granskning")
        _options_update(p)

    def updateMessages(self, parameters):
        p = _p(parameters)
        if p["in_layer"].value:
            try:
                missing = _missing_source_fields(p["in_layer"].value)
                if missing:
                    p["in_layer"].setErrorMessage(
                        "Lagret saknar fälten {}. Verktyget kräver schemat "
                        "Artinventering_generell eller Artforekomst_punkt."
                        .format(", ".join(missing)))
            except Exception:
                pass
        for name in ("out_table", "out_review"):
            if p[name].valueAsText and not _is_file_gdb(p[name].valueAsText):
                p[name].setErrorMessage("Utdata måste ligga i en fil-geodatabas (.gdb), så att "
                                        "Godkänn kan få en Ja/Nej-lista.")
        if p["tidigare"].value and p["out_review"].valueAsText:
            try:
                prev = os.path.normcase(os.path.abspath(
                    arcpy.Describe(p["tidigare"].value).catalogPath))
                out = os.path.normcase(os.path.abspath(p["out_review"].valueAsText))
                if prev == out:
                    p["tidigare"].setErrorMessage(
                        "Samma lager som utdata. Utdata raderas innan verktyget körs, så "
                        "besluten skulle gå förlorade. Ge granskningslagret ett annat namn.")
                else:
                    fm = _field_map(p["tidigare"].value)
                    if not all(n in fm for n in ("namn_i_lagret", "forslag", "godkann")):
                        p["tidigare"].setErrorMessage(
                            "Lagret saknar fälten Namn_i_lagret, Forslag och Godkann. Välj ett "
                            "granskningslager från det här verktyget.")
            except Exception:
                pass
        _options_messages(p)

    def execute(self, parameters, messages):
        p = _p(parameters)

        def run():
            dk, sk = _keys_and_save(p, messages)
            return _run_forbered(
                p["in_layer"].value, p["out_table"].valueAsText, p["out_review"].valueAsText,
                p["sprak"].valueAsText, p["noggrannhet"].valueAsText,
                _text(p["lokalnamn"].valueAsText), bool(p["dubbletter"].value),
                p["radie"].value or DEFAULT_RADIE_M, dk, sk, messages,
                previous=p["tidigare"].value,
                lokalnamn_lage=p["lokalnamn_lage"].valueAsText or LAGE_TOM)
        _execute(run, messages)


class LaggTillGranskade:
    def __init__(self):
        self.label = "2. Lägg till granskade fynd"
        self.description = SUMMARY_LAGG_TILL
        self.canRunInBackground = False

    def getParameterInfo(self):
        review = _param("in_review", "Granskningslager", "GPFeatureLayer", "Required")
        review.filter.list = ["Point"]
        table = _param("in_table", "Rapporttabell", "GPTableView", "Required")
        out = _param("out_updated", "Uppdaterad rapporttabell", "GPTableView", "Derived", "Output")
        out.parameterDependencies = ["in_table"]
        return [review, table, out] + _options_params(False)

    def isLicensed(self):
        return True

    def updateParameters(self, parameters):
        _options_update(_p(parameters))

    def updateMessages(self, parameters):
        p = _p(parameters)
        if p["in_review"].value:
            try:
                fm = _field_map(p["in_review"].value)
                missing = [f[0] for f in GRANSKNING_FALT if f[0].lower() not in fm]
                if missing:
                    p["in_review"].setErrorMessage(
                        "Lagret saknar fälten {}. Välj granskningslagret från Förbered rapport "
                        "till Artportalen.".format(", ".join(missing)))
            except Exception:
                pass
        _options_messages(p)

    def execute(self, parameters, messages):
        p = _p(parameters)

        def run():
            dk, sk = _keys_and_save(p, messages)
            table = arcpy.Describe(p["in_table"].value).catalogPath
            _run_lagg_till(p["in_review"].value, table, p["sprak"].valueAsText,
                           bool(p["dubbletter"].value), p["radie"].value or DEFAULT_RADIE_M,
                           dk, sk, messages)
            p["out_updated"].value = p["in_table"].value
        _execute(run, messages)


class ExporteraExcelmall:
    def __init__(self):
        self.label = "3. Exportera till Artportalens Excelmall"
        self.description = SUMMARY_EXPORTERA
        self.canRunInBackground = False

    def getParameterInfo(self):
        table = _param("in_report", "Rapporttabell", "GPTableView", "Required")
        mall = _param("mall", "Artportalens Excelmall (.xls)", "DEFile", "Required")
        mall.filter.list = ["xls", "xlsx"]
        out = _param("out_xlsx", "Excelfil för import", "DEFile", "Required", "Output")
        out.filter.list = ["xls", "xlsx"]
        return [table, mall, out]

    def isLicensed(self):
        return True

    def updateParameters(self, parameters):
        p = _p(parameters)
        mall = p["mall"].valueAsText or ""
        ext = ".xlsx" if mall.lower().endswith(".xlsx") else ".xls"
        if not p["out_xlsx"].altered:
            try:
                if p["out_xlsx"].valueAsText:
                    p["out_xlsx"].value = os.path.splitext(p["out_xlsx"].valueAsText)[0] + ext
                elif p["in_report"].value:
                    ws = os.path.dirname(_workspace_of(
                        arcpy.Describe(p["in_report"].value).catalogPath))
                    p["out_xlsx"].value = os.path.join(ws, "Artportalen_import" + ext)
            except Exception:
                pass

    def updateMessages(self, parameters):
        p = _p(parameters)
        mall = p["mall"].valueAsText
        out = p["out_xlsx"].valueAsText
        if mall and out and (os.path.splitext(mall)[1].lower()
                             != os.path.splitext(out)[1].lower()):
            p["out_xlsx"].setErrorMessage("Utdata måste ha samma filtyp som mallen ({}).".format(
                os.path.splitext(mall)[1].lower()))
        if p["in_report"].value:
            try:
                fm = _field_map(p["in_report"].value)
                missing = [n for n in RAPPORT_NAMN if n.lower() not in fm]
                if missing:
                    p["in_report"].setErrorMessage("Tabellen saknar fälten {}.".format(
                        ", ".join(missing)))
            except Exception:
                pass

    def execute(self, parameters, messages):
        p = _p(parameters)

        def run():
            table = arcpy.Describe(p["in_report"].value).catalogPath
            return _run_exportera(table, p["mall"].valueAsText, p["out_xlsx"].valueAsText,
                                  messages)
        _execute(run, messages)
