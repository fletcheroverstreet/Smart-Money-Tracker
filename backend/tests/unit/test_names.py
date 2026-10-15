"""Person vs organization, and display names (smt.domain.names). Cases are real EDGAR names."""

from __future__ import annotations

import pytest

from smt.domain.names import NameFrequencies, looks_like_organization, person_display_and_sort
from smt.ingest.entities import identity_from_name, is_placeholder_ticker, looks_like_ticker


@pytest.mark.parametrize("name", [
    "DE LA VEGA RAFAEL",      # 'DE' starts a surname, not a Delaware suffix
    "ERROL B DE SOUZA",
    "Colgrove John",
    "Cook Timothy D",
])
def test_people_are_not_organizations(name: str) -> None:
    assert not looks_like_organization(name)


@pytest.mark.parametrize("name", [
    "Siemens AG",
    "Banco Santander, S.A.",
    "Silver Lake Partners V DE (AIV), L.P.",
    "JPMorgan Chase Bank, N.A.",
    "OMNADORA CAPITAL LLC",
    "THE COLGROVE FAM CHARITABLE REM TRU/A DTD 04/05/2024",
    "Carlyle GP",
])
def test_organizations(name: str) -> None:
    assert looks_like_organization(name)


@pytest.mark.parametrize(("edgar", "display", "sort"), [
    ("COOK TIMOTHY D", "Timothy D Cook", "Cook, Timothy D"),
    ("SINGER DAVID V", "David V Singer", "Singer, David V"),          # a lone V is a middle initial
    ("ZHU RUI (nmn)", "Rui Zhu", "Zhu, Rui"),                         # "no middle name" marker dropped
    ("DIORIO CHRIS PH. D.", "Chris Diorio PhD", "Diorio, Chris PhD"),  # degree moved to the end
    ("SMITH JOHN JR", "John Smith Jr.", "Smith, John Jr."),
    ("SMITH JOHN A III", "John A Smith III", "Smith, John A III"),
    ("Li Bin (William)", "Bin (William) Li", "Li, Bin (William)"),
    ("DE LA VEGA RAFAEL", "Rafael De La Vega", "De La Vega, Rafael"),   # particles keep the surname whole
    ("VAN DER BERG JOHN A", "John A Van Der Berg", "Van Der Berg, John A"),
])
def test_person_display_names(edgar: str, display: str, sort: str) -> None:
    assert person_display_and_sort(edgar) == (display, sort)


@pytest.mark.parametrize("ticker", ["NA", "N/A", "[N/A]", "[NONE]", "[NONE", "(none)", "NONE.", "-", "Not Applicable", None])
def test_placeholder_tickers(ticker: str | None) -> None:
    assert is_placeholder_ticker(ticker)


@pytest.mark.parametrize("ticker", ["AAPL", "BRK.B", "ALF A", "NAT", "NONO"])
def test_real_tickers_are_not_placeholders(ticker: str) -> None:
    assert not is_placeholder_ticker(ticker)


@pytest.mark.parametrize(("edgar", "display", "sort"), [
    # EDGAR drops apostrophes: a lone O or D belongs to the surname.
    ("O HANLEY RONALD P", "Ronald P O'Hanley", "O'Hanley, Ronald P"),
    ("D AGOSTINO ROBERT P", "Robert P D'Agostino", "D'Agostino, Robert P"),
    # A stray trailing comma.
    ("Greene Kimberly S,", "Kimberly S Greene", "Greene, Kimberly S"),
    # Portuguese compound surnames, and the particle EDGAR moves to the end. A particle the filer
    # typed in lower case stays lower case; in capitals there is no telling.
    ("Batista de Lima Filho Pedro", "Pedro Batista de Lima Filho", "Batista De Lima Filho, Pedro"),
    ("AGUIAR LUCAS ARAUJO DE", "Lucas Araujo De Aguiar", "Aguiar, Lucas Araujo De"),
    ("SILVA DA COSTA JOAO", "Joao Silva Da Costa", "Silva Da Costa, Joao"),
    # "Holding" is a surname too (First Citizens' chairman).
    ("HOLDING FRANK B JR", "Frank B Holding Jr.", "Holding, Frank B Jr."),
    # A middle initial E is not a particle.
    ("SMITH E JOHN PAUL", "E John Paul Smith", "Smith, E John Paul"),
])
def test_edgar_name_forms(edgar: str, display: str, sort: str) -> None:
    assert person_display_and_sort(edgar) == (display, sort)


@pytest.mark.parametrize("name", ["HOLDING FRANK B JR", "Holding George Edward Bell", "AGUIAR LUCAS ARAUJO DE",
                                  "Souza Aloisio Macario Ferreira de", "Ai Na", "Hasbrook John S A",
                                  "TEUNISSEN ERNST 02494"])
def test_people_that_used_to_look_like_companies(name: str) -> None:
    assert not looks_like_organization(name)


@pytest.mark.parametrize("name", ["Grupo Bimbo, S.A.B. de C.V.", "XYZ S. de R.L. de C.V.", "ABC HOLDING",
                                  "First Citizens Holding Co", "Citibank, N.A.", "ACME CORP, DE"])
def test_company_forms_still_count(name: str) -> None:
    assert looks_like_organization(name)


@pytest.mark.parametrize(("edgar", "display"), [
    ("PEREZ DE LA MESA MANUEL J", "Manuel J Perez De La Mesa"),
    ("Elizondo de la Garza Oscar Balmore", "Oscar Balmore Elizondo de la Garza"),
    ("de Bourdoncle de Saint Salvy Anne-Francois", "Anne-Francois de Bourdoncle de Saint Salvy"),
])
def test_stacked_particles_stay_in_the_surname(edgar: str, display: str) -> None:
    assert person_display_and_sort(edgar)[0] == display


