from decimal import Decimal


def matches_credit(claim: dict, credit: dict) -> bool:
    """Exact demo matching; approximate amounts are never auto-confirmed."""
    fields = ["customer_id", "vendor", "service", "period", "currency", "claim_reference"]
    return all(claim.get(key) is not None and claim[key] == credit.get(key) for key in fields) and Decimal(str(claim["amount"])) == Decimal(str(credit["amount"]))
