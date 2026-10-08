"""
Citizen archetypes for synthetic populations.

Each archetype describes a recognisable segment of the online public in
African countries (e.g. boda boda riders, market vendors, Gen Z activists),
with demographic ranges, personality tendencies, platform habits, typical
concerns and keywords used to map research findings ("who is affected and
how") onto initial opinions.

Base weights approximate each segment's share of the *online* population and
are deliberately easy to tune per project.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Archetype:
    key: str
    label: str
    occupation: str
    age: tuple[int, int]
    female_share: float
    urban_prob: float
    income: str  # low | lower_middle | middle | upper
    education: str  # primary | secondary | tertiary
    weight: float
    concerns: tuple[str, ...]
    keywords: tuple[str, ...]
    big5_shift: dict = field(default_factory=dict)  # o, c, e, a, n deltas
    platform_shift: dict = field(default_factory=dict)  # microblog, forum, chat multipliers
    activity: float = 1.0
    leaning_shift: dict = field(default_factory=dict)  # pro_government, opposition, non_aligned multipliers
    credibility: float = 0.35
    voice: str = "plain, direct"


ARCHETYPES: list[Archetype] = [
    Archetype(
        "student", "University student", "University/college student", (18, 25), 0.48, 0.72, "low", "tertiary", 0.09,
        ("tuition fees", "student loans", "jobs after graduation", "cost of living"),
        ("student", "students", "university", "college", "youth", "education", "tuition", "fees", "loan", "scholarship", "learners"),
        {"o": 0.12, "e": 0.08, "c": -0.05}, {"microblog": 1.5, "forum": 0.9, "chat": 1.0}, 1.3,
        {"opposition": 1.15, "non_aligned": 1.2}, 0.30, "casual, witty, uses memes and slang",
    ),
    Archetype(
        "genz_activist", "Gen Z digital activist", "Student / early-career activist", (19, 28), 0.47, 0.85, "low", "tertiary", 0.05,
        ("accountability", "corruption", "governance", "youth jobs", "police conduct"),
        ("youth", "gen z", "young people", "protest", "protesters", "activists", "accountability", "governance", "corruption", "digital", "civic"),
        {"o": 0.18, "e": 0.18, "a": -0.10, "n": 0.05}, {"microblog": 2.0, "forum": 0.8, "chat": 0.9}, 1.9,
        {"opposition": 1.3, "non_aligned": 1.5, "pro_government": 0.4}, 0.40, "passionate, mobilising, uses hashtags and calls to action",
    ),
    Archetype(
        "unemployed_youth", "Job-seeking young graduate", "Unemployed graduate / hustler", (20, 32), 0.45, 0.60, "low", "tertiary", 0.08,
        ("jobs", "cost of living", "opportunities", "fairness of hiring"),
        ("unemployed", "unemployment", "youth", "jobs", "graduates", "hustlers", "employment", "job seekers", "opportunities", "young people"),
        {"n": 0.08, "e": 0.05}, {"microblog": 1.3, "forum": 1.0, "chat": 1.1}, 1.3,
        {"opposition": 1.2, "non_aligned": 1.1}, 0.30, "frustrated, sarcastic, relatable",
    ),
    Archetype(
        "boda_rider", "Motorbike taxi rider", "Motorbike taxi (boda boda) rider", (20, 45), 0.03, 0.50, "low", "secondary", 0.06,
        ("fuel prices", "police harassment", "licence fees", "loan repayments"),
        ("boda", "boda boda", "motorbike", "motorcycle", "riders", "transport", "fuel", "okada", "operators", "transport sector", "petrol"),
        {"c": -0.05, "e": 0.05}, {"microblog": 0.5, "forum": 1.0, "chat": 1.8}, 0.8,
        {}, 0.30, "short, blunt, street-level language",
    ),
    Archetype(
        "market_vendor", "Market vendor", "Market vendor (fresh produce)", (25, 60), 0.80, 0.55, "low", "primary", 0.07,
        ("food prices", "market levies", "customers' purchasing power", "mobile money charges"),
        ("traders", "vendors", "market", "small traders", "informal sector", "food prices", "hawkers", "mama mboga", "retailers", "consumers", "mobile money"),
        {"a": 0.08, "c": 0.05}, {"microblog": 0.3, "forum": 1.0, "chat": 2.0}, 0.7,
        {}, 0.30, "practical, everyday language, talks about prices and customers",
    ),
    Archetype(
        "smallholder_farmer", "Smallholder farmer", "Smallholder farmer", (30, 70), 0.45, 0.05, "low", "primary", 0.08,
        ("fertiliser prices", "crop prices", "weather", "subsidies", "market access"),
        ("farmers", "farming", "agriculture", "fertiliser", "fertilizer", "crops", "rural", "livestock", "maize", "tea", "coffee", "dairy", "subsidy", "food security"),
        {"c": 0.08, "o": -0.08}, {"microblog": 0.3, "forum": 1.3, "chat": 1.8}, 0.6,
        {}, 0.35, "plain, community-minded, cautious",
    ),
    Archetype(
        "sme_owner", "Small business owner", "Small business owner", (26, 55), 0.40, 0.75, "lower_middle", "secondary", 0.06,
        ("taxes", "licences", "access to credit", "compliance costs", "customer demand"),
        ("business", "businesses", "sme", "smes", "msmes", "entrepreneurs", "retail", "shops", "small businesses", "tax compliance", "licences", "traders", "employers"),
        {"c": 0.10, "e": 0.05}, {"microblog": 0.9, "forum": 1.2, "chat": 1.4}, 1.0,
        {}, 0.40, "pragmatic, numbers-focused",
    ),
    Archetype(
        "matatu_operator", "Minibus transport operator", "Public transport operator", (25, 55), 0.10, 0.80, "lower_middle", "secondary", 0.03,
        ("fuel prices", "fares", "police checks", "regulation"),
        ("matatu", "transport", "fares", "commuters", "psv", "operators", "fuel", "public transport", "bus", "transporters"),
        {"e": 0.08, "a": -0.05}, {"microblog": 0.6, "forum": 1.0, "chat": 1.6}, 0.8,
        {}, 0.30, "loud, street-smart",
    ),
    Archetype(
        "teacher", "Teacher", "Public school teacher", (25, 58), 0.55, 0.45, "middle", "tertiary", 0.05,
        ("salaries", "curriculum changes", "school funding", "payroll deductions"),
        ("teachers", "education", "schools", "salaries", "civil servants", "unions", "payroll", "deductions", "learners", "curriculum"),
        {"c": 0.12, "a": 0.06}, {"microblog": 0.8, "forum": 1.3, "chat": 1.4}, 0.9,
        {}, 0.50, "measured, explanatory",
    ),
    Archetype(
        "health_worker", "Nurse / health worker", "Nurse or clinical officer", (24, 55), 0.65, 0.60, "middle", "tertiary", 0.04,
        ("health financing", "staffing", "pay", "insurance schemes"),
        ("health", "healthcare", "nurses", "doctors", "hospitals", "health workers", "insurance", "medical", "patients", "clinics"),
        {"c": 0.10, "a": 0.10}, {"microblog": 0.9, "forum": 1.2, "chat": 1.3}, 0.9,
        {}, 0.55, "caring but firm, cites patient experience",
    ),
    Archetype(
        "civil_servant", "Civil servant", "Government employee", (28, 58), 0.45, 0.70, "middle", "tertiary", 0.04,
        ("payroll deductions", "pensions", "job security", "delayed salaries"),
        ("civil servants", "public servants", "government employees", "salaries", "pension", "payroll", "public sector", "workers"),
        {"c": 0.15, "o": -0.05}, {"microblog": 0.7, "forum": 1.2, "chat": 1.4}, 0.8,
        {"pro_government": 1.25}, 0.45, "careful, formal",
    ),
    Archetype(
        "corporate_professional", "Corporate professional", "Banking/telecoms professional", (25, 50), 0.45, 0.95, "middle", "tertiary", 0.05,
        ("income tax", "cost of living", "housing", "economic growth"),
        ("salaried", "employees", "professionals", "income tax", "paye", "middle class", "formal sector", "workers", "employers", "payroll"),
        {"c": 0.10, "o": 0.05}, {"microblog": 1.3, "forum": 1.0, "chat": 1.1}, 1.0,
        {}, 0.50, "articulate, analytical, sometimes ironic",
    ),
    Archetype(
        "tech_worker", "Tech professional", "Software developer / tech worker", (22, 40), 0.30, 0.95, "middle", "tertiary", 0.04,
        ("digital taxes", "internet costs", "startups", "innovation"),
        ("tech", "technology", "digital", "startups", "innovation", "internet", "data", "developers", "online", "digital economy", "ict"),
        {"o": 0.15, "c": 0.05}, {"microblog": 1.7, "forum": 1.2, "chat": 0.9}, 1.2,
        {"non_aligned": 1.3}, 0.50, "sharp, data-driven, sarcastic about bureaucracy",
    ),
    Archetype(
        "content_creator", "Content creator / influencer", "Content creator", (20, 35), 0.50, 0.90, "middle", "secondary", 0.03,
        ("creator taxes", "digital economy", "trends", "audience engagement"),
        ("creators", "influencers", "digital", "online", "content", "social media", "gig", "digital economy", "youth"),
        {"e": 0.25, "o": 0.10}, {"microblog": 2.2, "forum": 0.6, "chat": 0.8}, 2.0,
        {"non_aligned": 1.2}, 0.45, "punchy, humorous, trend-aware",
    ),
    Archetype(
        "gig_worker", "Gig / ride-hailing worker", "Ride-hailing driver or delivery rider", (21, 40), 0.15, 0.95, "low", "secondary", 0.04,
        ("platform commissions", "fuel prices", "taxes on digital work"),
        ("gig", "ride-hailing", "delivery", "drivers", "platform workers", "taxi", "fuel", "digital platforms", "transport"),
        {"n": 0.05}, {"microblog": 1.1, "forum": 0.9, "chat": 1.5}, 1.0,
        {}, 0.30, "tired, practical, blunt",
    ),
    Archetype(
        "landlord_property", "Landlord / property owner", "Landlord", (40, 70), 0.35, 0.60, "upper", "secondary", 0.02,
        ("rental income tax", "land rates", "housing market"),
        ("landlords", "property", "real estate", "housing", "rental income", "land", "property owners", "rates"),
        {"c": 0.10, "o": -0.10}, {"microblog": 0.6, "forum": 1.3, "chat": 1.4}, 0.7,
        {}, 0.40, "conservative, money-minded",
    ),
    Archetype(
        "pensioner", "Retiree / pensioner", "Retired civil servant", (60, 80), 0.50, 0.40, "lower_middle", "secondary", 0.03,
        ("pensions", "healthcare costs", "cost of living"),
        ("pensioners", "elderly", "retirees", "older persons", "pension", "senior citizens", "aged"),
        {"a": 0.10, "o": -0.12, "c": 0.08}, {"microblog": 0.3, "forum": 1.5, "chat": 1.6}, 0.6,
        {}, 0.45, "formal, reflective, references the past",
    ),
    Archetype(
        "religious_leader", "Religious leader", "Pastor / imam / church elder", (35, 70), 0.15, 0.50, "middle", "tertiary", 0.02,
        ("social welfare", "morality", "the poor", "peace"),
        ("church", "churches", "religious", "clergy", "faith", "mosque", "religious leaders", "community", "faith-based"),
        {"a": 0.15, "c": 0.10}, {"microblog": 0.7, "forum": 1.3, "chat": 1.5}, 0.8,
        {}, 0.65, "moral, calm, appeals to unity",
    ),
    Archetype(
        "journalist", "Journalist / blogger", "Journalist", (24, 55), 0.40, 0.90, "middle", "tertiary", 0.02,
        ("accountability", "press freedom", "facts", "public interest"),
        ("media", "journalists", "press", "information", "bloggers", "reporters", "newsrooms"),
        {"o": 0.12, "e": 0.12}, {"microblog": 2.0, "forum": 1.0, "chat": 0.8}, 1.6,
        {"non_aligned": 1.3}, 0.65, "factual, asks probing questions",
    ),
    Archetype(
        "policy_analyst", "Economist / policy analyst", "Economist", (28, 60), 0.35, 0.95, "upper", "tertiary", 0.02,
        ("fiscal policy", "public debt", "growth", "evidence"),
        ("economy", "economic", "fiscal", "debt", "budget", "revenue", "analysts", "economists", "growth", "investors", "imf"),
        {"c": 0.18, "o": 0.18, "n": -0.08}, {"microblog": 1.6, "forum": 1.2, "chat": 0.7}, 1.0,
        {"non_aligned": 1.4}, 0.70, "analytical, cites numbers, balanced",
    ),
    Archetype(
        "diaspora", "Diaspora citizen", "Professional living abroad", (25, 55), 0.45, 1.00, "upper", "tertiary", 0.02,
        ("remittances", "governance", "investment climate"),
        ("diaspora", "remittances", "abroad", "investors", "citizens abroad"),
        {"o": 0.10}, {"microblog": 1.6, "forum": 1.0, "chat": 1.0}, 1.0,
        {"opposition": 1.15}, 0.45, "comparative, references other countries",
    ),
    Archetype(
        "casual_labourer", "Casual worker", "Construction / casual worker", (20, 50), 0.20, 0.65, "low", "primary", 0.06,
        ("daily wages", "food prices", "transport costs"),
        ("casual workers", "informal workers", "labourers", "construction", "low-income", "daily wage", "jua kali", "poor", "workers", "informal sector"),
        {"o": -0.05}, {"microblog": 0.4, "forum": 0.9, "chat": 1.9}, 0.7,
        {}, 0.25, "simple, emotional, everyday struggles",
    ),
    Archetype(
        "parent_homemaker", "Parent / homemaker", "Stay-at-home parent", (24, 50), 0.85, 0.50, "lower_middle", "secondary", 0.04,
        ("school fees", "food prices", "healthcare for children"),
        ("parents", "mothers", "families", "households", "children", "consumers", "school fees", "women", "homes"),
        {"a": 0.10}, {"microblog": 0.6, "forum": 1.3, "chat": 1.8}, 0.8,
        {}, 0.35, "warm, worried about family budgets",
    ),
    Archetype(
        "ngo_worker", "Civil society worker", "NGO / advocacy worker", (25, 50), 0.55, 0.90, "middle", "tertiary", 0.02,
        ("human rights", "inclusion", "governance", "public participation"),
        ("civil society", "ngos", "rights", "human rights", "advocacy", "governance", "inclusion", "public participation", "vulnerable"),
        {"o": 0.15, "a": 0.05}, {"microblog": 1.6, "forum": 1.1, "chat": 1.0}, 1.2,
        {"non_aligned": 1.3}, 0.60, "principled, rights-based arguments",
    ),
    Archetype(
        "lawyer", "Lawyer", "Advocate of the High Court", (26, 60), 0.40, 0.95, "upper", "tertiary", 0.015,
        ("constitutionality", "rule of law", "due process"),
        ("legal", "constitution", "constitutional", "courts", "lawyers", "law society", "rule of law", "judiciary"),
        {"c": 0.15, "o": 0.10}, {"microblog": 1.5, "forum": 1.2, "chat": 0.8}, 1.0,
        {"non_aligned": 1.2}, 0.65, "precise, cites the law",
    ),
]

ARCHETYPES_BY_KEY = {a.key: a for a in ARCHETYPES}

INCOME_LABELS = {"low": "Low income", "lower_middle": "Lower-middle income", "middle": "Middle income", "upper": "Upper income"}


def archetype_label(archetype: Archetype, country) -> str:
    return (getattr(country, "archetype_labels", {}) or {}).get(archetype.key, archetype.label)
