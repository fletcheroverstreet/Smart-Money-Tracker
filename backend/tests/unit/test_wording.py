"""How titles, company names and plan use read on the site (smt.api.wording).

The cases are real: titles and company names as filers and EDGAR store them.
"""

from __future__ import annotations

from datetime import date

import pytest

from smt.api.routers.insiders import _summary
from smt.api.schemas import EntityLink, PersonStats, RoleOut
from smt.api.wording import (
    clean_title,
    company_name,
    plan_sentence,
    relationship_role,
    role_line,
    title_in_sentence,
)


def _sentence(title: str | None, role_type: str = "officer") -> str:
    """The title as it reads in the profile summary's first sentence."""
    article, text = title_in_sentence(clean_title(title, role_type), role_type)
    return f"{article} {text}"


@pytest.mark.parametrize(("title", "reads"), [
    # Posts one person holds take "the".
    ("Chief Executive Officer", "the Chief Executive Officer"),
    ("CEO", "the CEO"),
    ("President & CEO", "the President and CEO"),
    ("General Counsel and Secretary", "the General Counsel and Secretary"),
    ("Interim CFO", "the Interim CFO"),
    ("Lead Independent Director", "the Lead Independent Director"),
    # Posts several people hold take "a" or "an".
    ("Executive Vice President", "an Executive Vice President"),
    ("Vice President", "a Vice President"),
    ("Co-Chief Executive Officer", "a Co-Chief Executive Officer"),
    ("Group President", "a Group President"),
    ("ASSISTANT SECRETARY", "an Assistant Secretary"),
    ("Vice Chair", "a Vice Chair"),
    # A post with a single-holder post in it takes "the".
    ("EVP, Chief Financial Officer", "the Executive Vice President and Chief Financial Officer"),
    ("SEVP & Chief Financial Officer", "the Senior Executive Vice President and Chief Financial Officer"),
    ("EVP Chief Financial Officer", "the Executive Vice President and Chief Financial Officer"),
    ("EVP/CFO", "the Executive Vice President and CFO"),
    ("CFO, PAO, and Treasurer", "the CFO, Principal Accounting Officer and Treasurer"),
    # Abbreviations and punctuation.
    ("EVP, Gen. Counsel & Corp. Sec.", "the Executive Vice President, General Counsel and Corporate Secretary"),
    ("SVP, Gen Counsel, & Corp Sec", "the Senior Vice President, General Counsel and Corporate Secretary"),
    ("EVP & Chief Admin. Officer", "the Executive Vice President and Chief Administrative Officer"),
    ("Sr. VP", "a Senior Vice President"),
    ("SEVP", "a Senior Executive Vice President"),
    ("CCO", "the CCO"),
    ("Secretary of Issuer", "the Secretary"),
    ("Secy of Issuer", "the Secretary"),
    ("Chairman of the Company and CEO", "the Chairman and CEO"),
    ("Pres/CEO/Dir of Company & Bank", "the President, CEO and Director"),
    ("Chief Legal & Compliance Off.", "the Chief Legal and Compliance Officer"),
    ("CLO and Corporate Secy.", "the CLO and Corporate Secretary"),
    ("Chief Legal Off. & Corp. Sec'y", "the Chief Legal Officer and Corporate Secretary"),
    ("Asst Secy", "an Assistant Secretary"),
    ("GEN'L COUNSEL, CCO & SECY", "the General Counsel, CCO and Secretary"),
    ("Chief Credit & Fin'l Risk Off.", "the Chief Credit and Financial Risk Officer"),
    ("Chairman of the Bd, Pres & CEO", "the Chairman of the Board, President and CEO"),
    ("R&D DIRECTOR", "an R&D Director"),
    # "BD" is business development unless it is "the BD" or "BD of Directors".
    ("VP, BD & Global Marketing", "a Vice President, BD & Global Marketing,"),
    # A semicolon before "and" is a serial comma too.
    ("Sr. EVP; COO; and Secretary", "the Senior Executive Vice President, COO and Secretary"),
    # Posts joined by a hyphen or an "&" with no spaces.
    ("SVP-Chief Accounting Officer", "the Senior Vice President and Chief Accounting Officer"),
    ("SVP-CHRO", "the Senior Vice President and CHRO"),
    ("VP-Exploration", "a Vice President, Exploration,"),
    ("EVP&CFO", "the Executive Vice President and CFO"),
    ("EVP& CFO", "the Executive Vice President and CFO"),
    ("VICE CHAIR,CHIEF LENDING OFC", "the Vice Chair and Chief Lending Officer"),
    # Hyphens and single letters that are part of one post don't split it.
    ("President-Elect, Color Group", "the President-Elect, Color Group,"),
    ("Co-CEO", "a Co-CEO"),
    ("EVP Chief L & D Officer", "the Executive Vice President and Chief L&D Officer"),
    ("Co-Founder, Chief U/W Officer", "the Co-Founder and Chief U/W Officer"),
    # Typed in lower case: cased like an ALL-CAPS title, so the abbreviations are found.
    ("chairman and ceo", "the Chairman and CEO"),
    ("evp, cfo", "the Executive Vice President and CFO"),
    # An area after a post is set off by commas: "is a Senior Vice President, Operations, of Y".
    ("SVP Operations", "a Senior Vice President, Operations,"),
    ("EVP, Human Resources", "an Executive Vice President, Human Resources,"),
    ("VP, Finance and Treasurer", "the Vice President, Finance and Treasurer"),
    # A title that points elsewhere reads as the role.
    ("See Remarks", "an officer"),
    ("Please See Remarks", "an officer"),
    ("See Remarks below.", "an officer"),
    ('"Officer" Under Sec. 16 Rules', "an officer"),
    ("Officer w/o Specific Desig", "an officer"),
    (None, "an officer"),
])
def test_officer_titles_in_a_sentence(title: str | None, reads: str) -> None:
    assert _sentence(title) == reads