@pytest.mark.parametrize(("edgar", "signatures", "display", "sort"), [
    # Two surnames: only the owner's own signature shows where the surname ends.
    ("Silva de Sousa Oliveira Manuel Lino", ("/s/ Manuel Lino Silva de Sousa Oliveira",),
     "Manuel Lino Silva de Sousa Oliveira", "Silva De Sousa Oliveira, Manuel Lino"),
    ("Paz Montoya Esther America", ("Esther America Paz Montoya",), "Esther America Paz Montoya",
     "Paz Montoya, Esther America"),
    # An attorney-in-fact's own name is ignored; the owner named after "for" counts.
    ("Paz Montoya Esther America", ("/s/ Jane Roe, attorney-in-fact",),
     "Montoya Esther America Paz", "Paz, Montoya Esther America"),
    ("Paz Montoya Esther America", ("/s/ Jane Roe, attorney-in-fact for Esther America Paz Montoya",),
     "Esther America Paz Montoya", "Paz Montoya, Esther America"),
    # All capitals: the split is used, the spelling stays EDGAR's (title-cased).
    ("PILCHER CIAFONE KATHERINE", ("/S/ KATHERINE PILCHER CIAFONE",), "Katherine Pilcher Ciafone",
     "Pilcher Ciafone, Katherine"),
    # Suffixes and dropped apostrophes don't stop a match.
    ("HOLDING FRANK B JR", ("/s/ Frank B. Holding, Jr.",), "Frank B Holding Jr.", "Holding, Frank B Jr."),
    ("O HANLEY RONALD P", ("/s/ Ronald P. O'Hanley",), "Ronald P O'Hanley", "O'Hanley, Ronald P"),
    # Only a true rotation counts: a signature in EDGAR's own order, or naming other given
    # names, tells nothing (real filings do both; trusting them reversed correct names).
    ("MARTIN ERIC J", ("/s/ MARTIN ERIC J",), "Eric J Martin", "Martin, Eric J"),
    ("MARTIN ERIC J", ("Jane Roe for MARTIN ERIC J",), "Eric J Martin", "Martin, Eric J"),
    ("DENG FENG", ("/s/ Deng Feng",), "Feng Deng", "Deng, Feng"),
    ("MOSICH NICK", ("By: Derrick Walsh For: Nicholas Mosich",), "Nick Mosich", "Mosich, Nick"),
    # Capitals title case can't guess come from the signature; initials keep EDGAR's form.
    ("DESOUZA ERROL B", ("/s/ Errol B. DeSouza",), "Errol B DeSouza", "DeSouza, Errol B"),
    ("Carter Robert B", ("/s/ (Robert B. Carter)",), "Robert B Carter", "Carter, Robert B"),
    ("Silva de Sousa Oliveira Manuel Lino", ("/s/ Ana Roe, attorney-in-fact for Manuel Lino Silva de Sousa Oliveira",),
     "Manuel Lino Silva de Sousa Oliveira", "Silva De Sousa Oliveira, Manuel Lino"),
])
def test_signatures_decide_the_surname(edgar: str, signatures: tuple[str, ...], display: str, sort: str) -> None:
    assert person_display_and_sort(edgar, signatures) == (display, sort)


@pytest.mark.parametrize("name", [
    "Akastor AS", "Mercury HoldCo AS", "INVESTOR AB", "Evaxion A/S", "HB&Q NV (HB&Q COMPARTMENT III)",
    "CS Diagnostics Pharma GmbH", "TotalEnergies Gestion USA SARL", "Bpifrance Investissement S.A.S.",
    "Financiere de Gestions Internationales SCA", "CAISSE DE DEPOT ET PLACEMENT DU QUEBEC",
    "TEACHER RETIREMENT SYSTEM OF TEXAS", "City of Boca Raton General Employees Pension Plan",
    "Western States Regional Joint Board, Workers United", "Maurice Marciano Charitable Remainder Unitrust",
    "TEACHERS INSURANCE & ANNUITY ASSOCIATION OF AMERICA",
])
def test_more_organization_forms(name: str) -> None:
    assert looks_like_organization(name)


@pytest.mark.parametrize("name", ["ROTH JAY W", "Kaplan Ira C.", "CHURCH KATRINA J", "Board Mark P", "College Amy"])
def test_surnames_that_look_like_words_stay_people(name: str) -> None:
    assert not looks_like_organization(name)


@pytest.mark.parametrize("ticker", ["AAPL", "BRK.B", "BF-B", "RDS/A", "KCAC-UN", "BESS-WT", "F", "googl"])
def test_ticker_shapes(ticker: str) -> None:
    assert looks_like_ticker(ticker)


@pytest.mark.parametrize("ticker", ["21874U888", "1314152", "GRAYBAR", "IDAHOPWR", "INX TOKEN", "PCG-PREF", "", None])
def test_text_that_is_not_a_ticker(ticker: str | None) -> None:
    assert not looks_like_ticker(ticker)


def test_edgars_namesake_number_is_dropped() -> None:
    assert person_display_and_sort("TEUNISSEN ERNST 02494") == ("Ernst Teunissen", "Teunissen, Ernst")


# ---------------------------------------------------------------- round 5: names still in the wrong order
@pytest.mark.parametrize(("edgar", "signatures", "display"), [
    # A signature that leaves out a middle name still shows where the double surname ends.
    ("Aguiar Rodriguez Carlos Roberto", ("Tami D. Whitman, Attorney-in-Fact for Carlos Aguiar Rodriguez",),
     "Carlos Roberto Aguiar Rodriguez"),
    ("Marced Martin Maria Angeles", ("/s/Deborah A. Choate, Attorney-in-Fact for Maria A. Marced Martin",),
     "Maria Angeles Marced Martin"),
    # Already first-name first on the Form 4; the agent signs for "Nicholas A Mosich".
    ("Nick Mosich", ("By: Derrick Walsh For: Nicholas Mosich", "Derrick Walsh for Nicholas A Mosich"), "Nick Mosich"),
    # ...but a signature in EDGAR's own order says nothing.
    ("SMITH JOHN", ("/s/ Smith John",), "John Smith"),
    # A patronymic in second place completes a double surname, also before particles.
    ("Flores Gomez Hernan Diego", ("/s/ Flores Gomez Hernan Diego",), "Hernan Diego Flores Gomez"),
    ("Ortiz Martinez Guillermo Mr", (), "Guillermo Ortiz Martinez"),
    ("Arrigunaga Gomez del Campo Javier", (), "Javier Arrigunaga Gomez del Campo"),
    ("LOPEZ JUAN CARLOS", (), "Juan Carlos Lopez"),
    # Courtesy titles aren't part of the name; signature slashes aren't either.
    ("Burke William P. Mr.", (), "William P. Burke"),
    ("KELLY VINCENT D", ("/Vincent D. Kelly/",), "Vincent D Kelly"),
    # The signature's capitals, but not EDGAR's apostrophe lost or a shift-key slip.
    ("DEPASQUALE MICHAEL W", ("/s/ Michael DePasquale",), "Michael W DePasquale"),
    ("O'Leary Dennis M", ("/s/ Dennis OLeary",), "Dennis M O'Leary"),
    ("LIPPS RANDALL A", ("/s/ Eric G. Lehmann, Attorney-in-Fact for Randall LIpps",), "Randall A Lipps"),
])
def test_names_the_round_5_review_found(edgar: str, signatures: tuple[str, ...], display: str) -> None:
    assert person_display_and_sort(edgar, signatures)[0] == display


