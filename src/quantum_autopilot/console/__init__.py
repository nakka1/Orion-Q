"""Native PySide6 operator console for the NISQ Predictive Twin.

The console is a *view*. It renders `console.contract.Snapshot` objects and
nothing else — no HTTP client, no direct database handle, no physical path.
`console.service_provider.ServiceProvider` binds it to the in-process
`AssuranceDashboardService` — for normal operation and, over `demo_service`,
for the demonstration (simulated input, real pipeline). `console.providers.MockProvider`
and `console.synthetic.SyntheticProvider` are labelled UI fixtures for tests.
"""

__version__ = "1.0.0"
