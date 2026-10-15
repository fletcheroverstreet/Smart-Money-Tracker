"""How job titles, roles and company names read on the site.

Filings give job titles the way the filer typed them ("EVP, CFO", "CHIEF
FINANCIAL OFFICER", "See Remarks") and company names the way EDGAR stores them
("FIFTH THIRD BANCORP", "AMETEK INC/", "Rivian Automotive, Inc. / DE"). These
helpers make them readable without changing what they say: capitals, EDGAR's
filing codes and abbreviations change, the facts don't.

Used by the profile summary (routers/insiders.py), the search palette
(routers/search.py), the Live page's today strip (routers/feed.py) and the Live
feed's rows (queries.feed_item).
"""

from __future__ import annotations

import re
from collections.abc import Collection, Mapping, Sequence

# ---------------------------------------------------------------- job titles

#: What a role is called when the filing gives no usable title.
ROLE_LABELS = {"officer": "Officer", "director": "Director", "ten_pct_owner": "10% owner",
               "other_insider": "Other insider"}

#: Titles that say nothing: the filer pointed elsewhere ("See Remarks", "Please see
#: remarks below", "Refer to Footnote 2") or left a placeholder ("N/A", "Officer w/o
#: Specific Desig", '"Officer" Under Sec. 16 Rules', a lone dash). A title that starts
#: "See" always points elsewhere.
_PLACEHOLDER = re.compile(
    r"(?i)\W*(?:(?:please\s+)?(?:see|refer\s+to)\b.*|n/?a|none|-+|\.|officer\s+w/o\s+specific\s+desig\w*"
    r"|officer\W*\s+(?:under|per|pursuant\s+to)\s+sec\w*\.?\s*16\b.*)\W*")
#: Abbreviations kept in capitals when an ALL-CAPS title is title-cased.
_ACRONYMS = {"EVP", "SVP", "SEVP", "VP", "GC", "GM", "BD", "PAO", "PFO", "PEO", "US", "U.S.", "R&D", "IT", "HR", "MD",
             "II", "III", "IV"}
#: Chief-officer abbreviations: "CEO", "CFO", "CCO", "CSO", "CHRO", "CISO".
_CHIEF_ACRONYM = re.compile(r"C[A-Z]{1,3}O")
_SMALL_WORDS = {"of", "and", "the", "for", "to", "in", "on", "at", "&"}
#: Where an ALL-CAPS title's words split for casing: "CO-CHIEF", "EVP/CFO", "CHAIR,CHIEF", "EVP&CFO".
#: Each piece is cased on its own; single letters stay capitals, so "R&D" stays "R&D".
_TITLE_WORD_SPLIT = re.compile(r"([-/,&;])")


def _title_word(part: str, first: bool) -> str:
    """One word of an ALL-CAPS title (no spaces or _TITLE_WORD_SPLIT punctuation) in title case."""
    core = part.strip(",;:()")
    if not core:
        return part
    start = part.find(core)
    lead, tail = part[:start], part[start + len(core):]
    if core in _ACRONYMS or _CHIEF_ACRONYM.fullmatch(core):
        return part
    if not first and core.lower() in _SMALL_WORDS:
        return lead + core.lower() + tail
    return lead + core.capitalize() + tail


def clean_title(title: str | None, role_type: str) -> str:
    """A readable job title: placeholders become the role, and titles typed in ALL CAPS or all
    lower case are title-cased ("EVP/CHIEF FINANCIAL OFFICER" -> "EVP/Chief Financial Officer",
    "chairman and ceo" -> "Chairman and CEO"; "CCO" stays "CCO"). Mixed case stays as filed."""
    raw = " ".join((title or "").split())
    if not raw or _PLACEHOLDER.fullmatch(raw):
        return ROLE_LABELS.get(role_type, role_type.replace("_", " ").capitalize())
    if raw.isupper() or raw.islower():
        # The filer's capitals say nothing either way: case it word by word from capitals, so the
        # abbreviations are recognised ("evp, cfo" -> "EVP, CFO").
        words = []
        for i, word in enumerate(raw.upper().split(" ")):
            parts = _TITLE_WORD_SPLIT.split(word)  # the separators land at the odd positions
            words.append("".join(p if j % 2 else _title_word(p, i == 0 and j == 0) for j, p in enumerate(parts)))
        raw = " ".join(words)
    # A space after a comma or semicolon the filer ran into the next word ("VICE CHAIR,CHIEF ...").
    return re.sub(r"([,;])(?=[A-Za-z])", r"\1 ", raw)


