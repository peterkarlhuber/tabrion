# GOV-1: Name and Trademark Check: "Tabrion" / "Tabrion Studio"

- **Names:** "Tabrion" (Python database library), "Tabrion Studio" (database desktop/web tool)
- **Nice classes:** 9 (software), 42 (SaaS / software services)
- **Date of checks:** 2026-11-08
- **Excluded:** `peterkarlhuber/tabrion` and `peterkarlhuber/tabrion-studio` (the owner's own repos)

> **Update 2026-11-08 (owner decision of 2026-11-08 11:26Z):** the owner kept the name (GOV-1 "done:
> conditional") and did not order the manual follow-ups below; they are no prerequisite of gate G-T2. Tabrion is
> consumed through Git SHA pins without public marketing. A later public marketing under this name needs a new
> assessment. The report below is unchanged.

## Summary verdict

**G-0 not reached. HOLD: possible ST-10, needs a human decision.**

1. **Identical name already in use for software (unregistered use found):** "Tabrion" is a free Windows desktop app for merging Excel/CSV files and joining tables. It is published on the Microsoft Store (publisher "ALHUSSAIN, ALBURAEE"; per its README, v3.4.1 passed Store certification in October 2026). Source code: GitHub `Hussain-007M/Tabrion`. The name is identical, the goods are class 9 software, and the field (tabular data / table joins) is close to a database library and a database tool. I found no registration for it, but prior unregistered use can still give rights in some jurisdictions, and it would at least cause confusion between the products.
2. **No registered "Tabrion" mark was verified either way.** EUIPO/TMview, DPMA and WIPO Global Brand Database could not be queried automatically (details below). None of the trademark registers has been checked yet.
3. Package names (PyPI, npm) and the domains `.dev`, `.io`, `.de` appear free. `tabrion.com` is registered by a third party and parked.

ST-10 (identical conflicting **mark**) is not confirmed, because no registered mark was found and the registers were not searched. The identical prior use for related software is a material risk, though, so G-0 should not be declared until the manual follow-ups below are done.

## Results per source

| Source | Query | Date | Result | Evidence URL |
|---|---|---|---|---|
| PyPI (JSON API) | `tabrion` | 2026-11-08 | HTTP 404: name not taken | https://pypi.org/pypi/tabrion/json |
| PyPI (JSON API) | `tabrion-studio`, `tabrion_studio` | 2026-11-08 | HTTP 404: name not taken | https://pypi.org/pypi/tabrion-studio/json |
| PyPI (simple index) | `tabrion`, `tabrion-studio` | 2026-11-08 | HTTP 404 | https://pypi.org/simple/tabrion/ |
| PyPI (search page) | `tabrion` | 2026-11-08 | **Not evaluated**: page served a JavaScript challenge, no results in HTML | https://pypi.org/search/?q=tabrion |
| npm registry | `tabrion`, `tabrion-studio` | 2026-11-08 | HTTP 404: name not taken | https://registry.npmjs.org/tabrion |
| npm search API | `text=tabrion` | 2026-11-08 | 0 results | https://registry.npmjs.org/-/v1/search?text=tabrion |
| GitHub repo search (API) | `tabrion` (name, description, readme) | 2026-11-08 | 3 repos: `Hussain-007M/Tabrion` (**third party, see verdict**) plus the owner's 2 repos | https://github.com/Hussain-007M/Tabrion |
| GitHub user search (API) | `tabrion` | 2026-11-08 | Account `tabrion` (type User) exists; the profile could not be opened (HTTP 403 from this environment) | https://github.com/tabrion |
| Microsoft Store | product 9N3JNHRFQ9BL (linked from that README) | 2026-11-08 | Listing "Tabrion", publisher "ALHUSSAIN, ALBURAEE" | https://apps.microsoft.com/detail/9N3JNHRFQ9BL |
| Domain tabrion.com | RDAP (Verisign) and HTTP | 2026-11-08 | **Registered** since 2011-02-02 (registrar Alpine Domains Inc., expires 2029-02-02); parked page "Tabrion.com - Ready for Development", leasing offered | https://rdap.verisign.com/com/v1/domain/tabrion.com |
| Domain tabrion.dev | RDAP (Google Registry) and DNS | 2026-11-08 | RDAP 404, DNS NXDOMAIN: appears unregistered | https://pubapi.registry.google/rdap/domain/tabrion.dev |
| Domain tabrion.io | RDAP (Identity Digital) and DNS | 2026-11-08 | RDAP 404, DNS NXDOMAIN: appears unregistered | https://rdap.identitydigital.services/rdap/domain/tabrion.io |
| Domain tabrion.de | RDAP (DENIC) and DNS | 2026-11-08 | RDAP "No such domain", DNS NXDOMAIN: appears unregistered | https://rdap.denic.de/domain/tabrion.de |
| Domain tabrionstudio.com | RDAP (Verisign) and DNS | 2026-11-08 | RDAP 404, DNS NXDOMAIN: appears unregistered | https://rdap.verisign.com/com/v1/domain/tabrionstudio.com |
| EUIPO eSearch / TMview | `tabrion` | 2026-11-08 | **Not checked**: TMview returned "Request Rejected"; the EUIPO web search gave an empty reply; the EUIPO API needs credentials (HTTP 401) | https://www.tmdn.org/tmview/ |
| DPMA register | `tabrion` (basic search) | 2026-11-08 | **Not checked**: only the search form came back, no result list | https://register.dpma.de/DPMAregister/marke/basis |
| WIPO Global Brand Database | `tabrion` | 2026-11-08 | **Not checked**: page is behind a JavaScript/ALTCHA challenge | https://branddb.wipo.int/ |
| General web search | `"Tabrion" trademark software` | 2026-11-08 | No results mentioning a Tabrion trademark | n/a |

Note: whois/dig were not installed. Domain status comes from RDAP plus DNS-over-HTTPS (dns.google). "Appears unregistered" means only that; the domain is not confirmed available until a registrar check.

## Manual follow-ups

1. **TMview** (https://www.tmdn.org/tmview/): search the word mark `TABRION` and similar spellings (`TABRIAN`, `TABRYON`, `TABRIO`) in classes 9 and 42, all offices.
2. **EUIPO eSearch plus** (https://euipo.europa.eu/eSearch/): search `TABRION` for EU trademarks, classes 9 and 42.
3. **DPMAregister** (https://register.dpma.de/DPMAregister/marke/basis): search `tabrion` for German marks.
4. **WIPO Global Brand Database** (https://branddb.wipo.int/): search `tabrion` for international registrations (Madrid) and national marks.
5. **USPTO** (https://tmsearch.uspto.gov/), if the US market matters. Not part of the work package and not checked.
6. **Assess the prior user** "Tabrion" (Microsoft Store app 9N3JNHRFQ9BL / GitHub `Hussain-007M/Tabrion`): when did use start, in which countries, and is there any application filed by "ALHUSSAIN, ALBURAEE"? Then decide whether this triggers ST-10 or a rename. Legal advice is recommended.
7. **GitHub account `tabrion`**: open https://github.com/tabrion in a browser and check whether it is active or related to the app above.
8. **PyPI search page**: open https://pypi.org/search/?q=tabrion in a browser to look for similar names. Exact names were already confirmed free via the API.
9. **Domains**: confirm `tabrion.dev`, `tabrion.io`, `tabrion.de` and `tabrionstudio.com` are available at a registrar. `tabrion.com` is held by a domain investor (Alpine Domains) and would have to be bought or leased.