def test_roles_without_a_title() -> None:
    assert _sentence(None, "director") == "a Director"
    assert _sentence(None, "ten_pct_owner") == "a 10% owner"


def test_all_caps_titles_keep_their_abbreviations() -> None:
    assert clean_title("CHIEF FINANCIAL OFFICER", "officer") == "Chief Financial Officer"
    assert clean_title("EVP/CHIEF FINANCIAL OFFICER", "officer") == "EVP/Chief Financial Officer"
    assert clean_title("CO-CHIEF EXECUTIVE OFFICER", "officer") == "Co-Chief Executive Officer"
    assert clean_title("CSO", "officer") == "CSO"
    assert clean_title("VP, CONTROLLER & CAO", "officer") == "VP, Controller & CAO"
    assert clean_title("Chief Executive Officer", "officer") == "Chief Executive Officer"  # mixed case as filed
    # Punctuation with no space around it still separates words.
    assert clean_title("VICE CHAIR,CHIEF LENDING OFC", "officer") == "Vice Chair, Chief Lending Ofc"
    assert clean_title("VP,Controller & Chf Acct Off", "officer") == "VP, Controller & Chf Acct Off"
    assert clean_title("EVP&CFO", "officer") == "EVP&CFO"
    assert clean_title("R&D DIRECTOR", "officer") == "R&D Director"


def test_lower_case_titles_are_cased() -> None:
    assert clean_title("chairman and ceo", "officer") == "Chairman and CEO"
    assert clean_title("evp, cfo", "officer") == "EVP, CFO"
    assert clean_title("vice president", "officer") == "Vice President"


@pytest.mark.parametrize(("stored", "reads"), [
    ("FIFTH THIRD BANCORP", "Fifth Third Bancorp"),
    ("BANK OF AMERICA CORP /DE/", "Bank of America Corp"),
    ("AT&T INC.", "AT&T Inc."),
    ("AMETEK INC/", "Ametek Inc"),
    ("Rivian Automotive, Inc. / DE", "Rivian Automotive, Inc."),
    ("RUSH ENTERPRISES INC \\TX", "Rush Enterprises Inc"),
    ("Columbus Acquisition Corp/Cayman Islands", "Columbus Acquisition Corp"),
    ("US BANCORP \\DE\\", "US Bancorp"),
    ("PNC FINANCIAL SERVICES GROUP, INC.", "PNC Financial Services Group, Inc."),
    ("MARSH & MCLENNAN COMPANIES, INC.", "Marsh & McLennan Companies, Inc."),
    ("O REILLY AUTOMOTIVE INC", "O'Reilly Automotive Inc"),
    ("HYSTER-YALE MATERIALS HANDLING, INC.", "Hyster-Yale Materials Handling, Inc."),
    ("JOHN HANCOCK PREFERRED INCOME FUND II", "John Hancock Preferred Income Fund II"),
    ("AMERICA MOVIL SAB DE CV/", "America Movil SAB de CV"),
    ("ICAHN ENTERPRISES L.P.", "Icahn Enterprises L.P."),
    ("LANDS' END, INC.", "Lands' End, Inc."),
    ("ELI LILLY & Co", "Eli Lilly & Co"),
    ("1606 CORP.", "1606 Corp."),
    ("YXT.COM GROUP HOLDING Ltd", "YXT.com Group Holding Ltd"),
    # Capitals with a legal form typed in mixed case.
    ("HOOKER FURNISHINGS Corp", "Hooker Furnishings Corp"),
    # Hyphenated words: short parts that are words are cased as words.
    ("BIO-RAD LABORATORIES, INC.", "Bio-Rad Laboratories, Inc."),
    ("CAL-MAINE FOODS INC", "Cal-Maine Foods Inc"),
    ("SCOTTS MIRACLE-GRO CO", "Scotts Miracle-Gro Co"),
    ("DTF TAX-FREE INCOME 2028 TERM FUND INC", "DTF Tax-Free Income 2028 Term Fund Inc"),
    ("NUVEEN AMT-FREE MUNICIPAL CREDIT INCOME FUND", "Nuveen AMT-Free Municipal Credit Income Fund"),
    ("AGI-T, L.P.", "AGI-T, L.P."),
    ("BAR HARBOR BANKSHARES", "Bar Harbor Bankshares"),
    # Slashes that are part of the name stay.
    ("DATA I/O CORP", "Data I/O Corp"),
    ("M/I HOMES, INC.", "M/I Homes, Inc."),
    ("Novo Nordisk A/S", "Novo Nordisk A/S"),
    ("RE/MAX Holdings, Inc.", "RE/MAX Holdings, Inc."),
    # A name in mixed case keeps the company's own capitals.
    ("NIKE, Inc.", "NIKE, Inc."),
    ("Apple Inc.", "Apple Inc."),
])
def test_company_names(stored: str, reads: str) -> None:
    assert company_name(stored) == reads