#: One role in a title list that is a seat on the board: "Director", "Independent Director",
#: "Lead Independent Director", "Non-Executive Director", "Board Director".
_BOARD_SEAT = re.compile(r"(?:(?:lead|independent|non-?executive|board)\s+)*director", re.IGNORECASE)
#: What separates the roles in a cleaned title ("President, CEO and Director").
_ROLE_SEPARATOR = re.compile(r"\s*(?:[,;/&]|\band\b)\s*", re.IGNORECASE)


def _names_board_seat(title: str) -> bool:
    """Whether one of the roles listed in `title` is a seat on the board ("President, CEO
    and Director"), not a job with the word in it ("Director of Investor Relations",
    "Managing Director")."""
    return any(_BOARD_SEAT.fullmatch(part) for part in _ROLE_SEPARATOR.split(title.strip()) if part)


def relationship_role(rel: Mapping[str, object] | None) -> str | None:
    """Role text from a filing's reporting-owner relationship (filing_parties.relationship),
    e.g. "Chief Financial Officer" or "Director, 10% owner"."""
    if not rel:
        return None
    parts = []
    said_director = False
    if rel.get("officer"):
        title = rel.get("officer_title")
        cleaned = clean_title(title if isinstance(title, str) else None, "officer")
        # An officer title that already names the board seat ("Director, President and CEO")
        # covers the director box too: saying it twice read "..., Director, President and CEO,
        # Director". A job that only has the word in it ("Director of Investor Relations",
        # "Managing Director") isn't a board seat, so the box is still said.
        said_director = _names_board_seat(cleaned)
        parts.append(cleaned)
    if rel.get("director") and not said_director:
        parts.append(ROLE_LABELS["director"])
    if rel.get("ten_pct_owner"):
        parts.append(ROLE_LABELS["ten_pct_owner"])
    if rel.get("other"):
        # The filer's own description of an "Other" relationship ("Former 10% Owner").
        other = rel.get("other_text")
        parts.append(clean_title(other if isinstance(other, str) else None, "other_insider"))
    return ", ".join(parts) or None


def role_line(title: str | None, role_type: str | None, org_name: str | None,
              org_tickers: Collection[str] = ()) -> str | None:
    """A short "role, company" label: "Director, Snowflake Inc." or
    "Chief Executive Officer, Apple Inc." (the search palette's sublabel).
    `org_tickers` are the company's tickers (see company_name)."""
    if not role_type:
        return None
    role = clean_title(title, role_type)
    return f"{role}, {company_name(org_name, org_tickers)}" if org_name else role


