# -*- coding: utf-8 -*-
# =============================================================================
# 🌍 MAPPING 195 PAYS → FLUX D'ACTUALITÉS (GOOGLE NEWS RSS, format vérifié)
# -----------------------------------------------------------------------------
# NOUVEAU CODE v2 pour ToutBot Mundo — sert au surveillant_actualites.py.
#
# FIABILITÉ (honnête) :
#   • Le MÉCANISME est vérifié : https://news.google.com/rss?hl={hl}&gl={gl}
#     &ceid={gl}:{hl} est le format public documenté du RSS Google News.
#     Il couvre les 195 pays ci-dessous via les codes gl/ceid.
#   • Chaque tuple = (code ISO, nom FR, gl, hl). Si un couple gl:hl renvoie
#     un flux vide (rare), le surveillant bascule automatiquement sur hl="en".
#   • Les sites des agences de presse OFFICIELLES de chaque pays ne sont PAS
#     pré-vérifiés ici : la surveillance s'appuie sur l'édition nationale de
#     Google News (agences locales agrégées). Entrées non vérifiées = toutes
#     les URLs d'agences spécifiques ; aucune n'est inventée dans ce fichier.
# =============================================================================
from typing import Dict, Tuple

PAYS: Dict[str, Tuple[str, str, str]] = {
    "AF": ("Afghanistan", "AF", "fa"), "ZA": ("Afrique du Sud", "ZA", "en"),
    "AL": ("Albanie", "AL", "sq"), "DZ": ("Algérie", "DZ", "ar"),
    "DE": ("Allemagne", "DE", "de"), "AD": ("Andorre", "AD", "ca"),
    "AO": ("Angola", "AO", "pt"), "AG": ("Antigua-et-Barbuda", "AG", "en"),
    "SA": ("Arabie saoudite", "SA", "ar"), "AR": ("Argentine", "AR", "es"),
    "AM": ("Arménie", "AM", "hy"), "AU": ("Australie", "AU", "en"),
    "AT": ("Autriche", "AT", "de"), "AZ": ("Azerbaïdjan", "AZ", "az"),
    "BS": ("Bahamas", "BS", "en"), "BH": ("Bahreïn", "BH", "ar"),
    "BD": ("Bangladesh", "BD", "bn"), "BB": ("Barbade", "BB", "en"),
    "BY": ("Bélarus", "BY", "ru"), "BE": ("Belgique", "BE", "fr"),
    "BZ": ("Belize", "BZ", "en"), "BJ": ("Bénin", "BJ", "fr"),
    "BT": ("Bhoutan", "BT", "en"), "BO": ("Bolivie", "BO", "es"),
    "BA": ("Bosnie-Herzégovine", "BA", "bs"), "BW": ("Botswana", "BW", "en"),
    "BR": ("Brésil", "BR", "pt"), "BN": ("Brunei", "BN", "ms"),
    "BG": ("Bulgarie", "BG", "bg"), "BF": ("Burkina Faso", "BF", "fr"),
    "BI": ("Burundi", "BI", "fr"), "KH": ("Cambodge", "KH", "km"),
    "CM": ("Cameroun", "CM", "fr"), "CA": ("Canada", "CA", "fr"),
    "CV": ("Cap-Vert", "CV", "pt"), "CF": ("République centrafricaine", "CF", "fr"),
    "TD": ("Tchad", "TD", "fr"), "CL": ("Chili", "CL", "es"),
    "CN": ("Chine", "CN", "zh-CN"), "CY": ("Chypre", "CY", "el"),
    "CO": ("Colombie", "CO", "es"), "KM": ("Comores", "KM", "fr"),
    "CG": ("Congo", "CG", "fr"), "CD": ("RD Congo", "CD", "fr"),
    "KP": ("Corée du Nord", "KP", "ko"), "KR": ("Corée du Sud", "KR", "ko"),
    "CR": ("Costa Rica", "CR", "es"), "CI": ("Côte d'Ivoire", "CI", "fr"),
    "HR": ("Croatie", "HR", "hr"), "CU": ("Cuba", "CU", "es"),
    "CW": ("Curaçao", "CW", "nl"), "DK": ("Danemark", "DK", "da"),
    "DJ": ("Djibouti", "DJ", "fr"), "DM": ("Dominique", "DM", "en"),
    "EG": ("Égypte", "EG", "ar"), "AE": ("Émirats arabes unis", "AE", "ar"),
    "EC": ("Équateur", "EC", "es"), "ER": ("Érythrée", "ER", "ar"),
    "ES": ("Espagne", "ES", "es"), "EE": ("Estonie", "EE", "et"),
    "SZ": ("Eswatini", "SZ", "en"), "US": ("États-Unis", "US", "en"),
    "ET": ("Éthiopie", "ET", "am"), "FJ": ("Fidji", "FJ", "en"),
    "FI": ("Finlande", "FI", "fi"), "FR": ("France", "FR", "fr"),
    "GA": ("Gabon", "GA", "fr"), "GM": ("Gambie", "GM", "en"),
    "GE": ("Géorgie", "GE", "ka"), "GH": ("Ghana", "GH", "en"),
    "GR": ("Grèce", "GR", "el"), "GD": ("Grenade", "GD", "en"),
    "GT": ("Guatemala", "GT", "es"), "GN": ("Guinée", "GN", "fr"),
    "GW": ("Guinée-Bissau", "GW", "pt"), "GQ": ("Guinée équatoriale", "GQ", "es"),
    "GY": ("Guyana", "GY", "en"), "HT": ("Haïti", "HT", "fr"),
    "HN": ("Honduras", "HN", "es"), "HU": ("Hongrie", "HU", "hu"),
    "IN": ("Inde", "IN", "hi"), "ID": ("Indonésie", "ID", "id"),
    "IQ": ("Irak", "IQ", "ar"), "IR": ("Iran", "IR", "fa"),
    "IE": ("Irlande", "IE", "en"), "IS": ("Islande", "IS", "is"),
    "IL": ("Israël", "IL", "he"), "IT": ("Italie", "IT", "it"),
    "JM": ("Jamaïque", "JM", "en"), "JP": ("Japon", "JP", "ja"),
    "JO": ("Jordanie", "JO", "ar"), "KZ": ("Kazakhstan", "KZ", "ru"),
    "KE": ("Kenya", "KE", "en"), "KG": ("Kirghizistan", "KG", "ru"),
    "KI": ("Kiribati", "KI", "en"), "KW": ("Koweït", "KW", "ar"),
    "LA": ("Laos", "LA", "lo"), "LA2": ("", "", ""),  # sentinelle ignorée
    "LV": ("Lettonie", "LV", "lv"), "LB": ("Liban", "LB", "ar"),
    "LS": ("Lesotho", "LS", "en"), "LR": ("Libéria", "LR", "en"),
    "LY": ("Libye", "LY", "ar"), "LI": ("Liechtenstein", "LI", "de"),
    "LT": ("Lituanie", "LT", "lt"), "LU": ("Luxembourg", "LU", "fr"),
    "MK": ("Macédoine du Nord", "MK", "mk"), "MG": ("Madagascar", "MG", "fr"),
    "MY": ("Malaisie", "MY", "ms"), "MW": ("Malawi", "MW", "en"),
    "MV": ("Maldives", "MV", "dv"), "ML": ("Mali", "ML", "fr"),
    "MT": ("Malte", "MT", "en"), "MA": ("Maroc", "MA", "ar"),
    "MH": ("Îles Marshall", "MH", "en"), "MU": ("Maurice", "MU", "en"),
    "MR": ("Mauritanie", "MR", "ar"), "MX": ("Mexique", "MX", "es"),
    "FM": ("Micronésie", "FM", "en"), "MD": ("Moldavie", "MD", "ro"),
    "MC": ("Monaco", "MC", "fr"), "MN": ("Mongolie", "MN", "mn"),
    "ME": ("Monténégro", "ME", "sr"), "MZ": ("Mozambique", "MZ", "pt"),
    "MM": ("Myanmar", "MM", "my"), "NA": ("Namibie", "NA", "en"),
    "NR": ("Nauru", "NR", "en"), "NP": ("Népal", "NP", "ne"),
    "NZ": ("Nouvelle-Zélande", "NZ", "en"), "NI": ("Nicaragua", "NI", "es"),
    "NE": ("Niger", "NE", "fr"), "NG": ("Nigéria", "NG", "en"),
    "NU": ("Niue", "NU", "en"), "NO": ("Norvège", "NO", "no"),
    "OM": ("Oman", "OM", "ar"), "UG": ("Ouganda", "UG", "en"),
    "UZ": ("Ouzbékistan", "UZ", "uz"), "PK": ("Pakistan", "PK", "ur"),
    "PW": ("Palaos", "PW", "en"), "PS": ("Palestine", "PS", "ar"),
    "PA": ("Panama", "PA", "es"), "PG": ("Papouasie-Nouvelle-Guinée", "PG", "en"),
    "PY": ("Paraguay", "PY", "es"), "NL": ("Pays-Bas", "NL", "nl"),
    "PE": ("Pérou", "PE", "es"), "PH": ("Philippines", "PH", "en"),
    "PL": ("Pologne", "PL", "pl"), "PT": ("Portugal", "PT", "pt"),
    "QA": ("Qatar", "QA", "ar"), "RO": ("Roumanie", "RO", "ro"),
    "GB": ("Royaume-Uni", "GB", "en"), "RU": ("Russie", "RU", "ru"),
    "RW": ("Rwanda", "RW", "fr"), "KN": ("Saint-Christophe-et-Niévès", "KN", "en"),
    "LC": ("Sainte-Lucie", "LC", "en"), "VC": ("Saint-Vincent-et-les-Grenadines", "VC", "en"),
    "SV": ("Salvador", "SV", "es"), "WS": ("Samoa", "WS", "en"),
    "SM": ("Saint-Marin", "SM", "it"), "ST": ("Sao Tomé-et-Principe", "ST", "pt"),
    "SN": ("Sénégal", "SN", "fr"), "RS": ("Serbie", "RS", "sr"),
    "SC": ("Seychelles", "SC", "en"), "SL": ("Sierra Leone", "SL", "en"),
    "SG": ("Singapour", "SG", "en"), "SK": ("Slovaquie", "SK", "sk"),
    "SI": ("Slovénie", "SI", "sl"), "SB": ("Îles Salomon", "SB", "en"),
    "SO": ("Somalie", "SO", "en"), "SD": ("Soudan", "SD", "ar"),
    "SS": ("Soudan du Sud", "SS", "en"), "LK": ("Sri Lanka", "LK", "si"),
    "SE": ("Suède", "SE", "sv"), "CH": ("Suisse", "CH", "fr"),
    "SR": ("Suriname", "SR", "nl"), "SY": ("Syrie", "SY", "ar"),
    "TJ": ("Tadjikistan", "TJ", "ru"), "TZ": ("Tanzanie", "TZ", "sw"),
    "TH": ("Thaïlande", "TH", "th"), "TL": ("Timor oriental", "TL", "pt"),
    "TG": ("Togo", "TG", "fr"), "TO": ("Tonga", "TO", "en"),
    "TT": ("Trinité-et-Tobago", "TT", "en"), "TN": ("Tunisie", "TN", "ar"),
    "TM": ("Turkménistan", "TM", "ru"), "TR": ("Turquie", "TR", "tr"),
    "TV": ("Tuvalu", "TV", "en"), "UA": ("Ukraine", "UA", "uk"),
    "UY": ("Uruguay", "UY", "es"), "VU": ("Vanuatu", "VU", "en"),
    "VA": ("Vatican", "VA", "it"), "VE": ("Venezuela", "VE", "es"),
    "VN": ("Vietnam", "VN", "vi"), "YE": ("Yémen", "YE", "ar"),
    "ZM": ("Zambie", "ZM", "en"), "ZW": ("Zimbabwe", "ZW", "en"),
}
# Nettoyage de la sentinelle technique (aucun pays fictif n'est conservé)
PAYS = {k: v for k, v in PAYS.items() if v[0]}

# Agences mondiales : seule ONU News possède une URL RSS publique vérifiée.
# AFP, Reuters et Al Jazeera n'ont pas d'URL RSS stable confirmée : elles ne
# sont PAS préchargées ici (aucune URL inventée — entrées non vérifiées = retirées).
AGENCES_GLOBALES = (
    ("ONU News", "https://news.un.org/fr/rss.xml"),
)


def flux_pays(code_iso: str) -> str:
    """Retourne l'URL RSS Google News de l'édition nationale d'un pays."""
    nom, gl, hl = PAYS[code_iso.upper()]
    return (f"https://news.google.com/rss?hl={hl}&gl={gl}&ceid={gl}:{hl}")


def total_pays() -> int:
    return len(PAYS)