@pytest.mark.parametrize(("stored", "tickers", "reads"), [
    # The company's own ticker, which can't be read as a word, stays in capitals.
    ("ACNB CORP", ["ACNB"], "ACNB Corp"),
    ("VTEX", ["VTEX"], "VTEX"),
    ("CBRE GROUP, INC.", ["CBRE"], "CBRE Group, Inc."),
    ("GATX CORP", ["GATX"], "GATX Corp"),
    ("AAON, INC.", ["AAON"], "AAON, Inc."),
    ("ASML HOLDING NV", ["ASML"], "ASML Holding NV"),
    # A ticker that reads as a word is cased as a word.
    ("JACK IN THE BOX INC", ["JACK"], "Jack in the Box Inc"),
    ("POOL CORP", ["POOL"], "Pool Corp"),
    ("EBAY INC", ["EBAY"], "Ebay Inc"),
    ("RIO TINTO PLC", ["RIO"], "Rio Tinto PLC"),
    ("WYNN RESORTS LTD", ["WYNN"], "Wynn Resorts Ltd"),
    ("BANC OF CALIFORNIA, INC.", ["BANC"], "Banc of California, Inc."),
    ("BIO-RAD LABORATORIES, INC.", ["BIO", "BIO.B"], "Bio-Rad Laboratories, Inc."),
    # Without its tickers an acronym can't be told from a word.
    ("ACNB CORP", [], "Acnb Corp"),
])
def test_company_names_keep_their_own_ticker(stored: str, tickers: list[str], reads: str) -> None:
    assert company_name(stored, tickers) == reads


@pytest.mark.parametrize(("counts", "reads"), [
    ((0, 4, 4), "None of the 4 open-market sales were made under a Rule 10b5-1 plan."),
    ((4, 4, 4), "All 4 open-market sales were made under Rule 10b5-1 plans."),
    ((2, 2, 2), "Both open-market sales were made under Rule 10b5-1 plans."),
    ((0, 2, 2), "Neither open-market sale was made under a Rule 10b5-1 plan."),
    ((1, 1, 1), "The open-market sale was made under a Rule 10b5-1 plan."),
    ((0, 1, 1), "The open-market sale was not made under a Rule 10b5-1 plan."),
    ((3, 7, 7), "Of the 7 open-market sales, 3 (42.9%) were made under Rule 10b5-1 plans."),
    ((1, 4, 4), "Of the 4 open-market sales, 1 (25%) was made under a Rule 10b5-1 plan."),
    # Filings from before April 2023 have no plan box: only the sales that say are counted.
    ((0, 5, 7), "Of the 7 open-market sales, 5 have filings that say whether a Rule 10b5-1 plan was used: "
                "none of them were made under one."),
    ((2, 5, 7), "Of the 7 open-market sales, 5 have filings that say whether a Rule 10b5-1 plan was used: "
                "2 of them (40%) were made under one."),
    ((1, 1, 3), "Of the 3 open-market sales, 1 has a filing that says whether a Rule 10b5-1 plan was used: "
                "it was made under one."),
    ((0, 0, 3), None),
])
def test_plan_use_in_words(counts: tuple[int, int, int], reads: str | None) -> None:
    assert plan_sentence(*counts) == reads