# Titles in a sentence ("X is the Chief Executive Officer of Y").
#: Abbreviations spelled out in sentences, the common ones filers type ("EVP, Gen. Counsel
#: & Corp. Sec." -> "Executive Vice President, General Counsel and Corporate Secretary").
#: Chief-officer acronyms (CEO, CFO) stay: they read as words. Ambiguous ones ("Comm":
#: communications or commercial; "Int": interim or international) stay as filed.
_SPELLED_OUT = (
    (re.compile(r"\bSEVP\b"), "Senior Executive Vice President"),
    (re.compile(r"\bEVP\b"), "Executive Vice President"),
    (re.compile(r"\bSVP\b"), "Senior Vice President"),
    (re.compile(r"\bVP\b"), "Vice President"),
    (re.compile(r"\bGC\b"), "General Counsel"),
    (re.compile(r"\bPAO\b"), "Principal Accounting Officer"),
    (re.compile(r"\bPFO\b"), "Principal Financial Officer"),
    (re.compile(r"\bPEO\b"), "Principal Executive Officer"),
    (re.compile(r"(?i)\b(?:Sr|Snr)\b\.?"), "Senior"),
    (re.compile(r"(?i)\bExec\b\.?"), "Executive"),
    (re.compile(r"(?i)\bAsst\b\.?"), "Assistant"),
    (re.compile(r"(?i)\bPres\b\.?"), "President"),
    (re.compile(r"(?i)\bCorp\b\.?"), "Corporate"),
    (re.compile(r"(?i)\bSec(?:'?y|r)\b\.?"), "Secretary"),  # "Corp. Secy.", "Sec'y", "General Counsel & Secr."
    # "Sec" only ("SEC" is the Commission), and not before a number ("Sec. 16" is a section).
    (re.compile(r"\bSec\b\.?(?!\s*\d)"), "Secretary"),
    # "Chairman of the Bd.", "Vice Chairman, BD of Directors"; elsewhere "BD" is business development.
    (re.compile(r"\bBd\b\.?|(?<=\bof the )BD\b|\bBD(?= of Dir)"), "Board"),
    # "Off" only where it ends a post ("Chief Legal & Compliance Off."), never "Sign-Off".
    (re.compile(r"(?<!-)\bOff\b\.?(?=\s*(?:$|[,;&/]|and\b))"), "Officer"),
    (re.compile(r"(?i)\bGen(?:'l)?\b\.?"), "General"),  # "Gen. Counsel", "Gen'l Counsel"
    (re.compile(r"(?i)\bAdmin\b\.?(?=\s+Officer)"), "Administrative"),
    (re.compile(r"(?i)\bAdmin\b\.?"), "Administration"),
    (re.compile(r"(?i)\bTech\b\.?(?=\s+Officer)"), "Technology"),
    (re.compile(r"(?i)\bInfo\b\.?(?=\s+Officer)"), "Information"),
    (re.compile(r"(?i)\bAcct\b\.?(?=\s+Officer)"), "Accounting"),
    (re.compile(r"(?i)\b(?:Ofcr|Offcr|Ofc)\b\.?"), "Officer"),
    (re.compile(r"(?i)\bChf\b\.?"), "Chief"),
    (re.compile(r"(?i)\bDev\b\.?"), "Development"),
    (re.compile(r"(?i)\bOps\b\.?"), "Operations"),
    (re.compile(r"(?i)\bMktg\b\.?"), "Marketing"),
    (re.compile(r"(?i)\bMgmt\b\.?"), "Management"),
    (re.compile(r"(?i)\bMgr\b\.?"), "Manager"),
    (re.compile(r"(?i)\bAcctg\b\.?"), "Accounting"),
    (re.compile(r"(?i)\bFin'l\b\.?"), "Financial"),
    (re.compile(r"(?i)\bComm'l\b\.?"), "Commercial"),
    (re.compile(r"(?i)\bFin\b\.?"), "Finance"),
    (re.compile(r"(?i)\bIntl\b\.?|\bInt'l\b"), "International"),
    (re.compile(r"(?i)\bTreas\b\.?"), "Treasurer"),
    (re.compile(r"(?i)\bGrp\b\.?"), "Group"),
    (re.compile(r"(?i)\bSvcs\b\.?"), "Services"),
    (re.compile(r"(?i)\bStrat\b\.?"), "Strategy"),
    (re.compile(r"(?i)\bDir\b\.?"), "Director"),
)
#: "Secretary of Issuer", "Chairman of the Company and CEO", "Pres/CEO/Dir of Company & Bank":
#: the company is named in the sentence anyway. Only where the post ends (a separator or the
#: end follows), so "President of the Company's bank" stays.
_OF_ISSUER = re.compile(
    r"(?i)\s+of\s+(?:the\s+)?(?:issuer|company|corporation)(?:\s*(?:&|and)\s*(?:the\s+)?bank)?\b\.?"
    r"(?=\s*(?:$|[,;:/&]|-|and\b))")
