# Third-party data notice

BlueSky Passage 2.2.0 bundles a bit-packed land/water mask generated from the
`lsmask_1.25min_i.bin` dataset distributed by the Matplotlib Basemap data
package (`basemap-data` 2.0.0). The mask is used only as a conservative routing
constraint so comparison paths do not cross modeled dry land. The upstream
Basemap data package is distributed under LGPL-3.0-or-later and includes data
derived from GSHHG/GSHHS. The generated mask is not a nautical chart and must
not be used for navigation or hazard clearance.

The BlueSky Passage source code remains MIT-licensed; this notice applies to the
bundled land-mask data file.
