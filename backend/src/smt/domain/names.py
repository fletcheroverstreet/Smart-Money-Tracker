"""Names, slugs and organization detection for filers.

EDGAR prints an individual as "LAST FIRST MIDDLE" (e.g. "HUANG JEN HSUN" or
"Klamkin Jonathan"). Organizations keep their legal name ("Silver Lake
Partners V DE (AIV), L.P."). For display we show people as "First Middle
Last" and sort them as "Last, First Middle".
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import NamedTuple

#: Words that make a name an organization wherever they appear.
_ORG_MARKERS = re.compile(
    r"\b(L\.?L\.?C|L\.?P|INC|CORP|CORPORATION|COMPANY|LTD|LIMITED|PLC|TRUST|FUND|FUNDS|"
    r"PARTNERS|PARTNERSHIP|CAPITAL|MANAGEMENT|HOLDINGS|INVESTORS|INVESTMENTS?|ADVISORS?|ADVISERS?|"
    r"GROUP|ASSOCIATES|FOUNDATION|BANK|BANCORP|SECURITIES|VENTURES?|EQUITY|ASSET|LLP|ULC|SPV|MASTER|"
    r"OFFSHORE|HOLDCO|GMBH|SARL|S\.A\.S|UNITRUST|PENSION|INSURANCE|ASSOCIATION|UNIVERSITY|CAISSE|"
    r"INVESTISSEMENT|AKTIEBOLAG|RETIREMENT\s+SYSTEM|JOINT\s+BOARD|WORKERS\s+UNITED|"
    # Partnership and fund forms: a Luxembourg special limited partnership ("Advent International
    # GPE IX-A SCSp"), a limited liability limited partnership ("Auburn OWMH, LLLP"), a Belgian
    # limited partnership ("Hummingbird Collective CommV"), a Malaysian company ("Straits Energy
    # Resources Berhad"), a Brazilian fund ("... Fundo de Investimento Multimercado"), an employee
    # benefit plan ("UAW Ford Retirees Medical Benefits Plan"), a pension fund ("Guardians of New
    # Zealand Superannuation"), and fund and firm words no surname uses ("Karatage Opportunities",
    # "Thrivent Financial for Lutherans").
    r"SCSP|L\.?L\.?L\.?P|COMMV|BERHAD|BHD|FUNDOS?|PLAN|SUPERANNUATION|OPPORTUNIT(?:Y|IES)|FINANCIAL|SICAV|"
    # European holding and trust forms no surname uses: a Belgian or French "Groupe" ("Groupe
    # Bruxelles Lambert", Concentrix's 10% owner), a Dutch foundation that holds shares for others
    # ("Stichting Administratiekantoor Westend", CAVA's), a Spanish, Portuguese, German or French
    # foundation ("Fundacion Omerinta"), a French "Société", a court-supervised conservatorship
    # ("James F Heyneman Conservatorship, James Scott, Conservator"), a deceased person's estate,
    # and "Global" ("Mithaq Global", Children's Place's 10% owner; in 220 organisation names and
    # no person's in the data, 2026-10-12).
    r"GROUPE|STICHTING|ADMINISTRATIEKANTOOR|FUNDACI[OÓ]N|FUNDA[CÇ][AÃ]O|STIFTUNG|FONDATION|FONDAZIONE|"
    r"SOCI[EÉ]T[EÉ]|CONSERVATORSHIP|ESTATE\s+OF|GLOBAL)\b\.?"
    # A Luxembourg private company, however it is dotted: "S.a r.l.", "S.a.r.l.", "s.a.r.l." ("SARL" above).
    r"|\bS\.\s?[AÀ]\.?\s?R\.\s?L\b\.?"
    # Mexican and other Spanish-language company forms: "S.A. de C.V.", "S.A.B. de C.V.", "S. de R.L."
    r"|\bS\.?\s?(?:A\.?\s?(?:B\.?\s?)?|)DE\s+(?:C\.?\s?V|R\.?\s?L)\b"
    # "Holding" is also a surname (Frank B. Holding Jr.), so it marks a company only after another word.
    r"|\sHOLDING\b"
    # EDGAR's conformed names for people never contain "&" ("HB&Q NV", "Teachers Insurance & Annuity").
    r"|&",
    re.IGNORECASE,
)
#: Short legal suffixes (Co, S.A., AG, SE, N.V., B.V., KG, GP, AB, AS, A/S, SCA, SAS, a Jersey or
#: Scottish SLP, an Irish DAC) are also common in people's names, so they only count at the end of
#: the name, right after a comma or before a parenthesis: "Siemens AG", "Banco Santander, S.A.",
#: "Akastor AS", "HB&Q NV (...)", "Yucca (Jersey) SLP", "PM Alpha II DAC".
#: Dotted forms are written without a space ("N.V.", "S.C.A."). With a space, "N. V." is how EDGAR
#: writes two initials: "Tyagarajan N. V." is N. V. Tyagarajan, Genpact's chief executive. After a
#: comma a spaced form is still a company ("CANTOR FITZGERALD, L. P.").
#: Two are stricter still. "NA" needs a dot or a comma ("Citibank, N.A."), because "Ai Na" is a
#: person. "DE" needs a comma: EDGAR writes Brazilian names surname first with the particle last
#: ("AGUIAR LUCAS ARAUJO DE" is Lucas Araujo de Aguiar).
_WEAK_ORG_MARKERS = re.compile(
    r",\s*(CO|N\.\s?A|S\.\s?A|SA|AG|SE|N\.\s?V|NV|B\.\s?V|BV|KG|GP|AB|AS|A/S|SCA|S\.\s?C\.\s?A|SAS|SLP|DAC|"
    r"L\.\s?P|S\.\s?L|ASA)\.?\s*(?:$|,|\()"
    # "S A" or "N V" with a space between is more often two initials ("HASBROOK JOHN S A")
    r"|\s(CO|N\.A|S\.A|SA|AG|SE|N\.V|NV|B\.V|BV|KG|GP|AB|AS|A/S|SCA|S\.C\.A|SAS|SLP|DAC)\.?\s*(?:$|,|\()"
    r"|,\s*(NA|DE)\.?\s*$",
    re.IGNORECASE,
)
#: A Norwegian "ASA" at the end ("Akastor ASA"), in capitals. Åsa is also a Swedish given name
#: ("Tamsons Asa"), so an all-capital name needs two words before it ("YARA INTERNATIONAL ASA").
#: Known false positive: a Swede filed in capitals with Åsa as a second given name ("LINDBERG
#: MARIA ASA") reads as a company. No such filer is in the data (2026-09-29). Her own signature
#: ("/s/ Maria Asa Lindberg", or an attorney's "... for Maria Asa Lindberg") is a rotation of the
#: name and keeps her a person, at load time too (ensure_filer gets the filing's signatures).
_ASA = re.compile(r"\sASA\.?\s*(?:$|\()")
#: A Spanish "S.L." at the end ("Sport City Cadiz S.L.", the football club's owner). Dotted initials
#: end many people's names ("Paloma Diane S.L.", "Wunsch E.J."), so it needs three words before it.
#: Known false positive, rescued the same way by a rotated signature: a person with a double
#: surname and the initials S. L. ("GARCIA LOPEZ MARIA S.L.", none in the data on 2026-09-29).
_SL = re.compile(r"^\S+\s+\S+\s+.*\sS\.L\.?\s*$", re.IGNORECASE)
_FUND_MARKERS = re.compile(
    r"\b(FUND|FUNDS|FUNDOS?|PARTNERS|CAPITAL|MANAGEMENT|INVESTORS|INVESTMENTS?|ADVISORS?|ADVISERS?|ASSET|"
    r"VENTURES?|EQUITY|MASTER|OFFSHORE|OPPORTUNIT(?:Y|IES)|SICAV|SCSP|SLP|L\.?L\.?L\.?P|L\.?\s?P|GP)\b",
    re.IGNORECASE,
)
#: Generational and degree suffixes, moved to the end of the display name. A lone "V" is
#: not among them: in EDGAR names it is far more often a middle initial than "the fifth".
_SUFFIXES = {"JR", "SR", "II", "III", "IV", "MD", "PHD", "CPA", "ESQ", "JD", "MBA", "DDS", "DVM"}
_DEGREE_SUFFIXES = {"MD", "PHD", "CPA", "ESQ", "JD", "MBA", "DDS", "DVM", "DO"}
#: Courtesy titles some filers add to their EDGAR name ("SOTO AYECH ALEJANDRA YAZMIN MRS",
#: "TURNER R GERALD DR", "Akbari Dr. Homaira"): not shown.
_HONORIFICS = {"MR", "MRS", "MS", "MISS", "DR"}
#: Words left out when a name is compared with a signature.
_NOT_COMPARED = _SUFFIXES | _HONORIFICS
#: Words that begin a multi-word surname (EDGAR writes the surname first).
#: Not "LE" (a common Vietnamese surname on its own; the French "Le Peuch" is told apart by word
#: counts, see NameFrequencies.second_word_ends_surname) or "DAS" (usually a whole Indian
#: surname). The Dutch 't and 's ("'t Hart Cees" is Cees 't Hart) are always lower case.
_SURNAME_PARTICLES = {"DE", "LA", "LAS", "LOS", "DEL", "DELLA", "DELA", "DI", "DA", "DOS", "DU", "VAN", "VON",
                      "DER", "DEN", "TER", "TEN", "ST", "ST.", "SAN", "SANTA", "SAINT", "MAC", "AL", "EL",
                      "BIN", "BEN", "'T", "'S"}
_APOSTROPHE_PARTICLES = {"'T", "'S"}
#: Particles that join the two parts of a Portuguese or Spanish surname ("Batista de Lima"). The
#: Spanish "y" ("Garza y Garza", "Lopez y Lopez de Lorenzi") counts only in lower case, since a
#: capital Y is far more often a middle initial ("HOLDEN RICHMOND Y JR").
_INNER_PARTICLES = {"DE", "DA", "DO", "DOS", "DAS", "DEL"}
#: Portuguese generational words that end a surname ("Batista de Lima Filho").
_SURNAME_ENDINGS = {"FILHO", "NETO", "SOBRINHO", "JUNIOR"}
#: Spanish and Portuguese patronymic surnames ("son of"), which are never given names. In the
#: second place of EDGAR's name they are the second half of a double surname: "FLORES GOMEZ
#: HERNAN DIEGO" is Hernan Diego Flores Gomez, "ORTIZ MARTINEZ GUILLERMO" Guillermo Ortiz Martinez.
_PATRONYMICS = {
    "ALVAREZ", "BENITEZ", "CHAVEZ", "DIAZ", "DOMINGUEZ", "ENRIQUEZ", "ESTEVEZ", "FERNANDEZ", "GOMEZ",
    "GONZALEZ", "GUTIERREZ", "HERNANDEZ", "JIMENEZ", "LOPEZ", "MARQUEZ", "MARTINEZ", "MENDEZ", "MUNOZ",
    "NUNEZ", "ORDONEZ", "PEREZ", "RAMIREZ", "RODRIGUEZ", "RUIZ", "SANCHEZ", "SUAREZ", "VASQUEZ",
    "VAZQUEZ", "VELASQUEZ", "VELAZQUEZ", "YANEZ",
    "ALVES", "ANTUNES", "FERNANDES", "GOMES", "GONCALVES", "HENRIQUES", "LOPES", "MARQUES", "MENDES",
    "NUNES", "RODRIGUES", "SANCHES",
}

#: The signer names on an owner's filings: each signature with the number of filings that carry
#: it, or a plain sequence in which every entry counts once.
Signatures = Mapping[str, int] | Sequence[str]


@dataclass(frozen=True)
class NameFrequencies:
    """How often each word is a surname or a given name across EDGAR's conformed names of people.

    EDGAR writes people surname first, so the first word of each name counts as a surname and
    the later words as given names (initials are left out). A few filers type their name first-name
    first, which blurs the counts a little, so they are only used to confirm what a signature
    already suggests (see reads_first_name_first), never on their own.
    """

    surname: Mapping[str, int]
    given: Mapping[str, int]
    #: The comparable words of every name counted, so a name's own words can be left out when it
    #: is judged ("Robert W. Sharps" counts SHARPS as a given name once, and must not vote for it).
    counted: Mapping[tuple[str, ...], int] = field(default_factory=dict)

    @classmethod
    def from_names(cls, names: Iterable[str]) -> NameFrequencies:
        surname: dict[str, int] = {}
        given: dict[str, int] = {}
        counted: dict[tuple[str, ...], int] = {}
        for name in names:
            cleaned = _clean(name)
            if "," in cleaned:
                continue
            words = [w for _, w in _core(cleaned.split(" "))]
            counted[tuple(words)] = counted.get(tuple(words), 0) + 1
            for i, w in enumerate(words):
                if len(w) < 2 or w.isdigit():
                    continue
                counts = surname if i == 0 else given
                counts[w] = counts.get(w, 0) + 1
        return cls(surname, given, counted)

    def reads_first_name_first(self, words: list[str]) -> bool:
        """True when the word counts say a name's comparable `words` read first-name first.

        Only used for a name a signature writes with the same words in the same order, which is
        what a name already first-name first looks like, but also what a signer copying EDGAR's
        surname-first order writes. The name's own words are left out of the counts. Two ways:

        1. The first word is a given name, clearly: seen as one at least _GIVEN_MIN times and at
           least three times as often as a surname; the last word is not an initial; and the odds
           (given(first) + 1) / (surname(first) + 1) x (surname(last) + 1) / (given(last) + 1)
           reach _NATURAL_ODDS. "David P McGlade" (DAVID a given name 1,549 times, a surname 10;
           MCGLADE seen nowhere else) scores 141 and "Robert W. Sharps" 297. Below the bar, where
           the owner's real order is unclear, the name keeps EDGAR's order: "Joseph Deepa" (48:
           JOSEPH is also a surname 14 times, and Deepa is his given name), "Lei Ut Ha" (17, a
           Macau name written surname first), "Heath R Fear" (5: HEATH is a surname 4 times),
           unless the first word is never a surname and the last never a given name in any other
           name: "Sergey Brin" (8: SERGEY a given name 7 times, BRIN seen nowhere else).
        2. A two-word name whose first word is never a surname in any other name (and a given name
           fewer than _GIVEN_MIN times) and whose last word is a common surname (at least
           _COMMON_SURNAME times, three times as often as a given name): a Chinese name in natural
           order, "Jianchun Liu" (LIU a surname 147 times, JIANCHUN seen nowhere else).
        """
        first, last = words[0], words[-1]
        if len(last) < 2 or first == last:
            return False
        own = 1 if self.counted.get(tuple(words), 0) else 0
        g_first, s_first = self.given.get(first, 0), max(0, self.surname.get(first, 0) - own)
        g_last, s_last = max(0, self.given.get(last, 0) - own), self.surname.get(last, 0)
        if g_first >= _GIVEN_MIN:
            odds = (g_first + 1) / (s_first + 1) * (s_last + 1) / (g_last + 1)
            return g_first >= 3 * s_first and (odds >= _NATURAL_ODDS or (s_first == 0 and g_last == 0))
        return len(words) == 2 and s_first == 0 and s_last >= _COMMON_SURNAME and s_last >= 3 * g_last

    def second_word_ends_surname(self, tokens: list[str]) -> bool:
        """True when the first two words of a surname-first name are the surname, by the counts.

        For names with no signature to go by. The second word must never be a given name in any
        other name, and what follows must be a given name. Two cases:

        1. "Le" and one more word, then exactly one given name (initials aside): the French
           "Le Peuch Olivier" is Olivier Le Peuch (OLIVIER a given name 21 times, PEUCH never),
           and "Le Goff Corinne" is Corinne Le Goff (GOFF a surname 7 times). A Vietnamese name
           keeps Le as the whole surname: "Le Thuy Thi Thu" and "LE TRUONG VINH PHU" have two
           more words after the second, and in "Le Heather Tram" HEATHER is a given name.
        2. Any first word, when the second is a surname elsewhere at least _GIVEN_MIN times and
           the third a given name at least _GIVEN_MIN times and three times as often as it is a
           surname: "Calderon Rojas Francisco Jose" is Francisco Jose Calderon Rojas, "Soranno
           Keating Valerie" is Valerie Soranno Keating. A Chinese name with an English or second
           given name stays as it is: in "LEE CONWAY KONG WAI" and "CHANG KUO WEI HERBERT" the
           third word (KONG, WEI) is as much a surname as a given name.
        """
        words = [w for _, w in _core(tokens)]
        if len(words) < 3 or len(words[1]) < 2:
            return False
        own = 1 if self.counted.get(tuple(words), 0) else 0
        second, later = words[1], [w for w in words[2:] if len(w) >= 2]
        if not later or max(0, self.given.get(second, 0) - own) > 0:
            return False
        s_second = self.surname.get(second, 0)
        g_given = max(0, self.given.get(later[0], 0) - own)
        if words[0] == "LE":
            return len(later) == 1 and (g_given >= _GIVEN_MIN or (s_second >= _GIVEN_MIN and g_given >= 1))
        # Not a two-letter second word: "Jiong Ng Chee" (61934) is a Chinese name, signed the same.
        return (len(second) >= 3 and len(words[2]) >= 2 and s_second >= _GIVEN_MIN and g_given >= _GIVEN_MIN
                and g_given >= 3 * self.surname.get(later[0], 0))


#: How strongly the word counts must favour a first-name-first reading (see NameFrequencies).
#: Set from a dry run over every name in the database with a same-order signature: every one at
#: or above 50 was first-name first; below it they are mixed ("Hua Ye" at 21 is first-name
#: first, "Joseph Deepa" at 48 and "Lei Ut Ha" at 17 are not), so those keep EDGAR's order.
_GIVEN_MIN = 5
_NATURAL_ODDS = 50.0
_COMMON_SURNAME = 20


#: What EDGAR or the filer adds after a person's name to tell namesakes apart: a number
#: ("TEUNISSEN ERNST 02494"), or a short parenthesised tag ("Burns Patrick Joseph (2026)", a NACCO
#: director; "Kapoor Rohit (RK2)"). Its digits don't make the name an organisation's.
_TRAILING_TAG = re.compile(r"\s+(?:\d+|\([A-Za-z0-9]{1,6}\))\s*$")


def looks_like_organization(name: str, signatures: Signatures = ()) -> bool:
    """True if a filer name is an organization rather than an individual.

    A number is an organization marker ("Fund 3 LP"), except a trailing number or short
    parenthesised tag after a person's name (_TRAILING_TAG). A name of one word is an
    organisation's: EDGAR records an individual with a last and a first name ("Sanofi", "Amundi",
    "Synchron" are the only one-word filers, 2026-10-12).

    A person whose name happens to contain a marker signs with the same words in natural order:
    "Trust Jane" is Jane E. Trust (a Form 4 signed "for Jane E. Trust"), "Kapoor Rohit (RK2)" signs
    "/s/ Rohit Kapoor". An organization never signs its own name rotated, so such a signature
    (see _signature_choice) keeps the filer a person. Signatures read around a comma don't
    count here: "Insight Holdings Group, LLC, By: /s/ ..." would read as "LLC Insight Holdings
    Group", a rotation of the company's own name.

    A signature never makes a name with no marker an organisation's. Signature blocks of the form
    "<filer> By: /s/ <officer>, Director" are not proof: a company officer signs for its directors
    the same way ("William J. Bratton, by /s/ Alan R. Stewart, Chief Financial Officer", 22515), and
    so does a joint filer ("/s/ Patrick Soon-Shiong, /s/ Charles Kenworthy, Manager of MP 13
    Ventures", 8436). Organisations such as Mithaq Global and Groupe Bruxelles Lambert are caught
    by their marker words.
    """
    # Marker words are looked for in the whole name, tag included ("Doe Jane (Trust)" is a trust);
    # only the digit test and the one-word test leave the namesake tag out.
    numberless = re.sub(r"\s+\d+\s*$", "", name)
    bare = _TRAILING_TAG.sub("", name)
    marked = (bool(_ORG_MARKERS.search(numberless)) or bool(_WEAK_ORG_MARKERS.search(numberless))
              or any(ch.isdigit() for ch in bare)
              or (bool(_ASA.search(bare)) and (bare != bare.upper() or len(bare.split()) >= 3))
              or bool(_SL.search(bare)))
    tokens = _clean(bare).split(" ")
    if len(re.findall(r"[^\s,]+", _clean(bare))) < 2:
        return True
    if not signatures or not marked:
        return marked
    return not _signed_by_the_person(tokens, _weights(signatures))


def _signed_by_the_person(tokens: list[str], weights: dict[str, int]) -> bool:
    """A signature that names the filer in natural order with every word of the filer's name,
    the organisation marker included: "Jane E. Trust" for TRUST JANE (an added initial is fine),
    "/s/ Rohit Kapoor" for KAPOOR ROHIT (RK2) (tags aren't compared). A signature that names only
    part of the name is someone else: a joint filer signing "/s/ John Doe" on behalf of DOE JOHN
    FAMILY TRUST leaves the trust a trust. A signature in EDGAR's own order is the organisation
    signing its own name. Signatures around a comma aren't read ("Insight Holdings Group, LLC,
    By: ..." would read as a rotation of the company's own name)."""
    core = [w for _, w in _core(tokens)]
    if len(core) < 2:
        return False
    need: dict[str, int] = {}
    for w in core:
        need[w] = need.get(w, 0) + 1
    for signature in sorted(weights):
        # Dots split words the way _clean splits the name ("S.L." is "S. L." on both sides).
        for group in _readings(signature.replace(".", ". "), commas=False):
            for reading in group:
                signed = _joined_initials([_norm(w) for w in reading.words], need)
                have: dict[str, int] = {}
                for w in signed:
                    have[w] = have.get(w, 0) + 1
                if any(have.get(w, 0) < n for w, n in need.items()) or signed[:len(core)] == core:
                    continue
                # A rotation of the name, or nearly one: extra initials, the given name first,
                # EDGAR's leading block last.
                for k in range(1, len(core)):
                    if signed == core[k:] + core[:k] or (signed[-k:] == core[:k] and signed[0] == core[k]):
                        return True
    return False


def _joined_initials(signed: list[str], need: dict[str, int]) -> list[str]:
    """Signed words with a run of single letters joined when the name has them as one word:
    "Maria S. L. Garcia Lopez" signs GARCIA LOPEZ MARIA S.L. ("S", "L" -> "SL")."""
    out: list[str] = []
    i = 0
    while i < len(signed):
        j = i
        while j < len(signed) and len(signed[j]) == 1:
            j += 1
        run = "".join(signed[i:j])
        if j - i > 1 and run in need:
            out.append(run)
            i = j
        else:
            out.append(signed[i])
            i += 1
    return out


def looks_like_fund(name: str) -> bool:
    """True if an organization name reads like an investment vehicle or manager."""
    return bool(_FUND_MARKERS.search(name))


#: Irish and Italian surnames that are always written with an apostrophe, which EDGAR drops
#: ("OBRIEN RICHARD T" is Richard T O'Brien, "OCONNELL MAUREEN" Maureen O'Connell). Checked
#: against the signatures in the data (2026-10-12): each is signed with the apostrophe and never
#: as one plain word by its owner. Left out because they are also names of their own: Odell
#: ("Jamie Odell" signs so), Damico ("Jennifer B. Damico"), Diorio ("Chris Diorio"), O'Hara
#: (Ohara is a Japanese surname), Omara (Ugandan) and Okane (Japanese). A name written with the
#: capital kept ("ODonnell", "DAlessandro") gets its apostrophe back whatever the surname (see
#: _dropped_apostrophe).
_APOSTROPHE_SURNAMES = {
    "OBOYLE", "OBRIEN", "OBYRNE", "OCALLAGHAN", "OCALLAHAN", "OCONNELL", "OCONNOR", "ODONNELL", "ODONOGHUE",
    "ODONOVAN", "ODWYER", "OFARRELL", "OFLAHERTY", "OGARA", "OGORMAN", "OGRADY", "OHALLERAN", "OHALLORAN", "OHANLON",
    "OKEEFE", "OKEEFFE", "OLEARY", "OLOUGHLIN", "OMALLEY", "OMEARA", "ONEAL", "ONEIL", "ONEILL", "OREILLY",
    "ORIORDAN", "OROURKE", "OSHAUGHNESSY", "OSHEA", "OSULLIVAN", "OTOOLE",
    "DAGOSTINO", "DALESSANDRO", "DAMATO", "DANGELO",
}


#: Given names that start with O or D. Typed with the shift key held a letter too long ("DAvid",
#: "OLiver") they look like a fused O'/D' surname, but are a slip: no D'Avid or O'Liver exists.
#: (Every O/D + capital word in the data, 2026-10-12, is a real O'/D' surname: OBrien, ODonnell,
#: DAvila, DAmaro, DIorio, ORiordain, OCarroll.)
_O_D_GIVEN_NAMES = {
    "DALE", "DAMIAN", "DAN", "DANA", "DANIEL", "DANIELA", "DANIELLE", "DANNY", "DAPHNE", "DARIO", "DARREN",
    "DARRYL", "DARYL", "DAVE", "DAVID", "DAWN", "DEAN", "DEANNA", "DEBBIE", "DEBORAH", "DEBRA", "DEIRDRE",
    "DELIA", "DENIS", "DENISE", "DENNIS", "DEREK", "DIANA", "DIANE", "DIEGO", "DOLORES", "DOMINIC",
    "DOMINIQUE", "DON", "DONALD", "DONNA", "DORIS", "DOROTHY", "DOUG", "DOUGLAS", "DUANE", "DYLAN",
    "ODETTE", "OLA", "OLAF", "OLE", "OLEG", "OLGA", "OLIVER", "OLIVIA", "OLIVIER", "OMAR", "OREN",
    "ORLANDO", "OSCAR", "OSWALD", "OTIS", "OTTO", "OWEN",
}


#: Common surnames that start with D or O and have no apostrophe. Typed with the shift key held
#: ("DAvis", "OLson") they have the same shape as a fused O'/D' surname, but there is no D'Avis
#: or O'Lson. (None is in today's data; a signature "/s/ John DAvis" would otherwise turn John
#: Davis into John D'Avis.)
_O_D_PLAIN_SURNAMES = {
    "DALE", "DALEY", "DALTON", "DALY", "DANIEL", "DANIELS", "DARLING", "DAVENPORT", "DAVEY", "DAVIDSON",
    "DAVIES", "DAVIS", "DAWSON", "DAY", "DEAN", "DECKER", "DELANEY", "DENNIS", "DIAZ", "DICKSON", "DILLON",
    "DIXON", "DOLAN", "DONAHUE", "DONALDSON", "DONNELLY", "DONOVAN", "DOUGHERTY", "DOUGLAS", "DOWNEY",
    "DOYLE", "DRAKE", "DUFFY", "DUNCAN", "DUNN", "DURAN", "DYER",
    "OAKES", "OAKLEY", "OBERG", "ODOM", "OGDEN", "OLIVER", "OLIVEIRA", "OLSEN", "OLSON", "ORR", "ORTEGA",
    "ORTIZ", "OSBORN", "OSBORNE", "OTT", "OWEN", "OWENS",
}
#: Words that are never an O'/D' name, whatever their capitals.
_O_D_PLAIN_WORDS = _O_D_GIVEN_NAMES | _O_D_PLAIN_SURNAMES


def _shift_key_slip(part: str) -> bool:
    """A plain name typed with the first two letters in capitals: "DAvid", "DAvis", "OLson"."""
    return len(part) > 2 and part[:2].isupper() and part[2:].islower() and part.upper() in _O_D_PLAIN_WORDS


def _dropped_apostrophe(part: str) -> bool:
    """An O' or D' surname with EDGAR's apostrophe dropped: written with the capital after it kept
    ("ODonnell Kevin", "DAlessandro Henry", "OHara Ellen O"), or a surname on _APOSTROPHE_SURNAMES
    in one case ("OBRIEN", "Oconnell", "obrien"). A plain name typed with the shift key held
    ("DAvid", "DAvis", "OLson") is a slip, not an apostrophe (_O_D_PLAIN_WORDS)."""
    if len(part) < 4 or part[0].upper() not in "OD":
        return False
    if part[0] in "OD" and part[1].isupper() and part[2:].islower():
        return part.upper() not in _O_D_PLAIN_WORDS
    return (part.isupper() or part.islower() or part == part.capitalize()) and part.upper() in _APOSTROPHE_SURNAMES


def _cap(word: str) -> str:
    """Title-case one word, keeping Mc/Mac/O' and hyphenated parts readable."""
    if not word:
        return word
    parts = word.split("-")
    out = []
    for p in parts:
        low = p.lower()
        if low.startswith("mc") and len(p) > 2:
            out.append("Mc" + p[2:].capitalize())
        elif low.startswith(("o'", "d'")) and len(p) > 2:
            out.append(p[0].upper() + "'" + p[2:].capitalize())
        elif _dropped_apostrophe(p):
            out.append(p[0].upper() + "'" + p[1:].capitalize())  # "OBRIEN" -> "O'Brien"
        elif _shift_key_slip(p):
            out.append(p.capitalize())  # the slip "DAvid" is David; a deliberate "DeAnna" stays
        else:
            out.append(p.capitalize() if p.isupper() or p.islower() else p)
    return "-".join(out)


def _is_tag(token: str) -> bool:
    """A parenthesised word after a name: a nickname ("(Vivi)", "(Mike)") or initials ("(RK2)", "(TC)")."""
    return len(token) > 2 and token.startswith("(") and token.rstrip(",;.").endswith(")")


def _tag_text(token: str, all_caps: bool) -> str:
    """A parenthesised tag as displayed. In a mixed-case name it stays as filed ("(PiJi)", "(RK2)").
    In an all-capital name, initials stay in capitals ("(DC)", "(HLY)": two letters, no vowel or a
    digit) and a nickname is title-cased ("ZHANG WEI (VIVI)" is Wei (Vivi) Zhang)."""
    inner = token.strip("()")
    if not all_caps or len(inner) <= 2 or any(ch.isdigit() for ch in inner) or not re.search(r"[AEIOUY]", inner, re.I):
        return token
    return f"({_cap(inner)})"


def _norm(word: str) -> str:
    """A word reduced to letters and digits, upper case, for comparing spellings ("O'Hanley" = "OHANLEY")."""
    return re.sub(r"[^A-Z0-9]", "", unicodedata.normalize("NFKD", word).upper())


def _is_inner_particle(token: str) -> bool:
    return token.upper() in _INNER_PARTICLES or token == "y"


def _clean(edgar_name: str) -> str:
    """EDGAR's name with what is not part of the person's name removed, one space between words.

    EDGAR's "(nmn)" (no middle name), its namesake number ("TEUNISSEN ERNST 02494", or in
    parentheses: "Burns Patrick Joseph (2026)" is Patrick Joseph Burns), state codes such as
    "/CA/", and "ET AL" ("WATSA V PREM ET AL": the filing is for him and others, and is shown under
    his name) are dropped. Degrees written "Ph. D." read like "PhD". A tag with letters stays
    ("Kapoor Rohit (RK2)").
    """
    cleaned = re.sub(r"\(\s*nmn\s*\)", " ", edgar_name, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+(?:\d+|\(\d+\))\s*$", "", cleaned)
    cleaned = re.sub(r"\s*/[A-Z]{2}/?\s*", " ", cleaned)
    cleaned = re.sub(r"\bPH\.?\s*D\b\.?", "PHD", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bET\.?\s+AL\b\.?", " ", cleaned, flags=re.IGNORECASE)
    return " ".join(cleaned.replace(".", ". ").split()).strip().strip(",").strip()


_SIGNED = re.compile(r"^\s*(?:by:?\s*)?(?:/s/|s/)\s*", re.IGNORECASE)


def _weights(signatures: Signatures) -> dict[str, int]:
    """Each distinct signature with the number of filings that carry it."""
    if isinstance(signatures, Mapping):
        return {str(s): max(1, int(n)) for s, n in signatures.items()}
    counts: dict[str, int] = {}
    for s in signatures:
        counts[s] = counts.get(s, 0) + 1
    return counts


def _core(tokens: list[str]) -> list[tuple[int, str]]:
    """The words of a name that are compared with a signature: (position in `tokens`, comparable
    form), leaving out suffixes (Jr., III), titles (Mr, Dr) and parenthesised tags ("(RK2)")."""
    return [(i, _norm(t)) for i, t in enumerate(tokens)
            if _norm(t) and _norm(t) not in _NOT_COMPARED and not _is_tag(t)]


def _name_words(text: str) -> list[str]:
    """The name words of a piece of a signature, as written, in the same way as _core."""
    words = []
    for w in text.split():
        if _is_tag(w):
            continue
        # "/Todd Stein/": the slashes some filers sign with aren't part of the name.
        w = w.strip("()[]{},;:\"/")
        if _norm(w) and _norm(w) not in _NOT_COMPARED:
            words.append(w)
    return words


class _Reading(NamedTuple):
    """One way a signature can name the owner."""

    words: list[str]  # the name's words as signed
    surname: int | None  # "Last, First": how many of the words, at the end, are the surname
    mixed_case: bool  # written in mixed case, not copied in EDGAR's capitals


def _readings(signature: str, commas: bool = True) -> list[list[_Reading]]:
    """The ways a signature can name the owner, grouped by the part of the signature they come from,
    most specific first.

    The owner's name is the whole signature, or what follows "for" when someone signs on their
    behalf ("Derrick Walsh for Nicholas A Mosich"). A name written with a comma, "Sharps, Robert
    W.", is surname first, so its surname is known and it is not read as the words "Sharps Robert
    W." in that order. Text after a comma may also be a title ("Brandon G. Lutnick, as Chief
    Executive Officer"), so the words before the first comma are read on their own too. `commas`
    False reads every signature as plain words.
    """
    text_ = _SIGNED.sub("", signature)
    on_behalf = re.split(r"\b(?:for|on behalf of):?\s+", text_, maxsplit=1, flags=re.IGNORECASE)
    groups: list[list[_Reading]] = []
    for candidate in [text_, *on_behalf[1:]]:
        mixed = candidate != candidate.upper() and candidate != candidate.lower()
        group: list[_Reading] = []
        before, comma, after = candidate.partition(",")
        surname, given = _name_words(before), _name_words(after.split(",")[0])
        if comma and commas and surname:
            if given:
                group.append(_Reading([*given, *surname], len(surname), mixed))
            group.append(_Reading(surname, None, mixed))
        # Last, all the words in the order written. Hong Kong filers also sign "English name,
        # Chinese name" ("/s/ Andrew, Lee Yat Lung" for LEE YAT LUNG ANDREW, shown Andrew Lee Yat
        # Lung), which the surname-first reading above can't place.
        whole = _Reading(_name_words(candidate), None, mixed)
        if whole not in group:
            group.append(whole)
        groups.append(group)
    return groups


#: How a signature reads EDGAR's name: ("edgar", m) means the name is written surname first with an
#: m-word surname; ("natural", m) means it is already written first-name first and its last m
#: words are the surname.
_Order = tuple[str, int]


def _match(reading: _Reading, words: list[str], natural_ok: bool) -> _Order | None:
    """How one reading of a signature fits the name's comparable `words`. A first-name-first
    reading counts only when `natural_ok` (the word counts agree, see NameFrequencies)."""
    signed = [_norm(w) for w in reading.words]
    if len(signed) != len(words):
        return None
    if reading.surname is not None:
        if signed == words:
            # "Robert W. Sharps", signed for as "Sharps, Robert W.". Hong Kong filers also sign
            # "Given, Surname" ("Sin Ting, Yu" for YU SIN TING), hence the word counts.
            return ("natural", reading.surname) if natural_ok else None
        if 0 < reading.surname < len(words) and signed == words[reading.surname:] + words[:reading.surname]:
            return ("edgar", reading.surname)  # "Craighead Carey, Jennifer"
        return None
    if signed == words:
        # The same words in the same order: a name EDGAR already has first-name first, signed as
        # filed ("/s/ Heath R. Fear" for "Heath R Fear"), or a signer who copies EDGAR's order
        # ("/s/ Sherman H. David", "/s/ Chen Hua"). Only mixed-case signatures, and only when the
        # word counts agree. The surname is the last word, with any particles before it.
        if not (natural_ok and reading.mixed_case):
            return None
        m = 1
        while m < len(words) - 1 and words[-m - 1] in _SURNAME_PARTICLES:
            m += 1
        return ("natural", m)
    for k in range(1, len(words)):
        if signed == words[k:] + words[:k]:
            return ("edgar", k)
    return None


def _signature_choice(tokens: list[str], weights: dict[str, int], rule_split: int | None = None,
                      commas: bool = True, frequencies: NameFrequencies | None = None,
                      ) -> tuple[_Order, list[tuple[int, str]]] | None:
    """Where the surname ends, read from the signatures naming the owner in natural order.

    EDGAR writes "Silva de Sousa Oliveira Manuel Lino"; no rule can tell where a double surname
    ends. A signature with exactly the same words rotated ("/s/ Manuel Lino Silva de Sousa
    Oliveira", or an agent's "... for Manuel Lino Silva de Sousa Oliveira") would: the words it
    moves to the end are the surname. Without one, the rules stop the surname after the first
    particle and the word after it, which for this filer (52971, whose filings carry no such
    signature) gives Oliveira Manuel Lino Silva de Sousa, a known limitation of the rules alone.
    Only a true rotation counts. Signatures in EDGAR's own order,
    other given names ("Nicholas" for "Nick") or an attorney's own name tell nothing. Suffixes
    (Jr., III), titles and tags are left out of the comparison.

    A name EDGAR already has first-name first ("Robert W. Sharps", "David P McGlade") shows in a
    signature with the same words in the same order, or as "Sharps, Robert W.". Signers who keep
    EDGAR's order look the same, so this counts only when the word counts in `frequencies` say so
    (NameFrequencies.reads_first_name_first).

    When signatures disagree, the reading carried by more filings wins; on a tie, the one that
    agrees with the rules (`rule_split`, in words). "Akselbrad Dikla Czaczkes" has one Form 3
    signed "Dikla Czaczkes Akselbrad" and one Form 4 signed in EDGAR's order "Czaczkes Akselbrad
    Dikla", which is also a rotation; the rules make Akselbrad the surname, as her Form 3 does.

    Returns (the order, the words of the signatures that gave it with each signature's number of
    filings, those on more filings first), or None when no signature fits. Signatures are read in a
    fixed order (more filings first, then by text), so the result never depends on the order the
    database returns them in.
    """
    core = _core(tokens)
    words = [w for _, w in core]
    if len(words) < 2:
        return None
    natural_ok = frequencies is not None and frequencies.reads_first_name_first(words)
    votes: dict[_Order, int] = {}
    signed_as: dict[_Order, list[tuple[int, list[str]]]] = {}
    plain: list[tuple[int, list[str]]] = []
    for signature, weight in sorted(weights.items(), key=lambda kv: (-kv[1], kv[0])):
        found: tuple[_Order, list[str]] | None = None
        for group in _readings(signature, commas):
            for reading in group:
                order = _match(reading, words, natural_ok)
                if order is not None:
                    found = (order, reading.words)
                    break
                if reading.surname is None:
                    plain.append((weight, reading.words))
            if found:
                break
        if found:
            order, original = found
            votes[order] = votes.get(order, 0) + weight
            signed_as.setdefault(order, []).append((weight, original))
    if votes:
        best = max(votes, key=lambda o: (votes[o], o == ("edgar", rule_split), o[0] == "edgar", -o[1]))
        # The spellings of every signature that agrees, those on more filings first (see _spelling).
        return best, [(weight, w) for weight, original in signed_as[best] for w in original]
    # Nearly a rotation: the signature leaves out a middle name but keeps the first given name
    # and ends with EDGAR's whole leading surname block ("Carlos Aguiar Rodriguez" for AGUIAR
    # RODRIGUEZ CARLOS ROBERTO), or adds one ("Jane E. Trust" for TRUST JANE). Only when no
    # signature is an exact rotation.
    for weight, original in plain:
        signed = [_norm(w) for w in original]
        for k in range(1, len(words)):
            if len(signed) >= k + 1 and signed[-k:] == words[:k] and signed[0] == words[k] and signed != words:
                return ("edgar", k), [(weight, w) for w in original]
    return None


def _natural_order_by_signature(tokens: list[str], signatures: Iterable[str]) -> bool:
    """A two-word name already written first-name first ("Nick Mosich" on the owner's Form 4s):
    a signature ends with the same surname and spells the first name out in full ("Derrick Walsh
    for Nicholas A Mosich"). The signed first name must be longer and share the filed one's first
    three letters, so a signature in EDGAR's own order ("Smith John") never counts."""
    words = [w for _, w in _core(tokens)]
    if len(words) != 2 or len(words[0]) < 3:
        return False
    for signature in signatures:
        text_ = _SIGNED.sub("", signature)
        on_behalf = re.split(r"\b(?:for|on behalf of):?\s+", text_, maxsplit=1, flags=re.IGNORECASE)
        for candidate in [text_, *on_behalf[1:]]:
            signed = [_norm(w) for w in _name_words(candidate)]
            if (len(signed) >= 2 and signed[-1] == words[1] and len(signed[0]) > len(words[0])
                    and signed[0][:3] == words[0][:3]):
                return True
    return False


#: An owner named by a title and one word: "/s/ Derek Stark, attorney-in-fact for Mr. DuFauchard".
_BY_TITLE = re.compile(r"\b(?:Mr|Mrs|Ms|Miss|Dr)\.?\s+([A-Za-z][A-Za-z'\-]*)(?=\s*(?:$|[,;)]))")


def _surname_last_by_title(tokens: list[str], signatures: Iterable[str]) -> bool:
    """A name already written first-name first, shown by a signature that calls the owner by a
    title and the name's last word: "Preston Paul DuFauchard, Jr." (54509), signed for "Mr.
    DuFauchard", is Preston Paul DuFauchard Jr. A title with the name's first word confirms EDGAR's
    own order ("Simon, Christopher", signed for "Mr. Simon"), and then nothing changes."""
    words = [w for _, w in _core(tokens)]
    if len(words) < 2 or words[0] == words[-1]:
        return False
    named = {_norm(m.group(1)) for signature in signatures for m in _BY_TITLE.finditer(signature)}
    return words[-1] in named and words[0] not in named


def _plain_signers(weights: dict[str, int]) -> dict[str, int]:
    """The owner's name in each signature, read as plain words (what follows "for", else the whole
    signature), leaving out a name written with a comma. Used for a name EDGAR has with a comma,
    whose own comma already says where the surname ends: a signature with a comma ("/s/ Sze Hon,
    Johnson Chen" for "Chen Sze Hon, Johnson") is a Hong Kong ordering no rotation reads."""
    plain: dict[str, int] = {}
    for signature, weight in weights.items():
        text_ = _SIGNED.sub("", signature)
        name = re.split(r"\b(?:for|on behalf of):?\s+", text_, maxsplit=1, flags=re.IGNORECASE)[-1]
        if "," not in name and name.strip():
            plain[name.strip()] = plain.get(name.strip(), 0) + weight
    return plain


def _has_inner_capitals(form: str) -> bool:
    """A signed word with capitals title case can't guess ("DeGirolamo", "McDonald"), and not a
    slip of the shift key with the first two letters capital ("LIpps")."""
    return any(p != p.upper() and p != p.lower() and p != p.capitalize() and len(p) > 1 and not p[1].isupper()
               for p in form.rstrip(".").split("-"))


def _is_lower_particle(form: str) -> bool:
    """A particle signed in lower case ("de", "van"), as the owner writes it inside the name."""
    bare = form.rstrip(".")
    return bare == bare.lower() and bare.upper() in _SURNAME_PARTICLES | _INNER_PARTICLES


def _spelling(signed: list[tuple[int, str]]) -> dict[str, str]:
    """Each signed word's spelling, by its comparable form. `signed` lists the words of every
    signature that agrees with the chosen order, each with the number of filings its signature is
    on, in a fixed order (more filings first, then by signature text).

    A spelling with inner capitals beats one without: Sheldon Trainor-DeGirolamo signed
    "Trainor-Degirolamo" on two filings and "Trainor-DeGirolamo" on one, and a signer drops
    capitals far more often than inventing them. Otherwise the spelling on more filings wins, and
    on a tie a lower-case particle: Alexandre Teixeira de Araujo (26389) signed one filing with
    "de" and one with "De"; the lower-case form is the one no rule would guess. The result never
    depends on the order the signatures came in."""
    own: dict[str, tuple[int, str]] = {}
    for weight, w in signed:
        key = _norm(w)
        if key not in own:
            own[key] = (weight, w)
            continue
        best_weight, best = own[key]
        inner, best_inner = _has_inner_capitals(w), _has_inner_capitals(best)
        if inner != best_inner:
            better = inner
        elif weight != best_weight:
            better = weight > best_weight
        else:
            better = _is_lower_particle(w) and not _is_lower_particle(best)
        if better:
            own[key] = (weight, w)
    return {key: w for key, (_, w) in own.items()}


def _spelled(token: str, signed: dict[str, str], mixed_case: bool = False) -> str:
    """One name word as displayed: EDGAR's word, with the capitals of the owner's own signature
    when it shows something title case can't ("de", "DeSouza", "McDonald"). Initials and
    all-capital or all-lower signatures keep the title-case form.

    With no such signature, a particle the filer typed in lower case in a mixed-case name stays
    lower case (`mixed_case`): "Fernandez de Melero Adriana Maria" is Adriana Maria Fernandez de
    Melero. An O' or D' surname gets back the apostrophe EDGAR dropped (_dropped_apostrophe),
    unless the owner's signature writes it as one plain word ("Obrien")."""
    if token.upper() in _APOSTROPHE_PARTICLES:
        return token.lower()
    own = signed.get(_norm(token))
    # EDGAR's own apostrophe or hyphen stays ("O'Leary" signed "OLeary" is still O'Leary).
    if own is not None and set(re.sub(r"[A-Za-z0-9.]", "", token)) - set(own):
        own = None
    particle = token.rstrip(".").upper() in _SURNAME_PARTICLES | _INNER_PARTICLES
    if own is not None and len(_norm(token)) > 1:
        bare = own.rstrip(".")
        if particle and (bare.lower() == bare or bare == bare.capitalize()):
            # The particle as the owner signs it ("de", "da", "van", or "De"), with EDGAR's own
            # dot kept: "St. John Mary" signed "Mary St. John" is Mary St. John, not "St John".
            return bare + ("." if token.endswith(".") else "")
        # Inner capitals ("DeSouza", "McDonald", "O'Hanley"), but not a slip of the shift key
        # with the first two letters capital ("LIpps", "MIchael").
        if bare != bare.upper() and bare != bare.lower() and bare != bare.capitalize() and not bare[1].isupper():
            return bare
        # "OKelly" in a signature is EDGAR's own form copied ("... for Shane M. OKelly"): O'Kelly.
        if _dropped_apostrophe(bare) and bare[1].isupper():
            return _cap(bare)
        # The owner writes it as one plain word, apostrophe and all left out ("Obrien").
        if bare == bare.capitalize() and bare != bare.upper() and "'" in _cap(token):
            return bare
    if mixed_case and particle and token.islower():
        return token
    return _cap(token)


def _typed_first_name_first(raw: str, tokens: list[str], frequencies: NameFrequencies) -> bool:
    """True when a name with no signature to go by was typed first-name first.

    EDGAR's own all-capital names are surname first. A name the filer typed in mixed case
    sometimes is not, and one shape shows it: a given name, one initial, a surname ("Daniel K
    Weiss", 9817; "Trent T McKenna", 27186), where the word counts also say the first word is a
    given name (NameFrequencies.reads_first_name_first). Surname first, an initial right after
    the surname belongs to someone who goes by a middle name ("Turner R Gerald"), and there the
    first word counts as a surname.

    Word counts alone are not enough for other shapes (2026-10-13 dry run): they would turn
    "Darrell Bracken" (Bracken Darrell), "Bess Lane" (Lane Bess) and "George James Case" the
    wrong way round, so "Nicholas Breisacher" (49663) keeps EDGAR's order until a signature
    shows otherwise.
    """
    if raw == raw.upper() or raw == raw.lower():
        return False
    words = [w for _, w in _core(tokens)]
    return len(words) == 3 and len(words[1]) == 1 and frequencies.reads_first_name_first(words)


def _rule_split(tokens: list[str], frequencies: NameFrequencies | None = None) -> tuple[list[str], int, str]:
    """Where the surname ends by rule, for a name with no signature to go by: (the tokens, less a
    particle EDGAR moved to the end; how many of them form the surname; that particle).
    `frequencies`, when given, settle a two-word surname no rule can see ("Le Peuch Olivier",
    "Calderon Rojas Francisco Jose": NameFrequencies.second_word_ends_surname)."""
    tokens = list(tokens)
    trailing_particle = ""
    # A particle EDGAR moved to the end belongs before the surname.
    if len(tokens) >= 3 and tokens[-1].upper() in _INNER_PARTICLES:
        trailing_particle = tokens.pop()
    # A surname that starts with a particle continues past it: "DE LA VEGA RAFAEL" is
    # Rafael De La Vega, "VAN DER BERG JOHN" is John Van Der Berg, "de los Pinos Elisabet" is
    # Elisabet de los Pinos.
    n = 1
    while n < len(tokens) - 1 and tokens[n - 1].upper() in _SURNAME_PARTICLES:
        n += 1
    # A patronymic right after a one-word surname completes a double surname, which may
    # go on with particles ("CURADO GOMES DE LEMOS LEONARDO").
    if n == 1 and len(tokens) >= 3 and _norm(tokens[1]) in _PATRONYMICS:
        n = 2
    # A surname given twice is a Spanish double surname, the father's and the mother's ("garza garza
    # Alfonso" is Alfonso Garza Garza, "Prieto Prieto Maria Isabel" Maria Isabel Prieto Prieto).
    # Not a short East Asian surname, whose given name may repeat it ("Yoon Yoon Soo (Gene)" is
    # Yoon-soo Yoon), nor a name with a generational suffix ("Daniel Daniel David III").
    if (n == 1 and len(tokens) >= 3 and _norm(tokens[1]) == _norm(tokens[0]) and len(_norm(tokens[0])) >= 5
            and not _norm(tokens[0]).endswith("NG")
            and not any(_norm(t) in _SUFFIXES - _DEGREE_SUFFIXES for t in tokens)):
        n = 2
    if n == 1 and frequencies is not None and frequencies.second_word_ends_surname(tokens):
        n = 2
    # A compound surname joined by particles ("Perez de la Mesa", "Garza y Garza"), maybe with a
    # generational ending ("Batista de Lima Filho"). At least one given name stays.
    while n < len(tokens) - 2 and _is_inner_particle(tokens[n]):
        n += 1
        while n < len(tokens) - 2 and tokens[n].upper() in _SURNAME_PARTICLES:
            n += 1
        n += 1
    if n < len(tokens) - 1 and tokens[n].upper() in _SURNAME_ENDINGS:
        n += 1
    return tokens, n, trailing_particle


def person_display_and_sort(edgar_name: str, signatures: Signatures = (),
                            frequencies: NameFrequencies | None = None) -> tuple[str, str]:
    """Turn EDGAR's "LAST FIRST MIDDLE [SUFFIX]" into ("First Middle Last", "Last, First Middle").

    Mixed-case input that already contains a comma ("Doe, Jane") is respected.
    Clean-up (see _clean): EDGAR's "(nmn)", state codes such as "/CA/" and "ET AL" are dropped,
    stray trailing commas are ignored, degrees written "Ph. D." are recognized like "PhD", and
    titles (Mr, Dr) are not shown.

    Where the surname ends is taken from signatures on the owner's filings that name the owner
    with the same words in natural order (`signatures`, see _signature_choice), which also supply
    capitals title case can't guess ("Manuel Lino Silva de Sousa Oliveira"). A signature can also
    show that the name is already first-name first: "Nick Mosich" signed for as "Nicholas A
    Mosich", and, when the word counts in `frequencies` agree, "Robert W. Sharps" signed for as
    "Sharps, Robert W." or "Heath R Fear" signed "/s/ Heath R. Fear". Otherwise it follows these
    rules. EDGAR drops apostrophes, so "O HANLEY RONALD P" is Ronald P O'Hanley, "D AGOSTINO
    ROBERT" is Robert D'Agostino, and "OBRIEN RICHARD T" and "ODonnell Kevin" are Richard T
    O'Brien and Kevin O'Donnell (see _dropped_apostrophe). A comma before only a suffix
    ("Phillips John R, III") is ignored, and a signature can place the surname of a name filed
    with a comma ("Crespy Benjamin Noel, Camille", signed for as "Benjamin Crespy"). A surname
    given twice is a double surname ("garza garza Alfonso" is Alfonso Garza Garza), and a particle
    typed in lower case stays so ("Fernandez de Melero"). Portuguese and Spanish surnames keep
    their inner particles and generational ending ("BATISTA DE LIMA FILHO PEDRO" is Pedro Batista De Lima Filho, "PEREZ DE
    LA MESA MANUEL J" is Manuel J Perez De La Mesa), and a particle EDGAR moved to the end goes
    back before the surname ("AGUIAR LUCAS ARAUJO DE" is Lucas Araujo De Aguiar). A name with two
    surnames and no matching signature ("PAZ MONTOYA ESTHER") can still be split wrongly, and so
    can a name EDGAR already has first-name first with no signature to show it.
    """
    raw = _clean(edgar_name)
    trailing_particle = ""
    signed: list[tuple[int, str]] = []
    extra_suffixes: list[str] = []
    # A comma before nothing but a suffix doesn't split "Last, First": "Phillips John R, III" is
    # John R Phillips III (40236, signed for as "John R Phillips III").
    head, comma, tail = raw.rpartition(",")
    if comma and tail.split() and len(head.split()) >= 2 and all(
            w.rstrip(".").upper() in _SUFFIXES for w in tail.split()):
        raw = f"{head} {tail.strip()}"
    if "," in raw:
        last, _, rest = raw.partition(",")
        # Later commas list more given names ("Catrice Ranunkel Loisa, Marie, Anita"): not shown.
        given = " ".join(rest.replace(",", " ").split())
        before = last.split(" ")
        if len(before) >= 2 and signatures:
            # "Crespy Benjamin Noel, Camille" (46057) is Benjamin Noel Camille Crespy, signed for as
            # "Benjamin Crespy": French given names listed with a comma after EDGAR's surname-first
            # words. A signature naming the owner in natural order places the surname inside the
            # words before the comma; it never moves it past the comma.
            tokens = [*before, *given.split(" ")] if given else before
            choice = _signature_choice(tokens, _plain_signers(_weights(signatures)))
            core_before = len(_core(before))
            if choice is not None and choice[0][0] == "edgar" and choice[0][1] <= core_before:
                (_order, m), signed = choice
                n = _core(tokens)[m - 1][0] + 1
                last, given = " ".join(tokens[:n]), " ".join(tokens[n:])
    else:
        tokens = raw.split(" ")
        if len(tokens) == 1:
            return _cap(tokens[0]), _cap(tokens[0])
        # An apostrophe EDGAR dropped: a lone O or D before the surname.
        if len(tokens) >= 3 and tokens[0].upper() in ("O", "D"):
            tokens = [f"{tokens[0]}'{tokens[1]}", *tokens[2:]]
        # "DO" after a middle initial is a degree, Doctor of Osteopathic Medicine ("SHRUM KAYSE M
        # DO"), not the Portuguese particle, which never follows an initial.
        if len(tokens) >= 4 and tokens[-1].rstrip(".").upper() == "DO" and len(_norm(tokens[-2])) == 1:
            extra_suffixes.append(tokens.pop())
        rule_tokens, rule_n, rule_particle = _rule_split(tokens, frequencies)
        choice = None
        if signatures:
            rule_words = sum(1 for i, _ in _core(rule_tokens) if i < rule_n)
            choice = _signature_choice(tokens, _weights(signatures), rule_words, frequencies=frequencies)
            if choice is None and (_natural_order_by_signature(tokens, _weights(signatures))
                                   or _surname_last_by_title(tokens, _weights(signatures))):
                # "Nick Mosich", "Preston Paul DuFauchard Jr.": a one-word surname at the end
                choice = ("natural", 1), []
        if choice is None and frequencies is not None and rule_n == 1 and _typed_first_name_first(raw, tokens, frequencies):
            choice = ("natural", 1), []
        if choice is not None:
            (order, m), signed = choice
            core = _core(tokens)
            if order == "natural":
                # Put the surname first, like EDGAR's own order.
                start, end = core[-m][0], core[-1][0] + 1
                tokens = [*tokens[start:end], *tokens[:start], *tokens[end:]]
                n = end - start
            else:
                n = core[m - 1][0] + 1
        else:
            tokens, n, trailing_particle = rule_tokens, rule_n, rule_particle
        last, given = " ".join(tokens[:n]), " ".join(tokens[n:])
    all_caps = raw == raw.upper()
    given_words = [w for w in given.split(" ") if w]
    # A suffix may sit anywhere after the last name ("DIORIO CHRIS PHD M"); move it to the end.
    suffixes = [w for w in given_words if w.rstrip(".").upper() in _SUFFIXES] + extra_suffixes
    given_words = [w for w in given_words if w.rstrip(".").upper() not in _SUFFIXES | _HONORIFICS]
    # A tag filed right after the surname follows the first given name ("Zhang (Rain) Rui" is Rui
    # (Rain) Zhang).
    if len(given_words) >= 2 and _is_tag(given_words[0]) and not _is_tag(given_words[1]):
        given_words[0], given_words[1] = given_words[1], given_words[0]
    suffix = " ".join(_suffix_text(w.rstrip(".").upper()) for w in suffixes)
    own = _spelling(signed)
    # Typed in mixed case, the filer's own lower-case particles stay ("Fernandez de Melero").
    mixed = not all_caps and raw != raw.lower()
    given_fmt = " ".join(_tag_text(w, all_caps) if _is_tag(w) else _spelled(w, own, mixed) for w in given_words)
    last_words = [w for w in last.split(" ") if w.rstrip(".").upper() not in _HONORIFICS] or last.split(" ")
    # The Spanish "y" between two surnames stays lower case ("Eugenio Garza y Garza").
    last_fmt = " ".join("y" if w == "y" or (w == "Y" and 0 < i < len(last_words) - 1) else _spelled(w, own, mixed)
                        for i, w in enumerate(last_words))
    particle = _spelled(trailing_particle, own, mixed) if trailing_particle else ""
    display = " ".join(x for x in (given_fmt, particle, last_fmt, suffix) if x)
    # A lower-case particle never opens the displayed name ("Zegna di Monte Rubello Edoardo", with
    # no signature, can only be split by rule: Di Monte Rubello Edoardo Zegna).
    if display.split(" ")[0] in {p.lower() for p in _SURNAME_PARTICLES | _INNER_PARTICLES}:
        display = display[0].upper() + display[1:]
    # Sorted under the surname itself: "Aguiar, Lucas Araujo De".
    # The sort name is only an ordering key, never shown, and the database sorts byte by byte (C
    # collation), where every lower-case letter comes after every capital: "von Moltke, Lisa"
    # would sort after "Zyngier, Alexandre", and "Van der Valk" after "Van Zyl". So each word of
    # the key opens with a capital ("Van Der Valk, Eric"; "Aguiar, Lucas Araujo De"), while the
    # display keeps "Eric van der Valk".
    sort_last, sort_given = last_fmt, " ".join(x for x in (given_fmt, particle) if x)
    # The Dutch 't and 's are filed under the word after them: "Hart, Cees 't" (33399). Left in
    # front, the apostrophe would put the name before every letter.
    opening, _, rest = sort_last.partition(" ")
    if opening.upper() in _APOSTROPHE_PARTICLES and rest:
        sort_last, sort_given = rest, f"{sort_given} {opening}".strip()
    sort = f"{sort_last}, {sort_given}".strip(", ") + (f" {suffix}" if suffix else "")
    sort = " ".join(w[:1].upper() + w[1:] for w in sort.split(" "))
    return display, sort


def _suffix_text(token: str) -> str:
    if token in {"II", "III", "IV"}:
        return token
    if token == "PHD":
        return "PhD"
    if token in _DEGREE_SUFFIXES:
        return token
    return token.capitalize() + "."  # Jr., Sr.


def org_display(name: str) -> str:
    """Tidy an organization name: collapse spaces, drop EDGAR's trailing backslash or slash codes."""
    cleaned = " ".join(name.split()).rstrip("\\").strip()
    # EDGAR appends codes like "/DE/", "/MN" or "/NEW/" to conformed names.
    cleaned = re.sub(r"\s*/[A-Z]{2,5}/?$", "", cleaned)
    return cleaned


def slugify(text: str, suffix: str | None = None) -> str:
    """URL slug: ascii, lowercase, hyphens. `suffix` (e.g. CIK digits) guarantees uniqueness."""
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")[:80] or "entity"
    return f"{slug}-{suffix}" if suffix else slug


def pad_cik(cik: str | int) -> str:
    """Zero-pad a CIK to the 10 digits EDGAR uses in URLs and JSON."""
    return f"{int(str(cik).strip()):010d}"