# ---------------------------------------------------------------- round 6: organizations shown as people, and the reverse
@pytest.mark.parametrize(("name", "kind"), [
    ("Sport City Cadiz S.L.", "company"),                 # 29685: a Spanish S.L., 108 open-market buys
    ("THRIVENT FINANCIAL FOR LUTHERANS", "company"),      # 8469
    ("CANTOR FITZGERALD, L. P.", "fund"),                 # 24108: a spaced "L. P." after a comma
    ("General Atlantic (Lux) S.a r.l.", "company"),       # 13106: a Luxembourg S.a r.l., however dotted
    ("Next Alt S.a.r.l.", "company"),
    ("Joh. A. Benckiser s.a.r.l.", "company"),
    ("UAW Ford Retirees Medical Benefits Plan", "company"),  # 15101-15103: an employee benefit plan
    ("Advent International GPE IX-A SCSp", "fund"),       # 66699-66701: a Luxembourg SCSp
    ("METROPOLITAN LIFE INSURANCE CO/NY", "company"),     # 62148, named from the SEC record
    ("Auburn OWMH, LLLP", "fund"),
    ("Yucca (Jersey) SLP", "fund"),
    ("PM Alpha II DAC", "company"),
    ("Straits Energy Resources Berhad", "company"),
    ("KAPITALO TERAV FUNDO DE INVESTIMENTO FINANCEIRO EM COTAS DE FUNDOS DE INVESTIMENTO MULTIMERCADO", "fund"),
    ("Akastor ASA", "company"),
    ("Artal International S.C.A.", "company"),
    ("Hummingbird Collective CommV", "company"),
    ("Guardians of New Zealand Superannuation", "company"),
    ("Karatage Opportunities", "fund"),
    ("TotalEnergies Marketing Services S.A.S.", "company"),
])
def test_organizations_the_round_6_review_found(name: str, kind: str) -> None:
    assert looks_like_organization(name)
    assert identity_from_name(name)[0] == kind


@pytest.mark.parametrize("name", [
    "Tyagarajan N. V.",   # 25170: N. V. Tyagarajan, Genpact's chief executive; spaced initials, no comma
    "Tamsons Asa",        # Asa is a given name; only ASA after two words, or in capitals, is a Norwegian company
    "Paloma Diane S.L.",  # dotted initials, not a Spanish S.L.
    "Wunsch E.J.",
])
def test_initials_that_look_like_legal_forms_stay_people(name: str) -> None:
    assert not looks_like_organization(name)


@pytest.mark.parametrize(("name", "signatures", "display"), [
    # 8400: Jane E. Trust signs through an attorney-in-fact; a company never signs its name rotated.
    ("Trust Jane", ("/s/ Marc A. DeOliveira by Power of Attorney for Jane E. Trust",), "Jane Trust"),
])
def test_a_rotated_signature_keeps_a_person_a_person(name: str, signatures: tuple[str, ...], display: str) -> None:
    assert looks_like_organization(name)  # the name alone reads like an organization
    assert not looks_like_organization(name, signatures)
    assert identity_from_name(name, signatures=signatures)[:2] == ("person", display)


def test_a_company_signing_its_own_name_stays_a_company() -> None:
    signatures = ("Insight Holdings Group, LLC, By: /s/ Andrew Prodromos, Attorney-in-Fact",)
    assert looks_like_organization("Insight Holdings Group, LLC", signatures)


@pytest.mark.parametrize(("name", "signatures"), [
    # A joint filer signing with their own name: the signature names only part of the filer.
    ("DOE JOHN FAMILY TRUST", ("/s/ John Doe",)),
    ("SMITH JOHN A LIVING TRUST", ("/s/ John A. Smith",)),
    ("KAPOOR ROHIT CAPITAL LLC", ("/s/ Rohit Kapoor",)),
    ("JONES JANE FOUNDATION", ("/s/ Jane Jones",)),
    # 29726-29730 have this shape: a trust named after a person, co-filed with that person.
    ("Harrison William Scott Trust", ("/s/ William Harrison",)),
    # An organisation signing its own name in EDGAR's order.
    ("Harrison William Scott Trust", ("/s/ Harrison William Scott Trust",)),
])
def test_a_signature_naming_only_part_of_an_organisation_leaves_it_an_organisation(
    name: str, signatures: tuple[str, ...],
) -> None:
    assert looks_like_organization(name, signatures)
    assert identity_from_name(name, signatures=signatures)[0] != "person"


# ---------------------------------------------------------------- round 6: name order and clean-up
@pytest.mark.parametrize(("edgar", "display", "sort"), [
    ("WATSA V PREM ET AL", "V Prem Watsa", "Watsa, V Prem"),               # "et al": filed for him and others
    ("HARARI ELIYAHOU ET AL", "Eliyahou Harari", "Harari, Eliyahou"),
    ("FROST PHILLIP MD ET AL", "Phillip Frost MD", "Frost, Phillip MD"),
    ("TURNER R GERALD DR", "R Gerald Turner", "Turner, R Gerald"),          # DR is a title
    ("Akbari Dr. Homaira", "Homaira Akbari", "Akbari, Homaira"),
    ("YOUNGBLOOD DR KNEELAND", "Kneeland Youngblood", "Youngblood, Kneeland"),
    ("PRICE BILLY L JR DR", "Billy L Price Jr.", "Price, Billy L Jr."),
    ("SHRUM KAYSE M DO", "Kayse M Shrum DO", "Shrum, Kayse M DO"),         # DO after an initial is a degree
    ("de los Pinos Elisabet", "Elisabet de los Pinos", "De Los Pinos, Elisabet"),   # LOS, LAS are particles
    ("de las Heras Jose", "Jose de las Heras", "De Las Heras, Jose"),
    ("Garza y Garza Eugenio", "Eugenio Garza y Garza", "Garza Y Garza, Eugenio"),   # the Spanish "y"
    ("Lopez y Lopez De Lorenzi Cecilia Paola", "Cecilia Paola Lopez y Lopez De Lorenzi",
     "Lopez Y Lopez De Lorenzi, Cecilia Paola"),
    ("HOLDEN RICHMOND Y JR", "Richmond Y Holden Jr.", "Holden, Richmond Y Jr."),    # a capital Y is an initial
    # Short parenthesised tags: initials stay capitals, nicknames are title-cased, as filed if mixed case.
    ("ZHANG WEI (VIVI)", "Wei (Vivi) Zhang", "Zhang, Wei (Vivi)"),
    ("COHEN DANIEL (DC)", "Daniel (DC) Cohen", "Cohen, Daniel (DC)"),
    ("Yang Hongli (HLY)", "Hongli (HLY) Yang", "Yang, Hongli (HLY)"),
    ("Zhang (Rain) Rui", "Rui (Rain) Zhang", "Zhang, Rui (Rain)"),        # a tag after the surname follows the given name
])
def test_round_6_clean_up(edgar: str, display: str, sort: str) -> None:
    assert person_display_and_sort(edgar) == (display, sort)


