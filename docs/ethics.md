# Ethics and safeguards

Simulating public opinion is powerful. It can help governments explain policies better, organisations listen before acting, and civil society anticipate harms. The same capability could be misused to manipulate people. Baraza is built for the first purpose and this document explains the safeguards, the legal context and the responsibilities of users.

## Purpose

Use Baraza to:

* anticipate how different groups may be affected by and react to a policy, and why;
* improve policies before they are adopted (for example by testing concessions or safeguards);
* prepare clear, honest communication and responses to misinformation;
* plan public participation that reaches the people most affected;
* teach and research opinion dynamics, polarisation and misinformation.

Do not use Baraza to design disinformation, astroturfing or harassment campaigns, to target or profile individuals, to suppress participation, or to incite hostility between groups.

## Built-in safeguards

| Safeguard | How it works |
|---|---|
| Request screening | Requests to incite violence or hatred, promote genocide or ethnic cleansing, or target private individuals (for example by doxxing) are refused; requests about manipulating voters, bot armies or ethnic framing get a caution and are logged in the brief. |
| Synthetic people only | Agents are invented people with synthetic names. Real public figures are represented only through their institutions (a ministry, a party, a union), never impersonated as individuals. |
| No ethnicity | The population has no ethnic attributes, so results cannot be read as statements about ethnic groups. Regional differences reflect modelled economic and demographic exposure, not identity. |
| No AI-written disinformation | Rumours and posts by coordinated accounts are only ever written from templates or from text you supply; the language model never writes them. Misinformation in a simulation exists to rehearse responses to it. |
| No publishing | Baraza has no ability to post to any real platform. Exports are labelled as simulated. |
| Data minimisation | Only public content is collected; authors are pseudonymised by default with a keyed hash; robots.txt is respected; Baraza never logs in to personal accounts or circumvents access controls; private messaging (WhatsApp, private groups) is never collected. |
| Local by default | Everything (data, models, results) stays on your computer unless you choose a hosted model or deploy it on a server. |
| Honest uncertainty | Every report includes a confidence and limitations section; the interface encourages ensembles and validation and warns against reading simulations as polls. |
| Transparency | The method is documented, every number can be traced to its round, agents' opinion histories can be inspected, and runs are reproducible from their seed. |

## Data protection law

Collected posts are personal data when they relate to identifiable people, even if public. Know the law that applies to you:

* **Kenya: Data Protection Act, 2019** and its 2021 regulations; supervised by the Office of the Data Protection Commissioner (ODPC). Key duties: lawful basis, purpose limitation, data minimisation, storage limitation, security, and registration as a data controller/processor where required.
* **Nigeria: Nigeria Data Protection Act, 2023** (Nigeria Data Protection Commission).
* **South Africa: Protection of Personal Information Act (POPIA), 2013** (Information Regulator). POPIA allows processing for research with appropriate safeguards.
* **Ghana: Data Protection Act, 2012**; **Uganda: Data Protection and Privacy Act, 2019**; **Rwanda: Law No. 058/2021**; **Tanzania: Personal Data Protection Act, 2022**; **Ethiopia: Personal Data Protection Proclamation No. 1321/2024**.
* If you process data about people in the EU, the GDPR also applies.

Practical steps:

1. Keep `ANONYMIZE_SOCIAL_AUTHORS=True` unless you have a clear reason and legal basis not to.
2. Collect only what your question needs; untick and delete irrelevant sources.
3. Do not publish raw collected posts with identifying details; quote paraphrased or aggregated findings.
4. Delete projects when the work is done (Projects → Delete removes all its data).
5. If you deploy Baraza for others, turn on `REQUIRE_LOGIN`, use HTTPS (`SECURE_COOKIES=True`), restrict access, and document who can see what.
6. Respect each platform's terms of service; prefer official APIs and researcher-access programmes (see [data-sources.md](data-sources.md)).

## For researchers

Studies using Baraza with real social media data generally need research ethics approval from your institution (and, in Kenya, a research licence from NACOSTI for research conducted in the country). Points reviewers commonly ask about:

* the lawful basis and platform terms for each data source;
* pseudonymisation and storage security;
* that no human participants are deceived or contacted (Baraza does neither);
* the risk that results are misused for manipulation, and how dissemination will mitigate it;
* the limits of synthetic populations and how conclusions will be validated.

## For governments and organisations

* Treat simulation output as one input among many; it does not replace genuine public participation, which in Kenya is a constitutional requirement (Articles 10 and 118 of the Constitution).
* Use what you learn to improve the policy and to explain it honestly. Messages that work in a simulation because they mislead are not acceptable messages.
* Be prepared to explain how you used the tool if asked; publish your method when possible.
* When a simulation shows a group would be badly affected, the right response is usually to engage that group, not to find a way to make them stop objecting.

## Reporting misuse

If you become aware of Baraza being used for manipulation or harassment, report it to the platform concerned and, where relevant, to the data protection authority. Contributions that strengthen safeguards are very welcome.
