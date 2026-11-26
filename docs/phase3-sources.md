# Source review and availability contract

Private, personal, noncommercial local research only. No subscriptions were purchased,
no news bodies are redistributed, and local authentication remains mandatory.

Alpaca's [historical-news documentation](https://docs.alpaca.markets/us/docs/historical-news-data)
describes research/backtesting uses and source attribution. Its [news contract](https://docs.alpaca.markets/us/reference/news-3)
specifies timestamps, pagination and update-date ordering. The [service terms](https://s3.amazonaws.com/files.alpaca.markets/disclosures/library/TermsAndConditions.pdf)
restrict use and redistribution; API availability does not grant public/commercial distribution rights.
Existing credentials successfully accessed 2021, 2025 and 2026 news; the completed backfill covers
July 2021 through November 2026 as returned by the provider, not a certified complete original-vintage archive.
The importer is bounded at 600 pages, spaces requests, retries only bounded transient failures and
marks partial/unavailable coverage. It never purchases access or silently substitutes feeds.

Each article version retains ID, symbols, source, author, creation/update/acquisition timestamps and
source hashes. Exact duplicate versions deduplicate. A version cannot enter features before its
updated timestamp; actual live acquisition must also precede cutoff. Later revisions are excluded
from earlier inputs rather than assigning revised content to the creation date. Today’s download
cannot recover deleted articles, earlier symbol tags or all original versions; historical evidence is
explicitly exploratory. No analyst-expectation, earnings-surprise or sentiment values are fabricated.

[SEC submissions and facts](https://www.sec.gov/search-filings/edgar-application-programming-interfaces)
provide accession-linked as-filed observations and acceptance metadata. Unsupported exact times use
the conservative next midnight after filing date. Recent archived arrays extend back to 2016/2017
for these companies; no claim is made that all older archive files were retrieved. Item 2.02 filings
are labeled earnings-filing proxies, not original earnings-release times. Restatements are filtered by
their own publication availability. Units and reporting periods must match financial calculations.

Official announcements come from [Axon IR](https://investor.axon.com/press-releases/)
and [Motorola Solutions newsroom](https://www.motorolasolutions.com/newsroom.html).
Only allowlisted HTTPS pages permitted by robots are fetched, with bounded redirects, timeouts,
page counts and response sizes. Metadata is retained privately with source hashes/links. The current
capture contains nine unique announcements. An accessible Axon 2021 archive listing does not prove
an original-content vintage, so it was not backfilled into training. Exact publication metadata is
preserved when exposed; unknown edits keep availability at acquisition. MSI publication metadata
under an HTML `property` attribute was appended through the versioned publication-enrichment adapter;
no old record, model or trial was rewritten. Guidance directions require explicit raise/lower/reaffirm
language. Contract/acquisition flags are limited lexical classifications, not inferred economic impact.
Rights to redistribute complete releases or train a public text model have not been assumed.

[XLK](https://www.ssga.com/mainfund/XLK) and [XLI](https://www.ssga.com/mainfund/xli)
existed before the study window. Their daily prices use the same authorized SIP feed and split-only
convention. Both are named observable context proxies, not proof of historical membership for MSI
or AXON. The counterpart pair is explicitly selected and remains subject to survivor/selection bias;
it does not unlock pooled-universe claims. Price `asof` still means ticker mapping, not vintage proof.

[FRED/ALFRED vintages](https://fred.stlouisfed.org/docs/api/fred/series_vintagedates.html)
are a later group. A registered key, series-specific terms, release timestamps and revision-aware
joins are prerequisites. Latest revised macro histories will not be silently backfilled.
