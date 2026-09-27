# Data provenance and public-sharing review

Publisher: NHS England. Requested source workbook:
https://www.england.nhs.uk/statistics/wp-content/uploads/sites/2/2026/06/Beds-publication-Timeseries-March-2020-May-2026.xlsx

Obtain this exact version from NHS England, save it at the repository root as
`Beds-publication-Timeseries-March-2020-May-2026.xlsx`, and use sheet
`Timeseries all acute trusts`. Local original SHA-256: `d8adc9871e2a519a729d26a8c6fd6517e676ebd34a1ecd23b0fe5d74d20ebe89`.
The file is intentionally excluded and ignored. A different revision may not
reconcile to the frozen forecast and cost evidence.

Official collection context:
https://www.england.nhs.uk/statistics/statistical-work-areas/bed-availability-and-occupancy/

The public data gateway states OGL v3.0 terms:
https://data.england.nhs.uk/terms-conditions
That statement alone is not treated as proof of licensing for this separately
hosted workbook. The main website terms URL presented a browser verification
page during review:
https://www.england.nhs.uk/terms-and-conditions-2/
The workbook URL returned an Excel content type to the web tool, but its remote
bytes were not independently matched to the local file. There is no established
byte-for-byte remote provenance verification in this package.

Before pushing, confirm the applicable attribution/reuse conditions for the
workbook AND derived numeric tables/embedded notebook outputs. Keeping the XLSX
out does not eliminate that question. The included records are aggregate research
artifacts, not patient-level records. Attribution: source data NHS England;
forecasting and illustrative optimization calculations are the author's analysis,
not NHS England estimates or endorsement. No NHS logo is packaged.

The smallest proposed arrangement is this final notebook, its import closure,
minimal frozen-input/reference tables, and a user-downloaded workbook. Broader
model-selection reproducibility would require an additional separately approved
archive, not silently adding old experiments here.