@pytest.mark.parametrize(("edgar", "signatures", "display", "sort"), [
    # 51374: two signatures, each a rotation, one filing each: the rules break the tie and make
    # Akselbrad (the surname EDGAR puts first) the surname, as her own Form 3 does.
    ("Akselbrad Dikla Czaczkes", {"/s/ Czaczkes Akselbrad Dikla": 1, "/s/ Dikla Czaczkes Akselbrad": 1},
     "Dikla Czaczkes Akselbrad", "Akselbrad, Dikla Czaczkes"),
    # The signature on more filings wins, whatever the alphabetical order of the signatures.
    ("Chong Set Fui", {"/s/ Fui Chong Set": 1, "/s/ Set Fui Chong": 3}, "Set Fui Chong", "Chong, Set Fui"),
    ("Chong Set Fui", {"/s/ Fui Chong Set": 3, "/s/ Set Fui Chong": 1}, "Fui Chong Set", "Chong Set, Fui"),
    # "Last, First" is read surname first: 36628 signs "Craighead Carey, Jennifer".
    ("Craighead Carey Jennifer", ("Steven R. Horst, as attorney-in-fact for Craighead Carey, Jennifer",),
     "Jennifer Craighead Carey", "Craighead Carey, Jennifer"),
    ("LIM SIN FOO HARRIS", ("/s/ Lim Sin Foo, Harris",), "Harris Lim Sin Foo", "Lim Sin Foo, Harris"),
    # ...but a Hong Kong "English name, Chinese name" signature keeps its plain reading (24011).
    ("Lee Yat Lung Andrew", ("/s/ Andrew, Lee Yat Lung",), "Andrew Lee Yat Lung", "Lee Yat Lung, Andrew"),
    # A title in the signature no longer blocks the match (29722).
    ("Nair Krishnan Satis Waran", ("/s/ Dr. Satis Waran Nair Krishnan",), "Satis Waran Nair Krishnan",
     "Nair Krishnan, Satis Waran"),
    # The spelling with capitals title case can't guess wins over one that dropped them (50515).
    ("Trainor-Degirolamo Sheldon", {"/s/ Sheldon Trainor-DeGirolamo": 1, "/s/ Sheldon Trainor-Degirolamo": 2},
     "Sheldon Trainor-DeGirolamo", "Trainor-DeGirolamo, Sheldon"),
])
def test_round_6_signatures(edgar: str, signatures: tuple[str, ...] | dict[str, int], display: str,
                            sort: str) -> None:
    assert person_display_and_sort(edgar, signatures) == (display, sort)


@pytest.mark.parametrize(("edgar", "signed", "display"), [
    # Each owner signed one filing with "de" and one with "De" (26389, 26416, 53077).
    ("Araujo Alexandre Teixeira de", ("/s/ Alexandre Teixeira De Araujo", "/s/ Alexandre Teixeira de Araujo"),
     "Alexandre Teixeira de Araujo"),
    ("Chaves Roberto de Deus", ("/s/ Roberto De Deus Chaves", "/s/ Roberto de Deus Chaves"),
     "Roberto de Deus Chaves"),
    ("Queiroz Celso Mateus De", ("/s/ Celso Mateus De Queiroz", "/s/ Celso Mateus de Queiroz"),
     "Celso Mateus de Queiroz"),
])
def test_signatures_on_as_many_filings_give_one_name_in_any_order(edgar: str, signed: tuple[str, str],
                                                                   display: str) -> None:
    """The database returns signatures in no fixed order. On a tie the lower-case particle wins,
    whichever signature comes first, so the name never flips between two refreshes."""
    first, second = signed
    for order in ({first: 1, second: 1}, {second: 1, first: 1}, (first, second), (second, first)):
        assert person_display_and_sort(edgar, order)[0] == display
    # A spelling on more filings still wins.
    assert person_display_and_sort(edgar, {first: 2, second: 1})[0] == display.replace(" de ", " De ")


@pytest.mark.parametrize(("edgar", "signatures", "organization"), [
    # Real organisations at the boundary of the S.L. and ASA guards.
    ("Sport City Cadiz S.L.", (), True),
    ("Akastor ASA", (), True),
    ("YARA INTERNATIONAL ASA", (), True),
    # Real people just inside it: dotted initials after two words, Åsa as a given name.
    ("Paloma Diane S.L.", (), False),
    ("Tamsons Asa", (), False),
    ("LINDBERG ASA", (), False),       # capitals, one word before it
    ("Lindberg Maria Asa", (), False),  # "Asa" in mixed case is never the Norwegian form
    # The known false-positive shapes (no such filer in the data): a person with three words
    # before the S.L. or ASA. Her own signature, rotated, keeps her a person.
    ("GARCIA LOPEZ MARIA S.L.", ("/s/ Maria S.L. Garcia Lopez",), False),
    ("GARCIA LOPEZ MARIA S.L.", ("John Smith, Attorney-in-Fact for Maria S. L. Garcia Lopez",), False),
    ("LINDBERG MARIA ASA", ("/s/ Maria Asa Lindberg",), False),
    # A company signing its own name keeps its form.
    ("Sport City Cadiz S.L.", ("/s/ Sport City Cadiz S.L., by Juan Perez",), True),
])
def test_the_s_l_and_asa_guards(edgar: str, signatures: tuple[str, ...], organization: bool) -> None:
    assert looks_like_organization(edgar, signatures) is organization


#: Real word counts from the 58,660 conformed names in the database on 2026-09-29 (first word as
#: surname, later words as given names), without the names judged below.
FREQUENCIES = NameFrequencies(
    surname={"ROBERT": 1, "DAVID": 10, "SERGEY": 0, "JOSEPH": 14, "LEI": 3, "HEATH": 4, "JAMES": 32, "RODRIGUES": 5,
             "HA": 2, "FEAR": 1, "ANDERSON": 102, "LIU": 147, "LEE": 183},
    given={"ROBERT": 1187, "DAVID": 1549, "SERGEY": 7, "JOSEPH": 712, "LEI": 22, "HEATH": 12, "JAMES": 1254, "RODRIGUES": 6,
           "SHARPS": 1, "ANDERSON": 5, "LIU": 4, "LEE": 158, "ANDREW": 613},
)


@pytest.mark.parametrize(("edgar", "signatures", "display", "sort"), [
    # Already first-name first on EDGAR, signed the same way: the counts confirm it.
    ("David P McGlade", ("Ashran Jen, as Attorney-in-Fact for David P. McGlade",), "David P McGlade",
     "McGlade, David P"),
    ("Robert W. Sharps", ("Cheryl L. Emory, Assistant Corporate Secretary, POA for Sharps, Robert W.",),
     "Robert W. Sharps", "Sharps, Robert W."),
    ("Jianchun Liu", ("/s/ Jianchun Liu",), "Jianchun Liu", "Liu, Jianchun"),
    ("Sergey Brin", ("/s/ Kenneth Yi, as Attorney-in-Fact for Sergey Brin",), "Sergey Brin", "Brin, Sergey"),
    # Where the counts can't tell, EDGAR's order stands.
    ("Joseph Deepa", ("/s/ Joseph Deepa",), "Deepa Joseph", "Joseph, Deepa"),      # JOSEPH is also a surname
    ("Lei Ut Ha", ("/s/ Lei Ut Ha",), "Ut Ha Lei", "Lei, Ut Ha"),                  # a Macau name, surname first
    ("Heath R Fear", ("/s/ Heath R. Fear",), "R Fear Heath", "Heath, R Fear"),     # HEATH is a surname too
    ("James Craig W.", ("James Craig W.",), "Craig W. James", "James, Craig W."),  # an initial is no surname
    ("Rodrigues Anderson", ("Matheus Campos, Attorney-in-Fact for Rodrigues Anderson",), "Anderson Rodrigues",
     "Rodrigues, Anderson"),
    ("Lee Yat Lung Andrew", ("/s/ Andrew, Lee Yat Lung",), "Andrew Lee Yat Lung", "Lee Yat Lung, Andrew"),
    # All capitals is EDGAR's own form copied, not the owner's writing.
    ("SMITH JOHN", ("/s/ SMITH JOHN",), "John Smith", "Smith, John"),
])
def test_first_name_first_needs_the_word_counts(edgar: str, signatures: tuple[str, ...], display: str,
                                                sort: str) -> None:
    assert person_display_and_sort(edgar, signatures, FREQUENCIES) == (display, sort)