#: A single letter on each side of "&" or "/" is one abbreviation ("L & D", "U/W"), not two posts.
_LETTER_PAIR = re.compile(r"\b([A-Z])\s*([&/])\s*([A-Z])\b")
#: Where a combined title splits into posts: "EVP: Chief Legal Officer", "SVP, CFO",
#: "President & CEO", "EVP&CFO", "CFO/Treasurer", "SVP-Chief Legal Officer", and a space
#: after "President" before another capitalised word ("EVP Chief Financial Officer", "SVP
#: Operations"). "R&D", "U/W", "AT&T", "Co-Founder" and "President-Elect" don't split. (The
#: abbreviations are already spelled out here: "SVP-..." reads "Senior Vice President-...".)
_POST_SPLIT = re.compile(
    r"(\s*[,;:]\s*"
    r"|(?<!\b[A-Z])\s*/\s*(?![A-Z]\b)"
    r"|\s+&\s+|(?<=[A-Za-z]{2})\s*&\s*(?=[A-Z][A-Za-z])"
    r"|\s+-\s+|(?<=President)-(?=[A-Z])(?!(?:Elect|Designate)\b)"
    r"|\s+and\s+|(?<=President)\s+(?=[A-Z]))")
#: Separators that attach an area to a post ("Vice President, Finance"); they read as a comma.
_AREA_SEPARATORS = {",", ";", ":", "/", "-", ""}
#: A part that names a post rather than an area ("VP, Finance": Finance is an area).
_POST_WORD = re.compile(
    r"(?i)\b(officer|president|chief|chair\w*|director|counsel|secretary|treasurer|controller|comptroller|"
    r"founder|co-founder|partner|trustee|head|principal|manager|ceo|cfo|coo|cto|cio|cao|clo|cmo|chro|cro|"
    r"cco|cso|cdo|cpo|cbo|pfo|pao|evp|svp|vp)\b")
#: Posts only one person holds at a time, which take "the": "the Chief Financial Officer",
#: "the President", "the General Counsel". "Vice President", "Co-CEO", "Group President",
#: "Assistant Secretary" and "Vice Chair" don't start with these words, so they take "a"/"an".
_SINGLE_HOLDER = re.compile(
    r"(?i)^(?:interim\s+|acting\s+)?(?:chief\b|ceo\b|cfo\b|coo\b|cto\b|cio\b|cao\b|clo\b|cmo\b|chro\b|cro\b|"
    r"cco\b|cso\b|cdo\b|cpo\b|cbo\b|president\b|chair|executive\s+chair|general\s+counsel\b|"
    r"(?:corporate\s+)?secretary\b|treasurer\b|(?:corporate\s+)?controller\b|comptroller\b|"
    r"principal\s+(?:executive|financial|accounting|operating)\s+officer\b|lead\s+(?:independent\s+)?director\b)")


def _join_list(items: list[str]) -> str:
    """["A"] -> "A"; ["A", "B"] -> "A and B"; ["A", "B", "C"] -> "A, B and C"."""
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def _indefinite(word: str) -> str:
    """ "a" or "an" by how the word sounds: "an Executive", "a Senior", "a 10% owner", "an SVP"."""
    if not word:
        return "a"
    if word[0].isdigit():
        # Spoken numbers: "an 8", "an 11", "an 18"; "a 10" and the rest take "a".
        return "an" if word.startswith("8") or re.match(r"1[18](?!\d)", word) else "a"
    if len(word) > 1 and word.isupper() and re.fullmatch(r"[A-Z][A-Z&/.]*", word):
        # An acronym read letter by letter: "an MD", "an SVP", "an R&D", but "a VP", "a U.S.".
        return "an" if word[0] in "AEFHILMNORSX" else "a"
    low = word.lower()
    if low.startswith(("uni", "use", "usu", "one", "eu")):
        return "a"  # "a University", "a one-time"
    if low.startswith(("hon", "hour", "heir")):
        return "an"  # "an Honorary"
    return "an" if low[0] in "aeiou" else "a"


