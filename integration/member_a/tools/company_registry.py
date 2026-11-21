# Vendored from the member A archive (Data/Reviewer contract v0.1).
# Imports rewritten to integration.member_a so this tree does not collide with legacy root tools/.
"""Only the two companies agreed by the group."""
from copy import deepcopy

COMPANIES = {
    "MSFT": dict(company_id="MSFT", name="Microsoft Corporation", market="NASDAQ",
                 provider_symbols={"yahoo": "MSFT", "sec_cik": "0000789019"},
                 currency="USD", timezone="America/New_York", accounting_standard="US_GAAP"),
    "CSCO": dict(company_id="CSCO", name="Cisco Systems, Inc.", market="NASDAQ",
                 provider_symbols={"yahoo": "CSCO", "sec_cik": "0000858877"},
                 currency="USD", timezone="America/New_York", accounting_standard="US_GAAP"),
}


def resolve_company(ticker):
    if ticker not in COMPANIES:
        raise ValueError("Only MSFT and CSCO are supported by contract v0.1")
    return deepcopy(COMPANIES[ticker])
