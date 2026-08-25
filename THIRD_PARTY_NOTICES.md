# Third-party data notice

BlueSky Passage 2.5.0 retains a bit-packed land/water mask generated from the
`lsmask_1.25min_i.bin` dataset distributed by the Matplotlib Basemap data
package (`basemap-data` 2.0.0). The upstream Basemap data package is distributed
under LGPL-3.0-or-later and includes data derived from GSHHG/GSHHS. The generated
mask is retained for legacy compatibility and regression testing; beginning with
v2.5.0 it is **not** used to certify production route validity.

Production v2.5 route geography is requested on demand from **NOAA ENC Direct to
GIS**. No NOAA ENC dataset is bundled or redistributed by this repository. NOAA
states that the ENC Direct to GIS map services are not intended for navigation;
BlueSky uses their vector land/coverage geometry only as a planning-analysis
constraint and independent route-validation input.

The BlueSky Passage source code remains MIT-licensed. The included LGPL/GSHHG
license files apply to the retained legacy land-mask data file.