def test_without_word_counts_a_same_order_signature_tells_nothing() -> None:
    assert person_display_and_sort("David P McGlade", ("/s/ David P. McGlade",))[0] == "P McGlade David"


def test_a_names_own_words_are_left_out_of_the_counts() -> None:
    """Counted from its own name only, SHARPS is a given name once and ROBERT a surname once."""
    alone = NameFrequencies.from_names(["Robert W. Sharps"])
    assert alone.surname == {"ROBERT": 1}
    assert alone.given == {"SHARPS": 1}
    assert not alone.reads_first_name_first(["ROBERT", "W", "SHARPS"])  # ROBERT never seen as a given name
    many = NameFrequencies.from_names(["Robert W. Sharps", *(f"Surname{i} Robert" for i in range(60))])
    assert many.reads_first_name_first(["ROBERT", "W", "SHARPS"])


# ---------------------------------------------------------------- round 7: surnames no signature shows
#: Real word counts from the 55,470 names of people in the database on 2026-10-13, without the
#: names judged below.
COUNTS = NameFrequencies(
    surname={"LE": 21, "GOFF": 7, "OLIVIER": 2, "HEATHER": 1, "TRUONG": 3, "DANIEL": 17, "WEISS": 16,
             "BRACKEN": 3, "KEATING": 10, "CONWAY": 8, "KONG": 16, "KUO": 11, "WEI": 20, "CHANG": 53,
             "LEE": 183, "TURNER": 52, "NG": 40, "CHEE": 2, "NICHOLAS": 8},
    given={"OLIVIER": 21, "CORINNE": 1, "THI": 5, "HEATHER": 71, "DANIEL": 610, "WEISS": 2, "DARRELL": 18,
           "VALERIE": 28, "KONG": 12, "WEI": 46, "GERALD": 68, "CHEE": 18, "NICHOLAS": 148},
)


@pytest.mark.parametrize(("edgar", "display", "sort"), [
    # French "Le X": the second word is never a given name and exactly one given name follows.
    ("Le Peuch Olivier", "Olivier Le Peuch", "Le Peuch, Olivier"),             # 9481, SLB's chief executive
    ("Le Goff Corinne", "Corinne Le Goff", "Le Goff, Corinne"),                # 16479: GOFF is a surname elsewhere
    # Vietnamese "Le" is the whole surname.
    ("Le Thuy Thi Thu", "Thuy Thi Thu Le", "Le, Thuy Thi Thu"),                # 50115
    ("LE TRUONG VINH PHU", "Truong Vinh Phu Le", "Le, Truong Vinh Phu"),       # 61929
    ("Le Heather Tram", "Heather Tram Le", "Le, Heather Tram"),                # 67234
    ("Le Linh H.", "Linh H. Le", "Le, Linh H."),                               # 65851
    # A double surname: the second word is a surname elsewhere, never a given name.
    ("Soranno Keating Valerie", "Valerie Soranno Keating", "Soranno Keating, Valerie"),   # 34458
    # Chinese names with a second or English given name stay as they are.
    ("LEE CONWAY KONG WAI", "Conway Kong Wai Lee", "Lee, Conway Kong Wai"),
    ("CHANG KUO WEI HERBERT", "Kuo Wei Herbert Chang", "Chang, Kuo Wei Herbert"),
    ("Jiong Ng Chee", "Ng Chee Jiong", "Jiong, Ng Chee"),                      # 61934
    # Typed first-name first: a given name, one initial, a surname.
    ("Daniel K Weiss", "Daniel K Weiss", "Weiss, Daniel K"),                   # 9817
    ("Turner R Gerald", "R Gerald Turner", "Turner, R Gerald"),                # goes by his middle name
    # Other shapes can't be told by counts: VF's chief executive is Bracken Darrell.
    ("Darrell Bracken", "Bracken Darrell", "Darrell, Bracken"),
    ("Nicholas Breisacher", "Breisacher Nicholas", "Nicholas, Breisacher"),    # 49663: a known limit
])
def test_surnames_settled_by_word_counts_when_no_signature_names_the_owner(edgar: str, display: str,
                                                                          sort: str) -> None:
    assert person_display_and_sort(edgar, (), COUNTS) == (display, sort)
    # An attorney's signature tells nothing about the owner's name.
    assert person_display_and_sort(edgar, ("/s/ Jamie L. East, by Power of Attorney",), COUNTS) == (display, sort)


@pytest.mark.parametrize(("edgar", "display", "sort"), [
    ("'t Hart Cees", "Cees 't Hart", "Hart, Cees 't"),                         # 33399: filed under H
    ("'T HART CEES", "Cees 't Hart", "Hart, Cees 't"),
    ("van 't Noordende Alexander M", "Alexander M van 't Noordende", "Van 't Noordende, Alexander M"),
])
def test_the_dutch_t_is_a_lower_case_surname_particle(edgar: str, display: str, sort: str) -> None:
    assert person_display_and_sort(edgar) == (display, sort)


