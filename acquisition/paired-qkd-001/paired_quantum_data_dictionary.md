# Paired quantum telemetry acquisition dictionary

Every row is one synchronized observation for one campaign/device/channel.
`timestamp_utc` must include `Z` or an explicit UTC offset. `qber_ratio` is a
fraction in [0,1], never a percentage. `secure_key_rate_bps` is a performance
metric; raw keys/key bits are prohibited. `bell_fidelity_measured` may be null
for the measured-QKD profile, but a Bell-fidelity campaign requires method,
standard error and shot count. `alignment_error_ms` is the absolute optical
versus quantum clock/alignment error and must respect the manifest bound.

Optical fields use pre-FEC BER, OSNR in dB and received/transmit power in dBm.
Identifiers must remain stable across the complete acquisition campaign.
