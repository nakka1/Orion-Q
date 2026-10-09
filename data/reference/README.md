# Published reference aggregates

Files in this directory are small, explicitly attributed transcriptions of
numeric values printed in primary papers. They are **not raw datasets** and
must not be used as if rows were independent hardware samples.

## Gavignet et al. (2024)

`gavignet_2024_published_aggregate.csv` transcribes values stated in the prose
of:

> P. Gavignet et al., “Co-Propagation of QKD & 6 Tb/s (60 × 100G) DWDM
> Channels With ~17 dBm Total WDM Power in Single and Multi-Span
> Configurations,” *Journal of Lightwave Technology* 42(4), 1321–1327.

- arXiv: [2312.11924](https://arxiv.org/abs/2312.11924)
- DOI recorded by the accepted manuscript: `10.1109/JLT.2023.3324840`
- Bibliographic page: [Optica/JLT](https://opg.optica.org/jlt/abstract.cfm?uri=jlt-42-4-1321)

The arXiv source archive contains TeX and figure PDFs, but no CSV, spreadsheet,
or measurement log. The six rows here therefore retain
`observation_level=published_condition_mean` or `published_24h_mean`, use the
paper's approximate values without invented precision, and record the source
location. They support a descriptive cross-platform check only.

Do not bootstrap these rows, estimate neural models from them, treat the three
24-hour means as timestamps, or infer measurement uncertainty that the paper
does not report.