# ---------------------------------------------------------------- round 7: organisations still shown as people
@pytest.mark.parametrize(("name", "signatures", "kind", "display"), [
    # 19442: Concentrix's 10% owner, shown "Bruxelles Lambert Groupe".
    ("Groupe Bruxelles Lambert", ("Groupe Bruxelles Lambert., By: /s/ Priscilla Maters, Name: Priscilla Maters, "
                                  "Title: General Counsel",), "company", "Groupe Bruxelles Lambert"),
    # 19852: CAVA's 10% owner, a Dutch foundation holding shares for others.
    ("Stichting Administratiekantoor Westend", (), "company", "Stichting Administratiekantoor Westend"),
    # 64503: Children's Place's 10% owner, shown "Global Mithaq".
    ("Mithaq Global", ("Mithaq Capital By: /s/ Turki Saleh A. AlRajhi, Director",), "company", "Mithaq Global"),
    # 54991: read through the "Last, First" comma branch before.
    ("James F Heyneman Conservatorship, James Scott, Conservator", (), "company",
     "James F Heyneman Conservatorship, James Scott, Conservator"),
    # 16455, 16456: Spanish foundations, shown "Omerinta Fundacion".
    ("Fundacion Omerinta", ("/s/ See signatures attached as Exhibit 99.1",), "company", "Fundacion Omerinta"),
    ("Fundacion Barniz", (), "company", "Fundacion Barniz"),
    # 46744, 60433: one-word filers; EDGAR names every individual with a last and a first name.
    ("Synchron", (), "company", "Synchron"),
    ("Amundi", ("/s/ Marc Imsel, Head of Compliance - Market Integrity of Amundi S.A.",), "company", "Amundi"),
    ("Estate of John Doe", (), "company", "Estate of John Doe"),
    ("Societe Generale", (), "company", "Societe Generale"),
])
def test_organisations_the_round_7_review_found(name: str, signatures: tuple[str, ...], kind: str,
                                                display: str) -> None:
    assert identity_from_name(name, signatures=signatures)[:2] == (kind, display)


@pytest.mark.parametrize(("name", "signatures"), [
    # A signature block never makes a name with no marker an organisation's. A company officer signs
    # for its directors "Name, by /s/ <officer>, <title>" (22515, 22516, 22517), and a joint filer's
    # officer signs next to the owner (8436): these are people.
    ("William J. Bratton", ("William J. Bratton, by /s/ Alan R. Stewart, Chief Financial Officer",)),
    ("Ruby Sharma", ("Ruby Sharma, by /s/ Alan R. Stewart, Chief Financial Officer",)),
    ("Roberta S. Jacobson", ("Roberta S. Jacobson, by /s/ Alan R. Stewart, Chief Financial Officer",)),
    ("Patrick Soon-Shiong", ("/s/ Patrick Soon-Shiong, /s/ Charles Kenworthy, Manager of MP 13 Ventures, LLC",)),
    ("Smith John", ("/s/ John Smith, Director",) * 3 + ("Smith John By: /s/ Jane Doe, Secretary",)),
    # An attorney-in-fact signing (39622, 17258), the owner signing their own name (29795), a joint filing.
    ("Eddy Shalev", ("Eddy Shalev by: Oppenheimer Israel, as Attorney-in-fact",)),
    ("ZINGALE ANTHONY", ("/s/ Zingale Anthony by Charles Rogerson, as Attorney-in-Fact",)),
    ("Liu Yiheng (Gus)", ("Liu Yiheng (Gus), By: /s/ Liu Yiheng (Gus)",)),
    ("Smith John", ("Smith John Family LLC By: /s/ Jane Roe, Manager",)),
    ("Lasry Marc", ("Avenue Capital Management II, L.P., By: /s/ Andrew Schinder, Title: Attorney-in-Fact for "
                    "Marc Lasry, Member",)),
])
def test_a_signature_block_never_makes_a_person_an_organisation(name: str, signatures: tuple[str, ...]) -> None:
    assert not looks_like_organization(name)
    assert not looks_like_organization(name, signatures)
    assert identity_from_name(name, signatures=signatures)[0] == "person"


@pytest.mark.parametrize(("name", "kind", "display", "sort"), [
    # Insiders in the People directory whose names start lower case (15812, 64940, 31365, 59235).
    ("abrdn Inc.", "company", "abrdn Inc.", "Abrdn Inc."),
    ("venBio Global Strategic Fund IV, L.P.", "fund", "venBio Global Strategic Fund IV, L.P.",
     "VenBio Global Strategic Fund IV, L.P."),
    ("dMY Squared Sponsor, LLC", "company", "dMY Squared Sponsor, LLC", "DMY Squared Sponsor, LLC"),
])
def test_an_organisation_sort_name_opens_with_a_capital(name: str, kind: str, display: str, sort: str) -> None:
    """The database sorts byte by byte (C collation): a lower-case first letter would sort after Z.
    The display keeps the filer's own case."""
    assert identity_from_name(name) == (kind, display, sort)


@pytest.mark.parametrize("name", ["Global Mithaq Smith", "Moreno Global"])
def test_global_is_an_organisation_word(name: str) -> None:
    """No person's name in the data has GLOBAL; 220 organisations' do (2026-10-12)."""
    assert looks_like_organization(name)


# ---------------------------------------------------------------- round 7: namesake tags
@pytest.mark.parametrize(("name", "display", "sort"), [
    # 63772: a NACCO Industries director (Form 3, 2026-08-27). EDGAR's "(2026)" tells namesakes apart.
    ("Burns Patrick Joseph (2026)", "Patrick Joseph Burns", "Burns, Patrick Joseph"),
    # 27614: a tag with letters stays on display, and alone no longer makes an organisation.
    ("Kapoor Rohit (RK2)", "Rohit (RK2) Kapoor", "Kapoor, Rohit (RK2)"),
])
def test_a_trailing_tag_with_digits_leaves_a_person_a_person(name: str, display: str, sort: str) -> None:
    assert not looks_like_organization(name)
    assert identity_from_name(name) == ("person", display, sort)
    assert identity_from_name(name, signatures=("/s/ Rohit Kapoor",))[0] == "person"


@pytest.mark.parametrize("name", [
    # Real organisations with digits in parentheses keep their kind (18813, 18609, 54799, 66018, 66435).
    "CPP Investment Board Private Holdings (4) Inc.", "Atlas Capital Resources (A9) LP",
    "SOFTBANK VISION FUND (AIV M2) L.P.", "K ONE W ONE (NO 3) Ltd",
    "ICONIQ Strategic Partners II Co-Invest, L.P. (Series ST-2)", "FORMULA SYSTEMS (1985) LTD",
    "Acme (2)",  # one word once the tag is set aside
])
def test_organisations_with_digits_in_parentheses_stay_organisations(name: str) -> None:
    assert looks_like_organization(name)


