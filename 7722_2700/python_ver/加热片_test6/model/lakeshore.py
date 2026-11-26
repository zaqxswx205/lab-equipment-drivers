"""Lake Shore Model 331 manual, Table D-4, printed D-3 / PDF page 175.

Source: https://www.lakeshore.com/docs/default-source/product-downloads/331_manual.pdf
Original page: output/pdf/Lake_Shore_331_Table_D4_PT100_PT1000.pdf
All 29 breakpoints are transcribed unchanged. The PT-1000 column is exactly
ten times the PT-100 column. Intermediate values use linear interpolation;
this reference curve is not an individual sensor calibration.
"""

PT100_TABLE = (
    (30., 3.820),
    (32., 4.235),
    (36., 5.146),
    (38., 5.650),
    (40., 6.170),
    (42., 6.726),
    (46., 7.909),
    (52., 9.924),
    (58., 12.180),
    (65., 15.015),
    (75., 19.223),
    (85., 23.525),
    (105., 32.081),
    (140., 46.648),
    (180., 62.980),
    (210., 75.044),
    (270., 98.784),
    (315., 116.270),
    (355., 131.616),
    (400., 148.652),
    (445., 165.466),
    (490., 182.035),
    (535., 198.386),
    (585., 216.256),
    (630., 232.106),
    (675., 247.712),
    (715., 261.391),
    (760., 276.566),
    (800., 289.830),
)

TEMPERATURES_K = tuple(t for t, r in PT100_TABLE)
RESISTANCES_PT100 = tuple(r for t, r in PT100_TABLE)
MIN_TEMPERATURE_K = TEMPERATURES_K[0]
MAX_TEMPERATURE_K = TEMPERATURES_K[-1]
MIN_TEMPERATURE_C = round(MIN_TEMPERATURE_K - 273.15, 10)
MAX_TEMPERATURE_C = round(MAX_TEMPERATURE_K - 273.15, 10)