def title_in_sentence(title: str, role_type: str) -> tuple[str, str]:
    """The article and the title as they read in "X is <article> <title> of Y".

    `title` is already cleaned (clean_title). "Chief Executive Officer" ->
    ("the", "Chief Executive Officer"); "Director" -> ("a", "Director");
    "EVP: Chief Legal Officer" -> ("the", "Executive Vice President and Chief Legal Officer");
    "Sr. VP" -> ("a", "Senior Vice President"); the bare "Officer" -> ("an", "officer").
    A title that ends in an area gets a closing comma, so the sentence reads
    "is a Senior Vice President, Operations, of Y": "SVP Operations" -> ("a",
    "Senior Vice President, Operations,").
    """
    if title == ROLE_LABELS["officer"]:
        return "an", "officer"
    text = title
    for pattern, spelled in _SPELLED_OUT:
        text = pattern.sub(spelled, text)
    text = _OF_ISSUER.sub("", " ".join(text.split())) or title
    text = re.sub(r"\s*[,;]\s*(?:and|&)\s+", ", ", text)  # a serial comma: "CFO, PAO, and Treasurer"
    text = _LETTER_PAIR.sub(r"\1\2\3", text)  # "Chief L & D Officer" -> "Chief L&D Officer"
    # Split a combined title into its posts; an area ("Finance") stays with the post before it.
    pieces = _POST_SPLIT.split(text)
    posts = [pieces[0].strip()]
    ends_in_area = False
    for sep, piece in zip(pieces[1::2], pieces[2::2], strict=True):
        piece = piece.strip()
        if not piece:
            continue
        if _POST_WORD.search(piece) and _POST_WORD.search(posts[-1]):
            posts.append(piece)
            ends_in_area = False
        elif sep.strip() in _AREA_SEPARATORS:
            posts[-1] += ", " + piece
            ends_in_area = True
        else:
            posts[-1] += sep + piece  # "Research & Development", "Risk and Compliance"
    phrase = _join_list([p for p in posts if p] or [title]) + ("," if ends_in_area else "")
    if any(_SINGLE_HOLDER.match(p) for p in posts):
        return "the", phrase
    return _indefinite(phrase.split(" ")[0].strip(",;:/\"'(")), phrase


# ---------------------------------------------------------------- company names

#: Places EDGAR appends after a slash ("Nifco Inc/Japan", "Troops, Inc. /Cayman Islands/").
_PLACES = r"delaware|cayman|[a-z]+(?:\s+virgin)?\s+islands|bermuda|japan|canada|nevada|maryland|bahamas|luxembourg"
#: EDGAR's code at the end of a name: a state or country ("/DE/", " / MA", "\TX", "/CAN/"),
#: "/NEW/" for a company registered again, "/ADR" for depositary shares, or a bare "/".
#: Not part of the name. "A/S" (a Danish company form), "RE/MAX" and "DATA I/O" are left
#: alone: the code must be 2-3 letters or a place, and follow a space or a name of two words or more.
_EDGAR_TAIL = re.compile(
    r"^(?P<head>.+?)(?P<gap>\s*)[/\\]\s*(?:[a-z]{2,3}|" + _PLACES + r")?\s*[/\\]?\s*$", re.IGNORECASE)

#: Short words that are words, not acronyms, in company names ("One Stop Systems",
#: "Big Sky Industrial", "Fox Factory"). Other words of 1-3 letters stay in capitals
#: ("CME Group", "TG Therapeutics", "UMB Financial"), as do words without a vowel ("PPG", "HDFC").
_SHORT_WORDS = {"ONE", "TWO", "SIX", "TEN", "NEW", "OLD", "BIG", "TOP", "RED", "SUN", "SKY", "SEA", "BAY", "AIR",
                "OIL", "GAS", "CAR", "FOX", "BOX", "KEY", "ART", "ICE", "ACE", "AGE", "ALL", "ARK", "BIO", "DAY",
                "EYE", "FUN", "HUB", "INN", "JET", "LAB", "LAW", "MAN", "MED", "MID", "NET", "NOW", "OAK", "OUR",
                "PAY", "PET", "POP", "PRO", "RAY", "RUN", "TEA", "TOY", "TRI", "VIA", "WAY", "WEB", "WIN", "ZOO",
                "GEN", "GO", "MY", "UP", "INC", "CO", "LTD", "DOW", "END", "SAO", "SAN", "LOS", "RIO", "TAX",
                "CAP", "DOT", "BAR", "FIX", "VAN", "COX", "MAE", "BOY", "ELI", "LEE", "ECK"}
