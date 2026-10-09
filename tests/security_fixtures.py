"""Explicit administrator provisioning for isolated security acceptance fixtures.

Never imported by product code and never applied to an operator workspace.
"""
def provision_test_kme_identities(runtime):
    from quantum_autopilot.lab.independent_kme import required_kme_identities
    for item in required_kme_identities():
        runtime.grant_identity(item["identity"], item["roles"], item["scopes"], source="service")
