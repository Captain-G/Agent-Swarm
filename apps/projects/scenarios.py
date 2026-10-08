"""
Ready-made scenario prompts so first-time users can start in one click.

These are *hypothetical scenario framings* inspired by recurring policy debates
across Africa. They are starting points to edit, not statements of fact; the
research stage gathers current evidence before any simulation runs.
"""

SCENARIOS = [
    {
        "key": "ke-finance-bill",
        "country": "KE",
        "category": "Tax & budget",
        "icon": "receipt",
        "title": "Finance Bill: VAT on bread and mobile money",
        "prompt": (
            "The Kenyan National Treasury proposes a Finance Bill that introduces 16% VAT on bread and raises "
            "excise duty on mobile money transfer fees from 15% to 20% to close the budget deficit. How will Kenyans "
            "react online during the two weeks before the parliamentary vote, which groups will oppose it most, and "
            "what communication or policy adjustments could reduce the backlash?"
        ),
    },
    {
        "key": "ke-health-insurance",
        "country": "KE",
        "category": "Health",
        "icon": "heart-pulse",
        "title": "Health insurance contributions for informal workers",
        "prompt": (
            "Kenya's Social Health Authority requires all households, including informal-sector workers, to pay "
            "2.75% of household income as health insurance contributions, assessed through a means-testing tool. "
            "Simulate public sentiment over the first month of rollout and identify the main concerns and the most "
            "persuasive messages."
        ),
    },
    {
        "key": "ke-housing-levy",
        "country": "KE",
        "category": "Tax & budget",
        "icon": "building",
        "title": "Housing levy on salaries",
        "prompt": (
            "The government introduces a 1.5% housing levy deducted from all gross salaries (matched by employers) "
            "to fund affordable housing. How will salaried workers, employers and the unemployed react, and how "
            "does sentiment change if the government publishes a list of completed housing units?"
        ),
    },
    {
        "key": "ke-fuel-price",
        "country": "KE",
        "category": "Cost of living",
        "icon": "fuel",
        "title": "Fuel price increase",
        "prompt": (
            "The energy regulator raises pump prices by KES 15 per litre citing global oil prices and the end of a "
            "fuel stabilisation subsidy. Simulate the online reaction of boda boda riders, matatu operators, farmers "
            "and urban commuters over ten days."
        ),
    },
    {
        "key": "ke-cashless-fares",
        "country": "KE",
        "category": "Transport",
        "icon": "smartphone",
        "title": "Mandatory cashless matatu fares",
        "prompt": (
            "Kenya's transport ministry announces that all public service vehicles (matatus) must accept only "
            "cashless fare payments within six months. How will commuters, matatu operators and conductors react?"
        ),
    },
    {
        "key": "ke-university-funding",
        "country": "KE",
        "category": "Education",
        "icon": "graduation-cap",
        "title": "New university funding model",
        "prompt": (
            "A new university funding model replaces flat government capitation with means-tested scholarships and "
            "loans, meaning some families pay higher fees. Simulate the reaction of students, parents and lecturers."
        ),
    },
    {
        "key": "ng-fuel-subsidy",
        "country": "NG",
        "category": "Cost of living",
        "icon": "fuel",
        "title": "Petrol subsidy removal",
        "prompt": (
            "Nigeria's federal government removes the remaining petrol subsidy and pump prices nearly double "
            "overnight, while promising cash transfers to vulnerable households. Simulate public sentiment over two "
            "weeks and test whether visible palliatives change the trajectory."
        ),
    },
    {
        "key": "gh-e-levy",
        "country": "GH",
        "category": "Tax & budget",
        "icon": "smartphone",
        "title": "Levy on electronic money transfers",
        "prompt": (
            "Ghana's government proposes a 1.5% levy on electronic money transfers above GHS 100 per day to widen "
            "the tax net. How will mobile money users, traders and the tech community react online?"
        ),
    },
    {
        "key": "za-basic-income",
        "country": "ZA",
        "category": "Social protection",
        "icon": "hand-coins",
        "title": "Basic income grant funded by VAT",
        "prompt": (
            "South Africa proposes turning a temporary distress grant into a permanent basic income grant for "
            "unemployed adults, funded partly by a one percentage point VAT increase. Simulate the debate between "
            "beneficiaries, taxpayers, economists and business."
        ),
    },
    {
        "key": "ug-ott-tax",
        "country": "UG",
        "category": "Digital",
        "icon": "wifi",
        "title": "Daily social media tax",
        "prompt": (
            "Uganda's government proposes a daily tax on access to social media platforms to raise revenue. "
            "Simulate the reaction of young people, content creators and small businesses that sell online."
        ),
    },
    {
        "key": "corp-data-prices",
        "country": "KE",
        "category": "Business",
        "icon": "signal",
        "title": "Telecom raises data bundle prices",
        "prompt": (
            "A leading mobile network operator announces a 20% increase in data bundle prices and the end of "
            "unlimited night bundles. How will customers react online, and which apology or compensation "
            "messages would best recover sentiment?"
        ),
    },
    {
        "key": "health-vaccine-campaign",
        "country": "KE",
        "category": "Public health",
        "icon": "syringe",
        "title": "School HPV vaccination campaign",
        "prompt": (
            "The Ministry of Health launches a school-based HPV vaccination campaign for girls aged 10 to 14. "
            "Rumours about side effects start spreading on WhatsApp. Simulate parents' and religious leaders' "
            "reactions and test which fact-check messages and messengers work best."
        ),
    },
]

SCENARIOS_BY_KEY = {s["key"]: s for s in SCENARIOS}
