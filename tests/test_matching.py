"""Who an outage could owe a credit to: plan has an SLA, region overlaps, a covered feature is hit."""
import json

from shark.incidents.matching import incident_scope, load_directory, match_customers
from shark.pipeline import ROOT, load_scenarios

SEPT_3 = next(i for i in json.loads((ROOT / "docs/research/real-incidents.json").read_text())["incidents"] if i["id"] == "ws0393rxcgyl")


def statuses(incident):
    return {m["customer"]: m["status"] for m in match_customers(incident, "Atlassian", "Jira", load_directory(), load_scenarios())}


def test_real_us_east_outage_reaches_only_us_east_customers_with_an_sla():
    assert incident_scope(SEPT_3)["regions"] == ["US East"]
    assert statuses(SEPT_3) == {"Northstar Analytics (demo)": "affected", "Harbor Logistics (demo)": "affected",
                                "Cobalt Studio (demo)": "other_region", "Juniper Health (demo)": "other_region", "Pinecrest Labs (demo)": "no_sla"}


def test_region_from_the_update_text_and_unknown_region():
    eu = {"id": "x", "name": "Jira errors", "incident_updates": [{"body": "Customers in Europe may see errors viewing issues."}], "components": [{"name": "Viewing content"}]}
    assert statuses(eu)["Cobalt Studio (demo)"] == "affected" and statuses(eu)["Northstar Analytics (demo)"] == "other_region"
    unknown = {"id": "y", "name": "Jira errors", "incident_updates": [], "components": [{"name": "Viewing content"}]}
    assert set(statuses(unknown).values()) == {"possibly_affected", "no_sla"}


def test_outage_outside_covered_features_owes_nothing():
    marketplace = {"id": "z", "name": "Marketplace apps failing in US East", "incident_updates": [], "components": [{"name": "Marketplace"}]}
    result = statuses(marketplace)
    assert result["Northstar Analytics (demo)"] == "outside_covered" and result["Pinecrest Labs (demo)"] == "no_sla"


def test_every_match_explains_itself():
    for match in match_customers(SEPT_3, "Atlassian", "Jira", load_directory(), load_scenarios()):
        assert match["reason"] and match["plan"] and "simulated" in match


def test_a_case_someone_started_never_widens_which_plans_have_an_sla():
    scenarios = load_scenarios()
    typed = next(s for s in scenarios if s.id == "jira-sept-claim").model_copy(deep=True)
    typed.id, typed.role, typed.plan, typed.terms.plan.value = "case-x", "user_case", "Standard", "Standard"
    result = {m["customer"]: m["status"] for m in match_customers(SEPT_3, "Atlassian", "Jira", load_directory(), scenarios + [typed])}
    assert result["Pinecrest Labs (demo)"] == "no_sla"