#: Joining words, lower case inside a name ("Bank of America", "Coca Cola Femsa SAB de CV").
_JOINING_WORDS = {"OF", "AND", "THE", "FOR", "TO", "IN", "ON", "AT", "BY", "DE", "DEL", "DA", "DO", "DI", "DU"}
#: Longer abbreviations kept in capitals.
_KEEP_CAPS = {"REIT", "SICAV", "USA"}
_ROMAN = re.compile(r"(?=[IVXLC]{2,}$)C{0,3}(?:XC|XL|L?X{0,3})(?:IX|IV|V?I{0,3})")
_DOTTED = re.compile(r"(?:[A-Z]\.)+[A-Z]?\.?|[A-Z](?:\.[A-Z])+\.?")  # "L.L.C.", "N.V.", "S.A.P.I.", "C.V", "T."

# A word that is one of the company's own tickers is kept in capitals when it can't be said
# as a word: "ACNB", "VTEX", "CBRE", "GATX", "ASML" are letters, while "JACK", "POOL",
# "EBAY", "ETSY" and "RIO" are words even though they are tickers too. "Can't be said"
# means an English word couldn't start or end with its consonants, or it has three in a
# row mid-word. Y counts as a vowel. Brand names spelled in capitals that read as words
# ("IQVIA", "ONEOK", "CACI", "IMAX") are title-cased: a known limit.
_VOWELS = "AEIOUY"
#: Consonants an English word can start with.
_ONSETS = {"BL", "BR", "CH", "CL", "CR", "DR", "DW", "FL", "FR", "GH", "GL", "GN", "GR", "KL", "KN", "KR", "PH",
           "PL", "PR", "PS", "SC", "SH", "SK", "SL", "SM", "SN", "SP", "ST", "SW", "TH", "TR", "TW", "WH", "WR",
           "SCH", "SCR", "SHR", "SPL", "SPR", "STR", "THR", "CHR", "PHR"}
#: Consonants an English word can end with ("JACK", "WYNN", "BANC", "BOSS", "ARTS").
_CODAS = {"BB", "CH", "CK", "CT", "DD", "DS", "FF", "FT", "GG", "GH", "GS", "KS", "LB", "LD", "LF", "LK", "LL",
          "LM", "LP", "LS", "LT", "MB", "MP", "MS", "NC", "ND", "NG", "NK", "NN", "NS", "NT", "PH", "PS", "PT",
          "RB", "RC", "RD", "RF", "RG", "RK", "RL", "RM", "RN", "RP", "RR", "RS", "RT", "SH", "SK", "SP", "SS",
          "ST", "TH", "TS", "TT", "WK", "WL", "WN", "WS", "XT", "ZZ", "CKS", "CTS", "GHT", "LDS", "LTS", "NCH",
          "NDS", "NGS", "NKS", "NTS", "RCH", "RDS", "RKS", "RST", "RTH", "RTS", "TCH"}


def _said_as_letters(word: str) -> bool:
    """True when an all-caps word can't be read as an English word, so it is an acronym:
    "ACNB" (CNB), "VTEX" (no word starts VT), "WAFD" (none ends FD), "AAON". See above."""
    head = re.match(f"[^{_VOWELS}]*", word)
    tail = re.search(f"[^{_VOWELS}]*$", word)
    if head is None or tail is None or len(head.group()) == len(word):
        return True  # no vowel at all
    lead, end = head.group(), tail.group()
    middle = word[len(lead):len(word) - len(end)]
    return ((len(lead) > 1 and lead not in _ONSETS) or (len(end) > 1 and end not in _CODAS)
            or re.search(f"[^{_VOWELS}]{{3}}", middle) is not None or word.startswith("AA"))


