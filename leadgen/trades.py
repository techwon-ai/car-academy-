"""Trade keyword matching shared by every source.

Patterns are word-anchored on purpose: a bare "air" or "pool" substring matches
HAIR, FAIR, REPAIR, CARPOOL, LIVERPOOL and floods the list with junk.
"""
import re

TRADES = {
    "roofing": r"\bROOF(S|ING|ERS?)?\b|\bRE-?ROOF",
    "hvac": r"\bHVAC\b|\bA/?C\b(?= (SERVICE|REPAIR|INSTALL|PRO|EXPERT|COMPANY|MECH))|AIR CONDITION|\bAIR (CONDITIONING|SERVICES?|SOLUTIONS|PROS?|EXPERTS?|COMFORT|MECHANICAL|DUCT)\b|\bHEATING\b|\bCOOLING\b|\bREFRIGERATION\b|\bDUCTS?\b",
    "plumbing": r"\bPLUMB(ING|ER|ERS)?\b|\bDRAIN(S|AGE)?\b|\bSEPTIC\b|\bROOTER\b|\bLEAK DETECTION\b",
    "landscaping": r"\bLANDSCAP|\bLAWNS?\b|\bTREE (SERVICE|CARE|TRIM|REMOVAL|EXPERT)|\bIRRIGATION\b|\bSOD\b|\bHARDSCAP|\bMOWING\b|\bYARD (CARE|SERVICE|WORK|MAINTENANCE)",
    "cleaning": r"\bCLEAN(ING|ERS?|S)?\b|\bMAIDS?\b|\bJANITORIAL\b|\bHOUSEKEEP",
    "pressure_washing": r"\bPRESSURE ?WASH|\bPOWER ?WASH|\bSOFT ?WASH|\bEXTERIOR WASH",
    "pest_control": r"\bPEST\b|\bEXTERMINAT|\bTERMITE|\bMOSQUITO|\bBUG (CONTROL|SERVICE)|\bWILDLIFE (CONTROL|REMOVAL)",
    "pool_service": r"\bPOOLS?\b(?! (TABLE|HALL|PARTY|PLAYER))|\bSPA (SERVICE|REPAIR)",
}
# Names that hit a trade pattern but are clearly not an owner-operated home service.
NEGATIVE = r"\bCLEAN ?OUTS?\b|\bJUNK\b|\bPAINT|\bDEMOLITION|\bDRY CLEAN|\bCAR ?WASH|\bAUTO\b|\bDETAIL(ING)?\b|\bCARPOOL|\bHOLDINGS?\b|\bREALTY\b|\bINVEST|\bCAPITAL\b|\bDENTAL\b|\bCLEAN ENERGY\b|\bCLEAN EATS?\b|\bCLEAN CUT\b|\bCLEAN SLATE\b|\bCLEAN BEAUTY|\bSKIN\b|\bJUICE|\bFOODS?\b|\bMEDIA\b|\bAPPAREL|\bCLOTHING|\bTRUCKING|\bLOGISTICS|\bFREIGHT|\bAVIATION|\bAIRCRAFT|\bMORTGAGE|\bINSURANCE|\bHOME HEALTH|\bSWIM SCHOOL|\bSWIM ACADEMY|\bMOTOR POOL|\bTALENT POOL|\bGENE POOL|\bPOOL HALL|\bBILLIARD|\bDIRTY\b|\bTRUST\b|\bFOUNDATION\b|\bMINISTR|\bCHURCH\b|\bASSOCIATION\b|\bCONDOMINIUM|\bHOMEOWNERS"
_C = {k: re.compile(v, re.I) for k, v in TRADES.items()}
_NEG = re.compile(NEGATIVE, re.I)


def classify(name: str):
    """Return the first matching trade for a business name, or None."""
    if not name or _NEG.search(name):
        return None
    for trade, rx in _C.items():
        if rx.search(name):
            return trade
    return None


FL_CITIES = ["tampa", "orlando", "jacksonville", "jax", "miami", "fortlauderdale", "ftlauderdale",
             "stpete", "stpetersburg", "sarasota", "fortmyers", "ftmyers", "lakeland", "tallahassee",
             "naples", "clearwater", "brandon", "kissimmee", "ocala", "gainesville", "pensacola",
             "palmbeach", "westpalm", "boca", "bocaraton", "delray", "daytona", "melbourne",
             "portstlucie", "stlucie", "cocoa", "capecoral", "bradenton", "venice", "wesleychapel",
             "hollywood", "pompano", "coralsprings", "hialeah", "doral", "kendall", "homestead",
             "keywest", "destin", "panamacity", "sanford", "winterpark", "apopka", "deltona",
             "palmcoast", "staugustine", "spacecoast", "treasurecoast", "swfl", "nefl", "cfl", "sfl"]
