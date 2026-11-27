"""Member A v0.1 Reviewer entry.

Legacy `review(data, assessment, report_md)` remains in the repository-root
`agents` package. This module is only the contract v0.1 `run(...)` adapter.
"""


def run(request, config, data_bundle, analysis_bundle, report_draft):
    from integration.member_a.tools.member_a_review import run_review
    return run_review(request, config, data_bundle, analysis_bundle, report_draft)