def test_role_labels() -> None:
    assert role_line(None, "director", "SNOWFLAKE INC.") == "Director, Snowflake Inc."
    assert role_line("Chief Executive Officer", "officer", "Apple Inc.") == "Chief Executive Officer, Apple Inc."
    assert role_line("See Remarks", "officer", None) == "Officer"
    assert role_line(None, None, "Apple Inc.") is None
    assert relationship_role({"officer": True, "officer_title": "SEE REMARKS", "director": True}) == "Officer, Director"
    assert relationship_role({"ten_pct_owner": True}) == "10% owner"
    assert relationship_role(None) is None
    # An "Other" relationship reads as the filer describes it.
    assert relationship_role({"other": True, "other_text": "Former 10% Owner"}) == "Former 10% Owner"
    assert relationship_role({"director": True, "other": True}) == "Director, Other insider"
    assert role_line(None, "director", "ACNB CORP", ["ACNB"]) == "Director, ACNB Corp"


def _stats(sells: int) -> PersonStats:
    return PersonStats(trades=sells, open_market_buys=0, open_market_sells=sells, buy_value=None, sell_value=None,
                       first_filed=date(2026, 9, 1), last_filed=date(2026, 9, 1), median_delay_days=None,
                       late_filings=0, sales_under_10b5_1_pct=None)


def _role(role_type: str, title: str, org: str) -> RoleOut:
    return RoleOut(organization=EntityLink(name=company_name(org), slug="x-1", kind="company"),
                   role_type=role_type, title=clean_title(title, role_type), start_date=None,
                   last_seen_on=date(2026, 9, 1))


def test_summary_sentences() -> None:
    cook = _summary("Tim Cook", [_role("officer", "Chief Executive Officer", "Apple Inc.")], _stats(4),
                    sales_under_plan=4, sales_answered=4)
    assert cook == ("Tim Cook is the Chief Executive Officer of Apple Inc. 4 reported transactions on file, "
                    "including 4 open-market sales. All 4 open-market sales were made under Rule 10b5-1 plans.")
    feiger = _summary("Mitchell Stuart Feiger", [_role("director", "", "FIFTH THIRD BANCORP")], _stats(3),
                      sales_under_plan=0, sales_answered=3)
    assert feiger.startswith("Mitchell Stuart Feiger is a Director of Fifth Third Bancorp. ")
    assert feiger.endswith("None of the 3 open-market sales were made under a Rule 10b5-1 plan.")
    other = _summary("A Trust", [_role("other_insider", "Member of 13(d) group", "Eikon Therapeutics, Inc.")],
                     _stats(0))
    assert other == "A Trust is an insider of Eikon Therapeutics, Inc. (Member of 13(d) group)."
    area = _summary("J Doe", [_role("officer", "SVP, Operations", "ACME CORP")], _stats(0))
    assert area == "J Doe is a Senior Vice President, Operations, of Acme Corp."


def test_a_director_title_isnt_said_twice() -> None:
    from smt.api.wording import relationship_role

    rel = {"officer": True, "officer_title": "Director, President and CEO", "director": True}
    assert relationship_role(rel) == "Director, President and CEO"
    assert relationship_role({"officer": True, "officer_title": "Director", "director": True}) == "Director"
    assert relationship_role({"officer": True, "officer_title": "CFO", "director": True}) == "CFO, Director"
    # Listed as one of the roles, in any position or form: said once.
    for title in ("President, CEO & Director", "Chairman and Director", "Lead Independent Director",
                  "CEO/Director"):
        said = relationship_role({"officer": True, "officer_title": title, "director": True})
        assert said is not None, title
        assert said.lower().count("director") == 1, (title, said)
    # A job with the word in it isn't the board seat: the ticked director box is still said.
    for title, said in (("Director of Investor Relations", "Director of Investor Relations, Director"),
                        ("Managing Director", "Managing Director, Director"),
                        ("VP, Director of Sales", "VP, Director of Sales, Director")):
        assert relationship_role({"officer": True, "officer_title": title, "director": True}) == said, title


def test_form_144_relationships_read_plainly() -> None:
    from smt.api.wording import form144_relationships

    assert form144_relationships(["OFFICER", "Officer", "See Remarks", "10% Stockholder"]) == \
        ["Officer", "Other insider", "10% Stockholder"]
    assert form144_relationships(None) == []
