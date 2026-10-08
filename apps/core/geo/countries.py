"""
Country profiles used to sample realistic synthetic populations.

Figures are deliberately approximate (census-based where available: KNBS 2019
for Kenya, Stats SA 2022, GSS 2021, NISR 2022, NBS 2022; projections
elsewhere) and only shape the *distribution* of simulated agents. Platform
reach and institutional-trust values are rough priors (inspired by
DataReportal and Afrobarometer-style surveys) and are meant to be tuned.

Ethnicity is intentionally NOT modelled: assigning ethnic identities to
synthetic agents risks reproducing harmful political stereotypes.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Region:
    name: str
    population: int
    urban_share: float
    group: str = ""


@dataclass
class CountryProfile:
    code: str
    name: str
    demonym: str
    capital: str
    currency: str
    region_label: str
    regions: list[Region]
    languages: list[str]
    local_language_style: str
    context: str
    online_age_dist: dict[str, float]
    online_male_share: float
    platform_reach: dict[str, float]
    trust: dict[str, float]
    leaning_dist: dict[str, float]
    leaning_labels: dict[str, str]
    first_names_male: list[str]
    first_names_female: list[str]
    surnames: list[str]
    handle_suffixes: list[str]
    google_news: tuple[str, str] | None  # (hl, gl)
    gdelt_name: str
    subreddits: list[str]
    local_queries: bool = False  # also search in Swahili
    flavor: dict[str, list[str]] = field(default_factory=dict)
    archetype_labels: dict[str, str] = field(default_factory=dict)
    archetype_weights: dict[str, float] = field(default_factory=dict)
    media_outlets: list[str] = field(default_factory=list)

    @property
    def total_population(self) -> int:
        return sum(r.population for r in self.regions)

    def region(self, name: str) -> Region | None:
        for region in self.regions:
            if region.name.lower() == (name or "").lower():
                return region
        return None

    def region_groups(self) -> list[str]:
        seen = []
        for region in self.regions:
            if region.group and region.group not in seen:
                seen.append(region.group)
        return seen


ONLINE_AGE_DEFAULT = {"18-24": 0.30, "25-34": 0.33, "35-44": 0.18, "45-54": 0.10, "55-64": 0.06, "65+": 0.03}
LEANING_LABELS = {
    "pro_government": "Leans pro-government",
    "opposition": "Leans opposition",
    "non_aligned": "Non-aligned / independent",
}

# ---------------------------------------------------------------------------
# Kenya (detailed): 47 counties, KNBS 2019 census, grouped by former province.
# ---------------------------------------------------------------------------
KENYA_COUNTIES = [
    Region("Mombasa", 1208333, 1.00, "Coast"),
    Region("Kwale", 866820, 0.18, "Coast"),
    Region("Kilifi", 1453787, 0.25, "Coast"),
    Region("Tana River", 315943, 0.15, "Coast"),
    Region("Lamu", 143920, 0.25, "Coast"),
    Region("Taita-Taveta", 340671, 0.25, "Coast"),
    Region("Garissa", 841353, 0.20, "North Eastern"),
    Region("Wajir", 781263, 0.15, "North Eastern"),
    Region("Mandera", 867457, 0.15, "North Eastern"),
    Region("Marsabit", 459785, 0.20, "Eastern"),
    Region("Isiolo", 268002, 0.40, "Eastern"),
    Region("Meru", 1545714, 0.15, "Eastern"),
    Region("Tharaka-Nithi", 393177, 0.12, "Eastern"),
    Region("Embu", 608599, 0.20, "Eastern"),
    Region("Kitui", 1136187, 0.12, "Eastern"),
    Region("Machakos", 1421932, 0.40, "Eastern"),
    Region("Makueni", 987653, 0.12, "Eastern"),
    Region("Nyandarua", 638289, 0.12, "Central"),
    Region("Nyeri", 759164, 0.25, "Central"),
    Region("Kirinyaga", 610411, 0.20, "Central"),
    Region("Murang'a", 1056640, 0.15, "Central"),
    Region("Kiambu", 2417735, 0.60, "Central"),
    Region("Turkana", 926976, 0.15, "Rift Valley"),
    Region("West Pokot", 621241, 0.08, "Rift Valley"),
    Region("Samburu", 310327, 0.15, "Rift Valley"),
    Region("Trans Nzoia", 990341, 0.25, "Rift Valley"),
    Region("Uasin Gishu", 1163186, 0.45, "Rift Valley"),
    Region("Elgeyo-Marakwet", 454480, 0.12, "Rift Valley"),
    Region("Nandi", 885711, 0.12, "Rift Valley"),
    Region("Baringo", 666763, 0.12, "Rift Valley"),
    Region("Laikipia", 518560, 0.30, "Rift Valley"),
    Region("Nakuru", 2162202, 0.45, "Rift Valley"),
    Region("Narok", 1157873, 0.12, "Rift Valley"),
    Region("Kajiado", 1117840, 0.40, "Rift Valley"),
    Region("Kericho", 901777, 0.20, "Rift Valley"),
    Region("Bomet", 875689, 0.10, "Rift Valley"),
    Region("Kakamega", 1867579, 0.20, "Western"),
    Region("Vihiga", 590013, 0.30, "Western"),
    Region("Bungoma", 1670570, 0.18, "Western"),
    Region("Busia", 893681, 0.20, "Western"),
    Region("Siaya", 993183, 0.12, "Nyanza"),
    Region("Kisumu", 1155574, 0.50, "Nyanza"),
    Region("Homa Bay", 1131950, 0.15, "Nyanza"),
    Region("Migori", 1116436, 0.20, "Nyanza"),
    Region("Kisii", 1266860, 0.25, "Nyanza"),
    Region("Nyamira", 605576, 0.15, "Nyanza"),
    Region("Nairobi", 4397073, 1.00, "Nairobi"),
]

KENYA = CountryProfile(
    code="KE",
    name="Kenya",
    demonym="Kenyan",
    capital="Nairobi",
    currency="KES",
    region_label="County",
    regions=KENYA_COUNTIES,
    languages=["English", "Swahili", "Sheng"],
    local_language_style="English mixed with Swahili and Sheng (Kenyan urban slang)",
    context=(
        "Kenya (population about 50 million, capital Nairobi, currency Kenya shillings, KES). "
        "It has a very active online public sphere ('Kenyans on Twitter', KOT), a young mobile-first "
        "population and near-universal mobile money (M-Pesa). Recurring public concerns: cost of living, "
        "taxation, unemployment, corruption, and how healthcare and education are financed. "
        "Online discourse often mixes English, Swahili and Sheng."
    ),
    online_age_dist={"18-24": 0.32, "25-34": 0.34, "35-44": 0.17, "45-54": 0.09, "55-64": 0.05, "65+": 0.03},
    online_male_share=0.55,
    platform_reach={
        "whatsapp": 0.92, "facebook": 0.75, "youtube": 0.65, "tiktok": 0.55, "instagram": 0.40,
        "x": 0.28, "telegram": 0.25, "linkedin": 0.15, "reddit": 0.03,
    },
    trust={
        "government": 0.35, "parliament": 0.25, "media": 0.55, "religious": 0.70,
        "civil_society": 0.55, "business": 0.40, "international": 0.50, "experts": 0.55,
    },
    leaning_dist={"pro_government": 0.33, "opposition": 0.35, "non_aligned": 0.32},
    leaning_labels=LEANING_LABELS,
    first_names_male=[
        "Brian", "Kevin", "Dennis", "Collins", "Victor", "Peter", "John", "James", "David", "Joseph",
        "Samuel", "Daniel", "Stephen", "Francis", "Moses", "Wycliffe", "Erick", "Felix", "Allan", "Ian",
        "Kelvin", "Duncan", "Evans", "Hassan", "Omar", "Abdi", "Juma", "Baraka", "Kiprono", "Otieno",
        "Kipchumba", "Wafula", "Mutua", "Kamau", "Odhiambo", "Edwin", "Geoffrey", "Martin", "Simon", "Amos",
    ],
    first_names_female=[
        "Faith", "Mercy", "Grace", "Joy", "Purity", "Sharon", "Diana", "Esther", "Mary", "Ann",
        "Caroline", "Lucy", "Winnie", "Beatrice", "Ruth", "Naomi", "Cynthia", "Brenda", "Maureen", "Lilian",
        "Halima", "Amina", "Fatuma", "Zawadi", "Neema", "Wanjiru", "Akinyi", "Chebet", "Nafula", "Mwende",
        "Wairimu", "Atieno", "Jepchirchir", "Njeri", "Wambui", "Kerubo", "Moraa", "Nasimiyu", "Kawira", "Achieng",
    ],
    surnames=[
        "Mwangi", "Kamau", "Njoroge", "Otieno", "Odhiambo", "Ochieng", "Onyango", "Kiprono", "Kipkemboi",
        "Cheruiyot", "Kibet", "Wafula", "Wekesa", "Barasa", "Mutua", "Musembi", "Kiptoo", "Omondi", "Kariuki",
        "Maina", "Gitau", "Hassan", "Mohamed", "Abdullahi", "Mohamud", "Nyamweya", "Ombati", "Kirui", "Langat",
        "Lemayian", "Ekai", "Lokwang", "Mwakio", "Baya", "Katana", "Charo", "Wanyonyi", "Simiyu", "Owino",
        "Njuguna", "Kimani", "Wambua", "Kilonzo", "Mwende", "Nyaga", "Mugo", "Koech", "Rotich", "Tanui",
    ],
    handle_suffixes=["254", "_ke", "ke", "_254", "KE", "", "", ""],
    google_news=("en-KE", "KE"),
    gdelt_name="kenya",
    subreddits=["Kenya", "Nairobi"],
    local_queries=True,
    flavor={
        "anger": ["Aki this is too much.", "Tumechoka!", "Wueh, enough is enough.", "This is daylight robbery.", "Sisi wananchi tumechoka."],
        "fear": ["Hii itatuumiza sana.", "I'm honestly worried.", "Where are we headed as a country?"],
        "sadness": ["Maisha imekuwa ngumu.", "Life is already so hard."],
        "joy": ["Finally some good news!", "Hii ni poa sana."],
        "hope": ["Pole pole tutafika.", "Let's give it a chance, wasee.", "There might be something good here."],
        "trust": ["Hii ni sawa.", "Honestly this makes sense."],
        "neutral": ["Sasa who will explain this to us?", "Kwani what does this mean for mama mboga?", "Wasee, let's discuss."],
    },
    archetype_labels={"boda_rider": "Boda boda rider", "market_vendor": "Mama mboga (market vendor)", "matatu_operator": "Matatu operator", "casual_labourer": "Jua kali / casual worker"},
    media_outlets=["NewsDesk254", "KenyaDailyWire", "MetroFM_KE"],
)

# ---------------------------------------------------------------------------
# Other countries (region-level, approximate).
# ---------------------------------------------------------------------------
NIGERIA = CountryProfile(
    code="NG",
    name="Nigeria",
    demonym="Nigerian",
    capital="Abuja",
    currency="NGN",
    region_label="Zone",
    regions=[
        Region("Lagos", 15_400_000, 0.95, "South West"),
        Region("South West (excl. Lagos)", 25_000_000, 0.55, "South West"),
        Region("FCT Abuja", 3_600_000, 0.80, "North Central"),
        Region("North Central (excl. FCT)", 28_000_000, 0.40, "North Central"),
        Region("North West", 57_000_000, 0.33, "North West"),
        Region("North East", 29_000_000, 0.30, "North East"),
        Region("South South", 32_000_000, 0.50, "South South"),
        Region("South East", 22_000_000, 0.50, "South East"),
    ],
    languages=["English", "Nigerian Pidgin", "Hausa", "Yoruba", "Igbo"],
    local_language_style="English mixed with Nigerian Pidgin",
    context=(
        "Nigeria (population about 220 million, capital Abuja, currency naira, NGN). Large and very vocal online "
        "community, especially on X. Recurring concerns: inflation and food prices, fuel prices, insecurity, "
        "unemployment, exchange rates and corruption. Nigerian Pidgin is common online."
    ),
    online_age_dist={"18-24": 0.33, "25-34": 0.34, "35-44": 0.17, "45-54": 0.09, "55-64": 0.05, "65+": 0.02},
    online_male_share=0.57,
    platform_reach={"whatsapp": 0.93, "facebook": 0.85, "instagram": 0.60, "youtube": 0.60, "tiktok": 0.55, "x": 0.45, "telegram": 0.40, "linkedin": 0.15, "reddit": 0.03},
    trust={"government": 0.25, "parliament": 0.20, "media": 0.50, "religious": 0.75, "civil_society": 0.50, "business": 0.40, "international": 0.50, "experts": 0.50},
    leaning_dist={"pro_government": 0.30, "opposition": 0.38, "non_aligned": 0.32},
    leaning_labels=LEANING_LABELS,
    first_names_male=["Chinedu", "Emeka", "Tunde", "Segun", "Ibrahim", "Musa", "Abubakar", "Chukwuemeka", "Olumide", "Femi", "Kunle", "Yusuf", "Uche", "Obinna", "Ifeanyi", "Babatunde", "Sani", "Aliyu", "Daniel", "Samuel", "Michael", "Tobi", "Kelechi", "Usman"],
    first_names_female=["Ngozi", "Chioma", "Funmilayo", "Aisha", "Fatima", "Amaka", "Adaeze", "Bisi", "Yetunde", "Zainab", "Hauwa", "Blessing", "Precious", "Ifeoma", "Kemi", "Tolu", "Grace", "Esther", "Nkechi", "Halima", "Damilola", "Folake"],
    surnames=["Okafor", "Adeyemi", "Bello", "Ibrahim", "Okonkwo", "Eze", "Abubakar", "Adebayo", "Mohammed", "Nwosu", "Ogunleye", "Usman", "Okeke", "Balogun", "Danjuma", "Okoro", "Afolabi", "Lawal", "Chukwu", "Ojo", "Yakubu", "Ekpo", "Etim", "Igwe", "Adewale", "Garba", "Onuoha", "Bassey"],
    handle_suffixes=["NG", "_ng", "234", "_naija", "", "", ""],
    google_news=("en-NG", "NG"),
    gdelt_name="nigeria",
    subreddits=["Nigeria", "lagos"],
    flavor={
        "anger": ["This is wahala o!", "Na wa for this country.", "Enough is enough!", "Dem don start again."],
        "fear": ["I fear for where this is going.", "This one go affect us badly."],
        "sadness": ["Sapa is already biting hard.", "Life no easy at all."],
        "joy": ["This one sweet me o!", "Finally, good news."],
        "hope": ["Make we try am small.", "Things fit better sha."],
        "trust": ["This one make sense sha.", "I support this one."],
        "neutral": ["Who go explain this one to us?", "Abeg, what does this mean for small traders?"],
    },
    archetype_labels={"boda_rider": "Okada (motorbike taxi) rider", "matatu_operator": "Danfo/bus operator", "market_vendor": "Market trader", "casual_labourer": "Casual worker"},
    media_outlets=["NaijaNewsDesk", "LagosDaily", "AbujaWire"],
)

SOUTH_AFRICA = CountryProfile(
    code="ZA",
    name="South Africa",
    demonym="South African",
    capital="Pretoria",
    currency="ZAR",
    region_label="Province",
    regions=[
        Region("Gauteng", 15_100_000, 0.97, "Gauteng"),
        Region("KwaZulu-Natal", 12_400_000, 0.55, "KwaZulu-Natal"),
        Region("Western Cape", 7_400_000, 0.90, "Western Cape"),
        Region("Eastern Cape", 7_200_000, 0.50, "Eastern Cape"),
        Region("Limpopo", 6_600_000, 0.20, "Limpopo"),
        Region("Mpumalanga", 5_100_000, 0.45, "Mpumalanga"),
        Region("North West", 3_800_000, 0.45, "North West"),
        Region("Free State", 2_960_000, 0.85, "Free State"),
        Region("Northern Cape", 1_360_000, 0.80, "Northern Cape"),
    ],
    languages=["English", "isiZulu", "isiXhosa", "Afrikaans", "Sesotho", "Setswana"],
    local_language_style="English with South African slang (e.g. eish, sharp, lekker)",
    context=(
        "South Africa (population about 62 million, capitals Pretoria/Cape Town/Bloemfontein, currency rand, ZAR). "
        "Recurring concerns: unemployment, electricity supply (load-shedding), crime, service delivery, "
        "inequality and corruption. Coalition politics and a vibrant, often satirical online culture."
    ),
    online_age_dist={"18-24": 0.25, "25-34": 0.31, "35-44": 0.20, "45-54": 0.12, "55-64": 0.08, "65+": 0.04},
    online_male_share=0.50,
    platform_reach={"whatsapp": 0.93, "facebook": 0.80, "youtube": 0.70, "instagram": 0.60, "tiktok": 0.55, "x": 0.40, "linkedin": 0.30, "telegram": 0.15, "reddit": 0.05},
    trust={"government": 0.30, "parliament": 0.25, "media": 0.50, "religious": 0.70, "civil_society": 0.55, "business": 0.45, "international": 0.50, "experts": 0.55},
    leaning_dist={"pro_government": 0.35, "opposition": 0.35, "non_aligned": 0.30},
    leaning_labels=LEANING_LABELS,
    first_names_male=["Thabo", "Sipho", "Themba", "Bongani", "Lwazi", "Mandla", "Sibusiso", "Pieter", "Johan", "Ahmed", "Kagiso", "Tshepo", "Neo", "Jason", "Ryan", "Sizwe", "Lungile", "Musa", "Tumelo", "Andile"],
    first_names_female=["Lerato", "Nomvula", "Zanele", "Thandiwe", "Naledi", "Palesa", "Ayanda", "Busisiwe", "Precious", "Anele", "Michelle", "Chantelle", "Fatima", "Karabo", "Refilwe", "Nandi", "Mpho", "Lindiwe", "Annelie", "Priya"],
    surnames=["Nkosi", "Dlamini", "Ndlovu", "Khumalo", "Mokoena", "Mahlangu", "Sithole", "Botha", "van der Merwe", "Pillay", "Naidoo", "Molefe", "Mthembu", "Zulu", "Ngcobo", "Mabaso", "Smith", "Jacobs", "Adams", "Petersen", "Maseko", "Mkhize", "Radebe", "Coetzee"],
    handle_suffixes=["ZA", "_za", "_sa", "SA", "", "", ""],
    google_news=("en-ZA", "ZA"),
    gdelt_name="southafrica",
    subreddits=["southafrica"],
    flavor={
        "anger": ["Eish, this is too much.", "We are gatvol of this.", "Haibo! Not again."],
        "fear": ["Eish, this is worrying.", "Where is this country going?"],
        "sadness": ["It's tough out here, honestly.", "People are struggling."],
        "joy": ["Lekker news for a change!", "Sharp sharp, finally."],
        "hope": ["This could actually work.", "Let's see, maybe things improve."],
        "trust": ["Sharp, this makes sense.", "I'm behind this one."],
        "neutral": ["So who is going to explain this to us?", "What does this mean for ordinary people?"],
    },
    archetype_labels={"boda_rider": "Delivery motorbike rider", "matatu_operator": "Minibus taxi operator", "market_vendor": "Informal trader", "casual_labourer": "Piece-job worker"},
    media_outlets=["SANewsDesk", "JoziDaily", "CapeWire"],
)

GHANA = CountryProfile(
    code="GH",
    name="Ghana",
    demonym="Ghanaian",
    capital="Accra",
    currency="GHS",
    region_label="Region",
    regions=[
        Region("Greater Accra", 5_460_000, 0.92, "Southern"),
        Region("Ashanti", 5_440_000, 0.62, "Middle Belt"),
        Region("Eastern", 2_930_000, 0.45, "Southern"),
        Region("Central", 2_860_000, 0.50, "Southern"),
        Region("Western", 2_060_000, 0.50, "Southern"),
        Region("Western North", 880_000, 0.30, "Southern"),
        Region("Volta", 1_660_000, 0.40, "Southern"),
        Region("Oti", 740_000, 0.30, "Middle Belt"),
        Region("Bono", 1_210_000, 0.50, "Middle Belt"),
        Region("Bono East", 1_200_000, 0.40, "Middle Belt"),
        Region("Ahafo", 560_000, 0.40, "Middle Belt"),
        Region("Northern", 2_310_000, 0.40, "Northern"),
        Region("Savannah", 650_000, 0.25, "Northern"),
        Region("North East", 660_000, 0.25, "Northern"),
        Region("Upper East", 1_300_000, 0.25, "Northern"),
        Region("Upper West", 900_000, 0.25, "Northern"),
    ],
    languages=["English", "Twi", "Ga", "Ewe", "Pidgin"],
    local_language_style="English mixed with Ghanaian expressions (e.g. chale, herh)",
    context=(
        "Ghana (population about 34 million, capital Accra, currency cedi, GHS). Recurring concerns: cost of living, "
        "currency depreciation, taxes and levies, jobs, illegal mining (galamsey) and corruption. "
        "Competitive two-party politics and lively radio and online debate."
    ),
    online_age_dist=ONLINE_AGE_DEFAULT,
    online_male_share=0.55,
    platform_reach={"whatsapp": 0.90, "facebook": 0.75, "youtube": 0.60, "tiktok": 0.50, "instagram": 0.50, "x": 0.30, "telegram": 0.25, "linkedin": 0.15, "reddit": 0.02},
    trust={"government": 0.35, "parliament": 0.30, "media": 0.55, "religious": 0.75, "civil_society": 0.55, "business": 0.45, "international": 0.50, "experts": 0.55},
    leaning_dist={"pro_government": 0.35, "opposition": 0.35, "non_aligned": 0.30},
    leaning_labels=LEANING_LABELS,
    first_names_male=["Kwame", "Kofi", "Kwaku", "Yaw", "Kojo", "Kwabena", "Kwesi", "Emmanuel", "Isaac", "Samuel", "Prince", "Ebo", "Nii", "Fiifi", "Ibrahim", "Abdul", "Richmond", "Eric"],
    first_names_female=["Ama", "Akosua", "Adwoa", "Abena", "Afia", "Yaa", "Esi", "Efua", "Akua", "Adjoa", "Naa", "Gifty", "Mavis", "Priscilla", "Comfort", "Fati", "Rahinatu", "Linda"],
    surnames=["Mensah", "Owusu", "Boateng", "Asante", "Osei", "Agyeman", "Appiah", "Addo", "Ofori", "Darko", "Quaye", "Tetteh", "Amoah", "Ansah", "Frimpong", "Sarpong", "Yeboah", "Danso", "Mohammed", "Abubakari", "Nartey", "Acheampong"],
    handle_suffixes=["GH", "_gh", "233", "", "", ""],
    google_news=("en-GH", "GH"),
    gdelt_name="ghana",
    subreddits=["ghana"],
    flavor={
        "anger": ["Chale, this is too much.", "Herh, we are suffering paa.", "This one, we won't accept."],
        "fear": ["Chale, I'm worried about this.", "Where are we going?"],
        "sadness": ["Things are hard, chale.", "Hmm, life is tough."],
        "joy": ["Chale, this one dey sweet!", "Finally, good news."],
        "hope": ["Let's see how it goes.", "Maybe this one will help."],
        "trust": ["This one makes sense, chale.", "I back this."],
        "neutral": ["Who will explain this to us?", "What does it mean for market women?"],
    },
    archetype_labels={"boda_rider": "Okada rider", "matatu_operator": "Trotro operator", "market_vendor": "Market trader", "casual_labourer": "Casual worker"},
    media_outlets=["AccraNewsDesk", "GhanaDailyWire", "KumasiFM"],
)

UGANDA = CountryProfile(
    code="UG",
    name="Uganda",
    demonym="Ugandan",
    capital="Kampala",
    currency="UGX",
    region_label="Region",
    regions=[
        Region("Kampala", 1_900_000, 1.00, "Central"),
        Region("Central (excl. Kampala)", 11_000_000, 0.35, "Central"),
        Region("Eastern", 12_000_000, 0.20, "Eastern"),
        Region("Western", 11_500_000, 0.20, "Western"),
        Region("Northern", 9_500_000, 0.18, "Northern"),
    ],
    languages=["English", "Luganda", "Swahili"],
    local_language_style="English mixed with Luganda expressions",
    context=(
        "Uganda (population about 46 million, capital Kampala, currency Uganda shilling, UGX). One of the youngest "
        "populations in the world. Recurring concerns: jobs, cost of living, service delivery, corruption and governance."
    ),
    online_age_dist={"18-24": 0.36, "25-34": 0.34, "35-44": 0.16, "45-54": 0.08, "55-64": 0.04, "65+": 0.02},
    online_male_share=0.58,
    platform_reach={"whatsapp": 0.90, "facebook": 0.75, "youtube": 0.50, "tiktok": 0.45, "instagram": 0.30, "x": 0.25, "telegram": 0.20, "linkedin": 0.10, "reddit": 0.02},
    trust={"government": 0.40, "parliament": 0.30, "media": 0.55, "religious": 0.75, "civil_society": 0.55, "business": 0.40, "international": 0.50, "experts": 0.55},
    leaning_dist={"pro_government": 0.38, "opposition": 0.32, "non_aligned": 0.30},
    leaning_labels=LEANING_LABELS,
    first_names_male=["Moses", "Isaac", "Ivan", "Brian", "Joseph", "Ronald", "Denis", "Patrick", "Robert", "Emmanuel", "Henry", "Godfrey", "Musa", "Edgar", "Arnold", "Solomon"],
    first_names_female=["Sarah", "Esther", "Prossy", "Brenda", "Doreen", "Florence", "Gloria", "Patience", "Juliet", "Annet", "Agnes", "Mercy", "Shamim", "Faridah", "Joan", "Winnie"],
    surnames=["Mugisha", "Okello", "Nakato", "Namubiru", "Kato", "Ssebunya", "Kiggundu", "Mukasa", "Tumusiime", "Byaruhanga", "Atuhaire", "Opio", "Odongo", "Wanyama", "Namutebi", "Lubega", "Nsubuga", "Kizza", "Muwanga", "Akello", "Nalwoga", "Twinomujuni"],
    handle_suffixes=["UG", "_ug", "256", "", "", ""],
    google_news=("en-UG", "UG"),
    gdelt_name="uganda",
    subreddits=["Uganda"],
    local_queries=False,
    flavor={
        "anger": ["Banange, this is too much!", "We are tired of this.", "This is not fair at all."],
        "fear": ["I'm worried about where this is going.", "This will hurt ordinary people."],
        "sadness": ["Life is already hard, banange.", "People are struggling."],
        "joy": ["This is good news, finally!", "Nice one."],
        "hope": ["Let's give it a chance.", "Maybe things will improve."],
        "trust": ["This makes sense to me.", "I support this."],
        "neutral": ["Who will explain this to us?", "What does this mean for small traders?"],
    },
    archetype_labels={"boda_rider": "Boda boda rider", "matatu_operator": "Taxi (minibus) operator", "market_vendor": "Market vendor", "casual_labourer": "Casual worker"},
    media_outlets=["KampalaNewsDesk", "UgandaDailyWire", "PearlFM_UG"],
)

TANZANIA = CountryProfile(
    code="TZ",
    name="Tanzania",
    demonym="Tanzanian",
    capital="Dodoma",
    currency="TZS",
    region_label="Zone",
    regions=[
        Region("Dar es Salaam", 5_400_000, 1.00, "Coastal"),
        Region("Lake Zone", 17_000_000, 0.30, "Lake"),
        Region("Northern Zone", 8_500_000, 0.35, "Northern"),
        Region("Central Zone", 8_000_000, 0.25, "Central"),
        Region("Southern Highlands", 9_000_000, 0.28, "Southern Highlands"),
        Region("Eastern & Southern Coast", 7_000_000, 0.30, "Coastal"),
        Region("Western Zone", 4_500_000, 0.25, "Western"),
        Region("Zanzibar", 1_900_000, 0.45, "Zanzibar"),
    ],
    languages=["Swahili", "English"],
    local_language_style="Swahili-heavy, mixed with English",
    context=(
        "Tanzania (population about 67 million, capital Dodoma, largest city Dar es Salaam, currency Tanzania shilling, TZS). "
        "Swahili dominates online discourse; Instagram and WhatsApp are major platforms and access to X has been "
        "restricted at times. Recurring concerns: cost of living, jobs, service delivery and civic space."
    ),
    online_age_dist={"18-24": 0.34, "25-34": 0.34, "35-44": 0.17, "45-54": 0.09, "55-64": 0.04, "65+": 0.02},
    online_male_share=0.56,
    platform_reach={"whatsapp": 0.88, "instagram": 0.65, "facebook": 0.60, "youtube": 0.55, "tiktok": 0.50, "x": 0.15, "telegram": 0.20, "linkedin": 0.08, "reddit": 0.01},
    trust={"government": 0.50, "parliament": 0.40, "media": 0.50, "religious": 0.75, "civil_society": 0.50, "business": 0.40, "international": 0.50, "experts": 0.55},
    leaning_dist={"pro_government": 0.42, "opposition": 0.28, "non_aligned": 0.30},
    leaning_labels=LEANING_LABELS,
    first_names_male=["Juma", "Hamisi", "Baraka", "Godfrey", "Emmanuel", "Rashidi", "Salim", "Joseph", "Daudi", "Elia", "Frank", "Hassani", "Ally", "Mussa", "Abdallah", "Erasto"],
    first_names_female=["Neema", "Rehema", "Zawadi", "Mwanaisha", "Asha", "Halima", "Upendo", "Grace", "Happiness", "Witness", "Faraja", "Salma", "Mariamu", "Agnes", "Restituta", "Imani"],
    surnames=["Mollel", "Massawe", "Mushi", "Kimaro", "Mwakyusa", "Mwakalinga", "Shirima", "Lyimo", "Komba", "Ngowi", "Mrema", "Swai", "Kombo", "Juma", "Salim", "Hassan", "Mbwambo", "Temba", "Mahenge", "Mfinanga"],
    handle_suffixes=["TZ", "_tz", "255", "", "", ""],
    google_news=("en-TZ", "TZ"),
    gdelt_name="tanzania",
    subreddits=["tanzania"],
    local_queries=True,
    flavor={
        "anger": ["Hii haikubaliki!", "Tumechoka jamani.", "This is too much."],
        "fear": ["Hii itatuumiza.", "Tunaenda wapi?"],
        "sadness": ["Maisha ni magumu sana.", "Life is hard."],
        "joy": ["Habari njema kabisa!", "Hii ni nzuri sana."],
        "hope": ["Hii ni hatua nzuri.", "Tuone itakavyokwenda."],
        "trust": ["Naunga mkono.", "Hii ina mantiki."],
        "neutral": ["Nani atatueleza hili?", "What does this mean for wananchi?"],
    },
    archetype_labels={"boda_rider": "Bodaboda rider", "matatu_operator": "Daladala operator", "market_vendor": "Market vendor (mama lishe)", "casual_labourer": "Casual worker"},
    media_outlets=["DarNewsDesk", "TanzaniaDailyWire", "DodomaFM"],
)

RWANDA = CountryProfile(
    code="RW",
    name="Rwanda",
    demonym="Rwandan",
    capital="Kigali",
    currency="RWF",
    region_label="Province",
    regions=[
        Region("Kigali", 1_750_000, 0.90, "Kigali"),
        Region("Eastern", 3_560_000, 0.15, "Eastern"),
        Region("Southern", 3_000_000, 0.12, "Southern"),
        Region("Northern", 2_040_000, 0.12, "Northern"),
        Region("Western", 2_900_000, 0.15, "Western"),
    ],
    languages=["Kinyarwanda", "English", "French", "Swahili"],
    local_language_style="English with occasional Kinyarwanda expressions",
    context=(
        "Rwanda (population about 14 million, capital Kigali, currency Rwandan franc, RWF). Recurring concerns: cost of living, "
        "jobs, service delivery and regional affairs. Online political discourse tends to be more restrained; "
        "officials are active on X."
    ),
    online_age_dist={"18-24": 0.32, "25-34": 0.35, "35-44": 0.18, "45-54": 0.09, "55-64": 0.04, "65+": 0.02},
    online_male_share=0.55,
    platform_reach={"whatsapp": 0.90, "youtube": 0.50, "facebook": 0.50, "instagram": 0.40, "tiktok": 0.40, "x": 0.35, "telegram": 0.10, "linkedin": 0.12, "reddit": 0.01},
    trust={"government": 0.65, "parliament": 0.55, "media": 0.50, "religious": 0.70, "civil_society": 0.50, "business": 0.45, "international": 0.50, "experts": 0.55},
    leaning_dist={"pro_government": 0.55, "opposition": 0.15, "non_aligned": 0.30},
    leaning_labels=LEANING_LABELS,
    first_names_male=["Jean Claude", "Eric", "Patrick", "Emmanuel", "Olivier", "Innocent", "Jean Paul", "Fabrice", "Didier", "Thierry", "Gilbert", "Yves"],
    first_names_female=["Aline", "Diane", "Claudine", "Ange", "Josiane", "Clarisse", "Grace", "Alice", "Divine", "Sandrine", "Esperance", "Liliane"],
    surnames=["Uwimana", "Niyonzima", "Mugisha", "Habimana", "Nshimiyimana", "Uwase", "Mukamana", "Ndayisaba", "Hakizimana", "Iradukunda", "Kayitesi", "Mutoni", "Nkurunziza", "Bizimana", "Ingabire", "Umutoni"],
    handle_suffixes=["RW", "_rw", "250", "", "", ""],
    google_news=None,
    gdelt_name="rwanda",
    subreddits=["rwanda"],
    flavor={
        "anger": ["This is not acceptable.", "People are tired of this."],
        "fear": ["I'm worried about the impact.", "This could hurt families."],
        "sadness": ["Life is getting harder.", "Many are struggling."],
        "joy": ["Great news for us!", "This is good."],
        "hope": ["Let's see how it works.", "This could help."],
        "trust": ["This makes sense.", "I support this step."],
        "neutral": ["Who will explain the details?", "What does this mean for ordinary people?"],
    },
    archetype_labels={"boda_rider": "Moto (motorbike taxi) rider", "matatu_operator": "Bus operator", "market_vendor": "Market vendor", "casual_labourer": "Casual worker"},
    media_outlets=["KigaliNewsDesk", "RwandaDailyWire", "KigaliFM"],
)

ETHIOPIA = CountryProfile(
    code="ET",
    name="Ethiopia",
    demonym="Ethiopian",
    capital="Addis Ababa",
    currency="ETB",
    region_label="Region",
    regions=[
        Region("Addis Ababa", 5_500_000, 1.00, "Addis Ababa"),
        Region("Oromia", 40_000_000, 0.18, "Oromia"),
        Region("Amhara", 23_000_000, 0.18, "Amhara"),
        Region("Southern regions", 21_000_000, 0.15, "South"),
        Region("Sidama", 4_500_000, 0.15, "South"),
        Region("Somali", 6_500_000, 0.20, "Somali"),
        Region("Tigray", 6_000_000, 0.25, "Tigray"),
        Region("Afar", 2_000_000, 0.15, "Afar"),
        Region("Dire Dawa & Harari", 800_000, 0.70, "East"),
        Region("Benishangul-Gumuz & Gambela", 1_700_000, 0.15, "West"),
    ],
    languages=["Amharic", "Afaan Oromo", "Tigrinya", "English"],
    local_language_style="English with occasional Amharic expressions",
    context=(
        "Ethiopia (population about 125 million, capital Addis Ababa, currency birr, ETB). Telegram and Facebook dominate "
        "online discourse. Recurring concerns: inflation, conflict and security, jobs, the currency, and regional politics."
    ),
    online_age_dist={"18-24": 0.35, "25-34": 0.35, "35-44": 0.16, "45-54": 0.08, "55-64": 0.04, "65+": 0.02},
    online_male_share=0.62,
    platform_reach={"telegram": 0.80, "facebook": 0.75, "youtube": 0.55, "tiktok": 0.45, "whatsapp": 0.40, "instagram": 0.25, "x": 0.15, "linkedin": 0.08, "reddit": 0.01},
    trust={"government": 0.45, "parliament": 0.35, "media": 0.45, "religious": 0.80, "civil_society": 0.45, "business": 0.40, "international": 0.45, "experts": 0.55},
    leaning_dist={"pro_government": 0.35, "opposition": 0.30, "non_aligned": 0.35},
    leaning_labels=LEANING_LABELS,
    first_names_male=["Abebe", "Dawit", "Yonas", "Tesfaye", "Bereket", "Henok", "Mulugeta", "Getachew", "Solomon", "Biniam", "Abdi", "Mohammed", "Lemma", "Fikru", "Natnael", "Kaleb"],
    first_names_female=["Hiwot", "Selam", "Meron", "Tigist", "Bethlehem", "Mekdes", "Helen", "Rahel", "Saba", "Liya", "Hana", "Feven", "Lensa", "Chaltu", "Tsion", "Mahlet"],
    surnames=["Tadesse", "Bekele", "Alemu", "Haile", "Girma", "Kebede", "Tesfaye", "Desta", "Mekonnen", "Wolde", "Gebre", "Abera", "Lemma", "Ayele", "Tefera", "Hussein", "Ahmed", "Chala", "Gudeta", "Negash"],
    handle_suffixes=["ET", "_et", "251", "", "", ""],
    google_news=("en-ET", "ET"),
    gdelt_name="ethiopia",
    subreddits=["Ethiopia"],
    flavor={
        "anger": ["This is unacceptable.", "People are fed up."],
        "fear": ["I'm worried about what comes next.", "This could make things worse."],
        "sadness": ["Prices are crushing families.", "Life is very hard now."],
        "joy": ["Good news, finally!", "This is great."],
        "hope": ["Let's hope this helps.", "Maybe this is a step forward."],
        "trust": ["This makes sense.", "I support it."],
        "neutral": ["Who will explain the details?", "What does this mean for ordinary families?"],
    },
    archetype_labels={"boda_rider": "Bajaj/motorbike driver", "matatu_operator": "Minibus operator", "market_vendor": "Market vendor", "casual_labourer": "Daily labourer"},
    media_outlets=["AddisNewsDesk", "EthiopiaDailyWire", "AddisFM"],
)

GENERIC = CountryProfile(
    code="XX",
    name="Generic African country",
    demonym="citizen",
    capital="the capital",
    currency="local currency",
    region_label="Region",
    regions=[
        Region("Capital city", 5_000_000, 1.00, "Urban"),
        Region("Secondary cities", 6_000_000, 0.85, "Urban"),
        Region("Peri-urban areas", 6_000_000, 0.50, "Peri-urban"),
        Region("Rural areas", 18_000_000, 0.10, "Rural"),
    ],
    languages=["English"],
    local_language_style="Plain English",
    context="An African country with a young, mobile-first population. Recurring concerns: cost of living, jobs, taxes, service delivery and corruption.",
    online_age_dist=ONLINE_AGE_DEFAULT,
    online_male_share=0.55,
    platform_reach={"whatsapp": 0.90, "facebook": 0.75, "youtube": 0.60, "tiktok": 0.50, "instagram": 0.40, "x": 0.25, "telegram": 0.25, "linkedin": 0.12, "reddit": 0.02},
    trust={"government": 0.35, "parliament": 0.30, "media": 0.50, "religious": 0.70, "civil_society": 0.50, "business": 0.40, "international": 0.50, "experts": 0.55},
    leaning_dist={"pro_government": 0.33, "opposition": 0.33, "non_aligned": 0.34},
    leaning_labels=LEANING_LABELS,
    first_names_male=KENYA.first_names_male[:10] + NIGERIA.first_names_male[:8] + GHANA.first_names_male[:6] + SOUTH_AFRICA.first_names_male[:6],
    first_names_female=KENYA.first_names_female[:10] + NIGERIA.first_names_female[:8] + GHANA.first_names_female[:6] + SOUTH_AFRICA.first_names_female[:6],
    surnames=KENYA.surnames[:10] + NIGERIA.surnames[:8] + GHANA.surnames[:6] + SOUTH_AFRICA.surnames[:6],
    handle_suffixes=["", "_", "01", "x", ""],
    google_news=None,
    gdelt_name="",
    subreddits=["Africa"],
    flavor={
        "anger": ["This is unacceptable.", "Enough is enough!"],
        "fear": ["I'm worried about this.", "This could hurt a lot of people."],
        "sadness": ["Life is already hard.", "People are struggling."],
        "joy": ["Great news!", "Finally!"],
        "hope": ["Let's give it a chance.", "This could help."],
        "trust": ["This makes sense.", "I support this."],
        "neutral": ["Who will explain this to us?", "What does this mean for ordinary people?"],
    },
    media_outlets=["NewsDesk", "DailyWire", "CityFM"],
)

COUNTRIES: dict[str, CountryProfile] = {
    profile.code: profile for profile in [KENYA, NIGERIA, SOUTH_AFRICA, GHANA, UGANDA, TANZANIA, RWANDA, ETHIOPIA, GENERIC]
}


def get_country(code: str | None) -> CountryProfile:
    return COUNTRIES.get((code or "").upper(), KENYA if not code else GENERIC)


def country_choices() -> list[tuple[str, str]]:
    return [(c.code, c.name) for c in COUNTRIES.values()]