# ---------------------------------------------------------------- round 7: name order
@pytest.mark.parametrize(("edgar", "signatures", "display", "sort"), [
    # 40236: a comma before only a suffix is no "Last, First" split.
    ("Phillips John R, III", ("/s/ Kira Schwartz, attorney in fact for John R Phillips III",),
     "John R Phillips III", "Phillips, John R III"),
    ("Phillips John R, III", (), "John R Phillips III", "Phillips, John R III"),
    # 54509: already first-name first; the attorney signs for "Mr. DuFauchard".
    ("Preston Paul DuFauchard, Jr.", ("/s/ Derek Stark, attorney-in-fact for Mr. DuFauchard",),
     "Preston Paul DuFauchard Jr.", "DuFauchard, Preston Paul Jr."),
    # 28940: the same attorney, "for Mr. Sauer".
    ("Scott Sauer", ("/s/ Derek W. Stark, attorney-in-fact for Mr. Sauer",), "Scott Sauer", "Sauer, Scott"),
    # ...and a title with the name's first word confirms EDGAR's order (9901).
    ("Simon Christopher", ("/s/ Thomas V. Powers, attorney-in-fact for Mr. Simon",), "Christopher Simon",
     "Simon, Christopher"),
    # 46057, 46058, 46060: French given names listed with a comma; the signature places the surname.
    ("Crespy Benjamin Noel, Camille", ("/s/ Marta Bragado, attorney-in-fact for Benjamin Crespy",),
     "Benjamin Noel Camille Crespy", "Crespy, Benjamin Noel Camille"),
    ("Ollivier Benoit Emile, Norbert", ("/s/ Marta Bragado, attorney-in-fact for Benoit Ollivier",),
     "Benoit Emile Norbert Ollivier", "Ollivier, Benoit Emile Norbert"),
    ("Girardeau David Marie, Gabriel", ("/s/ Marta Bragado, attorney-in-fact for David Girardeau",),
     "David Marie Gabriel Girardeau", "Girardeau, David Marie Gabriel"),
    # 16381: no signature fits; later commas are not shown.
    ("Catrice Ranunkel Loisa, Marie, Anita", ("/s/ Karla von Felbinger, Attorney-in-Fact",),
     "Marie Anita Catrice Ranunkel Loisa", "Catrice Ranunkel Loisa, Marie Anita"),
    # Hong Kong "Chinese name, English name" keeps EDGAR's comma whatever the signature (55337,
    # 29482, 50939, 66602).
    ("Chen Sze Hon, Johnson", ("/s/ Sze Hon, Johnson Chen",), "Johnson Chen Sze Hon", "Chen Sze Hon, Johnson"),
    ("Lui Yi Sin, Edith", ("/s/ Lui Yi Sin, Edith",), "Edith Lui Yi Sin", "Lui Yi Sin, Edith"),
    ("Yap Beng Tat, Richard", ("/s/ Richard Yap Beng Tat",), "Richard Yap Beng Tat", "Yap Beng Tat, Richard"),
    ("Lim Tze Ming, Kelvin", ("/s/ Lim Tze Ming Kelvin",), "Kelvin Lim Tze Ming", "Lim Tze Ming, Kelvin"),
    # One word before the comma: EDGAR's split stands (10883).
    ("Young, III Shannon E.", ("/s/ Marcus G. Bolinder, attorney-in-fact",), "Shannon E. Young III",
     "Young, Shannon E. III"),
])
def test_names_with_commas(edgar: str, signatures: tuple[str, ...], display: str, sort: str) -> None:
    assert person_display_and_sort(edgar, signatures) == (display, sort)


@pytest.mark.parametrize(("edgar", "display", "sort"), [
    # 32387, 57425, 46340: a Spanish double surname, the same name twice.
    ("garza garza Alfonso", "Alfonso Garza Garza", "Garza Garza, Alfonso"),
    ("garza garza juan carlos", "Juan Carlos Garza Garza", "Garza Garza, Juan Carlos"),
    ("Prieto Prieto Maria Isabel", "Maria Isabel Prieto Prieto", "Prieto Prieto, Maria Isabel"),
    # Not a Korean name whose given name repeats the surname (34901, Yoon-soo "Gene" Yoon), nor an
    # American with a generational suffix (32451), nor a Chinese surname ending in -ng.
    ("Yoon Yoon Soo (Gene)", "Yoon Soo (Gene) Yoon", "Yoon, Yoon Soo (Gene)"),
    ("Daniel Daniel David III", "Daniel David Daniel III", "Daniel, Daniel David III"),
    ("ZHENG ZHENG WEI", "Zheng Wei Zheng", "Zheng, Zheng Wei"),
])
def test_a_surname_given_twice(edgar: str, display: str, sort: str) -> None:
    assert person_display_and_sort(edgar) == (display, sort)


@pytest.mark.parametrize(("edgar", "display", "sort"), [
    # The filer's own lower-case particle stays (53101, 33880, 15845, 23293, 33556); one typed with
    # a capital stays a capital (56870 Dirk Van de Put, 44265).
    ("Fernandez de Melero Adriana Maria", "Adriana Maria Fernandez de Melero", "Fernandez De Melero, Adriana Maria"),
    ("de Jesus Assis Ana Paula", "Assis Ana Paula de Jesus", "De Jesus, Assis Ana Paula"),
    ("von Moltke Lisa", "Lisa von Moltke", "Von Moltke, Lisa"),
    ("van der Valk Eric", "Eric van der Valk", "Van Der Valk, Eric"),
    ("du Pont Eleuthere I", "Eleuthere I du Pont", "Du Pont, Eleuthere I"),
    ("Van de Put Dirk", "Dirk Van de Put", "Van De Put, Dirk"),
    ("De la Barra Jara Jaime Eduardo", "Jara Jaime Eduardo De la Barra", "De La Barra, Jara Jaime Eduardo"),
    ("Aguiar Lucas Araujo de", "Lucas Araujo de Aguiar", "Aguiar, Lucas Araujo De"),
    # In capitals, or all lower case, there is no telling: title case.
    ("VAN DER BERG JOHN A", "John A Van Der Berg", "Van Der Berg, John A"),
    ("de la cruz juan", "Juan De La Cruz", "De La Cruz, Juan"),
    # 52971: no signature, so the rules split the double surname wrongly (a known limitation);
    # the particle keeps its case.
    ("Silva de Sousa Oliveira Manuel Lino", "Oliveira Manuel Lino Silva de Sousa",
     "Silva De Sousa, Oliveira Manuel Lino"),
    # A particle never opens the displayed name (41624, split by rule alone).
    ("Zegna di Monte Rubello Edoardo", "Di Monte Rubello Edoardo Zegna", "Zegna, Di Monte Rubello Edoardo"),
])
def test_lower_case_particles(edgar: str, display: str, sort: str) -> None:
    """The display keeps the filer's lower-case particle; the sort name always opens with a capital,
    because the database sorts byte by byte ("von Moltke" would follow "Zyngier")."""
    assert person_display_and_sort(edgar) == (display, sort)


def test_sort_names_open_with_a_capital() -> None:
    """Every person's sort name starts upper case, so the People directory sorts "Van der Valk"
    under V (C collation puts lower case after Z)."""
    for edgar in ("van der Valk Eric", "ten Bosch John Roderick", "de Souza Paulo", "von Rickenbach Peter",
                  "de los Pinos Elisabet"):
        display, sort = person_display_and_sort(edgar)
        assert sort[0].isupper(), sort
        assert display.split(" ")[-1][0].islower() or display.split(" ")[-2][0].islower()  # display unchanged
    assert person_display_and_sort("ten Bosch John Roderick") == ("John Roderick ten Bosch", "Ten Bosch, John Roderick")


