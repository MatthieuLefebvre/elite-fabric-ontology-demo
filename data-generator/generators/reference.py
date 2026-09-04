"""Generate firm-isolated reference entities from authored fictional names."""

from __future__ import annotations

from .common import PRACTICES, Config, Tables, identifier

GIVEN_NAMES = ("Briony", "Corin", "Delia", "Emrys", "Fenella", "Galen", "Hester", "Isolde")
SURNAMES = ("Alderwick", "Bellmere", "Cresswell", "Doveton", "Everleigh")
INDUSTRIES = ("industrials", "investment_holdings", "life_sciences", "retail", "energy",
              "logistics", "manufacturing", "financial_services", "agriculture", "maritime",
              "textiles", "materials", "instruments", "technology", "food")


def generate_reference(config: Config, tables: Tables) -> None:
    """Populate two offices/entities per firm and currency-specific standard rates."""
    sim = config["simulation"]
    for firm in config["firms"]:
        slug = firm["slug"]
        firm_id = identifier(slug, "f", 1)
        given_names = firm.get("given_names", GIVEN_NAMES)
        surnames = firm.get("surnames", SURNAMES)
        tables["firms"].append({"firm_id": firm_id, "slug": slug,
                                "firm_name": firm["name"], "reporting_currency": "USD"})
        for number, (city, country, currency) in enumerate(
            (("New York", "US", "USD"), ("London", "GB", "GBP")), start=1
        ):
            office_id = identifier(slug, "o", number)
            tables["offices"].append({"office_id": office_id, "firm_id": firm_id,
                                      "office_name": city, "country_code": country})
            tables["legal_entities"].append({
                "legal_entity_id": identifier(slug, "le", number), "firm_id": firm_id,
                "office_id": office_id, "legal_entity_name": f"{firm['name']} — {country}",
                "currency": currency, "jurisdiction": "US-NY" if country == "US" else "GB-EAW",
            })
        for number, practice in enumerate(PRACTICES, start=1):
            tables["practice_groups"].append({
                "practice_group_id": identifier(slug, "pg", number), "firm_id": firm_id,
                "practice_group_name": practice,
                "group_head_id": identifier(slug, "t", number),
            })
        for number in range(1, firm["timekeepers"] + 1):
            is_uk = number > firm["timekeepers"] - 2
            entity_number = 2 if is_uk else 1
            name_index = number - 2
            name = (firm["partner_name"] if number == 1 else
                    f"{given_names[name_index % len(given_names)]} "
                    f"{surnames[(name_index // len(given_names)) % len(surnames)]}")
            if number > len(given_names) * len(surnames) + 1:
                name += f" {number}"
            tables["timekeepers"].append({
                "timekeeper_id": identifier(slug, "t", number), "firm_id": firm_id,
                "office_id": identifier(slug, "o", entity_number),
                "legal_entity_id": identifier(slug, "le", entity_number),
                "practice_group_id": identifier(slug, "pg", (number - 1) % len(PRACTICES) + 1),
                "full_name": name, "role": "partner" if number <= len(PRACTICES) else "associate",
                "grade": "partner" if number <= len(PRACTICES) else ("senior_associate" if number % 2 else "associate"),
                "office": "London" if is_uk else "New York",
                "department": PRACTICES[(number - 1) % len(PRACTICES)],
                "standard_rate_cents": sim["gbp_standard_rate_cents" if is_uk else
                                            "usd_standard_rate_cents"],
                "standard_rate_currency": "GBP" if is_uk else "USD", "active": True,
            })
        for number, name in enumerate(firm.get("client_names", config["clients"])[:firm["clients"]], start=1):
            tables["clients"].append({"client_id": identifier(slug, "c", number),
                                      "firm_id": firm_id, "client_name": name,
                                      "industry": INDUSTRIES[(number - 1) % len(INDUSTRIES)],
                                      "relationship_partner_id": identifier(slug, "t", (number - 1) % 3 + 1),
                                      "credit_status": "watch" if number == 8 else "good"})