def _cap_part(part: str, first: bool, tickers: Collection[str] = (), in_compound: bool = False) -> str:
    """One all-caps word (no spaces, hyphens or slashes) in title case, by the rules above.

    `tickers` are the company's own tickers, upper case. `in_compound` is set for a part of
    a hyphenated word that has a word in it ("BIO-RAD", "CAL-MAINE"): there a short part
    that can be said as a word is one too ("Bio-Rad", "Cal-Maine", "Miracle-Gro"), while
    "AMT-Free" keeps AMT and "AGI-T" (no word in it) stays as it is.
    """
    letters = part.rstrip(".")
    dot = part[len(letters):]
    if not letters or not letters.isupper() or any(ch.isdigit() for ch in letters):
        return part  # "3M", punctuation
    if letters.endswith(".COM") and len(letters) > 4:
        return _cap_part(letters[:-4], first, tickers) + ".com" + dot  # "YXT.COM" -> "YXT.com"
    if "&" in letters:
        # "AT&T", "J&J", "M&T" stay as they are; a long "X&Y" is two words.
        if len(letters) <= 5:
            return part
        return "&".join(_cap_part(p, first, tickers) for p in letters.split("&")) + dot
    if _DOTTED.fullmatch(part):
        return part
    if letters in tickers and _said_as_letters(letters):
        return part  # the company's ticker, read letter by letter: "ACNB", "CBRE"
    if letters in _JOINING_WORDS and not first:
        return letters.lower() + dot
    if letters in _SHORT_WORDS or letters in _JOINING_WORDS:
        return letters.capitalize() + dot
    if letters in _KEEP_CAPS or _ROMAN.fullmatch(letters) or not re.search(f"[{_VOWELS}]", letters):
        return part  # an abbreviation or Roman numeral, or no vowel: "REIT", "XXII", "PPG"
    if len(letters) <= 3 and (not in_compound or _said_as_letters(letters)):
        return part  # an acronym, initial or legal form: "PNC", "LLC"
    if letters.startswith("MC") and len(letters) > 3:
        return "Mc" + letters[2:].capitalize() + dot  # "McKinnon", "McLennan"
    if re.fullmatch(r"[A-Z]'[A-Z]{2,}", letters):
        return letters[:2] + letters[2:].capitalize() + dot  # "O'Reilly"
    return letters.capitalize() + dot  # "Bancorp", "Dillard's"


def _title_case_company(name: str, tickers: Collection[str] = ()) -> str:
    """Title-case a company name EDGAR stores in capitals: "FIFTH THIRD BANCORP" ->
    "Fifth Third Bancorp", "AT&T INC." -> "AT&T Inc.", "BANK OF AMERICA CORP" -> "Bank of America Corp".
    `tickers` are the company's own tickers, upper case (see _said_as_letters)."""
    tokens = name.split(" ")
    # EDGAR drops apostrophes: "O REILLY AUTOMOTIVE INC" is O'Reilly Automotive.
    if len(tokens) > 2 and tokens[0] == "O" and len(tokens[1]) > 2:
        tokens = [f"O'{tokens[1]}", *tokens[2:]]
    # A trailing state code with no slash: "US BANCORP DE" is US Bancorp (of Delaware).
    if len(tokens) > 2 and tokens[-1] == "DE":
        tokens = tokens[:-1]
    out = []
    for i, token in enumerate(tokens):
        core = token.strip(",;:()\"")
        start = token.find(core) if core else 0
        lead, tail = token[:start], token[start + len(core):]
        words = re.split(r"([-/])", core)  # "HYSTER-YALE", "M/I"; the separators land at odd positions
        # A hyphenated word with a word in it (4 letters or more, or a short word such as BIO).
        compound = "-" in core and any(len(w) >= 4 or w in _SHORT_WORDS for w in words[::2])
        cased = "".join(w if j % 2 else _cap_part(w, i == 0 and j == 0, tickers, compound)
                        for j, w in enumerate(words))
        out.append(lead + cased + tail)
    return " ".join(out)


#: Legal forms, which some filers type in mixed case after a name in capitals
#: ("HOOKER FURNISHINGS Corp").
_LEGAL_FORM = re.compile(r"(?i)\b(?:corp|inc|co|ltd|llc|plc|lp)\b\.?")