def test_a_signed_particle_still_wins() -> None:
    """The owner's own spelling of a particle beats the filed one (26389 signed "De" on more filings)."""
    signed = {"/s/ Alexandre Teixeira De Araujo": 2, "/s/ Alexandre Teixeira de Araujo": 1}
    assert person_display_and_sort("Araujo Alexandre Teixeira de", signed)[0] == "Alexandre Teixeira De Araujo"


@pytest.mark.parametrize(("edgar", "signatures", "display"), [
    # Surnames always written with an apostrophe, which EDGAR drops (8543, 16064, 13491, 17608, 38713).
    ("OBRIEN RICHARD T", (), "Richard T O'Brien"),
    ("OCONNELL MAUREEN", (), "Maureen O'Connell"),
    ("ONEIL JAMES F", (), "James F O'Neil"),
    ("OLEARY JOHN C G", (), "John C G O'Leary"),
    ("Obrien Donna M", (), "Donna M O'Brien"),
    ("OCONNOR MAYES TONIA", (), "Mayes Tonia O'Connor"),
    ("DALESSANDRO HENRY", (), "Henry D'Alessandro"),
    ("OHALLERAN MICHAEL D", (), "Michael D O'Halleran"),  # 13449
    # Filed with the capital after it kept: any O' or D' surname (43790, 45488, 20338, 53133, 57435).
    ("ODonnell Kevin", (), "Kevin O'Donnell"),
    ("DAlessandro Henry", (), "Henry D'Alessandro"),
    ("OHara Michael Gregory", (), "Michael Gregory O'Hara"),
    ("Boetger Bruno DAvila Melo", (), "Bruno D'Avila Melo Boetger"),
    ("DAmaro Joshua W", (), "Joshua W D'Amaro"),
    # An attorney copying EDGAR's form is no spelling of the owner's (32864).
    ("OKelly Shane M", ("/s/ Amanda L. Keister, as Attorney-in-Fact for Shane M. OKelly",), "Shane M O'Kelly"),
    # Names of their own without one stay so (13199, 17538, 25525, 35023), and so does Ohara.
    ("Odell Jamie", ("/s/ Sweta Gabhawala, attorney-in-fact for Jamie Odell",), "Jamie Odell"),
    ("ODELL RICHARD D", (), "Richard D Odell"),
    ("DAMICO JENNIFER B.", ("/s/ Shanice A. Reid, by power of atty, for Jennifer B. Damico",), "Jennifer B. Damico"),
    ("DIORIO CHRIS PH.D.", (), "Chris Diorio PhD"),
    ("OHARA KENJI", (), "Kenji Ohara"),
    ("Oneill Charles Odell", (), "Charles Odell O'Neill"),
    # The owner's own signature writing it as one plain word wins.
    ("OBRIEN DONNA M", ("/s/ Donna M. Obrien",), "Donna M Obrien"),
    # EDGAR's own apostrophe stays (36248), and so does a split one.
    ("O'Toole Brian E", ("/s/ Christiana L. Lin, attorney-in-fact on behalf of Brian E. OToole",), "Brian E O'Toole"),
    ("O BRIEN DANIEL B", (), "Daniel B O'Brien"),
])
def test_apostrophes_edgar_dropped(edgar: str, signatures: tuple[str, ...], display: str) -> None:
    assert person_display_and_sort(edgar, signatures)[0] == display


@pytest.mark.parametrize(("edgar", "display"), [
    # A given name typed with the shift key held a letter too long is a slip, not a dropped
    # apostrophe: there is no D'Avid or O'Liver.
    ("Smith DAvid", "David Smith"),
    ("Jones OLiver K", "Oliver K Jones"),
    ("Brown DAniel", "Daniel Brown"),
    # The real O'/D' surnames written that way still get theirs (43790, 53133).
    ("ODonnell Kevin", "Kevin O'Donnell"),
    ("Boetger Bruno DAvila Melo", "Bruno D'Avila Melo Boetger"),
])
def test_a_shift_key_slip_is_no_apostrophe(edgar: str, display: str) -> None:
    assert person_display_and_sort(edgar)[0] == display


# ---------------------------------------------------------------- second review of the round-7 name rules
@pytest.mark.parametrize(("edgar", "signatures", "display"), [
    # A shift-key slip in a plain surname is not a dropped apostrophe: there is no D'Avis or O'Lson.
    ("DAvis John", (), "John Davis"),
    ("OLson Mary", (), "Mary Olson"),
    ("OWens Bill", (), "Bill Owens"),
    ("DAVIS JOHN", ("/s/ John DAvis",), "John Davis"),
    # Real O'/D' surnames written with the capital kept still get their apostrophe.
    ("ODonnell Kevin", (), "Kevin O'Donnell"),
    ("DAlessandro Henry", (), "Henry D'Alessandro"),
    # A deliberately camel-cased given name keeps its inner capital.
    ("Burger DeAnna", (), "DeAnna Burger"),
    # EDGAR's own dot on "St." survives a matching signature.
    ("St. John Mary", ("/s/ Mary St. John",), "Mary St. John"),
])
def test_slips_camel_case_and_dotted_particles(edgar: str, signatures: tuple[str, ...], display: str) -> None:
    assert person_display_and_sort(edgar, signatures)[0] == display


@pytest.mark.parametrize(("name", "organization"), [
    ("Doe Jane (Trust)", True),          # the marker is inside the tag: still an organisation
    ("Acme Widgets (LLC)", True),
    ("Smith Family (LP)", True),
    ("Kapoor Rohit (RK2)", False),       # a tag with a digit after a person's name
    ("Burns Patrick Joseph (2026)", False),
])
def test_a_tag_hides_digits_but_not_marker_words(name: str, organization: bool) -> None:
    assert looks_like_organization(name) is organization


@pytest.mark.parametrize(("edgar", "display", "sort"), [
    # The sort key capitalises every surname word (the database sorts byte by byte), the display doesn't.
    ("van der Valk Eric", "Eric van der Valk", "Van Der Valk, Eric"),
    ("de la Calle Luis", "Luis de la Calle", "De La Calle, Luis"),
    ("von Moltke Lisa", "Lisa von Moltke", "Von Moltke, Lisa"),
])
def test_the_sort_key_has_no_lower_case_surname_words(edgar: str, display: str, sort: str) -> None:
    assert person_display_and_sort(edgar) == (display, sort)


def test_no_plain_name_is_also_an_apostrophe_surname() -> None:
    """A name on both lists would get an apostrophe in capitals and lose it as a slip."""
    from smt.domain.names import _APOSTROPHE_SURNAMES, _O_D_PLAIN_WORDS

    assert set() == _APOSTROPHE_SURNAMES & _O_D_PLAIN_WORDS


def test_a_particle_after_the_given_names_is_capitalised_in_the_sort_key_too() -> None:
    assert person_display_and_sort("Aguiar Lucas Araujo de") == ("Lucas Araujo de Aguiar", "Aguiar, Lucas Araujo De")
