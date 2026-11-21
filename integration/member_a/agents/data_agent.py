"""Member A v0.1 Data entry.

Legacy `collect(ticker)` remains in the repository-root `agents` package and is
what `python -m pipeline` still calls. This module is only the contract v0.1
`run(request, config, feedback)` adapter.
"""


def run(request, config, feedback=None):
    from integration.member_a.tools.member_a_data import run_data
    return run_data(request, config, feedback)