def company_name(name: str, tickers: Collection[str] = ()) -> str:
    """A company name as it reads in a sentence or a label.

    EDGAR's trailing codes go ("AMETEK INC/" -> "AMETEK INC", "Rivian Automotive,
    Inc. / DE" -> "Rivian Automotive, Inc.") and a name stored in capitals is
    title-cased ("FIFTH THIRD BANCORP" -> "Fifth Third Bancorp"), also when only the
    legal form is in mixed case ("HOOKER FURNISHINGS Corp"). Names already in mixed
    case ("NIKE, Inc.", "Apple Inc.") keep the company's own capitals.

    `tickers` are the company's own tickers: a word that is one of them and can't be
    read as a word stays in capitals ("ACNB CORP" -> "ACNB Corp", not "Acnb Corp").
    """
    cleaned = " ".join(name.split()).strip()
    while (match := _EDGAR_TAIL.match(cleaned)) and (match["gap"] or " " in match["head"]):
        cleaned = match["head"].rstrip()
    # Whether the name is in capitals is decided without its legal form. A single word in
    # capitals before a mixed-case form is a brand the company spells that way ("NIKE, Inc.").
    core = _LEGAL_FORM.sub("", cleaned)
    in_caps = any(ch.isalpha() for ch in cleaned) and cleaned == cleaned.upper()
    core_in_caps = any(ch.isalpha() for ch in core) and core == core.upper() and len(core.split()) > 1
    if in_caps or core_in_caps:
        cleaned = _title_case_company(cleaned, {t.upper() for t in tickers if t})
    return cleaned


# ---------------------------------------------------------------- Rule 10b5-1 plans

def plan_sentence(under_plan: int, answered: int, sales: int) -> str | None:
    """How many open-market sales were made under a Rule 10b5-1 plan, in words.

    A Rule 10b5-1 plan is a trading plan set up in advance. Form 4 has had a box
    saying whether a trade was made under one since April 2023; older filings
    don't say. `answered` counts the sales whose filing says either way,
    `under_plan` those that say yes, `sales` all of them. Counts, not a bare
    percentage: "None of the 4 open-market sales were made under a Rule 10b5-1
    plan." rather than "0% of reported sales ...".
    """
    if answered <= 0 or sales <= 0:
        return None
    verb = "was" if under_plan == 1 else "were"
    pct = f"{round(100 * under_plan / answered, 1):g}%"
    if answered < sales:
        # Some sales don't say: count only the ones that do, and say how many those are.
        have = "has a filing that says" if answered == 1 else "have filings that say"
        lead = f"Of the {sales} open-market sales, {answered} {have} whether a Rule 10b5-1 plan was used: "
        if answered == 1:
            outcome = "it was made under one." if under_plan else "it was not made under one."
        elif under_plan == 0:
            outcome = "neither was made under one." if answered == 2 else "none of them were made under one."
        elif under_plan == answered:
            outcome = "both were made under one." if answered == 2 else "all of them were made under one."
        else:
            outcome = f"{under_plan} of them ({pct}) {verb} made under one."
        return lead + outcome
    if sales == 1:
        return f"The open-market sale was{'' if under_plan else ' not'} made under a Rule 10b5-1 plan."
    if under_plan == 0:
        if sales == 2:
            return "Neither open-market sale was made under a Rule 10b5-1 plan."
        return f"None of the {sales} open-market sales were made under a Rule 10b5-1 plan."
    if under_plan == sales:
        both = "Both" if sales == 2 else f"All {sales}"
        return f"{both} open-market sales were made under Rule 10b5-1 plans."
    plans = "a Rule 10b5-1 plan" if under_plan == 1 else "Rule 10b5-1 plans"
    return f"Of the {sales} open-market sales, {under_plan} ({pct}) {verb} made under {plans}."


def form144_relationships(values: Sequence[str] | None) -> list[str]:
    """A Form 144 seller's relationships as shown: "OFFICER" becomes "Officer", and the
    placeholder "See Remarks" becomes the generic "Other insider" (the notice's remarks say more)."""
    out: list[str] = []
    for value in values or []:
        text_ = clean_title(value, "other_insider")
        if text_ and text_ not in out:
            out.append(text_)
    return out
